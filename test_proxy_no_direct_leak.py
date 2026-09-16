"""Nick CÓ proxy.txt thì mọi đường tới Dola phải đi proxy đó — đọc không ra proxy thì DỪNG, không lặng lẽ đi IP máy.

Đo thật 16/09 (proxy giả ghi kết nối): proxy.txt sai định dạng (vd "1.2.3.4:8080@user:pass" — kiểu tool khác xuất ra)
→ account_proxy trả "không proxy" → bước kiểm cookie trước khi mở nick gọi Dola THẲNG từ IP máy bằng cookie của nick,
và nút "Kiểm tra proxy" báo ổn với IP máy. Lệnh tạo video thì đã bị ensure_proxy_alive chặn.
Chạy: .venv/bin/python test_proxy_no_direct_leak.py
"""
import asyncio
import json
import tempfile
from pathlib import Path

import browser
import config

BAD = "1.2.3.4:8080@user:pass"


def _nick(root, name, proxy_txt=None):
    d = root / name
    d.mkdir(parents=True)
    (d / "cookies.json").write_text(json.dumps([{"name": "sessionid", "value": "FAKE"}]), encoding="utf-8")
    if proxy_txt is not None:
        (d / "proxy.txt").write_text(proxy_txt, encoding="utf-8")


class _cfg:
    def __enter__(self):
        self.saved = (config.ACCOUNTS_DIR, config.PROXY, browser.verify_cookie_http)
        self.root = Path(tempfile.mkdtemp()) / "accounts"
        config.ACCOUNTS_DIR, config.PROXY = self.root, ""
        self.calls = []

        async def spy(cookie_str, timeout=20, proxy=None):
            self.calls.append(proxy)
            return True, "giả"
        browser.verify_cookie_http = spy
        return self

    def __exit__(self, *a):
        config.ACCOUNTS_DIR, config.PROXY, browser.verify_cookie_http = self.saved


def test_malformed_proxy_raises_instead_of_direct():
    with _cfg() as c:
        _nick(c.root, "bad", BAD)
        try:
            got = browser.account_proxy("bad")
            raise AssertionError(f"proxy.txt sai định dạng mà account_proxy trả {got!r} → nick đi thẳng IP máy")
        except RuntimeError as e:
            assert "bad" in str(e), e


def test_no_proxy_file_still_direct_as_before():
    with _cfg() as c:
        _nick(c.root, "plain")
        assert browser.account_proxy("plain") is None, "nick không có proxy.txt + không proxy chung → đi thẳng như cũ"


def test_cookie_check_never_goes_direct_for_malformed_proxy():
    with _cfg() as c:
        _nick(c.root, "bad", BAD)
        browser._cookie_check_cache.pop("bad", None)
        try:
            asyncio.run(browser.quick_cookie_check("bad"))
        except RuntimeError:
            pass
        assert c.calls == [], f"đã gọi Dola kiểm cookie với proxy={c.calls} (None = IP máy)"


LINK_FAIL = "https://proxyxoay.shop/api/get.php?key=LEAKTEST&nhamang=random&tinhthanh=0"


class _rotating_fail:
    """Proxy xoay lấy IP luôn lỗi (chưa whitelist) — không gọi mạng."""
    def __enter__(self):
        import proxyxoay
        self.px = proxyxoay
        self.saved = (proxyxoay._get, proxyxoay._public_ipv4)
        proxyxoay._cache.pop(LINK_FAIL, None)
        proxyxoay._get = lambda url: '{"status":102,"message":"IP chua duoc whitelist"}'
        proxyxoay._public_ipv4 = lambda now: ""
        return self

    def __exit__(self, *a):
        self.px._get, self.px._public_ipv4 = self.saved
        self.px._cache.pop(LINK_FAIL, None)


def test_shared_rotating_proxy_failure_is_not_direct():
    """Proxy CHUNG dạng xoay lấy IP lỗi → nick không có proxy riêng cũng không được thành 'không proxy'."""
    with _cfg() as c, _rotating_fail():
        _nick(c.root, "plain")
        config.PROXY = LINK_FAIL
        try:
            got = browser.account_proxy("plain")
            raise AssertionError(f"proxy chung lỗi mà trả {got!r} → đi IP máy")
        except RuntimeError:
            pass


def test_poll_waits_instead_of_reading_via_machine_ip():
    import video_worker_ui as vw
    with _cfg() as c, _rotating_fail():
        _nick(c.root, "rot", LINK_FAIL)
        # đổi đường khi lỗi: không bao giờ trả None (= IP máy) cho nick đã khai proxy
        assert vw._next_poll_proxy("rot", "http://1.1.1.1:80") == vw.PROXY_PENDING
        # vòng theo dõi: proxy lỗi từ đầu → không có request nào đi ra với proxy None
        used, saved = [], (vw._fetch_single, vw.asyncio.sleep)
        real_fetch = vw._fetch_single

        async def spy(session, cookie, ms, fp, conv, proxy, limit=20):
            used.append(proxy)
            return await real_fetch(session, cookie, ms, fp, conv, proxy, limit)
        real_sleep = asyncio.sleep

        async def fast(t):
            await real_sleep(0)
        vw._fetch_single, vw.asyncio.sleep = spy, fast
        try:
            try:
                asyncio.run(vw.poll_conversation_http("rot", "sessionid=FAKE", "x", "fp", "123", 0.05))
            except TimeoutError:
                pass
        finally:
            vw._fetch_single, vw.asyncio.sleep = saved
        assert used and None not in used, f"theo dõi video đã đọc bằng IP máy: {used}"
        assert set(used) == {vw.PROXY_PENDING}, used


def test_scan_raises_instead_of_reading_via_machine_ip():
    import video_worker_ui as vw
    with _cfg() as c, _rotating_fail():
        _nick(c.root, "rot", LINK_FAIL)
        calls, saved = [], vw._recent_conversations

        async def spy(session, cookie, ms, fp, limit, proxy):
            calls.append(proxy)
            return None
        vw._recent_conversations = spy
        try:
            try:
                asyncio.run(vw.scan_account_videos("rot", 5))
                raise AssertionError("proxy lỗi mà quét vẫn chạy")
            except RuntimeError:
                pass
        finally:
            vw._recent_conversations = saved
        assert calls == [], f"quét video đã gọi Dola với proxy={calls}"


def test_download_never_direct_unless_enabled():
    import video_worker as vwk
    with _cfg() as c:
        _nick(c.root, "st", "1.2.3.4:8080")
        lanes, saved = [], (vwk._fetch_to_file, config.DOWNLOAD_DIR, config.DIRECT_DOWNLOAD_FALLBACK, vwk.DOWNLOAD_RETRY_SEC)
        config.DOWNLOAD_DIR, vwk.DOWNLOAD_RETRY_SEC = tempfile.mkdtemp(), 0

        async def failing(url, fname, proxy=None):
            lanes.append(proxy)
            raise RuntimeError("proxy cắt ngang: not completed")
        vwk._fetch_to_file = failing
        try:
            for flag, want_direct in ((False, False), (True, True)):
                config.DIRECT_DOWNLOAD_FALLBACK = flag
                lanes.clear()
                try:
                    asyncio.run(vwk._download("https://cdn/v.mp4", "st"))
                except vwk.DownloadError:
                    pass
                assert (None in lanes) is want_direct, f"DIRECT_DOWNLOAD_FALLBACK={flag}: các lane đã thử {lanes}"
                assert "http://1.2.3.4:8080" in lanes, lanes
        finally:
            vwk._fetch_to_file, config.DOWNLOAD_DIR, config.DIRECT_DOWNLOAD_FALLBACK, vwk.DOWNLOAD_RETRY_SEC = saved


def test_download_proxy_error_is_download_error_not_direct():
    import video_worker as vwk
    with _cfg() as c, _rotating_fail():
        _nick(c.root, "rot", LINK_FAIL)
        lanes, saved = [], (vwk._fetch_to_file, config.DIRECT_DOWNLOAD_FALLBACK)
        config.DIRECT_DOWNLOAD_FALLBACK = False

        async def spy(url, fname, proxy=None):
            lanes.append(proxy)
        vwk._fetch_to_file = spy
        try:
            try:
                asyncio.run(vwk._download("https://cdn/v.mp4", "rot"))
                raise AssertionError("proxy lỗi mà vẫn tải")
            except vwk.DownloadError as e:
                assert "IP proxy" in str(e), e
        finally:
            vwk._fetch_to_file, config.DIRECT_DOWNLOAD_FALLBACK = saved
        assert lanes == [], f"đã tải với proxy={lanes}"


def test_add_account_login_uses_nick_own_proxy():
    import add_account
    with _cfg() as c:
        _nick(c.root, "own", "9.9.9.9:3128:u:p")
        config.PROXY = "http://shared:1111"
        seen, saved = {}, add_account.async_playwright

        class _Stop(Exception):
            pass

        class _Chromium:
            async def launch_persistent_context(self, profile, **kw):
                seen.update(kw)
                raise _Stop()

        class _P:
            chromium = _Chromium()

        class _AP:
            async def __aenter__(self):
                return _P()

            async def __aexit__(self, *a):
                return False
        add_account.async_playwright = lambda: _AP()
        try:
            try:
                asyncio.run(add_account.add_account_flow("own", "e@x", "pw", ""))
            except _Stop:
                pass
        finally:
            add_account.async_playwright = saved
        assert seen.get("proxy") == {"server": "http://9.9.9.9:3128", "username": "u", "password": "p"}, seen.get("proxy")


if __name__ == "__main__":
    test_malformed_proxy_raises_instead_of_direct()
    test_no_proxy_file_still_direct_as_before()
    test_cookie_check_never_goes_direct_for_malformed_proxy()
    test_shared_rotating_proxy_failure_is_not_direct()
    test_poll_waits_instead_of_reading_via_machine_ip()
    test_scan_raises_instead_of_reading_via_machine_ip()
    test_download_never_direct_unless_enabled()
    test_download_proxy_error_is_download_error_not_direct()
    test_add_account_login_uses_nick_own_proxy()
    print("OK")
