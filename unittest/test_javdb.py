import os
import sys
from email.utils import format_datetime
from datetime import datetime, timezone

from lxml import html as lxml_html

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import web.javdb as javdb
from web.exceptions import SiteBlocked


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


def test_parse_retry_after_supports_seconds_and_http_date():
    assert javdb._parse_retry_after('10') == 10.0
    future = datetime.now(timezone.utc).replace(microsecond=0)
    future = future.fromtimestamp(future.timestamp() + 30, timezone.utc)
    assert 28 <= javdb._parse_retry_after(format_datetime(future)) <= 30


def test_web_get_retries_429(monkeypatch):
    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code
            self.headers = {'Retry-After': '0'} if status_code == 429 else {}

    class FakeRequest:
        def __init__(self):
            self.calls = 0

        def get(self, url, delay_raise=False):
            self.calls += 1
            return FakeResponse(429 if self.calls == 1 else 200)

    monkeypatch.setattr(javdb, '_WEB_MIN_INTERVAL', 0)
    monkeypatch.setattr(javdb, '_WEB_COOLDOWN_BASE', 0)
    monkeypatch.setattr(javdb, '_WEB_COOLDOWN_MAX', 0)
    monkeypatch.setattr(javdb, '_web_next_start', 0)
    monkeypatch.setattr(javdb, '_web_cooldown_until', 0)
    monkeypatch.setattr(javdb, '_web_rate_limit_streak', 0)
    fake = FakeRequest()

    response = javdb._web_get('https://javdb.com/test', req=fake)

    assert response.status_code == 200
    assert fake.calls == 2


def test_web_get_stops_after_repeated_429(monkeypatch):
    class FakeResponse:
        status_code = 429
        headers = {'Retry-After': '0'}

    class FakeRequest:
        calls = 0

        def get(self, url, delay_raise=False):
            self.calls += 1
            return FakeResponse()

    monkeypatch.setattr(javdb, '_WEB_MIN_INTERVAL', 0)
    monkeypatch.setattr(javdb, '_WEB_RATE_LIMIT_ATTEMPTS', 2)
    monkeypatch.setattr(javdb, '_WEB_COOLDOWN_BASE', 0)
    monkeypatch.setattr(javdb, '_WEB_COOLDOWN_MAX', 0)
    monkeypatch.setattr(javdb, '_web_next_start', 0)
    monkeypatch.setattr(javdb, '_web_cooldown_until', 0)
    monkeypatch.setattr(javdb, '_web_rate_limit_streak', 0)
    fake = FakeRequest()

    try:
        javdb._web_get('https://javdb.com/test', req=fake)
    except SiteBlocked:
        pass
    else:
        raise AssertionError('连续 429 后应抛出 SiteBlocked，避免外层立即重试')

    assert fake.calls == 2
