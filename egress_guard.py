"""Chốt chặn cuối: KHÔNG kết nối thẳng tới Dola bằng IP máy khi mọi nick đều đã khai proxy.

Đo thật 17/9 10:57: tiến trình gateway mở 9 kết nối TCP thẳng tới www.dola.com (Akamai 23.58.144.237) trong khi cả 95
nick đều gắn tmproxy — đi qua proxy thì TCP phải tới IP proxy, không tới Dola. Các đường đã biết đều truyền proxy, nên
chặn ở tầng mở kết nối (asyncio + socket) và in ngăn xếp gọi để lộ đúng chỗ còn sót.

Kết nối QUA proxy đi tới IP/host proxy → không bị đụng. curl_cffi (libcurl) và Chrome không đi qua đây — hai đường đó
tự truyền proxy (submit_http, --proxy-server).
Test: .venv/bin/python test_egress_guard.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import time
import traceback

import config

DOLA_HOSTS = ("dola.com",)            # www.dola.com, api…dola.com
_CACHE_SEC = 60
_state = {"at": 0.0, "strict": False}
_installed = False


class DirectDolaBlocked(ConnectionError):
    """Chặn kết nối thẳng tới Dola (mọi nick đều có proxy → đây là rò IP máy)."""


def _is_dola(host) -> bool:
    h = str(host or "").lower().rstrip(".")
    return any(h == d or h.endswith("." + d) for d in DOLA_HOSTS)


def all_nicks_proxied() -> bool:
    """True khi có proxy chung, hoặc MỌI nick đều có proxy.txt không rỗng (đọc lại tối đa mỗi 60s)."""
    now = time.time()
    if now - _state["at"] < _CACHE_SEC:
        return _state["strict"]
    strict = False
    try:
        if (config.PROXY or "").strip():
            strict = True
        else:
            root = config.ACCOUNTS_DIR
            names = [n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n))]
            strict = bool(names) and all(_has_proxy_file(os.path.join(root, n, "proxy.txt")) for n in names)
    except OSError:
        strict = False
    _state.update(at=now, strict=strict)
    return strict


def _has_proxy_file(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            return bool(f.read().strip())
    except OSError:
        return False


def _check(host, server_hostname=None, strict_fn=all_nicks_proxied) -> None:
    if not (_is_dola(host) or _is_dola(server_hostname)):
        return
    if not strict_fn():
        return   # còn nick không khai proxy → đi thẳng là cấu hình của người dùng
    # khung TRONG CÙNG (gần chỗ gọi nhất), bỏ 2 khung của chính chốt chặn; khung thư viện aiohttp/asyncio lọc bớt
    frames = [f for f in traceback.format_stack()[:-2] if "/site-packages/" not in f and "/asyncio/" not in f]
    stack = "".join(frames[-8:])
    print(f"[egress] CHẶN kết nối THẲNG tới {server_hostname or host} bằng IP máy (mọi nick đều có proxy). "
          f"Nơi gọi:\n{stack}", flush=True)
    raise DirectDolaBlocked(f"Chặn kết nối thẳng tới {server_hostname or host} — nick có proxy không được đi IP máy.")


def install(strict_fn=all_nicks_proxied) -> None:
    """Gắn chốt chặn (1 lần):
    - aiohttp: TCPConnector._create_direct_connection — nơi DUY NHẤT biết chắc đích TCP (đi qua proxy thì aiohttp gọi hàm
      này với request tới PROXY; đi thẳng thì với request tới Dola). Chặn ở asyncio.create_connection không phân biệt
      được: aiohttp 3.10+ mở socket trước (Happy Eyeballs) rồi mới create_connection(sock=, server_hostname=Dola) cho
      cả hai trường hợp.
    - asyncio.open_connection / loop.create_connection(host=…) không kèm sock (probe đường hầm, script tự viết).
    - socket.create_connection (thư viện đồng bộ)."""
    global _installed
    if _installed:
        return
    _installed = True
    from aiohttp.connector import TCPConnector

    orig_direct = TCPConnector._create_direct_connection
    orig_loop_conn = asyncio.base_events.BaseEventLoop.create_connection
    orig_sock_conn = socket.create_connection

    async def guarded_direct(self, req, *args, **kwargs):
        _check(getattr(req, "host", None) or getattr(getattr(req, "url", None), "host", None), None, strict_fn)
        return await orig_direct(self, req, *args, **kwargs)

    async def guarded_loop_conn(self, protocol_factory, host=None, port=None, *args, **kwargs):
        if kwargs.get("sock") is None:   # sock= : bọc lên socket đã nối (đã qua chốt ở tầng trên) → không xét lại
            _check(host, kwargs.get("server_hostname"), strict_fn)
        return await orig_loop_conn(self, protocol_factory, host, port, *args, **kwargs)

    def guarded_sock_conn(address, *args, **kwargs):
        _check(address[0] if isinstance(address, tuple) else None, None, strict_fn)
        return orig_sock_conn(address, *args, **kwargs)

    TCPConnector._create_direct_connection = guarded_direct
    asyncio.base_events.BaseEventLoop.create_connection = guarded_loop_conn
    socket.create_connection = guarded_sock_conn
