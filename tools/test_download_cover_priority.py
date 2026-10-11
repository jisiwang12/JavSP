"""验证 download_cover 严格按 covers 优先级顺序取结果（不被并发抢跑）"""
import os, sys
ROOT = r'H:\Idea\JavSP-1.8'
os.chdir(ROOT); sys.path.insert(0, ROOT)
sys.argv = [sys.argv[0], '-c', os.path.join(ROOT, 'config.ini')]
import time
import tempfile
import unittest.mock as mock


def test_priority_order():
    # 低优先级 URL「下载」更快，但必须让位给高优先级
    # covers 顺序 = 优先级：[slow_high, fast_low]，javdb 模拟为末尾兜底
    slow_high = 'https://high.example/cover.jpg'
    fast_low = 'https://low.example/cover.jpg'
    fallback = 'https://javdb.example/cover.jpg'
    covers = [slow_high, fast_low, fallback]  # fallback 已由 info_summary 放在末尾

    call_order = []

    def fake_download(url, pic_path, desc=None):
        call_order.append(url)
        if url == slow_high:
            time.sleep(0.3)  # 高优先级慢
        elif url == fast_low:
            time.sleep(0.01)  # 低优先级快
        else:
            time.sleep(0.01)
        with open(pic_path, 'wb') as f:
            f.write(b'x')
        return {'total': 1}

    def fake_valid_pic(pic_path):
        return os.path.exists(pic_path) and os.path.getsize(pic_path) > 0

    import JavSP
    with mock.patch.object(JavSP, 'download', side_effect=fake_download), \
         mock.patch.object(JavSP, 'valid_pic', side_effect=fake_valid_pic), \
         mock.patch.object(JavSP, 'get_fmt_size', return_value='1B'), \
         mock.patch.object(JavSP, 'get_pic_size', return_value=(1, 1)):
        with tempfile.TemporaryDirectory() as td:
            fanart = os.path.join(td, 'fanart.jpg')
            result = JavSP.download_cover(covers, fanart)
            print('result:', result)
            print('call_order:', call_order)
            assert result is not None, 'download_cover 应返回成功结果'
            url, path = result
            assert url == slow_high, f'必须取优先级最高的 slow_high，而不是更快的 fast_low，实际={url}'
            print('PASS: 高优先级虽慢但胜出')


def test_javdb_fallback():
    """前面的都失败时才轮到 javdb 兜底"""
    high = 'https://high.example/cover.jpg'
    javdb = 'https://javdb.example/cover.jpg'
    covers = [high, javdb]

    def fake_download(url, pic_path, desc=None):
        if url == high:
            raise RuntimeError('404')
        with open(pic_path, 'wb') as f:
            f.write(b'x')
        return {}

    def fake_valid_pic(pic_path):
        return os.path.exists(pic_path) and os.path.getsize(pic_path) > 0

    import JavSP
    with mock.patch.object(JavSP, 'download', side_effect=fake_download), \
         mock.patch.object(JavSP, 'valid_pic', side_effect=fake_valid_pic):
        with tempfile.TemporaryDirectory() as td:
            fanart = os.path.join(td, 'fanart.jpg')
            result = JavSP.download_cover(covers, fanart)
            assert result is not None
            url, _ = result
            assert url == javdb, f'高优先级失败后应取 javdb 兜底，实际={url}'
            print('PASS: javdb 仅作兜底')


def test_all_fail():
    import JavSP
    def fake_download(url, pic_path, desc=None):
        raise RuntimeError('fail')

    with mock.patch.object(JavSP, 'download', side_effect=fake_download):
        with tempfile.TemporaryDirectory() as td:
            fanart = os.path.join(td, 'fanart.jpg')
            result = JavSP.download_cover(['https://a/x.jpg'], fanart)
            assert result is None
            print('PASS: 全部失败返回 None')


if __name__ == '__main__':
    test_priority_order()
    test_javdb_fallback()
    test_all_fail()
    print('\nALL PASS')
