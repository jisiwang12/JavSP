"""从TokyoLib抓取数据（仅标题和简介）"""
import os
import re
import sys
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from web.base import Request, resp2html
from web.exceptions import *
from core.config import cfg
from core.datatype import MovieInfo


logger = logging.getLogger(__name__)

request = Request(use_scraper=True)
request.headers['Accept-Language'] = 'ja,zh-CN,zh;q=0.9,en;q=0.6'

base_url = 'https://www.tokyolib.com'


def _deobfuscate_plot(text):
    """还原TokyoLib混淆的简介文本。

    网站在<p class='contents'>中通过JS混淆插入垃圾文本，
    以中文标点（。！？；，：）为分隔符，每隔一段保留一段。
    """
    if not text:
        return text
    regex = re.compile(r'[。！？；，：]')
    parts = regex.split(text)
    # 原JS逻辑：count从0开始，count%2==0时保留，分隔符本身也算一次分割
    # regex.split 会把分隔符之间的内容取出，等价于JS中按分隔符切分后取偶数索引段
    # 但JS逻辑是：遇到第N个分隔符时，如果N是偶数则保留[begin, match.index)
    # 简化等价：用标点切分，取索引0,2,4...的片段
    result = ''.join(parts[i] for i in range(0, len(parts), 2))
    return result.strip()


def parse_data(movie: MovieInfo):
    """从TokyoLib抓取标题和简介
    Args:
        movie (MovieInfo): 要解析的影片信息，解析后的信息直接更新到此变量内
    """
    # 搜索番号
    search_url = f'{base_url}/search?type=id&q={movie.dvdid}'
    r = request.get(search_url, delay_raise=True)
    if r.status_code != 200:
        raise MovieNotFoundError(__name__, movie.dvdid)
    html = resp2html(r)

    # 从搜索结果中找到详情页链接（格式: /v/数字）
    detail_links = html.xpath("//a[contains(@href, '/v/')]/@href")
    if not detail_links:
        raise MovieNotFoundError(__name__, movie.dvdid)
    detail_url = detail_links[0]
    if detail_url.startswith('/'):
        detail_url = base_url + detail_url

    # 访问详情页
    r = request.get(detail_url, delay_raise=True)
    if r.status_code != 200:
        raise MovieNotFoundError(__name__, movie.dvdid)
    html = resp2html(r)

    # 提取标题（h1.title.is-4）
    title_tags = html.xpath("//h1[@class='title is-4']/text()")
    if not title_tags:
        raise MovieNotFoundError(__name__, movie.dvdid)
    title = title_tags[0].strip()

    # 提取简介（p.contents），需要还原混淆
    plot = None
    contents_tags = html.xpath("//p[@class='contents']")
    if contents_tags:
        raw_plot = contents_tags[0].text_content().strip()
        plot = _deobfuscate_plot(raw_plot)

    movie.url = detail_url
    movie.title = title
    if plot:
        movie.plot = plot


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('IPX-177')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
