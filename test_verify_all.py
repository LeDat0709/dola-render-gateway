"""Regression: "Kiểm tra tất cả" (verify_all) không được dính vào hàng giãn nhịp gửi video.

Log 19/09 22:08: verify_account gọi _global_submit_gate → mỗi nick giữ 1 khe gửi (~15s/khe). Khe của nick thứ 3
trở đi nằm quá VERIFY_TIMEOUT_SEC=30s → cả loạt báo "quá 30s (proxy chết?)" mà chưa kiểm gì, còn khe đã giữ
thì không trả lại → video thật phải chờ 242s mới được gửi. Thời gian xếp hàng chờ slot Chrome cũng không được
tính vào giờ kiểm của nick.

Chạy: .venv/bin/python test_verify_all.py
"""
import asyncio
import tempfile
from pathlib import Path

import browser
import browser_pool
import video_worker_ui as vw
from browser_pool import BrowserPool


def _pool(tmp: str, names) -> BrowserPool:
    root = Path(tmp) / "accounts"
    for n in names:   # không có cookies.json → verify_all phải đi đường Chrome (check_login_state)
        (root / n).mkdir(parents=True)
    return BrowserPool(accounts_dir=str(root), db_path=str(Path(tmp) / "p.db"), max_concurrency=2)


async def _run(names, check_login_state, gate=None):
    saved = (browser.check_login_state, vw._global_submit_gate)
    browser.check_login_state = check_login_state
    if gate:
        vw._global_submit_gate = gate
    try:
        with tempfile.TemporaryDirectory() as tmp:
            return await _pool(tmp, names).verify_all()
    finally:
        browser.check_login_state, vw._global_submit_gate = saved


async def test_verify_all_does_not_take_submit_slots():
    seen = []

    async def gate(account):
        seen.append(account)

    async def ok(_name):
        return True

    results = await _run(["n1", "n2", "n3"], ok, gate)
    assert seen == [], f"verify_all giữ khe gửi video của: {seen}"
    assert all(r["checked"] and r["ok"] for r in results), results


async def test_waiting_for_browser_slot_does_not_count_toward_timeout():
    cfg = browser_pool.config   # test khác có thể thay sys.modules["config"]; sửa đúng object browser_pool đang đọc
    saved = (browser_pool.VERIFY_TIMEOUT_SEC, cfg.LOGIN_CONCURRENCY)
    browser_pool.VERIFY_TIMEOUT_SEC, cfg.LOGIN_CONCURRENCY = 0.3, 1

    async def slow_ok(_name):
        await asyncio.sleep(0.2)   # mỗi nick 0.2s < 0.3s; nick thứ 3 phải xếp hàng 0.4s
        return True

    try:
        results = await _run(["n1", "n2", "n3"], slow_ok)
    finally:
        browser_pool.VERIFY_TIMEOUT_SEC, cfg.LOGIN_CONCURRENCY = saved
    assert all(r["checked"] for r in results), f"xếp hàng bị tính là quá giờ: {results}"


async def test_really_slow_check_still_times_out():
    saved = browser_pool.VERIFY_TIMEOUT_SEC
    browser_pool.VERIFY_TIMEOUT_SEC = 0.1

    async def hang(_name):
        await asyncio.sleep(5)

    try:
        results = await _run(["n1"], hang)
    finally:
        browser_pool.VERIFY_TIMEOUT_SEC = saved
    assert results == [{"name": "n1", "ok": False, "checked": False}], results


if __name__ == "__main__":
    tests = [test_verify_all_does_not_take_submit_slots,
             test_waiting_for_browser_slot_does_not_count_toward_timeout,
             test_really_slow_check_still_times_out]
    failed = 0
    for t in tests:
        try:
            asyncio.run(t())
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    raise SystemExit(1 if failed else 0)
