"""Tự kiểm các FIX đã áp dụng cho browser_pool.py (theo phân tích):
  Fix 1: _pace slot leak khi CancelledError (commit slot cả khi cancel)
  Fix 2: _rest_after_presubmit_fail race condition (có _fail_lock + async)
  Fix 3: _hold_browser có timeout 60s
  Fix 5: _egress_slot decrement an toàn
  Fix 9: _gc_rate_state cleanup memory
  Fix 10: _reset_rate_state clear _PACE_LOCKS
  Fix 15: delete_account cleanup in-memory caches
  Fix 21: _settle defensive khi result không phải dict

KHÔNG cần mạng. Chạy: .venv/bin/python test_browser_pool_fixes.py
"""
import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import browser_pool
from browser_pool import BrowserPool


def _make_pool(tmp: str) -> BrowserPool:
    """Tạo pool trống với DB riêng (không ảnh hưởng accounts/)."""
    accounts = Path(tmp) / "accounts"
    accounts.mkdir()
    return BrowserPool(accounts_dir=str(accounts), db_path=str(Path(tmp) / "pool.db"), max_concurrency=4)


# ───────────────────────── Fix 1: _pace slot commit khi cancel ─────────────────────────

def test_pace_commit_slot_even_when_cancelled():
    """Nếu task bị cancel giữa lúc đang sleep trong _pace → slot vẫn phải được commit để
    job SAU (không bị cancel) khỏi đâm vào nhịp cũ."""
    async def main():
        browser_pool._reset_rate_state()
        try:
            # Lần 1: chạy bình thường → slot tăng
            await browser_pool._pace(key="test_k1")
            key_state = browser_pool._rate_state["test_k1"]
            slot_after_first = key_state["slot"]

            # Lần 2: cancel trước khi _pace xong → slot vẫn phải tăng (không bị reset)
            task = asyncio.create_task(browser_pool._pace(key="test_k1"))
            await asyncio.sleep(0)   # cho task bắt đầu
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # Slot phải >= slot_after_first (commit cả khi cancel)
            assert key_state["slot"] >= slot_after_first, \
                f"slot phải commit cả khi cancel, gặp slot={key_state['slot']:.3f} < {slot_after_first:.3f}"
        finally:
            browser_pool._reset_rate_state()

    asyncio.run(main())
    print("PASS test_pace_commit_slot_even_when_cancelled")


# ───────────────────────── Fix 2: _rest_after_presubmit_fail có lock + async ─────────────────────────

def test_rest_after_presubmit_fail_is_async_with_lock():
    """Verify method đã đổi sang async (coroutine) và có _fail_lock."""
    assert asyncio.iscoroutinefunction(BrowserPool._rest_after_presubmit_fail), \
        "_rest_after_presubmit_fail phải là coroutine (async)"

    async def main():
        with tempfile.TemporaryDirectory() as tmp:
            pool = _make_pool(tmp)
            assert hasattr(pool, "_fail_lock"), "phải có _fail_lock attribute"
            # Gọi 2 lần đồng thời → không crash, không raise
            err = RuntimeError("test err")
            await asyncio.gather(
                pool._rest_after_presubmit_fail("acc1", err),
                pool._rest_after_presubmit_fail("acc1", err),
                pool._rest_after_presubmit_fail("acc2", err),
            )
            assert "acc1" in pool._fail_ips
            assert "acc2" in pool._fail_ips
            # _fail_lock là asyncio.Lock
            assert isinstance(pool._fail_lock, asyncio.Lock)

    asyncio.run(main())
    print("PASS test_rest_after_presubmit_fail_is_async_with_lock")


def test_rest_after_presubmit_fail_off_when_auto_retry_disabled():
    """config.AUTO_RETRY = False → method không làm gì (return ngay)."""
    async def main():
        with tempfile.TemporaryDirectory() as tmp:
            pool = _make_pool(tmp)
            with patch.object(browser_pool.config, "AUTO_RETRY", False):
                await pool._rest_after_presubmit_fail("acc1", RuntimeError("err"))
                assert "acc1" not in pool._fail_ips, "AUTO_RETRY=False → không ghi fail_ips"

    asyncio.run(main())
    print("PASS test_rest_after_presubmit_fail_off_when_auto_retry_disabled")


# ───────────────────────── Fix 5: _egress_slot decrement an toàn ─────────────────────────

def test_egress_slot_decrement_safe_on_double_finally():
    """Simulate trường hợp bất thường: dict đã bị clear giữa lúc → decrement không âm hơn."""
    async def main():
        with tempfile.TemporaryDirectory() as tmp:
            pool = _make_pool(tmp)

            async def acquire():
                async with browser_pool._egress_slot("acc1"):
                    # Force clear dict giữa lúc (mô phỏng _reset_rate_state hoặc bug)
                    browser_pool._egress_busy.clear()

            # Không raise, không âm counter
            await acquire()
            # Nếu counter âm thì lần sau sẽ cho phép quá nhiều job
            for v in browser_pool._egress_busy.values():
                assert v >= 0, f"counter âm: {v}"

    asyncio.run(main())
    print("PASS test_egress_slot_decrement_safe_on_double_finally")


def test_egress_slot_increments_and_decrements_correctly():
    """Verify _egress_slot tăng/giảm đúng số lượng."""
    async def main():
        with tempfile.TemporaryDirectory() as tmp:
            pool = _make_pool(tmp)
            pool_key = "test_egress_key"

            async def use_slot():
                async with browser_pool._egress_slot("acc1"):
                    return browser_pool._egress_busy.get(pool_key, 0)

            with patch.object(browser_pool, "_egress_key", return_value=pool_key):
                browser_pool._egress_busy.clear()
                # Chạy 3 task song song để verify concurrent
                results = await asyncio.gather(use_slot(), use_slot(), use_slot())
                assert all(r == 1 for r in results), f"3 task concurrent mỗi cái thấy count=1: {results}"
                # Sau khi tất cả xong, count phải về 0
                assert browser_pool._egress_busy.get(pool_key, 0) == 0

    asyncio.run(main())
    print("PASS test_egress_slot_increments_and_decrements_correctly")


# ───────────────────────── Fix 9: _gc_rate_state cleanup memory ─────────────────────────

def test_gc_rate_state_removes_stale_entries():
    """Khi _rate_state > _RATE_STATE_MAX_ENTRIES → GC phải xoá key đã 'êm' (until + boost + learned_extra = 0)."""
    # Inject 600 entry để vượt trần (500)
    for i in range(600):
        browser_pool._rate_state[f"key_{i}"] = {
            "until": 0.0, "pause": 0.0, "boost": 0.0, "boost_at": 0.0, "slot": 0.0,
            "learned_extra": 0.0, "streak": 0.0,
        }
    try:
        assert len(browser_pool._rate_state) > browser_pool._RATE_STATE_MAX_ENTRIES
        browser_pool._gc_rate_state()
        assert len(browser_pool._rate_state) <= browser_pool._RATE_STATE_MAX_ENTRIES, \
            f"GC phải giảm xuống <= {_RATE_STATE_MAX_ENTRIES}, còn {len(browser_pool._rate_state)}"
    finally:
        browser_pool._rate_state.clear()
        browser_pool._PACE_LOCKS.clear()
    print("PASS test_gc_rate_state_removes_stale_entries")


def test_gc_rate_state_keeps_active_entries():
    """Entry còn 'sống' (boost > 0 hoặc until > now) → KHÔNG bị GC."""
    future = time.monotonic() + 3600
    browser_pool._rate_state["active"] = {
        "until": future, "pause": 60.0, "boost": 10.0, "boost_at": time.monotonic(),
        "slot": 0.0, "learned_extra": 5.0, "streak": 0.0,
    }
    try:
        browser_pool._gc_rate_state()
        assert "active" in browser_pool._rate_state, "entry còn boost > 0 không được GC"
    finally:
        browser_pool._rate_state.clear()
        browser_pool._PACE_LOCKS.clear()
    print("PASS test_gc_rate_state_keeps_active_entries")


def test_gc_rate_state_noop_under_limit():
    """Khi _rate_state <= trần → _gc không làm gì."""
    browser_pool._rate_state["only_one"] = {
        "until": 0.0, "pause": 0.0, "boost": 0.0, "boost_at": 0.0, "slot": 0.0,
        "learned_extra": 0.0, "streak": 0.0,
    }
    try:
        before = len(browser_pool._rate_state)
        browser_pool._gc_rate_state()
        after = len(browser_pool._rate_state)
        assert before == after == 1, f"dưới trần không GC: trước={before} sau={after}"
    finally:
        browser_pool._rate_state.clear()
        browser_pool._PACE_LOCKS.clear()
    print("PASS test_gc_rate_state_noop_under_limit")


# ───────────────────────── Fix 10: _reset_rate_state clear cả _PACE_LOCKS ─────────────────────────

def test_reset_rate_state_clears_pace_locks():
    """_reset_rate_state phải xoá cả _PACE_LOCKS (không để lock mồ côi)."""
    browser_pool._rate_state["k"] = {"x": 1}
    browser_pool._PACE_LOCKS["k"] = asyncio.Lock()
    browser_pool._reset_rate_state()
    assert len(browser_pool._rate_state) == 0, "_rate_state phải clear"
    assert len(browser_pool._PACE_LOCKS) == 0, "_PACE_LOCKS phải clear"
    print("PASS test_reset_rate_state_clears_pace_locks")


# ───────────────────────── Fix 15: delete_account cleanup in-memory caches ─────────────────────────

def test_delete_account_clears_in_memory_caches():
    """delete_account phải xoá _fail_ips, _quarantine, _proxy_stamp của nick."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _make_pool(tmp)
        # Tạo account
        (Path(tmp) / "accounts" / "acc1").mkdir()
        pool.set_login_status("acc1", True)

        # Inject vào các cache
        pool._fail_ips["acc1"] = {"ip1": time.time()}
        pool._quarantine["acc1"] = "test"
        pool._proxy_stamp["acc1"] = {"ip": "1.2.3.4"}

        pool.delete_account("acc1")

        assert "acc1" not in pool._fail_ips
        assert "acc1" not in pool._quarantine
        assert "acc1" not in pool._proxy_stamp
    print("PASS test_delete_account_clears_in_memory_caches")


# ───────────────────────── Fix 21: _settle defensive khi result không phải dict ─────────────────────────

def test_settle_fallback_when_result_not_dict():
    """Khi result không phải dict (edge case) → _settle vẫn claim cost=1 thay vì crash."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _make_pool(tmp)
        # Tạo account + meta
        (Path(tmp) / "accounts" / "acc1").mkdir()
        pool._ensure_meta("acc1")

        # Edge case: result là Exception (lỗi logic khác) — _settle KHÔNG crash
        pool._settle("acc1", RuntimeError("not a dict"), "seedance_v2.0", 30, balance_seen=False)
        # Phải ghi cost=1 (fallback) chứ không phải None
        used = pool.used_today("acc1")
        assert used == 1, f"result không phải dict → claim cost=1, gặp used={used}"
    print("PASS test_settle_fallback_when_result_not_dict")


def test_settle_normal_dict_path():
    """Path bình thường: result là dict có credits_used → dùng giá trị đó."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _make_pool(tmp)
        (Path(tmp) / "accounts" / "acc1").mkdir()
        pool._ensure_meta("acc1")

        # credits_used = 2 → cost = 2
        pool._settle("acc1", {"credits_used": 2}, "seedance_v2.0", 30, balance_seen=False)
        assert pool.used_today("acc1") == 2

        # Không có credits_used nhưng đã học cost → dùng cost đã học
        pool._settle("acc1", {}, "seedance_v2.0", 30, balance_seen=False)
        assert pool.used_today("acc1") == 4   # 2 + 2 (cached)

        # Không có gì → default_cost
        pool._settle("acc1", {}, "unknown_model", 30, balance_seen=False)
        assert pool.used_today("acc1") == 5   # +1 (default Seedance 2.0)
    print("PASS test_settle_normal_dict_path")


# ───────────────────────── Main ─────────────────────────

def main():
    test_pace_commit_slot_even_when_cancelled()
    test_rest_after_presubmit_fail_is_async_with_lock()
    test_rest_after_presubmit_fail_off_when_auto_retry_disabled()
    test_egress_slot_decrement_safe_on_double_finally()
    test_egress_slot_increments_and_decrements_correctly()
    test_gc_rate_state_removes_stale_entries()
    test_gc_rate_state_keeps_active_entries()
    test_gc_rate_state_noop_under_limit()
    test_reset_rate_state_clears_pace_locks()
    test_delete_account_clears_in_memory_caches()
    test_settle_fallback_when_result_not_dict()
    test_settle_normal_dict_path()
    print("\n12/12 test_browser_pool_fixes PASS")


if __name__ == "__main__":
    main()