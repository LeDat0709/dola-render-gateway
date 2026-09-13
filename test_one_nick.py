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


async def _run(tmp, one_nick, proxy, fail_first=None):
    config.ONE_NICK = one_nick
    config.PROXY = proxy
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


def main():
    orig = (browser_pool.generate_video, browser_pool._pace, browser.rotate_effective_proxy,
            config.ONE_NICK, config.PROXY)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            r = asyncio.run(_run(tmp, True, ROT))
        assert r["peak"] == 1, f"phải 1 nick/lần, gặp {r['peak']}"
        assert len(r["rotated"]) == 2, f"phải đổi IP 2 lần, gặp {r['rotated']}"
        assert r["one_nick_val"] == 1, f"semaphore rò rỉ (value={r['one_nick_val']})"
        assert all(not isinstance(x, Exception) for x in r["results"]), r["results"]

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

        print("test_one_nick: OK")
    finally:
        (browser_pool.generate_video, browser_pool._pace, browser.rotate_effective_proxy,
         config.ONE_NICK, config.PROXY) = orig


if __name__ == "__main__":
    main()
