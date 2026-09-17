"""Chốt chặn kết nối thẳng tới Dola (egress_guard.py). KHÔNG ra mạng thật: proxy giả chạy trên 127.0.0.1.
Chạy: .venv/bin/python test_egress_guard.py"""
import asyncio
import socket

import aiohttp

import egress_guard as g

STRICT = {"on": True}
g.install(strict_fn=lambda: STRICT["on"])


async def fake_proxy(seen):
    """Proxy HTTP giả: nhận CONNECT, ghi lại đích, trả 502 (không đi ra ngoài)."""
    async def handle(r, w):
        line = await r.readline()
        seen.append(line.decode(errors="ignore").strip())
        while (await r.readline()) not in (b"\r\n", b""):
            pass
        w.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        await w.drain()
        w.close()
    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


async def main():
    # 1) Đi thẳng tới Dola khi mọi nick có proxy → bị chặn TRƯỚC khi mở TCP
    try:
        async with aiohttp.ClientSession() as s:
            await s.get("https://www.dola.com/im/chain/single", timeout=aiohttp.ClientTimeout(total=5))
        raise AssertionError("đi thẳng tới Dola phải bị chặn")
    except aiohttp.ClientOSError as e:   # aiohttp gói lỗi kết nối; nguyên nhân gốc phải là chốt chặn
        assert "Chặn kết nối thẳng" in str(e) or isinstance(e.__cause__, g.DirectDolaBlocked), repr(e)
    try:
        socket.create_connection(("www.dola.com", 443), timeout=3)
        raise AssertionError("socket thẳng tới Dola phải bị chặn")
    except g.DirectDolaBlocked:
        pass

    # 2) Đi QUA proxy → không bị chặn: TCP tới proxy, proxy nhận đúng CONNECT www.dola.com:443
    seen = []
    srv, port = await fake_proxy(seen)
    try:
        async with aiohttp.ClientSession() as s:
            await s.get("https://www.dola.com/im/chain/single", proxy=f"http://127.0.0.1:{port}",
                        timeout=aiohttp.ClientTimeout(total=5))
    except aiohttp.ClientError as e:   # proxy giả trả 502 — đúng mong đợi; nhưng KHÔNG được là do chốt chặn
        assert "Chặn kết nối thẳng" not in str(e) and not isinstance(e.__cause__, g.DirectDolaBlocked), \
            "đi qua proxy KHÔNG được bị chặn: " + repr(e)
    finally:
        srv.close()
    assert seen and seen[0].startswith("CONNECT www.dola.com:443"), seen

    # 3) Còn nick không khai proxy (strict tắt) → đi thẳng là cấu hình người dùng, không chặn
    STRICT["on"] = False
    g._check("www.dola.com", strict_fn=lambda: STRICT["on"])
    # 4) Host khác Dola không bị đụng
    STRICT["on"] = True
    g._check("tmproxy.com", strict_fn=lambda: True)
    g._check("api.ipify.org", "api.ipify.org", strict_fn=lambda: True)
    assert g._is_dola("www.dola.com") and g._is_dola("dola.com.") and not g._is_dola("notdola.com")
    print("ALL PASS (chặn kết nối thẳng tới Dola)")


asyncio.run(main())
