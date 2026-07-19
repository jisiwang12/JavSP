"""从Hanime1.me抓取里番数据"""
import os
import re
import sys
import logging


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from web.base import *
from web.exceptions import *
from core.config import cfg
from core.datatype import MovieInfo


logger = logging.getLogger(__name__)
base_url = 'https://hanime1.me'


def search(query: str):
    """搜索里番，返回视频ID列表"""
    url = f'{base_url}/search?query={query}'
    resp = request_get(url, delay_raise=True)
    html = resp2html(resp)

    # 查找视频链接
    links = html.xpath("//a[contains(@href, '/watch?v=')]/@href")
    video_ids = []
    for link in links:
        match = re.search(r'v=(\d+)', link)
        if match:
            video_ids.append(match.group(1))
    return list(set(video_ids))  # 去重


def parse_data(movie: MovieInfo):
    """从网页抓取并解析指定番号的数据"""
    # 如果没有URL，先搜索
    if not movie.url:
        video_ids = search(movie.dvdid)
        if not video_ids:
            raise MovieNotFoundError(__name__, movie.dvdid)
        movie.url = f'{base_url}/watch?v={video_ids[0]}'

    resp = request_get(movie.url, delay_raise=True)
    html = resp2html(resp)

    # 提取标题
    title_tag = html.xpath("//meta[@name='title']/@content")
    if title_tag:
        # 清理标题，移除网站后缀
        title = title_tag[0].replace(' - H動漫/裏番/線上看 - Hanime1.me', '').strip()
        movie.title = title
    else:
        raise MovieNotFoundError(__name__, movie.dvdid)

    # 提取描述
    desc_tag = html.xpath("//meta[@name='description']/@content")
    if desc_tag:
        movie.plot = desc_tag[0]

    # 提取标签
    keywords_tag = html.xpath("//meta[@name='keywords']/@content")
    if keywords_tag:
        tags = [t.strip() for t in keywords_tag[0].split(',')]
        movie.genre = tags

    # 提取封面图片 (og:image)
    cover_tag = html.xpath("//meta[@property='og:image']/@content")
    if cover_tag:
        movie.cover = cover_tag[0]

    # 提取系列信息（从页面内容中查找）
    series_text = html.xpath("//a[contains(@href, '/search?series=')]/text()")
    if series_text:
        movie.serial = series_text[0].strip()

    # 提取发布日期
    date_text = re.search(r'(\d{4}-\d{2}-\d{2})', str(html))
    if date_text:
        movie.publish_date = date_text.group(1)

    # 提取预览图片 - 从所有thumbnail图片中获取
    all_images = html.xpath("//img/@src")
    preview_pics = []
    for img in all_images:
        if 'thumbnail' in img and 'vdownload.hembed.com' in img:
            preview_pics.append(img)
    movie.preview_pics = list(set(preview_pics))[:20]  # 去重并限制数量


if __name__ == '__main__':
    import pretty_errors
    pretty_errors.configure(display_link=True)
    info = MovieInfo('鬼父')
    parse_data(info)
    print(f'Title: {info.title}')
    print(f'Genre: {info.genre}')
    print(f'Cover: {info.cover}')
