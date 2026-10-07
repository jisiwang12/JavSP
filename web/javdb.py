"""从JavDB抓取数据：优先走App私有API，失败时回退到网页抓取"""
import os
import re
import sys
import time
import hashlib
import logging
import urllib.parse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from web.base import Request, resp2html
from web.exceptions import *
from core.func import *
from core.avid import guess_av_type
from core.config import cfg
from core.datatype import MovieInfo, GenreMap
from core.chromium import get_browsers_cookies


logger = logging.getLogger(__name__)
genre_map = GenreMap('data/genre_javdb.csv')
permanent_url = 'https://javdb.com'
if cfg.Network.proxy:
    base_url = permanent_url
else:
    base_url = cfg.ProxyFree.javdb


# ---------------------------------------------------------------------------
# App 私有 API（主路径）
# ---------------------------------------------------------------------------
API_BASE = 'https://jdforrepam.com/api'
API_SECRET = (
    '71cf27bb3c0bcdf207b64abecddc970098c7421ee7203b9cdae54478478a199e'
    '7d5a6e1a57691123c1a931c057842fb73ba3b3c83bcd69c17ccf174081e3d8aa'
)
API_UA = 'Dart/3.5 (dart:io)'
# 服务端按时间戳容忍约300秒，签名可短时复用
_sign_cache = {'sig': None, 'ts': 0}

api_request = Request()
api_request.timeout = max(cfg.Network.timeout, 20)
api_request.headers.update({
    'User-Agent': API_UA,
    'Accept-Language': 'zh-TW',
})


def _api_sign() -> str:
    now = time.time()
    if _sign_cache['sig'] and now - _sign_cache['ts'] < 240:
        return _sign_cache['sig']
    ts = str(int(now))
    digest = hashlib.md5((ts + API_SECRET).encode()).hexdigest()
    sig = f'{ts}.lpw6vgqzsp.{digest}'
    _sign_cache.update(sig=sig, ts=now)
    return sig


def _fix_img(url):
    """API返回的图片域名是负载均衡占位，替换为官方CDN"""
    if not url:
        return url
    return re.sub(r'https://.*?/rhe951l4q', 'https://c0.jdbstatic.com', url)


def _api_get(path, **params):
    """请求私有API并返回data字段"""
    sig = _api_sign()
    api_request.headers['jdsignature'] = sig
    api_request.headers['jdSignature'] = sig
    qs = '&'.join(f'{k}={urllib.parse.quote(str(v))}' for k, v in params.items() if v is not None)
    url = f'{API_BASE}{path}' + (f'?{qs}' if qs else '')
    r = api_request.get(url, delay_raise=True)
    if r.status_code == 404:
        raise WebsiteError(f'JavDB API: 404 Not Found: {path}')
    if r.status_code != 200:
        raise WebsiteError(f'JavDB API: {r.status_code} 非预期状态码: {url}')
    try:
        body = r.json()
    except ValueError:
        raise WebsiteError(f'JavDB API: 响应不是JSON: {url}')
    if not body.get('success'):
        action = body.get('action') or ''
        message = body.get('message') or ''
        if action == 'JWTVerificationError':
            raise CredentialError(f'JavDB API: 需要登录: {message}')
        if action in ('ParameterInvalid', 'NonExistentUser'):
            raise WebsiteError(f'JavDB API: {action}: {message}')
        # 签名被拒/密钥失效等一律视为站点阻断，触发网页兜底
        raise SiteBlocked(f'JavDB API: {action or "失败"}: {message or url}')
    return body.get('data') or {}


def _norm_code(s) -> str:
    """归一化番号用于匹配：忽略大小写与连字符/下划线/空格"""
    return re.sub(r'[-_\s]', '', s or '').upper()


def _search_movie_id(dvdid: str) -> str:
    """番号 → 影片内部id。0个匹配抛MovieNotFoundError，多个精确匹配抛MovieDuplicateError"""
    candidates = [dvdid]
    normalized = re.sub(r'[-_\s]', '', dvdid)
    if normalized and normalized != dvdid:
        candidates.append(normalized)
    # 标题第一个空白分隔token（dvdid本身通常已是）
    first_token = dvdid.split()[0] if dvdid.split() else ''
    if first_token and first_token not in candidates:
        candidates.append(first_token)

    target = _norm_code(dvdid)
    seen_ids = set()
    matched = []
    for q in candidates:
        data = _api_get(
            '/v2/search', q=q, type='movie', page=1, limit=20,
            movie_type='all', from_recent='false',
            movie_filter_by='all', movie_sort_by='relevance',
        )
        for m in data.get('movies') or []:
            mid = m.get('id')
            if not mid or mid in seen_ids:
                continue
            seen_ids.add(mid)
            if _norm_code(m.get('number')) == target:
                matched.append(mid)
        if matched:
            break
        time.sleep(0.3)
    if not matched:
        raise MovieNotFoundError(__name__, dvdid)
    if len(matched) > 1:
        raise MovieDuplicateError(__name__, dvdid, len(matched))
    return matched[0]


def parse_data_api(movie: MovieInfo):
    """通过App私有API抓取并解析指定番号的数据"""
    movie_id = _search_movie_id(movie.dvdid)
    time.sleep(0.3)
    detail = _api_get(f'/v4/movies/{movie_id}').get('movie') or {}
    if not detail:
        raise MovieNotFoundError(__name__, movie.dvdid)

    # 标题：origin_title优先作为日文原标题，title去掉番号前缀
    title_raw = (detail.get('title') or '').strip()
    origin_title = (detail.get('origin_title') or '').strip()
    movie.ori_title = (origin_title or title_raw) or None
    title = re.sub(r'^[A-Za-z]+-\d+\s*', '', title_raw or origin_title)
    dvdid = (detail.get('number') or movie.dvdid or '').strip()
    movie.title = title.replace(dvdid, '').strip() or None

    movie.dvdid = dvdid or movie.dvdid
    movie.url = f'{permanent_url}/v/{movie_id}'
    movie.plot = (detail.get('summary') or '').strip() or None
    movie.cover = _fix_img(detail.get('cover_url'))
    movie.preview_pics = [_fix_img(p.get('large_url') or p.get('url')) for p in detail.get('preview_images') or []] or None
    movie.preview_video = detail.get('preview_video_url') or None

    # 评分：API为5分制字符串，统一换算为10分制
    score = detail.get('score')
    if score:
        try:
            movie.score = '{:.2f}'.format(float(score) * 2)
        except ValueError:
            pass

    publish_date = (detail.get('release_date') or '').strip()
    if publish_date and publish_date != '0000-00-00':
        movie.publish_date = publish_date
    duration = detail.get('duration')
    if duration:
        movie.duration = str(duration)

    movie.director = (detail.get('director_name') or '').strip() or None
    movie.producer = (detail.get('maker_name') or '').strip() or None
    movie.publisher = (detail.get('publisher_name') or '').strip() or None
    movie.serial = (detail.get('series_name') or '').strip() or None

    # 标签：API返回的是展示名，无法对应网页版tag id，直接用名称做genre
    genre = [t.get('name') for t in detail.get('tags') or [] if t.get('name')]
    movie.genre = genre or None

    # type: 0=有码类；无码/欧美等类型文档未给出取值，仅在确认有码时写False
    if detail.get('type') == 0:
        movie.uncensored = False

    # 女优：gender 0=女 1=男
    actress = [a.get('name') for a in detail.get('actors') or []
               if a.get('gender') == 0 and a.get('name')]
    movie.actress = actress or None

    # 磁力：独立端点，拼装为magnet URI。失败不影响主流程
    try:
        time.sleep(0.3)
        magnets = _api_get(f'/v1/movies/{movie_id}/magnets').get('magnets') or []
    except Exception as e:
        logger.debug(f'JavDB API: 获取磁力失败: {e}', exc_info=True)
        magnets = []
    links = []
    for m in magnets:
        h = m.get('hash')
        if not h:
            continue
        name = (m.get('name') or '').replace('[javdb.com]', '')
        links.append(f'magnet:?xt=urn:btih:{h}&dn={name}' if name else f'magnet:?xt=urn:btih:{h}')
    movie.magnet = links or None


# ---------------------------------------------------------------------------
# 网页抓取（兜底路径）
# ---------------------------------------------------------------------------
# 初始化Request实例。使用scraper绕过CloudFlare后，需要指定网页语言，否则可能会返回其他语言网页，影响解析
request = Request(use_scraper=True)
request.headers['Accept-Language'] = 'zh-CN,zh;q=0.9,zh-TW;q=0.8,en-US;q=0.7,en;q=0.6,ja;q=0.5'


def _load_cookies_pool():
    """加载浏览器Cookies池，仅在首次需要时读取"""
    global cookies_pool
    if 'cookies_pool' not in globals():
        try:
            cookies_pool = get_browsers_cookies()
        except (PermissionError, OSError) as e:
            logger.warning(f"无法从浏览器Cookies文件获取JavDB的登录凭据({e})，可能是安全软件在保护浏览器Cookies文件", exc_info=True)
            cookies_pool = []
        except Exception as e:
            logger.warning(f"获取JavDB的登录凭据时出错({e})，你可能使用的是国内定制版等非官方Chrome系浏览器", exc_info=True)
            cookies_pool = []
    return cookies_pool


def _try_cookies_bypass(url):
    """尝试使用浏览器Cookies绕过Cloudflare或登录限制，成功返回html，失败返回None"""
    global request
    pool = _load_cookies_pool()
    while len(pool) > 0:
        item = pool.pop()
        # 更换Cookies时需要创建新的request实例，否则cloudscraper会保留它内部第一次发起网络访问时获得的Cookies
        request = Request(use_scraper=True)
        request.cookies = item['cookies']
        cookies_source = (item['profile'], item['site'])
        logger.debug(f'尝试使用浏览器Cookies绕过: {cookies_source}')
        r = request.get(url, delay_raise=True)
        if r.status_code == 200:
            if r.history and '/login' in r.url:
                # 这组Cookies也过期了，继续尝试下一组
                logger.debug(f'{cookies_source}: Cookies已过期，跳过')
                continue
            html = resp2html(r)
            return html
        elif r.status_code in (403, 503):
            # 这组Cookies也没能绕过，继续尝试下一组
            logger.debug(f'{cookies_source}: 仍然被Cloudflare阻断({r.status_code})，尝试下一组Cookies')
            continue
        else:
            # 非预期状态码，不再继续尝试
            break
    return None


def get_html_wrapper(url):
    """包装外发的request请求并负责转换为可xpath的html，同时处理Cookies无效等问题"""
    global request
    r = request.get(url, delay_raise=True)
    if r.status_code == 200:
        # 发生重定向可能仅仅是域名重定向，因此还要检查url以判断是否被跳转到了登录页
        if r.history and '/login' in r.url:
            html = _try_cookies_bypass(url)
            if html is not None:
                return html
            raise CredentialError('JavDB: 所有浏览器Cookies均已过期')
        elif r.history and 'pay' in r.url.split('/')[-1]:
            raise SitePermissionError(f"JavDB: 此资源被限制为仅VIP可见: '{r.history[0].url}'")
        else:
            html = resp2html(r)
            return html
    elif r.status_code in (403, 503):
        # Cloudflare拦截，尝试使用浏览器Cookies绕过
        html = _try_cookies_bypass(url)
        if html is not None:
            return html
        # 所有Cookies均失败，报错
        html = resp2html(r)
        code_tag = html.xpath("//span[@class='code-label']/span")
        error_code = code_tag[0].text if code_tag else None
        if error_code:
            if error_code == '1020':
                block_msg = f'JavDB: {r.status_code} 禁止访问: 站点屏蔽了来自日本地区的IP地址，请使用其他地区的代理服务器'
            else:
                block_msg = f'JavDB: {r.status_code} 禁止访问: {url} (Error code: {error_code})'
        else:
            block_msg = f'JavDB: {r.status_code} 禁止访问: {url}'
        raise SiteBlocked(block_msg)
    else:
        raise WebsiteError(f'JavDB: {r.status_code} 非预期状态码: {url}')


def get_user_info(site, cookies):
    """获取cookies对应的JavDB用户信息"""
    try:
        request.cookies = cookies
        html = request.get_html(f'https://{site}/users/profile')
    except Exception as e:
        logger.info('JavDB: 获取用户信息时出错')
        logger.debug(e, exc_info=1)
        return
    # 扫描浏览器得到的Cookies对应的临时域名可能会过期，因此需要先判断域名是否仍然指向JavDB的站点
    if 'JavDB' in html.text:
        email = html.xpath("//div[@class='user-profile']/ul/li[1]/span/following-sibling::text()")[0].strip()
        username = html.xpath("//div[@class='user-profile']/ul/li[2]/span/following-sibling::text()")[0].strip()
        return email, username
    else:
        logger.debug('JavDB: 域名已过期: ' + site)


def get_valid_cookies():
    """扫描浏览器，获取一个可用的Cookies"""
    # 经测试，Cookies所发往的域名不需要和登录时的域名保持一致，只要Cookies有效即可在多个域名间使用
    for d in cookies_pool:
        info = get_user_info(d['site'], d['cookies'])
        if info:
            return d['cookies']
        else:
            logger.debug(f"{d['profile']}, {d['site']}: Cookies无效")


def parse_data_web(movie: MovieInfo):
    """从网页抓取并解析指定番号的数据（API不可用时的兜底）
    Args:
        movie (MovieInfo): 要解析的影片信息，解析后的信息直接更新到此变量内
    """
    # JavDB搜索番号时会有多个搜索结果，从中查找匹配番号的那个
    html = get_html_wrapper(f'{base_url}/search?q={movie.dvdid}')
    ids = list(map(str.lower, html.xpath("//div[@class='video-title']/strong/text()")))
    movie_urls = html.xpath("//a[@class='box']/@href")
    match_count = len([i for i in ids if i == movie.dvdid.lower()])
    if match_count == 0:
        raise MovieNotFoundError(__name__, movie.dvdid, ids)
    elif match_count == 1:
        index = ids.index(movie.dvdid.lower())
        new_url = movie_urls[index]
        try:
            html2 = get_html_wrapper(new_url)
        except (SitePermissionError, CredentialError):
            # 不开VIP不让看，过分。决定榨出能获得的信息，毕竟有时候只有这里能找到标题和封面
            box = html.xpath("//a[@class='box']")[index]
            movie.url = new_url
            movie.title = box.get('title')
            movie.cover = box.xpath("div/img/@src")[0]
            score_str = box.xpath("div[@class='score']/span/span")[0].tail
            score = re.search(r'([\d.]+)分', score_str).group(1)
            movie.score = "{:.2f}".format(float(score)*2)
            movie.publish_date = box.xpath("div[@class='meta']/text()")[0].strip()
            return
    else:
        raise MovieDuplicateError(__name__, movie.dvdid, match_count)

    container = html2.xpath("/html/body/section/div/div[@class='video-detail']")[0]
    info = container.xpath("//nav[@class='panel movie-panel-info']")[0]
    title_raw = container.xpath("h2/strong[@class='current-title']/text()")[0]
    # 保留日文原标题（current-title本身就是日文）
    movie.ori_title = title_raw.strip()
    # 去掉标题开头的番号（如 "IPZZ-895 家中..." -> "家中..."）
    title = re.sub(r'^[A-Za-z]+-\d+\s*', '', title_raw)
    show_orig_title = container.xpath("//a[contains(@class, 'meta-link') and not(contains(@style, 'display: none'))]")
    if show_orig_title:
        movie.ori_title = container.xpath("h2/span[@class='origin-title']/text()")[0]
    cover = container.xpath("//img[@class='video-cover']/@src")[0]
    preview_pics = container.xpath("//a[@class='tile-item'][@data-fancybox='gallery']/@href")
    preview_video_tag = container.xpath("//video[@id='preview-video']/source/@src")
    if preview_video_tag:
        preview_video = preview_video_tag[0]
        if preview_video.startswith('//'):
            preview_video = 'https:' + preview_video
        movie.preview_video = preview_video
    dvdid = info.xpath("div/span")[0].text_content()
    publish_date = info.xpath("div/strong[text()='日期:']")[0].getnext().text
    duration = info.xpath("div/strong[text()='時長:']")[0].getnext().text.replace('分鍾', '').strip()
    director_tag = info.xpath("div/strong[text()='導演:']")
    if director_tag:
        movie.director = director_tag[0].getnext().text_content().strip()
    av_type = guess_av_type(movie.dvdid)
    if av_type != 'fc2':
        producer_tag = info.xpath("div/strong[text()='片商:']")
    else:
        producer_tag = info.xpath("div/strong[text()='賣家:']")
    if producer_tag:
        movie.producer = producer_tag[0].getnext().text_content().strip()
    publisher_tag = info.xpath("div/strong[text()='發行:']")
    if publisher_tag:
        movie.publisher = publisher_tag[0].getnext().text_content().strip()
    serial_tag = info.xpath("div/strong[text()='系列:']")
    if serial_tag:
        movie.serial = serial_tag[0].getnext().text_content().strip()
    score_tag = info.xpath("//span[@class='score-stars']")
    if score_tag:
        score_str = score_tag[0].tail
        score = re.search(r'([\d.]+)分', score_str).group(1)
        movie.score = "{:.2f}".format(float(score)*2)
    genre_tags = info.xpath("//strong[text()='類別:']/../span/a")
    genre, genre_id = [], []
    for tag in genre_tags:
        pre_id = tag.get('href').split('/')[-1]
        genre.append(tag.text)
        genre_id.append(pre_id)
        # 判定影片有码/无码
        subsite = pre_id.split('?')[0]
        movie.uncensored = {'uncensored': True, 'tags':False}.get(subsite)
    # JavDB目前同时提供男女优信息，根据用来标识性别的符号筛选出女优
    actors_tag = info.xpath("//strong[text()='演員:']/../span")[0]
    all_actors = actors_tag.xpath("a/text()")
    genders = actors_tag.xpath("strong/text()")
    actress = [i for i in all_actors if genders[all_actors.index(i)] == '♀']
    magnet = container.xpath("//div[@class='magnet-name column is-four-fifths']/a/@href")

    movie.dvdid = dvdid
    movie.url = new_url.replace(base_url, permanent_url)
    movie.title = title.replace(dvdid, '').strip()
    movie.cover = cover
    movie.preview_pics = preview_pics
    movie.publish_date = publish_date
    movie.duration = duration
    movie.genre = genre
    movie.genre_id = genre_id
    movie.actress = actress
    movie.magnet = [i.replace('[javdb.com]','') for i in magnet]


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def parse_data(movie: MovieInfo):
    """从JavDB抓取并解析指定番号的数据：优先私有API，失败则回退网页抓取"""
    try:
        parse_data_api(movie)
        return
    except (MovieNotFoundError, MovieDuplicateError):
        # 搜索无结果/结果不唯一时仍尝试网页（两边索引可能不一致）
        logger.debug('JavDB API: 搜索未命中，回退到网页抓取')
    except Exception as e:
        logger.warning(f'JavDB API抓取失败，回退到网页抓取: {e}', exc_info=True)
    parse_data_web(movie)


def parse_clean_data(movie: MovieInfo):
    """解析指定番号的影片数据并进行清洗"""
    try:
        parse_data(movie)
        # 检查封面URL是否真的存在对应图片
        if movie.cover is not None:
            r = request.head(movie.cover)
            if r.status_code != 200:
                movie.cover = None
    except SiteBlocked:
        raise
    if movie.genre_id and (not movie.genre_id[0].startswith('fc2?')):
        movie.genre_norm = genre_map.map(movie.genre_id)
        movie.genre_id = None   # 没有别的地方需要再用到，清空genre id（表明已经完成转换）


def collect_actress_alias(type=0, use_original=True):
    """
    收集女优的别名
    type: 0-有码, 1-无码, 2-欧美
    use_original: 是否使用原名而非译名，True-田中レモン，False-田中檸檬
    """
    import json
    import random

    actressAliasMap = {}

    actressAliasFilePath = "data/actress_alias.json"
    # 检查文件是否存在
    if not os.path.exists(actressAliasFilePath):
        # 如果文件不存在，创建文件并写入空字典
        with open(actressAliasFilePath, "w", encoding="utf-8") as file:
            json.dump({}, file)

    typeList = ["censored", "uncensored", "western"]
    page_url = f"{base_url}/actors/{typeList[type]}"
    while True:
        try:
            html = get_html_wrapper(page_url)
            actors = html.xpath("//div[@class='box actor-box']/a")

            count = 0
            for actor in actors:
                count += 1
                actor_name = actor.xpath("strong/text()")[0].strip()
                actor_url = actor.xpath("@href")[0]
                # actor_url = f"https://javdb.com{actor_url}"  # 构造演员主页的完整URL

                # 进入演员主页，获取更多信息
                actor_html = get_html_wrapper(actor_url)
                # 解析演员所有名字信息
                names_span = actor_html.xpath("//span[@class='actor-section-name']")[0]
                aliases_span_list = actor_html.xpath("//span[@class='section-meta']")
                aliases_span = aliases_span_list[0]

                names_list = [name.strip() for name in names_span.text.split(",")]
                if len(aliases_span_list) > 1:
                    aliases_list = [
                        alias.strip() for alias in aliases_span.text.split(",")
                    ]
                else:
                    aliases_list = []

                # 将信息添加到actressAliasMap中
                actressAliasMap[names_list[-1 if use_original else 0]] = (
                    names_list + aliases_list
                )
                print(
                    f"{count} --- {names_list[-1 if use_original else 0]}: {names_list + aliases_list}"
                )

                if count == 10:
                    # 将数据写回文件
                    with open(actressAliasFilePath, "r", encoding="utf-8") as file:
                        existing_data = json.load(file)

                    # 合并现有数据和新爬取的数据
                    existing_data.update(actressAliasMap)

                    # 将合并后的数据写回文件
                    with open(actressAliasFilePath, "w", encoding="utf-8") as file:
                        json.dump(existing_data, file, ensure_ascii=False, indent=2)

                    actressAliasMap = {}  # 重置actressAliasMap

                    print(
                        f"已爬取 {count} 个女优，数据已更新并写回文件:",
                        actressAliasFilePath,
                    )

                    # 重置计数器
                    count = 0

                time.sleep(max(1, 10 * random.random()))  # 随机等待 1-10 秒

            # 判断是否有下一页按钮
            next_page_link = html.xpath(
                "//a[@rel='next' and @class='pagination-next']/@href"
            )
            if not next_page_link:
                break  # 没有下一页，结束循环
            else:
                next_page_url = f"{next_page_link[0]}"
                page_url = next_page_url

        except SiteBlocked:
            raise

    with open(actressAliasFilePath, "r", encoding="utf-8") as file:
        existing_data = json.load(file)

    # 合并现有数据和新爬取的数据
    existing_data.update(actressAliasMap)

    # 将合并后的数据写回文件
    with open(actressAliasFilePath, "w", encoding="utf-8") as file:
        json.dump(existing_data, file, ensure_ascii=False, indent=2)

    print(f"已爬取 {count} 个女优，数据已更新并写回文件:", actressAliasFilePath)


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    # collect_actress_alias()
    movie = MovieInfo('FC2-2735981')
    try:
        parse_clean_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
