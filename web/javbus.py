"""从JavBus抓取数据"""
import os
import re
import sys
import logging
import threading

import requests
import lxml.html


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from web.base import *
from web.exceptions import *
from core.func import *
from core.config import cfg
from core.datatype import MovieInfo, GenreMap


logger = logging.getLogger(__name__)
genre_map = GenreMap('data/genre_javbus.csv')
permanent_url = 'https://www.javbus.com'
if cfg.Network.proxy:
    base_url = permanent_url
else:
    base_url = cfg.ProxyFree.javbus

# JavBus的“地区年龄检测”通过后服务端会下发 dv=1 cookie，之后才可正常访问影片页。
# 检测流程为：勾选“我已经成年” → 答5道驾照理论题 → 通过后种下 dv cookie。
_session = requests.Session()
_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
})
_verify_lock = threading.Lock()

# 驾照理论题库：题干独有关键词 -> 正确选项。用于自动通过年龄检测；命中时取最长关键词。
_QUIZ_BANK = [
    # 责任认定
    ('故意破坏、伪造现场、毁灭证据的，应承担什么责任', 'A'),
    ('发生事故后故意损坏、伪造现场、毁灭证据', 'C'),
    ('哪种行为，要承担交通事故全部责任', 'C'),
    # 记分
    ('造成致人轻伤以上或者死亡的交通事故后逃逸', 'B'),
    ('高速公路上倒车', 'B'),
    ('人行横道，不按规定减速', 'C'),
    ('高速公路或城市快速路上不按规定车道行驶', 'D'),
    # 申领/换证
    ('考试过程中有贿赂.舞弊行为', 'D'),
    ('考试过程中有贿赂、舞弊行为', 'D'),
    ('在考试过程中有舞弊行为', 'B'),
    ('一年内不得再次申领机动车驾驶证', 'B'),
    ('隐瞒有关情况或提供虚假材料', 'A'),
    ('超过机动车驾驶证有效期一年以上未换证被注销', 'B'),
    ('有效期满前多长时间申请换证', 'C'),
    ('信息发生变化的要在多长时间内申请换证', 'C'),
    # 准驾车型/年龄
    ('初次申领机动车驾驶证的，可以申请以下哪种准驾车型', 'A'),
    ('申请轻型牵引挂车准驾车型的，年龄不得超过', 'C'),
    ('70周岁以上的驾驶人多长时间要提交一次身体条件证明', 'B'),
    # 其他
    ('实习期内驾驶人驾驶机动车上高速公路', 'C'),
    ('户籍迁出原车辆管理所', 'D'),
    ('尚未注册登记，需要临时上道路行驶', 'A'),
    ('逾期不参加审验仍然驾驶机动车', 'A'),
]


def _guess_quiz_answer(question):
    best, best_len = None, 0
    for kw, ans in _QUIZ_BANK:
        if kw in question and len(kw) > best_len:
            best, best_len = ans, len(kw)
    return best


def _extract_quiz(html_text):
    """解析年龄测验题目，返回 [{id, question, options}]"""
    doc = lxml.html.fromstring(html_text)
    questions = []
    for li in doc.xpath('//li'):
        radios = li.xpath('.//input[@type="radio"]')
        if not radios:
            continue
        m = re.match(r'userAnswers\[(\d+)\]', radios[0].get('name') or '')
        if not m:
            continue
        options = {}
        for r in radios:
            tail = re.sub(r'^[A-D]\.\s*', '', (r.tail or '').strip()).strip()
            options[r.get('value')] = tail
        raw = li.text_content()
        for val, text in options.items():
            raw = raw.replace(f'{val}. {text}', '')
        questions.append({
            'id': m.group(1),
            'question': re.sub(r'\s+', ' ', raw).strip(),
            'options': options,
        })
    return questions


def _is_age_gate(resp):
    """判断响应是否落在年龄检测页"""
    if resp.url and 'driver-verify' in resp.url:
        return True
    text = resp.text or ''
    return 'Age Verification' in text or '年齡測驗' in text or '你是否已經成年' in text


def _pass_age_gate(target_url):
    """通过 JavBus 的地区年龄检测（驾照理论题），成功后 session 带上 dv cookie

    每轮测验随机抽题，出现未知题目时清空 cookie 重试以换取新题。
    """
    last_unknown = []
    for attempt in range(5):
        with _verify_lock:
            if _session.cookies.get('dv'):
                return
            # 未通过时清空 cookie，重新进入测验以换一批题目
            _session.cookies.clear()
            resp = _session.get(target_url, proxies=cfg.Network.proxy,
                                timeout=cfg.Network.timeout, allow_redirects=True)
            text = resp.text or ''
            if 'userAnswers[' not in text:
                # 第一步：勾选“我已经成年”进入测验
                r = _session.post(resp.url, data={'Submit': '確認'},
                                  proxies=cfg.Network.proxy, timeout=cfg.Network.timeout,
                                  allow_redirects=True)
                text = r.text
            questions = _extract_quiz(text)
            if not questions:
                raise SiteBlocked(f'无法解析JavBus年龄测验题目: {target_url}')
            answers = {}
            unknown = []
            for q in questions:
                ans = _guess_quiz_answer(q['question'])
                if ans:
                    answers[f'userAnswers[{q["id"]}]'] = ans
                else:
                    unknown.append(q['question'])
            if unknown:
                last_unknown = unknown
                logger.debug(f'JavBus年龄测验第{attempt+1}轮出现未知题目，换题重试')
                continue
            answers['submit'] = 'question'
            action = f'{permanent_url}/doc/driver-verify.php?referer=' + \
                requests.utils.quote(target_url, safe='')
            _session.post(action, data=answers, proxies=cfg.Network.proxy,
                          timeout=cfg.Network.timeout, allow_redirects=True)
            if _session.cookies.get('dv'):
                logger.debug('JavBus年龄测验已通过')
                return
            last_unknown = ['答案提交后未下发dv cookie（可能答错）']
    raise SiteBlocked(
        'JavBus年龄测验未能自动通过，未知题目: ' + '; '.join(last_unknown)
    )


def _get_movie_page(url):
    """请求影片页，必要时自动通过年龄检测后重试"""
    resp = _session.get(url, proxies=cfg.Network.proxy,
                        timeout=cfg.Network.timeout, allow_redirects=True)
    if _is_age_gate(resp):
        logger.debug('JavBus触发地区年龄检测，尝试自动通过')
        _pass_age_gate(url)
        resp = _session.get(url, proxies=cfg.Network.proxy,
                            timeout=cfg.Network.timeout, allow_redirects=True)
    return resp


def parse_data(movie: MovieInfo):
    """从网页抓取并解析指定番号的数据
    Args:
        movie (MovieInfo): 要解析的影片信息，解析后的信息直接更新到此变量内
    """
    url = f'{base_url}/{movie.dvdid}'
    resp = _get_movie_page(url)
    html = resp2html(resp)
    # 引入登录验证后状态码不再准确，因此还要额外通过检测标题来确认是否发生了404
    page_title = html.xpath('/html/head/title/text()')
    if page_title and page_title[0].startswith('404 Page Not Found!'):
        raise MovieNotFoundError(__name__, movie.dvdid)

    container = html.xpath("//div[@class='container']")[0]
    title = container.xpath("h3/text()")[0]
    cover = container.xpath("//a[@class='bigImage']/img/@src")[0]
    preview_pics = container.xpath("//div[@id='sample-waterfall']/a/@href")
    info = container.xpath("//div[@class='col-md-3 info']")[0]
    dvdid = info.xpath("p/span[text()='識別碼:']")[0].getnext().text
    publish_date = info.xpath("p/span[text()='發行日期:']")[0].tail.strip()
    duration = info.xpath("p/span[text()='長度:']")[0].tail.replace('分鐘', '').strip()
    director_tag = info.xpath("p/span[text()='導演:']")
    if director_tag:    # xpath没有匹配时将得到空列表
        movie.director = director_tag[0].getnext().text.strip()
    producer_tag = info.xpath("p/span[text()='製作商:']")
    if producer_tag:
        text = producer_tag[0].getnext().text
        if text:
            movie.producer = text.strip()
    publisher_tag = info.xpath("p/span[text()='發行商:']")
    if publisher_tag:
        text = publisher_tag[0].getnext().text
        if text:
            movie.publisher = text.strip()
    serial_tag = info.xpath("p/span[text()='系列:']")
    if serial_tag:
        movie.serial = serial_tag[0].getnext().text
    # genre, genre_id
    genre_tags = info.xpath("//span[@class='genre']/label/a")
    genre, genre_id = [], []
    for tag in genre_tags:
        tag_url = tag.get('href')
        pre_id = tag_url.split('/')[-1]
        genre.append(tag.text)
        if 'uncensored' in tag_url:
            movie.uncensored = True
            genre_id.append('uncensored-' + pre_id)
        else:
            movie.uncensored = False
            genre_id.append(pre_id)
    # JavBus的磁力链接是依赖js脚本加载的，无法通过静态网页来解析
    # actress, actress_pics（演员卡片在 #star-div 内的 a.avatar-box 下，img 的 title 属性为艺名）
    actress, actress_pics = [], {}
    actress_tags = html.xpath("//a[contains(@class,'avatar-box')]//img")
    for tag in actress_tags:
        name = (tag.get('title') or '').strip()
        if not name:
            continue
        pic_url = tag.get('src')
        actress.append(name)
        if pic_url and not pic_url.endswith('nowprinting.gif'):     # 略过默认的头像
            actress_pics[name] = pic_url
    if not actress:
        # JavBus对部分影片会显示「暫無出演者」，此时不是解析问题
        logger.debug(f"{movie.dvdid}: JavBus未提供演员信息（页面可能标注「暫無出演者」）")
    # 整理数据并更新movie的相应属性
    movie.url = f'{permanent_url}/{movie.dvdid}'
    movie.dvdid = dvdid
    movie.ori_title = title.strip()  # 保留完整日文原标题
    movie.title = title.replace(dvdid, '').strip()
    movie.cover = cover
    movie.preview_pics = preview_pics
    if publish_date != '0000-00-00':    # 丢弃无效的发布日期
        movie.publish_date = publish_date
    movie.duration = duration if int(duration) else None
    movie.genre = genre
    movie.genre_id = genre_id
    movie.actress = actress
    movie.actress_pics = actress_pics


def parse_clean_data(movie: MovieInfo):
    """解析指定番号的影片数据并进行清洗"""
    parse_data(movie)
    movie.genre_norm = genre_map.map(movie.genre_id)
    movie.genre_id = None   # 没有别的地方需要再用到，清空genre id（暗示已经完成转换）


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('NHDTC-258')
    try:
        parse_clean_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
