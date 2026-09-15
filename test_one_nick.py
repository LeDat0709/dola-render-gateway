"""Tự kiểm MỖI LẦN MỘT NICK (Feature #4), không cần mạng: chạy .venv/bin/python test_one_nick.py

Chốt các bất biến của cổng one-nick trong browser_pool.generate_video:
  1) BẬT + proxy chung XOAY  → tuần tự 1 nick/lần, đổi IP đầu mỗi nick, semaphore nhả hết.
  2) BẬT + proxy KHÔNG xoay   → không serialize (render chồng như cũ), không đổi IP.
  3) TẮT + proxy xoay         → không serialize, không đổi IP.
  4) BẬT + proxy xoay + nick đầu LỖI (retry) → KHÔNG deadlock (bug đã sửa: khoá theo thứ tự one_nick→browser),
     semaphore nhả hết, cả 2 job xong.
_pace bị vô hiệu để test không phụ thuộc DOLA_SUBMIT_GAP của môi trường."""
import asyncio
import tempfile
import time
from pathlib import Path

import config
import browser
import browser_pool
from browser_pool import BrowserPool

ROT = "tmproxy://" + "a" * 32   # is_rotating_proxy → True (không gọi mạng ở test này)


async def _nopace(*a, **k):      # bỏ giãn nhịp gửi để đo song song thuần, khỏi lệ thuộc SUBMIT_GAP
    return None


def _pool(tmp, nicks=("acc1", "acc2")):
    accounts = Path(tmp) / "accounts"
    for n in nicks:
        (accounts / n).mkdir(parents=True)
    p = BrowserPool(accounts_dir=str(accounts), db_path=str(Path(tmp) / "pool.db"), max_concurrency=1)
    for n in nicks:
        p.set_login_status(n, True)
    return p


def _install_fakes(rotated, peak, fail_first=None):
    live = {"n": 0}

    async def fake_generate(account, prompt, *a, on_browser_free=None, **kw):
        live["n"] += 1
        peak.append(live["n"])
        try:
            await asyncio.sleep(0.15)
            if on_browser_free:
                on_browser_free()          # trả slot Chrome sớm (như thật)
            if fail_first and account == fail_first and account not in rotated["failed"]:
                rotated["failed"].add(account)
                raise browser_pool.TransientDolaError("lỗi giả 1 lần")
            await asyncio.sleep(0.5)        # render
            return {"local_path": f"/tmp/{account}.mp4", "account": account}
        finally:
            live["n"] -= 1

    browser_pool.generate_video = fake_generate
    browser_pool._pace = _nopace
    browser.rotate_effective_proxy = lambda account: rotated["ips"].append(account)


async def _run(tmp, one_nick, proxy, fail_first=None, nicks_per_ip=1):
    config.ONE_NICK = one_nick
    config.PROXY = proxy
    config.NICKS_PER_IP = nicks_per_ip
    pool = _pool(tmp)
    rotated = {"ips": [], "failed": set()}
    peak = []
    _install_fakes(rotated, peak, fail_first)
    t0 = time.monotonic()
    results = await asyncio.wait_for(
        asyncio.gather(pool.generate_video("p1"), pool.generate_video("p2"), return_exceptions=True),
        timeout=20)   # deadlock (bug cũ) → treo → TimeoutError làm test đỏ thay vì treo mãi
    return {"elapsed": time.monotonic() - t0, "peak": max(peak), "rotated": rotated["ips"],
            "results": results, "one_nick_val": pool._one_nick._value}


async def _run_parallel(tmp):
    config.ONE_NICK, config.PROXY, config.NICKS_PER_IP, config.PARALLEL_PER_IP = True, ROT, 2, 2
    browser_pool.IP_DRAIN_POLL_SEC = 0.05
    nicks = ("acc1", "acc2", "acc3", "acc4")
    pool = _pool(tmp, nicks)
    pool.set_max_concurrency(4)
    rotated = {"ips": [], "failed": set()}
    peak, leases = [], []
    _install_fakes(rotated, peak)
    key = browser.normalize_proxy_input(ROT)

    def fake_rotate(account):
        leases.append(browser._proxy_leases.get(key, 0))
        rotated["ips"].append(account)
    browser.rotate_effective_proxy = fake_rotate
    results = await asyncio.wait_for(asyncio.gather(*(pool.generate_video(f"p{i}") for i in range(4)), return_exceptions=True), timeout=20)
    return {"peak": max(peak), "rotated": rotated["ips"], "leases_at_rotate": leases, "results": results,
            "one_nick_val": pool._one_nick._value, "sem_val": pool.semaphore._value}


def main():
    orig = (browser_pool.generate_video, browser_pool._pace, browser.rotate_effective_proxy,
            config.ONE_NICK, config.PROXY, config.NICKS_PER_IP, config.AUTO_RETRY, config.PARALLEL_PER_IP)
    config.AUTO_RETRY = True   # ép BẬT: kịch bản 4 kiểm retry+xoay, không phụ thuộc DOLA_AUTO_RETRY của .env.local
    try:
        # N=1: mỗi nick 1 IP → 2 nick đổi IP 2 lần, tuần tự
        with tempfile.TemporaryDirectory() as tmp:
            r = asyncio.run(_run(tmp, True, ROT, nicks_per_ip=1))
        assert r["peak"] == 1, f"phải 1 nick/lần, gặp {r['peak']}"
        assert len(r["rotated"]) == 2, f"N=1 phải đổi IP 2 lần, gặp {r['rotated']}"
        assert r["one_nick_val"] == 1, f"semaphore rò rỉ (value={r['one_nick_val']})"
        assert all(not isinstance(x, Exception) for x in r["results"]), r["results"]

        # N=2: 2 nick dùng CHUNG 1 IP → chỉ đổi IP 1 lần (tái dùng cho nick thứ 2), vẫn tuần tự
        with tempfile.TemporaryDirectory() as tmp:
            r = asyncio.run(_run(tmp, True, ROT, nicks_per_ip=2))
        assert r["peak"] == 1, f"N=2 vẫn tuần tự, gặp {r['peak']}"
        assert len(r["rotated"]) == 1, f"N=2 chỉ đổi IP 1 lần cho 2 nick, gặp {r['rotated']}"

        with tempfile.TemporaryDirectory() as tmp:
            r = asyncio.run(_run(tmp, True, ""))
        assert r["peak"] == 2 and r["rotated"] == [], f"proxy không xoay: phải chồng, không đổi IP ({r})"

        with tempfile.TemporaryDirectory() as tmp:
            r = asyncio.run(_run(tmp, False, ROT))
        assert r["peak"] == 2 and r["rotated"] == [], f"tắt: phải chồng, không đổi IP ({r})"

        with tempfile.TemporaryDirectory() as tmp:
            r = asyncio.run(_run(tmp, True, ROT, fail_first="acc1"))   # không được deadlock
        assert r["one_nick_val"] == 1, f"lỗi giữa chừng rò rỉ semaphore (value={r['one_nick_val']})"
        assert r["peak"] == 1, f"vẫn 1 nick/lần khi có lỗi, gặp {r['peak']}"
        assert len([x for x in r["results"] if isinstance(x, dict)]) == 2, r["results"]

        # K=2 song song trên IP chung, N=2 job/IP, 4 job: tối đa 2 job cùng lúc; đổi IP 2 lần (đầu lô 1, đầu lô 2) và
        # CHỈ đổi khi không còn job nào chạy trên IP (sổ giữ chỗ = 0) — đổi lúc đang chạy = cắt IP của job đó.
        with tempfile.TemporaryDirectory() as tmp:
            r = asyncio.run(_run_parallel(tmp))
        assert r["peak"] == 2, f"K=2 phải chạy 2 job song song, gặp {r['peak']}"
        assert len(r["rotated"]) == 2, f"4 job, 2 job/IP → đổi IP 2 lần, gặp {r['rotated']}"
        assert r["leases_at_rotate"] == [0, 0], f"đổi IP lúc còn job chạy trên IP: {r['leases_at_rotate']}"
        assert all(isinstance(x, dict) for x in r["results"]), r["results"]
        assert r["one_nick_val"] == 2 and r["sem_val"] == 4, (r["one_nick_val"], r["sem_val"])

        print("test_one_nick: OK")
    finally:
        (browser_pool.generate_video, browser_pool._pace, browser.rotate_effective_proxy,
         config.ONE_NICK, config.PROXY, config.NICKS_PER_IP, config.AUTO_RETRY, config.PARALLEL_PER_IP) = orig


if __name__ == "__main__":
    main()
