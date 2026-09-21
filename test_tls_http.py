"""Gọi HTTP giả vân tay TLS/HTTP2 của Chrome cho các đường KHÔNG mở trình duyệt.

Đo 21/09 (tls.browserleaks.com):
  Chrome thật   ja3=8aad40f6…  akamai=52d84b11737d980a
  curl_cffi     ja3=<ho Chrome> akamai=52d84b11737d980a   ← khớp HTTP/2
  aiohttp       ja3=478843a4…  akamai=(TRỐNG)             ← không phải trình duyệt
Chrome hiện đại xáo thứ tự phần mở rộng TLS nên JA3 vốn đổi mỗi lần; thứ so được là vân tay HTTP/2.
Đường gửi lệnh (submit_http) đã dùng curl_cffi từ trước, còn theo dõi video + kiểm cookie vẫn là aiohttp.
"""
import asyncio

import tls_http


class _Resp:
    def __init__(self, status=200, text="ok"):
        self.status_code, self.text = status, text


class _FakeSession:
    goi = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, **kw):
        _FakeSession.goi.append({"method": method, "url": url, **kw})
        return _Resp()


def _dung_session_gia(monkeypatch, loi=None):
    _FakeSession.goi = []

    class S(_FakeSession):
        async def request(self, method, url, **kw):
            if loi:
                raise loi
            return await _FakeSession.request(self, method, url, **kw)

    monkeypatch.setattr(tls_http, "AsyncSession", S, raising=False)
    monkeypatch.setattr(tls_http, "USE_TLS_IMPERSONATE", True, raising=False)
    return _FakeSession.goi


def test_gia_van_tay_chrome_va_tra_ve_status_text(monkeypatch):
    goi = _dung_session_gia(monkeypatch)
    st, body = asyncio.run(tls_http.request("GET", "https://x/y", headers={"a": "b"}))
    assert (st, body) == (200, "ok")
    assert goi[0]["impersonate"].startswith("chrome")     # có giả vân tay
    assert goi[0]["headers"] == {"a": "b"}


def test_truyen_proxy_cua_nick(monkeypatch):
    goi = _dung_session_gia(monkeypatch)
    asyncio.run(tls_http.request("POST", "https://x/y", proxy="http://1.2.3.4:8080", data="{}"))
    assert goi[0]["proxy"] == "http://1.2.3.4:8080" and goi[0]["data"] == "{}"


def test_curl_hong_thi_lui_ve_aiohttp(monkeypatch):
    """curl_cffi lỗi (thiếu thư viện hệ thống, proxy lạ) KHÔNG được làm chết job."""
    _dung_session_gia(monkeypatch, loi=RuntimeError("curl hong"))
    da_lui = {}

    async def fake_aiohttp(method, url, **kw):
        da_lui.update(method=method, url=url)
        return 204, "qua aiohttp"

    monkeypatch.setattr(tls_http, "_qua_aiohttp", fake_aiohttp)
    st, body = asyncio.run(tls_http.request("GET", "https://x/y"))
    assert (st, body) == (204, "qua aiohttp") and da_lui["url"] == "https://x/y"


def test_tat_cong_tac_thi_dung_thang_aiohttp(monkeypatch):
    goi = _dung_session_gia(monkeypatch)
    monkeypatch.setattr(tls_http, "USE_TLS_IMPERSONATE", False, raising=False)

    async def fake_aiohttp(method, url, **kw):
        return 200, "aiohttp"

    monkeypatch.setattr(tls_http, "_qua_aiohttp", fake_aiohttp)
    assert asyncio.run(tls_http.request("GET", "https://x/y"))[1] == "aiohttp"
    assert goi == []          # không đụng curl_cffi
