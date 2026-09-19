"""Regression test cho fix `account_proxy(prefer_cached=True)` trong browser.py.

Vấn đề: trước đây `launch_account_context()` gọi `account_proxy(account)` không qua cờ `prefer_cached`.
Khi proxy xoay đang có job khác dựng trên nó (proxy_busy>0), `account_proxy()` lại gọi API nhà bán lấy IP —
nhà bán có thể cấp IP mới và giết cổng của video đang dựng (đã trừ lượt).

Fix: `account_proxy(account, prefer_cached=True)` kiểm `proxy_busy` trước. Nếu proxy đang bận:
- Cache còn hạn → trả cache, KHÔNG gọi API
- Cache hết hạn → NÉM RuntimeError. Trước đây trả None: None trùng nghĩa "nick không có proxy" nên
  launch_account_context mở Chrome KHÔNG proxy = lộ IP máy (nick khác dùng chung proxy xoay, đang kiểm cookie…).
Nếu proxy không bận: hành vi cũ (gọi API bình thường).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import browser
import config
import tmproxy
import proxyxoay


def _setup(tmp: str) -> None:
    """MỌI test trong file chạy CHUNG 1 process — share _proxy_leases / _proxy_sessions.
    Reset về trạng thái sạch để test độc lập."""
    browser._proxy_leases.clear()
    browser._proxy_sessions.clear()
    config.ACCOUNTS_DIR = type(config.ACCOUNTS_DIR)(tmp)
    config.PROXY = ""


def _write_proxy(account: str, raw: str) -> None:
    d = config.ACCOUNTS_DIR / account
    d.mkdir(parents=True, exist_ok=True)
    (d / "proxy.txt").write_text(raw, encoding="utf-8")


# ── Test 1: nickname không có proxy.txt + PROXY rỗng → None ────────────
def test_returns_none_when_no_proxy_at_all():
    """Không proxy.txt + config.PROXY rỗng → trả None (đi thẳng, cấu hình của user)."""
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        assert browser.account_proxy("n", True) is None
        assert browser.account_proxy("n", False) is None


# ── Test 2: proxy tĩnh không bị ảnh hưởng ──────────────────────────────
def test_static_proxy_always_works():
    """Proxy tĩnh (host:port) → trả dict bình thường, không quan tâm prefer_cached."""
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        _write_proxy("n", "127.0.0.1:8888")
        got_cached = browser.account_proxy("n", True)
        got_full = browser.account_proxy("n", False)
        assert got_cached == got_full == {"server": "http://127.0.0.1:8888"}


# ── Test 3: proxy xoay + KHÔNG có job khác chạy → trả cache như cũ ─────
def test_rotating_proxy_idle_returns_cached_dict():
    """Proxy xoay không có lease → prefer_cached=True vẫn trả cache (current() không gọi API nếu cache còn hạn)."""
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        _write_proxy("n", "tmproxy://" + "a" * 32)
        # Đặt cache giả để current() trả ngay không gọi API
        tmproxy._cache["a" * 32] = {
            "fetched_at": 0, "exp": 9e18,
            "https": "1.2.3.4:5678", "username": "", "password": "",
            "public_ip": "1.2.3.4", "next_ok": 0, "day": "2099-01-01", "changes": 1,
        }
        # Cache còn hạn → cả hai đều trả cache
        assert browser.account_proxy("n", True)["server"] == "http://1.2.3.4:5678"
        assert browser.account_proxy("n", False)["server"] == "http://1.2.3.4:5678"


# ── Test 4: QUAN TRỌNG — proxy xoay + CÓ lease + cache còn hạn → cache ─
def test_rotating_proxy_busy_returns_cache_no_api_call():
    """Proxy xoay đang có job khác chạy + cache còn hạn → prefer_cached=True trả cache.

    Hành vi cũ (prefer_cached=False) cũng trả cache (vì cache còn hạn, current() không gọi API)."""
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        key = "tmproxy://" + "a" * 32
        _write_proxy("n", key)
        tmproxy._cache["a" * 32] = {
            "fetched_at": 0, "exp": 9e18,
            "https": "1.2.3.4:5678", "username": "", "password": "",
            "public_ip": "1.2.3.4", "next_ok": 0, "day": "2099-01-01", "changes": 1,
        }
        norm = browser.normalize_proxy_input(key)
        browser._proxy_leases[norm] = 1   # đang có job khác chạy

        got = browser.account_proxy("n", True)
        assert got == {"server": "http://1.2.3.4:5678"}, \
            f"phải trả cache mà không gọi API, thực tế {got!r}"

        got2 = browser.account_proxy("n", False)
        assert got2 == {"server": "http://1.2.3.4:5678"}


# ── Test 5: proxy xoay + CÓ lease + cache HẾT HẠN → ném lỗi, KHÔNG None ─
def test_rotating_proxy_busy_cache_expired_raises_not_none():
    """Cache hết hạn + đang bận → KHÔNG gọi API (đổi IP giữa lúc đang dựng = cắt cổng) và KHÔNG trả None
    (None = "không có proxy" → Chrome mở bằng IP máy). Ném lỗi → pool xoay sang nick khác."""
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        key = "tmproxy://" + "a" * 32
        _write_proxy("n", key)
        tmproxy._cache["a" * 32] = {
            "fetched_at": 0, "exp": 0,   # hết hạn ngay
            "https": "1.2.3.4:5678", "username": "", "password": "",
            "public_ip": "1.2.3.4", "next_ok": 0, "day": "2099-01-01", "changes": 1,
        }
        norm = browser.normalize_proxy_input(key)
        browser._proxy_leases[norm] = 2
        calls = []
        saved_current = tmproxy.current
        tmproxy.current = lambda *a, **k: calls.append(a) or saved_current(*a, **k)   # gọi nhà bán = sai
        try:
            browser.account_proxy("n", True)
            raise AssertionError("bận + cache hết hạn phải NÉM lỗi — trả None là Chrome mở bằng IP máy")
        except RuntimeError as e:
            assert "đang có job" in str(e), str(e)
        finally:
            tmproxy.current = saved_current
        assert not calls, "không được gọi nhà bán lấy IP khi proxy đang có job dựng"


def test_shared_rotating_proxy_busy_cache_expired_raises():
    """Proxy CHUNG xoay đang bận + cache hết hạn → cũng ném lỗi (trước đây None → nick đi IP máy)."""
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        link = "https://proxyxoay.shop/api/get.php?key=ABC&nhamang=random&tinhthanh=0"
        config.PROXY = link
        proxyxoay._cache.pop(link, None)   # không có cache còn hạn
        browser._proxy_leases[browser.normalize_proxy_input(link)] = 1
        try:
            browser.account_proxy("plain", True)
            raise AssertionError("proxy chung bận + hết cache phải NÉM lỗi, không trả None")
        except RuntimeError as e:
            assert "đang có job" in str(e), str(e)


# ── Test 6: proxy CHUNG xoay + bận → cache ─────────────────────────────
def test_shared_rotating_proxy_busy_returns_cache():
    """Nick không có proxy.txt + config.PROXY là proxy xoay + bận → prefer_cached=True trả cache."""
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        link = "https://proxyxoay.shop/api/get.php?key=ABC&nhamang=random&tinhthanh=0"
        config.PROXY = link
        proxyxoay._cache[link] = {
            "server": "http://5.6.7.8:9999", "username": "", "password": "",
            "ip": "5.6.7.8", "network": "Viettel", "location": "HCM",
            "expiration": "", "message": "",
            "fetched_at": 0, "ttl": 9e18,
            "next_ok": 0, "day": "2099-01-01", "changes": 1, "life": 0,
        }
        norm = browser.normalize_proxy_input(link)
        browser._proxy_leases[norm] = 1

        got = browser.account_proxy("plain", True)
        assert got == {"server": "http://5.6.7.8:9999"}, \
            f"phải trả cache cho proxy chung khi bận, thực tế {got!r}"


# ── Test 7: Backward-compat — caller cũ không truyền prefer_cached ─────
def test_default_prefer_cached_false_keeps_old_behavior():
    """Không truyền prefer_cached → False (mặc định) → hành vi cũ cho admin/test.

    Đảm bảo caller cũ (test_proxy_input.py, test_proxyxoay.py, server.py admin endpoint,
    cookie_service) vẫn hoạt động đúng sau refactor."""
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        _write_proxy("plain", "")
        assert browser.account_proxy("plain") is None
        _write_proxy("static", "1.2.3.4:8888")
        assert browser.account_proxy("static") == {"server": "http://1.2.3.4:8888"}
        _write_proxy("bad", "host@user:pass")
        try:
            browser.account_proxy("bad")
            raise AssertionError("phải raise RuntimeError với proxy sai định dạng")
        except RuntimeError as e:
            assert "sai định dạng" in str(e).lower() or "sửa proxy" in str(e).lower()


def main():
    tests = [
        test_returns_none_when_no_proxy_at_all,
        test_static_proxy_always_works,
        test_rotating_proxy_idle_returns_cached_dict,
        test_rotating_proxy_busy_returns_cache_no_api_call,
        test_rotating_proxy_busy_cache_expired_returns_none,
        test_shared_rotating_proxy_busy_returns_cache,
        test_default_prefer_cached_false_keeps_old_behavior,
    ]
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            raise
    print("OK")


if __name__ == "__main__":
    main()


# ── Test 8: vừa khởi động (cache trống) → bước trước-khi-mở-nick lấy IP, job trong lease đọc được ─
def test_rotate_if_expiring_warms_empty_cache_before_lease():
    """Job Chrome giữ lease của CHÍNH nó rồi mới mở Chrome → trong lease proxy luôn 'bận'. Cache trống (vừa bật app)
    mà không ai lấy IP trước lease thì account_proxy(prefer_cached) báo lỗi oan và job hỏng dù proxy đang rảnh."""
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        key = "a" * 32
        _write_proxy("n", "tmproxy://" + key)
        tmproxy._cache.pop(key, None)
        saved = tmproxy.current

        def fake_current(k):
            ent = {"fetched_at": 0, "exp": 9e18, "https": "9.9.9.9:1111", "username": "", "password": "",
                   "public_ip": "9.9.9.9", "next_ok": 0, "day": "2099-01-01", "changes": 1}
            tmproxy._cache[k] = ent
            return ent

        tmproxy.current = fake_current
        try:
            browser.rotate_if_expiring("n", min_life=60)   # proxy RẢNH → lấy IP lúc còn an toàn
        finally:
            tmproxy.current = saved
        with browser.proxy_lease("n"):                      # job giữ lease rồi mới mở Chrome
            assert browser.account_proxy("n", True) == {"server": "http://9.9.9.9:1111"}
