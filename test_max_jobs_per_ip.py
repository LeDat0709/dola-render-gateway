"""Trần job CHẠY CÙNG LÚC trên một IP ra (DOLA_MAX_JOBS_PER_IP) — học từ đối thủ v1.0.88:
"Đang chờ chỗ trên IP chung — còn …", "1 key = 1 IP sống".

Máy anh: 27 nick, không proxy, 10 job cùng lúc trên MỘT IP → dính 710022002 hàng loạt.
Chạy: .venv/bin/python test_max_jobs_per_ip.py
"""
import asyncio
import tempfile
import time
from pathlib import Path

import browser
import browser_pool
from browser_pool import BrowserPool

browser_pool.config.SUBMIT_GAP_SEC = 0
browser_pool.config.SUBMIT_JITTER_SEC = 0
browser_pool.config.AUTO_RETRY = True
browser_pool.EGRESS_POLL_SEC = 0.02


def _setup(tmp, proxies, max_concurrency=10):
    acc = Path(tmp) / "accounts"
    for nick, raw in proxies.items():
        (acc / nick).mkdir(parents=True)
        if raw:
            (acc / nick / "proxy.txt").write_text(raw, encoding="utf-8")
    browser_pool.config.ACCOUNTS_DIR, browser_pool.config.PROXY = acc, ""
    return BrowserPool(accounts_dir=str(acc), db_path=str(Path(tmp) / "p.db"), max_concurrency=max_concurrency)


class _env:
    """Giả worker: đo số job ĐANG chạy cùng lúc theo từng IP và mốc bắt đầu/kết thúc."""
    def __init__(self, cap, hold=0.15):
        self.cap, self.hold = cap, hold

    def __enter__(self):
        self.saved = (browser_pool.config.MAX_JOBS_PER_IP, browser_pool.config.ACCOUNTS_DIR, browser_pool.config.PROXY,
                      browser_pool.generate_video, browser.probe_proxy)
        browser_pool.config.MAX_JOBS_PER_IP = self.cap
        self.running, self.peak, self.start, self.end = 0, 0, {}, {}

        async def ok_probe(raw, timeout=3.0):
            return ""
        browser.probe_proxy = ok_probe
        browser._proxy_probe_cache.clear()

        async def gen(account, *a, on_submitted=None, **kw):
            self.start[account] = time.monotonic()
            self.running += 1
            self.peak = max(self.peak, self.running)
            try:
                if on_submitted:
                    on_submitted(account, True)
                await asyncio.sleep(self.hold)
                return {"video_url": "u", "account": account}
            finally:
                self.running -= 1
                self.end[account] = time.monotonic()
        browser_pool.generate_video = gen
        return self

    def __exit__(self, *a):
        (browser_pool.config.MAX_JOBS_PER_IP, browser_pool.config.ACCOUNTS_DIR, browser_pool.config.PROXY,
         browser_pool.generate_video, browser.probe_proxy) = self.saved


async def _all(pool, nicks):
    return await asyncio.gather(*(pool.generate_video("p", "9:16", 10, account=n) for n in nicks))


def test_cap_limits_jobs_on_same_ip():
    with tempfile.TemporaryDirectory() as tmp, _env(cap=2) as env:
        pool = _setup(tmp, {"n1": "", "n2": "", "n3": ""})          # cả 3 đi IP máy
        res = asyncio.run(_all(pool, ["n1", "n2", "n3"]))
        assert env.peak == 2, f"trần 2 mà có {env.peak} job cùng lúc trên một IP"
        assert sorted(r["account"] for r in res) == ["n1", "n2", "n3"], "job phải CHỜ rồi chạy, không bỏ"
        assert browser_pool._egress_busy == {}, f"hết job phải trả chỗ: {browser_pool._egress_busy}"


def test_zero_means_unlimited_like_before():
    with tempfile.TemporaryDirectory() as tmp, _env(cap=0) as env:
        pool = _setup(tmp, {"n1": "", "n2": "", "n3": ""})
        asyncio.run(_all(pool, ["n1", "n2", "n3"]))
        assert env.peak == 3, f"0 = không giới hạn (như cũ), gặp {env.peak}"


def test_different_ips_do_not_block_each_other():
    with tempfile.TemporaryDirectory() as tmp, _env(cap=1) as env:
        pool = _setup(tmp, {"n1": "1.1.1.1:8080", "n2": "2.2.2.2:8080"})
        asyncio.run(_all(pool, ["n1", "n2"]))
        assert env.peak == 2, f"hai IP khác nhau không được chờ nhau, gặp {env.peak}"


def test_waiting_job_releases_chrome_slot():
    """Job chờ chỗ trên IP bận KHÔNG được giữ slot Chrome — không thì job trên IP rảnh cũng phải đứng chờ theo."""
    with tempfile.TemporaryDirectory() as tmp, _env(cap=1, hold=0.3) as env:
        pool = _setup(tmp, {"n1": "", "n2": "", "n3": "3.3.3.3:8080"}, max_concurrency=2)

        async def main():
            a = asyncio.create_task(pool.generate_video("p", "9:16", 10, account="n1"))
            await asyncio.sleep(0.05)                       # n1 đã chiếm IP máy
            b = asyncio.create_task(pool.generate_video("p", "9:16", 10, account="n2"))   # chờ chỗ IP máy
            await asyncio.sleep(0.05)
            c = asyncio.create_task(pool.generate_video("p", "9:16", 10, account="n3"))   # IP khác, phải chạy ngay
            await asyncio.gather(a, b, c)
        asyncio.run(main())
        assert env.start["n3"] < env.end["n1"], "n3 (IP rảnh) bị kẹt sau n1 vì n2 đang chờ mà vẫn giữ slot Chrome"
        assert env.start["n2"] >= env.end["n1"] - 0.01, "n2 cùng IP với n1 phải chờ n1 xong"


def test_slot_released_when_job_fails():
    with tempfile.TemporaryDirectory() as tmp, _env(cap=1) as env:
        pool = _setup(tmp, {"n1": ""})

        async def boom(account, *a, **kw):
            raise ValueError("lỗi code giả")
        browser_pool.generate_video = boom
        try:
            asyncio.run(pool.generate_video("p", "9:16", 10, account="n1"))
        except Exception:
            pass
        assert browser_pool._egress_busy == {}, f"job lỗi vẫn giữ chỗ IP: {browser_pool._egress_busy}"


if __name__ == "__main__":
    test_cap_limits_jobs_on_same_ip()
    test_zero_means_unlimited_like_before()
    test_different_ips_do_not_block_each_other()
    test_waiting_job_releases_chrome_slot()
    test_slot_released_when_job_fails()
    print("OK")
