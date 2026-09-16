"""Xoay IP proxy xoay khi IP không đủ sống hết job, hoặc đã CHẾT giữa lúc dựng.

Phiên thật 16/09 16:42–17:25: IP proxyxoay sống ~25–30 phút; job 30s kèm hỏi-đáp chạy 15–33 phút. 17:04 IP chết →
2 job đọc hội thoại qua cổng chết 17 phút (244 lỗi mạng), "đổi đường" 3 lần đều lấy lại ĐÚNG cổng chết từ bộ nhớ đệm;
cả phiên tool đổi IP 0 lần. Chạy: .venv/bin/python test_proxy_dead_rotation.py
"""
import asyncio
import json
import tempfile
import time
from pathlib import Path

import browser
import config
import proxyxoay
import video_worker_ui as vw

LINK = "https://proxyxoay.shop/api/get.php?key=DEADROT&nhamang=random&tinhthanh=0"
A, B = "1.1.1.1:11", "2.2.2.2:22"


class _env:
    """Nick n1 đi proxy xoay LINK; cache đang giữ IP A (còn hạn); nhà bán nếu được gọi sẽ cấp IP B."""
    def __init__(self, life=0, leases=0):
        self.life, self.leases = life, leases

    def __enter__(self):
        self.saved = (config.ACCOUNTS_DIR, config.PROXY, proxyxoay._get, proxyxoay._public_ipv4)
        root = Path(tempfile.mkdtemp()) / "accounts"
        (root / "n1").mkdir(parents=True)
        (root / "n1" / "proxy.txt").write_text(LINK, encoding="utf-8")
        (root / "n1" / "cookies.json").write_text(json.dumps([{"name": "sessionid", "value": "s"}]), encoding="utf-8")
        config.ACCOUNTS_DIR, config.PROXY = root, ""
        now = time.time()
        proxyxoay._cache[LINK] = {"server": f"http://{A}", "ip": A, "network": "", "location": "", "message": "",
                                  "fetched_at": now, "ttl": max(self.life - 30, 0) if self.life else 600,
                                  "life": self.life, "next_ok": 0, "day": time.strftime("%Y-%m-%d"), "changes": 1}
        self.calls = []

        def provider(url):
            self.calls.append(url)
            return json.dumps({"status": 100, "proxyhttp": B})
        proxyxoay._get, proxyxoay._public_ipv4 = provider, (lambda now: "")
        browser._proxy_leases.clear()
        if self.leases:
            browser._proxy_leases[LINK] = self.leases
        browser._DEAD_ROTATED_AT.clear()
        return self

    def __exit__(self, *a):
        config.ACCOUNTS_DIR, config.PROXY, proxyxoay._get, proxyxoay._public_ipv4 = self.saved
        proxyxoay._cache.pop(LINK, None)
        browser._proxy_leases.clear()
        browser._DEAD_ROTATED_AT.clear()


# ---------- 1) trước khi mở nick: IP phải đủ sống hết job ----------
def test_min_life_depends_on_duration():
    assert browser.min_life_for(30) == browser.PROXY_MIN_LIFE_30S_SEC == 1200
    assert browser.min_life_for(10) == browser.min_life_for(15) == browser.min_life_for(None) == browser.PROXY_MIN_LIFE_SEC == 600


def test_rotates_before_job_when_ip_would_die_mid_render():
    with _env(life=900) as env:                       # IP còn ~15 phút
        assert browser.rotate_if_expiring("n1", min_life=600) is False and env.calls == [], "đủ cho video 10–15s → giữ IP"
        assert browser.rotate_if_expiring("n1", min_life=1200) is True and len(env.calls) == 1, "không đủ cho video 30s → đổi"
        assert proxyxoay._cache[LINK]["ip"] == B


def test_pool_passes_duration_based_min_life():
    import browser_pool
    from browser_pool import BrowserPool
    seen, saved = [], (browser.rotate_if_expiring, browser_pool.generate_video, config.SUBMIT_GAP_SEC,
                       config.SUBMIT_JITTER_SEC, config.SUBMIT_GAP_GLOBAL_SEC)

    def spy(account, min_life=None):
        seen.append(min_life); return False

    async def gen(account, *a, on_submitted=None, **k):
        on_submitted(account, True); return {"video_url": "u", "account": account}
    browser.rotate_if_expiring, browser_pool.generate_video = spy, gen
    config.SUBMIT_GAP_SEC = config.SUBMIT_JITTER_SEC = config.SUBMIT_GAP_GLOBAL_SEC = 0
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "accounts"; (root / "n1").mkdir(parents=True)
            config.ACCOUNTS_DIR, config.PROXY = root, ""
            pool = BrowserPool(accounts_dir=str(root), db_path=str(Path(tmp) / "p.db"), max_concurrency=2)
            asyncio.run(pool.generate_video("p", "9:16", 30, account="n1"))
            asyncio.run(pool.generate_video("p", "9:16", 10, account="n1"))
    finally:
        (browser.rotate_if_expiring, browser_pool.generate_video, config.SUBMIT_GAP_SEC, config.SUBMIT_JITTER_SEC,
         config.SUBMIT_GAP_GLOBAL_SEC) = saved
    assert seen == [1200, 600], seen


# ---------- 2+3) IP chết giữa lúc dựng: xin IP MỚI thật, kể cả khi còn job khác trên key ----------
def test_dead_proxy_forces_new_ip_even_with_other_jobs_on_key():
    with _env(leases=3) as env:                       # 3 job khác đang chạy trên key → luật giữ chỗ bình thường sẽ chặn
        got = vw._next_poll_proxy("n1", f"http://{A}", proxy_dead=True)
        assert got == f"http://{B}" and len(env.calls) == 1, (got, env.calls)


def test_many_jobs_detecting_same_dead_ip_rotate_only_once():
    with _env(leases=3) as env:
        first = vw._next_poll_proxy("n1", f"http://{A}", proxy_dead=True)
        again = vw._next_poll_proxy("n1", f"http://{A}", proxy_dead=True)   # job khác cùng key cũng thấy chết
        assert first == again == f"http://{B}" and len(env.calls) == 1, (first, again, env.calls)


def test_http_error_from_dola_is_not_a_dead_proxy():
    with _env(leases=3) as env:                       # proxy vẫn sống (Dola trả mã HTTP) → không được đổi IP
        assert vw._next_poll_proxy("n1", f"http://{A}", proxy_dead=False) == f"http://{A}" and env.calls == []


def test_poll_recovers_from_dead_ip_by_fetching_new_ip():
    """Toàn tuyến theo dõi HTTP: cổng A chết (lỗi kết nối) → sau POLL_NET_FAILS lần xin IP B → đọc được video."""
    video_page = {"downlink_body": {"pull_singe_chain_downlink_body": {"messages": [{"content": json.dumps([
        {"block_type": 2074, "content": {"creation_block": {"creations": [{"type": 2, "video": {"download_url": "https://x/v.mp4", "video_model": ""}}]}}}])}]}}}
    used = []

    class Resp:
        status = 200
        async def json(self, **_): return video_page
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class Session:
        def post(self, *a, proxy=None, **k):
            used.append(proxy)
            if proxy == f"http://{A}":
                raise OSError("Connection reset by peer")
            return Resp()
        async def close(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    async def no_download(url, account, prompt=""): return "/tmp/v.mp4", None
    real_sleep = asyncio.sleep

    async def fast(t): await real_sleep(0)
    with _env(leases=2) as env:
        saved = (vw.aiohttp.ClientSession, vw._download_or_link, vw.asyncio.sleep)
        vw.aiohttp.ClientSession, vw._download_or_link, vw.asyncio.sleep = Session, no_download, fast
        try:
            out = asyncio.run(vw.poll_conversation_http("n1", "c=1", "", "", "77", 60, answered=set()))
        finally:
            vw.aiohttp.ClientSession, vw._download_or_link, vw.asyncio.sleep = saved
        assert out.get("local_path"), out
        assert used[:vw.POLL_NET_FAILS] == [f"http://{A}"] * vw.POLL_NET_FAILS and used[-1] == f"http://{B}", used
        assert len(env.calls) == 1, env.calls


def test_static_proxy_is_never_rotated():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "accounts"; (root / "st").mkdir(parents=True)
        (root / "st" / "proxy.txt").write_text("9.9.9.9:3128", encoding="utf-8")
        saved = (config.ACCOUNTS_DIR, config.PROXY)
        config.ACCOUNTS_DIR, config.PROXY = root, ""
        try:
            assert browser.rotate_dead_proxy("st", "test") is False
            assert vw._next_poll_proxy("st", "http://9.9.9.9:3128", proxy_dead=True) == "http://9.9.9.9:3128"
        finally:
            config.ACCOUNTS_DIR, config.PROXY = saved


if __name__ == "__main__":
    for name in [n for n in dir() if n.startswith("test_")]:
        globals()[name]()
        print("PASS", name)
    print("OK")
