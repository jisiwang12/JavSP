"""从JavLibrary抓取数据"""
import os
import sys
import logging
from urllib.parse import urlsplit
from requests.exceptions import ConnectionError


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from web.base import Request, resp2html
from web.exceptions import *
from web.proxyfree import get_proxy_free_url
from core.config import cfg
from core.datatype import MovieInfo
from core.chromium import get_browsers_cookies


# 初始化Request实例
request = Request(use_scraper=True)

logger = logging.getLogger(__name__)
permanent_url = 'https://www.javlibrary.com'
base_url = ''


def _load_cookies_pool():
    """加载浏览器Cookies池，仅在首次需要时读取"""
    global cookies_pool
    if 'cookies_pool' not in globals():
        try:
            cookies_pool = get_browsers_cookies()
        except (PermissionError, OSError) as e:
            logger.warning(f"无法从浏览器Cookies文件获取JavLib的登录凭据({e})，可能是安全软件在保护浏览器Cookies文件", exc_info=True)
            cookies_pool = []
        except Exception as e:
            logger.warning(f"获取JavLib的登录凭据时出错({e})，你可能使用的是国内定制版等非官方Chrome系浏览器", exc_info=True)
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
        logger.debug(f'尝试使用浏览器Cookies绕过JavLib: {cookies_source}')
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
            logger.debug(f'{cookies_source}: 非预期状态码({r.status_code})，停止尝试')
            break
    return None


def init_network_cfg():
    """设置合适的代理模式和base_url"""
    request.timeout = 5
    proxy_free_url = get_proxy_free_url('javlib')
    urls = [cfg.ProxyFree.javlib, permanent_url]
    if proxy_free_url and proxy_free_url not in urls:
        urls.insert(1, proxy_free_url)
    # 先尝试不使用代理访问（代理容易触发CloudFlare）
    logger.debug('尝试不使用代理访问JavLib')
    request.proxies = {}
    for url in urls:
        try:
            resp = request.get(url, delay_raise=True)
            if resp.status_code == 200:
                request.timeout = cfg.Network.timeout
                logger.debug(f'不使用代理成功访问: {url}')
                return url
        except Exception as e:
            logger.debug(f"不使用代理访问失败 '{url}': {e}")
    # 如果不使用代理失败，再尝试使用代理
    if cfg.Network.proxy:
        logger.debug('不使用代理失败，尝试使用代理访问JavLib')
        request.proxies = cfg.Network.proxy
        for url in urls:
            try:
                resp = request.get(url, delay_raise=True)
                if resp.status_code == 200:
                    request.timeout = cfg.Network.timeout
                    logger.debug(f'使用代理成功访问: {url}')
                    return url
            except Exception as e:
                logger.debug(f"使用代理访问失败 '{url}': {e}")
    logger.warning('无法绕开JavLib的反爬机制')
    request.timeout = cfg.Network.timeout
    return permanent_url


# TODO: 发现JavLibrary支持使用cid搜索，会直接跳转到对应的影片页面，也许可以利用这个功能来做cid到dvdid的转换
def parse_data(movie: MovieInfo):
    """解析指定番号的影片数据"""
    global base_url
    if not base_url:
        base_url = init_network_cfg()
        logger.debug(f"JavLib网络配置: {base_url}, proxy={request.proxies}")
    url = new_url = f'{base_url}/cn/vl_searchbyid.php?keyword={movie.dvdid}'
    resp = request.get(url, delay_raise=True)
    # 如果请求失败（403/503），尝试使用浏览器Cookies绕过
    if resp.status_code in (403, 503):
        logger.debug(f"JavLib返回{resp.status_code}，尝试使用浏览器Cookies绕过")
        html = _try_cookies_bypass(url)
        if html is None:
            raise SiteBlocked(__name__, movie.dvdid)
        # 使用Cookies绕过成功，直接解析html
        resp = None  # 标记resp已失效
    else:
        html = resp2html(resp)
    if resp and resp.history:
        if urlsplit(resp.url).netloc == urlsplit(base_url).netloc:
            # 出现301重定向通常且新老地址netloc相同时，说明搜索到了影片且只有一个结果
            new_url = resp.url
        else:
            # 重定向到了不同的netloc时，新地址并不是影片地址。这种情况下新地址中丢失了path字段，
            # 为无效地址（应该是JavBus重定向配置有问题），需要使用新的base_url抓取数据
            base_url = 'https://' + urlsplit(resp.url).netloc
            logger.warning(f"请将配置文件中的JavLib免代理地址更新为: {base_url}")
            return parse_data(movie)
    elif not resp:
        # 使用Cookies绕过成功，html已经是搜索结果页面
        # 检查是否有多个搜索结果
        video_tags = html.xpath("//div[@class='video'][@id]/a")
        if video_tags:
            # 有搜索结果，选择第一个匹配的
            for tag in video_tags:
                tag_dvdid = tag.xpath("div[@class='id']/text()")[0]
                if tag_dvdid.upper() == movie.dvdid.upper():
                    new_url = tag.get('href')
                    html = request.get_html(new_url)
                    break
    else:   # 如果有多个搜索结果则不会自动跳转，此时需要程序介入选择搜索结果
        video_tags = html.xpath("//div[@class='video'][@id]/a")
        # 通常第一部影片就是我们要找的，但是以免万一还是遍历所有搜索结果
        pre_choose = []
        for tag in video_tags:
            tag_dvdid = tag.xpath("div[@class='id']/text()")[0]
            if tag_dvdid.upper() == movie.dvdid.upper():
                pre_choose.append(tag)
        pre_choose_urls = [i.get('href') for i in pre_choose]
        match_count = len(pre_choose)
        if match_count == 0:
            raise MovieNotFoundError(__name__, movie.dvdid)
        elif match_count == 1:
            new_url = pre_choose_urls[0]
        elif match_count == 2:
            no_blueray = []
            for tag in pre_choose:
                if 'ブルーレイディスク' not in tag.get('title'):    # Blu-ray Disc
                    no_blueray.append(tag)
            no_blueray_count = len(no_blueray)
            if no_blueray_count == 1:
                new_url = no_blueray[0].get('href')
                logger.debug(f"'{movie.dvdid}': 存在{match_count}个同番号搜索结果，已自动选择封面比例正确的一个: {new_url}")
            else:
                # 两个结果中没有谁是蓝光影片，说明影片番号重复了
                raise MovieDuplicateError(__name__, movie.dvdid, match_count, pre_choose_urls)
        else:
            # 存在不同影片但是番号相同的情况，如MIDV-010
            raise MovieDuplicateError(__name__, movie.dvdid, match_count, pre_choose_urls)
        # 重新抓取网页
        html = request.get_html(new_url)
    container = html.xpath("/html/body/div/div[@id='rightcolumn']")[0]
    title_tag = container.xpath("div/h3/a/text()")
    title = title_tag[0]
    cover = container.xpath("//img[@id='video_jacket_img']/@src")[0]
    info = container.xpath("//div[@id='video_info']")[0]
    dvdid = info.xpath("div[@id='video_id']//td[@class='text']/text()")[0]
    publish_date = info.xpath("div[@id='video_date']//td[@class='text']/text()")[0]
    duration = info.xpath("div[@id='video_length']//span[@class='text']/text()")[0]
    director_tag = info.xpath("//span[@class='director']/a/text()")
    if director_tag:
        movie.director = director_tag[0]
    producer = info.xpath("//span[@class='maker']/a/text()")[0]
    publisher_tag = info.xpath("//span[@class='label']/a/text()")
    if publisher_tag:
        movie.publisher = publisher_tag[0]
    score_tag = info.xpath("//span[@class='score']/text()")
    if score_tag:
        movie.score = score_tag[0].strip('()')
    genre = info.xpath("//span[@class='genre']/a/text()")
    actress = info.xpath("//span[@class='star']/a/text()")

    movie.dvdid = dvdid
    movie.url = new_url.replace(base_url, permanent_url)
    movie.title = title.replace(dvdid, '').strip()
    if cover.startswith('//'):  # 补全URL中缺少的协议段
        cover = 'https:' + cover
    movie.cover = cover
    movie.publish_date = publish_date
    movie.duration = duration
    movie.producer = producer
    movie.genre = genre
    movie.actress = actress


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    base_url = permanent_url
    movie = MovieInfo('IPX-177')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
