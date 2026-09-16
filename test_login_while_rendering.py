"""Đăng nhập lại nick trong lúc proxy của nó đang có job dựng video.

Trước đây /proxy/current từ chối (409) HỄ proxy bận → mỗi key gánh 6–7 nick thì một job bất kỳ khoá đăng nhập của cả
7 nick suốt lúc dựng (tới 40 phút). Rủi ro thật chỉ có khi phải GỌI nhà bán (cache hết hạn → get.php có thể cấp IP
mới, giết cổng của job). IP trong cache còn hạn → trả luôn, KHÔNG gọi mạng. Chạy: .venv/bin/python test_login_while_rendering.py
"""
import asyncio
import tempfile
import time
from pathlib import Path

import browser
import config
import proxyxoay
import server
import tmproxy

LINK = "https://proxyxoay.shop/api/get.php?key=LOGINBUSY&nhamang=random&tinhthanh=0"
TMKEY = "a" * 32


def _no_network(*a, **k):
    raise AssertionError("đã gọi nhà bán proxy trong lúc có job đang dựng")


def _nick(root, name, raw):
    (root / name).mkdir(parents=True)
    (root / name / "proxy.txt").write_text(raw, encoding="utf-8")


def _call(name):
    return asyncio.run(server.admin_account_proxy_current(name, x_admin_key=None))


def test_busy_proxy_with_fresh_cache_returns_cached_ip_without_network():
    saved = (config.ACCOUNTS_DIR, proxyxoay._get, tmproxy._post, server._admin_auth)
    root = Path(tempfile.mkdtemp()) / "accounts"
    try:
        config.ACCOUNTS_DIR = root
        server._admin_auth = lambda k: None
        proxyxoay._get, tmproxy._post = _no_network, _no_network
        _nick(root, "px", LINK)
        _nick(root, "tm", f"tmproxy://{TMKEY}")
        now = time.time()
        proxyxoay._cache[LINK] = {"server": "http://160.250.166.23:10881", "username": "", "password": "",
                                  "ip": "160.250.166.23:10881", "fetched_at": now, "ttl": 600, "next_ok": now}
        tmproxy._cache[TMKEY] = {"https": "1.2.3.4:5000", "username": "u", "password": "p", "public_ip": "9.9.9.9",
                                 "fetched_at": now, "exp": now + 600, "next_ok": now}
        browser._proxy_leases[LINK] = 1
        browser._proxy_leases[f"tmproxy://{TMKEY}"] = 2
        assert _call("px") == {"proxy": {"server": "http://160.250.166.23:10881"}}, _call("px")
        assert _call("tm") == {"proxy": {"server": "http://1.2.3.4:5000", "username": "u", "password": "p"}}, _call("tm")
    finally:
        config.ACCOUNTS_DIR, proxyxoay._get, tmproxy._post, server._admin_auth = saved
        proxyxoay._cache.pop(LINK, None); tmproxy._cache.pop(TMKEY, None)
        browser._proxy_leases.pop(LINK, None); browser._proxy_leases.pop(f"tmproxy://{TMKEY}", None)


def test_busy_proxy_with_expired_cache_still_refuses():
    saved = (config.ACCOUNTS_DIR, proxyxoay._get, server._admin_auth)
    root = Path(tempfile.mkdtemp()) / "accounts"
    try:
        config.ACCOUNTS_DIR = root
        server._admin_auth = lambda k: None
        proxyxoay._get = _no_network
        _nick(root, "px", LINK)
        old = time.time() - 3600
        proxyxoay._cache[LINK] = {"server": "http://160.250.166.23:10881", "ip": "160.250.166.23:10881",
                                  "fetched_at": old, "ttl": 600, "next_ok": old}
        browser._proxy_leases[LINK] = 1
        try:
            _call("px")
            raise AssertionError("cache hết hạn + proxy bận mà vẫn cho lấy IP (sẽ gọi nhà bán, có thể cắt job)")
        except server.HTTPException as e:
            assert e.status_code == 409, e
    finally:
        config.ACCOUNTS_DIR, proxyxoay._get, server._admin_auth = saved
        proxyxoay._cache.pop(LINK, None); browser._proxy_leases.pop(LINK, None)


if __name__ == "__main__":
    test_busy_proxy_with_fresh_cache_returns_cached_ip_without_network()
    test_busy_proxy_with_expired_cache_still_refuses()
    print("OK")
