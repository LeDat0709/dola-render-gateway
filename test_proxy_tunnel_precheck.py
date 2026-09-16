"""Thử nối XUYÊN proxy tới Dola ngay trước khi gửi lệnh + tự khai lại whitelist khi IP máy đổi.

Phiên thật 16/09 18:15: IP máy đổi 171.241.56.41 → 193.176.211.154 → .164 (VPN). proxyxoay xác thực theo IP máy trong
whitelist → mọi cổng proxy cắt kết nối (curl 56 sau 0,15s). Engine HTTP gửi luôn → "Mất kết nối SAU khi đã gửi lệnh"
→ coi như CÓ THỂ đã trừ lượt, không gửi lại, dù thực tế proxy từ chối TRƯỚC khi lệnh tới Dola.
Chạy: .venv/bin/python test_proxy_tunnel_precheck.py
"""
import asyncio
import json
import tempfile
import time
from pathlib import Path

import browser
import config
import proxyxoay
import submit_http
import video_worker_ui as vw


async def _fake_proxy(reply: bytes | None, seen: list):
    async def handle(r, w):
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = await r.read(1024)
            if not chunk:
                break
            head += chunk
        seen.append(head.decode(errors="replace"))
        if reply is None:
            w.transport.abort()          # cắt ngang như proxy từ chối IP chưa whitelist
            return
        w.write(reply); await w.drain(); w.close()
    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


def test_probe_tunnel_ok_rejected_and_auth():
    async def main():
        seen = []
        srv, port = await _fake_proxy(b"HTTP/1.1 200 Connection established\r\n\r\n", seen)
        ok = await browser.probe_proxy_tunnel(f"http://us%40er:p%3Ass@127.0.0.1:{port}")
        srv.close()
        assert ok == "", ok
        assert seen[0].startswith("CONNECT www.dola.com:443 HTTP/1.1"), seen
        assert "Proxy-Authorization: Basic " in seen[0], "proxy có mật khẩu phải gửi xác thực"
        srv, port = await _fake_proxy(None, [])
        bad = await browser.probe_proxy_tunnel(f"http://127.0.0.1:{port}")
        srv.close()
        assert bad, "proxy cắt kết nối mà báo ổn"
        srv, port = await _fake_proxy(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n", [])
        auth = await browser.probe_proxy_tunnel(f"http://127.0.0.1:{port}")
        srv.close()
        assert "407" in auth, auth
        assert await browser.probe_proxy_tunnel(None) == "" and await browser.probe_proxy_tunnel("socks5://1.2.3.4:1080") == ""
    asyncio.run(main())


def _nick(root, name, proxy_txt):
    (root / name).mkdir(parents=True)
    (root / name / "cookies.json").write_text(json.dumps([{"name": "sessionid", "value": "s"}]), encoding="utf-8")
    if proxy_txt:
        (root / name / "proxy.txt").write_text(proxy_txt, encoding="utf-8")


def _run_http(account, probe_results, refresh=None):
    saved = (browser.probe_proxy_tunnel, submit_http.submit_via_http, vw._global_submit_gate, vw._recent_conv_ids_http,
             browser.refresh_proxy_whitelist, browser.account_proxy_url)
    it, calls, flags = iter(probe_results), {"submit": 0, "refresh": 0, "probe": []}, []

    async def probe(url, **k):
        calls["probe"].append(url); return next(it)

    async def submit(acc, *a, on_submitted=None, **k):
        calls["submit"] += 1; on_submitted(acc, True)
        raise submit_http.SubmitHttpRejected("dừng test sau khi gửi")

    async def gate(acc): pass

    async def snap(acc, proxy): return set()

    def do_refresh(acc):
        calls["refresh"] += 1; return refresh
    browser.probe_proxy_tunnel, submit_http.submit_via_http, vw._global_submit_gate = probe, submit, gate
    vw._recent_conv_ids_http, browser.refresh_proxy_whitelist = snap, do_refresh
    browser.account_proxy_url = lambda acc: "http://1.1.1.1:11"
    try:
        try:
            asyncio.run(vw._generate_via_http(account, "p", "9:16", 10, "m", 60, None, None, None,
                                              on_submitted=lambda a, s: flags.append(s)))
            return None, calls, flags
        except Exception as e:  # noqa: BLE001
            return e, calls, flags
    finally:
        (browser.probe_proxy_tunnel, submit_http.submit_via_http, vw._global_submit_gate, vw._recent_conv_ids_http,
         browser.refresh_proxy_whitelist, browser.account_proxy_url) = saved


def test_dead_tunnel_stops_before_sending_so_no_credit_lost():
    err, calls, flags = _run_http("n1", ["Connection reset by peer", "Connection reset by peer"], refresh=False)
    assert isinstance(err, RuntimeError) and not isinstance(err, submit_http.SubmitHttpRejected), err
    assert "CHƯA gửi" in str(err), err
    assert calls["submit"] == 0 and True not in flags, (calls, flags)


def test_tunnel_rejected_refreshes_whitelist_then_sends():
    err, calls, flags = _run_http("n1", ["Connection reset by peer", ""], refresh=True)
    assert calls["refresh"] == 1 and calls["submit"] == 1 and len(calls["probe"]) == 2, calls
    assert isinstance(err, submit_http.SubmitHttpRejected), err     # đã tới bước gửi


def test_healthy_tunnel_sends_without_refresh():
    err, calls, flags = _run_http("n1", [""], refresh=True)
    assert calls["refresh"] == 0 and calls["submit"] == 1, calls


def test_vpn_rotating_ip_gets_explicit_message():
    link = "https://proxyxoay.shop/api/get.php?key=VPNTEST&nhamang=random&tinhthanh=0"
    root = Path(tempfile.mkdtemp()) / "accounts"; _nick(root, "vpn", link)
    saved = (config.ACCOUNTS_DIR, config.PROXY, proxyxoay._read_public_ip)
    ips = iter(["193.176.211.171", "193.176.211.158"])
    config.ACCOUNTS_DIR, config.PROXY = root, ""
    proxyxoay._read_public_ip = lambda: next(ips)
    try:
        assert proxyxoay.machine_ip_unstable() is True
        proxyxoay._read_public_ip = lambda: "171.241.56.41"
        assert proxyxoay.machine_ip_unstable() is False
        ips2 = iter(["193.176.211.171", "193.176.211.158"])
        proxyxoay._read_public_ip = lambda: next(ips2)
        err, calls, flags = _run_http("vpn", ["Connection reset by peer", "Connection reset by peer"], refresh=True)
        assert isinstance(err, RuntimeError) and "VPN" in str(err) and "CHƯA gửi" in str(err), err
        assert calls["submit"] == 0, calls
    finally:
        config.ACCOUNTS_DIR, config.PROXY, proxyxoay._read_public_ip = saved


def test_refresh_whitelist_forces_new_machine_ip_now():
    link = "https://proxyxoay.shop/api/get.php?key=WLTEST&nhamang=random&tinhthanh=0"
    root = Path(tempfile.mkdtemp()) / "accounts"; _nick(root, "px", link)
    saved = (config.ACCOUNTS_DIR, config.PROXY, proxyxoay._get, proxyxoay._public_ipv4, dict(proxyxoay._pub_ip))
    urls, ips = [], iter(["193.176.211.164"])
    now = time.time()
    config.ACCOUNTS_DIR, config.PROXY = root, ""
    proxyxoay._cache[link] = {"server": "http://1.1.1.1:11", "ip": "1.1.1.1:11", "fetched_at": now, "ttl": 600,
                              "next_ok": 0, "life": 0, "day": time.strftime("%Y-%m-%d"), "changes": 1}
    proxyxoay._wl_sent[link] = "193.176.211.154"          # đã khai IP CŨ; bộ nhớ đệm IP máy còn hạn 5 phút
    proxyxoay._pub_ip.update(ip="193.176.211.154", at=now)
    proxyxoay._get = lambda url: (urls.append(url), json.dumps({"status": 100, "proxyhttp": "1.1.1.1:11"}))[1]

    def fresh_ip(t):
        return next(ips) if proxyxoay._pub_ip["at"] == 0 else proxyxoay._pub_ip["ip"]
    proxyxoay._public_ipv4 = fresh_ip
    try:
        assert browser.refresh_proxy_whitelist("px") is True
        assert urls and urls[-1].endswith("&whitelist=193.176.211.164"), urls
        assert browser.refresh_proxy_whitelist("nope") is False     # nick không có proxy xoay
    finally:
        config.ACCOUNTS_DIR, config.PROXY, proxyxoay._get, proxyxoay._public_ipv4 = saved[:4]
        proxyxoay._pub_ip.clear(); proxyxoay._pub_ip.update(saved[4])
        proxyxoay._cache.pop(link, None); proxyxoay._wl_sent.pop(link, None)


if __name__ == "__main__":
    for name in [n for n in dir() if n.startswith("test_")]:
        globals()[name]()
        print("PASS", name)
    print("OK")
