import os
import sys

from lxml import html as lxml_html

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import web.javdb as javdb


def test_parse_cookie_string():
    cookies = javdb._parse_cookie_string(
        'Cookie: locale=zh; _jdb_session="abc=="; remember_me_token=xyz'
    )

    assert cookies == {
        'locale': 'zh',
        '_jdb_session': 'abc==',
        'remember_me_token': 'xyz',
    }


def test_new_web_request_uses_cookie_and_language():
    request = javdb._new_web_request({'locale': 'zh', '_jdb_session': 'abc'})

    assert request.cookies['_jdb_session'] == 'abc'
    assert 'zh-CN' in request.headers['Accept-Language']


def test_info_value_supports_simplified_and_traditional_labels():
    node = lxml_html.fromstring(
        '''
        <nav>
          <div><strong>时长:</strong><span class="value">170 分钟</span></div>
          <div><strong>導演:</strong><span class="value">导演名</span></div>
        </nav>
        '''
    )

    assert javdb._info_value(node, '时长:', '時長:') == '170 分钟'
    assert javdb._info_value(node, '导演:', '導演:') == '导演名'
