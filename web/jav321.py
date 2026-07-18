"""从jav321抓取数据"""
import os
import re
import sys
import logging


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from web.base import post_html
from web.exceptions import *
from core.datatype import MovieInfo


logger = logging.getLogger(__name__)
base_url = 'https://www.jav321.com'


def parse_data(movie: MovieInfo):
    """解析指定番号的影片数据"""
    html = post_html(f'{base_url}/search', data={'sn': movie.dvdid})
    page_url = html.xpath("//ul[@class='dropdown-menu']/li/a/@href")[0]
    #TODO: 注意cid是dmm的概念。如果影片来自MGSTAGE，这里的cid很可能是jav321自己添加的，例如 345SIMM-542
    cid = page_url.split('/')[-1]   # /video/ipx00177
    # 如果从URL匹配到的cid是'search'，说明还停留在搜索页面，找不到这部影片
    if cid == 'search':
        raise MovieNotFoundError(__name__, movie.dvdid)
    title = html.xpath("//div[@class='panel-heading']/h3/text()")[0]
    info = html.xpath("//div[@class='col-md-9']")[0]
    # jav321的不同信息字段间没有明显分隔，只能通过url来匹配目标标签
    company_tags = info.xpath("a[contains(@href,'/company/')]/text()")
    if company_tags:
        movie.producer = company_tags[0]
    # actress, actress_pics
    # jav321改版后，女优信息在col-md-9信息区内以<a href="/star/...">名字</a>的形式呈现
    actress, actress_pics = [], {}
    actress_links = info.xpath("a[contains(@href,'/star/')]")
    for tag in actress_links:
        name = tag.text.strip() if tag.text else ''
        if name:
            actress.append(name)
    # genre, genre_id
    genre_tags = info.xpath("a[contains(@href,'/genre/')]")
    genre, genre_id = [], []
    for tag in genre_tags:
        genre.append(tag.text)
        genre_id.append(tag.get('href').split('/')[-2]) # genre/4025/1
    dvdid_tag = info.xpath("b[text()='品番']")
    if dvdid_tag:
        movie.dvdid = dvdid_tag[0].tail.replace(': ', '').upper()
    publish_date_tag = info.xpath("b[text()='配信開始日']")
    if publish_date_tag:
        movie.publish_date = publish_date_tag[0].tail.replace(': ', '')
    duration_tag = info.xpath("b[text()='収録時間']")
    if duration_tag:
        duration_str = duration_tag[0].tail
        match = re.search(r'\d+', duration_str)
        if match:
            movie.duration = match.group(0)
    # 评分现在是纯文本格式，如 ': 3.5' 或 ': 5'
    score_tag = info.xpath("b[text()='平均評価']")
    if score_tag:
        score_text = score_tag[0].tail.strip().replace(': ', '')
        match = re.match(r'[\d.]+', score_text)
        if match:
            movie.score = match.group(0)
    serial_tag = info.xpath("a[contains(@href,'/series/')]/text()")
    if serial_tag:
        movie.serial = serial_tag[0]
    preview_video_tag = info.xpath("//video/source/@src")
    if preview_video_tag:
        movie.preview_video = preview_video_tag[0]
    plot_tag = info.xpath("//div[@class='panel-body']/div[@class='row']/div[@class='col-md-12']/text()")
    if plot_tag:
        movie.plot = plot_tag[0]
    preview_pics = html.xpath("//div[@class='col-xs-12 col-md-12']/p/a/img[@class='img-responsive']/@src")
    if len(preview_pics) == 0:
        # 尝试搜索另一种布局下的封面，在panel panel-info内的col-md-3中查找
        preview_pics = html.xpath("//div[@class='panel panel-info']//div[@class='col-md-3']/img[@class='img-responsive']/@src")
    # 有的图片链接里有多个//，网站质量堪忧……
    preview_pics = [i[:8] + i[8:].replace('//', '/') for i in preview_pics]
    # 磁力和ed2k链接是依赖js脚本加载的，无法通过静态网页来解析

    movie.url = page_url
    movie.cid = cid
    # dvdid 已在上面设置
    movie.title = title
    movie.actress = actress
    movie.actress_pics = actress_pics
    movie.genre = genre
    movie.genre_id = genre_id
    # publish_date 已在上面设置
    # preview_pics的第一张图始终是封面，剩下的才是预览图
    if len(preview_pics) > 0:
        movie.cover = preview_pics[0]
        movie.preview_pics = preview_pics[1:]


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('SCUTE-1177')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
