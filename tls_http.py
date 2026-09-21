"""Gọi HTTP giả vân tay TLS/HTTP2 của Chrome cho các đường KHÔNG mở trình duyệt.

Vì sao: đo 21/09 trên tls.browserleaks.com
    Chrome thật   ja3=8aad40f6…   akamai=52d84b11737d980a
    curl_cffi     ja3=<họ Chrome> akamai=52d84b11737d980a   ← khớp vân tay HTTP/2
    aiohttp       ja3=478843a4…   akamai=(TRỐNG)            ← lộ ngay là không phải trình duyệt
Chrome hiện đại xáo thứ tự phần mở rộng TLS nên JA3 vốn đổi mỗi lần; thứ so được là vân tay HTTP/2.

Đường GỬI LỆNH (submit_http) đã dùng curl_cffi từ trước, nhưng theo dõi video và kiểm cookie — hai việc
chạy nhiều hơn hẳn — vẫn đi bằng aiohttp. Module này để mọi đường không-Chrome nói cùng một giọng.

Tắt: DOLA_TLS_FAKE=0. Đổi hồ sơ giả: DOLA_TLS_IMPERSONATE=chrome146.
"""
from __future__ import annotations

import os

try:                                            # thiếu curl_cffi thì vẫn chạy được bằng aiohttp
    from curl_cffi.requests import AsyncSession
except Exception:                               # noqa: BLE001
    AsyncSession = None

IMPERSONATE = os.getenv("DOLA_TLS_IMPERSONATE", "chrome").strip() or "chrome"
USE_TLS_IMPERSONATE = os.getenv("DOLA_TLS_FAKE", "1").strip().lower() in ("1", "true", "yes", "on")


async def _qua_aiohttp(method: str, url: str, *, headers=None, data=None,
                       proxy: str | None = None, timeout: float = 25.0) -> tuple[int, str]:
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.request(method, url, headers=headers, data=data, proxy=proxy,
                             timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            return r.status, await r.text()


async def request(method: str, url: str, *, headers=None, data=None,
                  proxy: str | None = None, timeout: float = 25.0) -> tuple[int, str]:
    """(status, text). Đi bằng curl_cffi giả Chrome; hỏng thì LUI về aiohttp — vân tay đẹp không đáng
    để đánh đổi việc job chết vì thiếu thư viện hệ thống hay proxy kiểu lạ."""
    if USE_TLS_IMPERSONATE and AsyncSession is not None:
        try:
            async with AsyncSession() as s:
                r = await s.request(method, url, headers=headers, data=data, proxy=proxy,
                                    timeout=timeout, impersonate=IMPERSONATE)
                return r.status_code, r.text
        except Exception as exc:  # noqa: BLE001
            print(f"[tls_http] curl_cffi lỗi ({str(exc)[:70]}) — lui về aiohttp", flush=True)
    return await _qua_aiohttp(method, url, headers=headers, data=data, proxy=proxy, timeout=timeout)
