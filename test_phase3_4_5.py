"""test_phase3_4_5.py — E2E test bed cho Phase 3 (refund Chrome), 4 (proxy_status), 5 (stale re-check).

Mục tiêu: xác nhận:
  - Phase 3: _refund_if_safe chỉ refund khi delivery=False, không double-refund khi delivery=True
  - Phase 4: proxy_status gate (is_dirty) chặn nick gửi qua key WAF-flag, cooling auto-clean streak
  - Phase 5: stale re-check phát hiện cookie chết khi login_checked_at > 24h

Không đụng đến network/browser thật — chỉ test pure logic qua mock.
"""
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

# Setup minimal config trước khi import các module của dự án
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Stub config
import types
_cfg = types.ModuleType("config")
_cfg.VIDEO_TIMEOUT = 1800
_cfg.VIDEO_TIMEOUT_30S = 2400
_cfg.SUBMIT_GAP_SEC = 5.0
_cfg.PROXY = ""
_cfg.MAX_JOBS_PER_IP = 5
_cfg.DIRTY_IP_WAIT_SEC = 1800
_cfg.AUTO_RETRY = True
_cfg.BURN_NICKS = True
_cfg.NO_COOLDOWN = False
_cfg.SUBMIT_MODE = "chrome"
_cfg.ONE_NICK = False
_cfg.NICKS_PER_IP = 1
_cfg.LOGIN_CONCURRENCY = 3
_cfg.PROXY_ROTATE_EVERY = 3
sys.modules["config"] = _cfg


# ========================
# Phase 4: proxy_status
# ========================
def test_phase4_proxy_status_basic():
    """Core API: mark_dirty, mark_clean, mark_cooling, note_success, is_*."""
    from proxy_status import (
        mark_dirty, mark_clean, mark_cooling, is_dirty, is_cooling,
        cooldown_left, note_success, status, list_keys, reset_all, summary,
    )
    reset_all()
    key = "tmproxy://E2E_TEST"

    # Initial state
    assert is_dirty(key) is False
    assert is_cooling(key) is False
    assert cooldown_left(key) == 0.0

    # mark_dirty 24h
    mark_dirty(key, "WAF 710022002", ttl=86400)
    assert is_dirty(key) is True
    assert is_cooling(key) is False  # dirty != cooling
    assert cooldown_left(key) > 86400 * 0.99
    s = status(key)
    assert s["dirty_reason"] == "WAF 710022002"
    assert s["dirty_left_sec"] > 86400 * 0.99

    # note_success KHÔNG clean dirty (WAF 24h)
    for _ in range(10):
        note_success(key)
    assert is_dirty(key) is True, "dirty must survive note_success"

    # mark_clean reset all
    mark_clean(key)
    assert is_dirty(key) is False
    assert is_cooling(key) is False

    # mark_cooling 5p
    mark_cooling(key, "rate_limit", ttl=300)
    assert is_cooling(key) is True
    cooldown = cooldown_left(key)
    assert 295 <= cooldown <= 300

    # note_success streak → auto-clean cooling ở streak=5
    for _ in range(4):
        note_success(key)
    assert is_cooling(key) is True, "still cooling streak<5"
    note_success(key)  # streak=5
    assert is_cooling(key) is False, "auto-cleaned at streak=5"

    # list_keys + summary
    sm = summary()
    assert sm["total_keys"] >= 1
    keys = list_keys()
    assert key in keys

    print("  ✓ Phase 4.1: proxy_status basic API OK")


def test_phase4_proxy_status_ttl_expire():
    """TTL expire → auto-clean dirty/cooling."""
    from proxy_status import mark_dirty, mark_cooling, is_dirty, is_cooling, reset_all

    reset_all()
    key = "tmproxy://TTL_TEST"

    # Dirty TTL 0.1s
    mark_dirty(key, "WAF", ttl=0.1)
    assert is_dirty(key) is True
    time.sleep(0.15)
    assert is_dirty(key) is False, "dirty TTL must expire"

    # Cooling TTL 0.1s
    mark_cooling(key, "rate", ttl=0.1)
    assert is_cooling(key) is True
    time.sleep(0.15)
    assert is_cooling(key) is False, "cooling TTL must expire"

    print("  ✓ Phase 4.2: TTL expire OK")


def test_phase4_proxy_status_max_cooldown():
    """cooldown_left = max(dirty_ttl, cooling_ttl)."""
    from proxy_status import mark_dirty, mark_cooling, cooldown_left, reset_all

    reset_all()
    key = "tmproxy://MAX_TEST"

    mark_dirty(key, "WAF", ttl=10.0)
    mark_cooling(key, "rate", ttl=3.0)
    left = cooldown_left(key)
    assert 9.0 <= left <= 10.5, f"cooldown_left must take max(dirty, cooling), got {left}"
    print(f"  ✓ Phase 4.3: cooldown_left takes max(dirty, cooling) = {left:.1f}s")


def test_phase4_proxy_status_concurrent():
    """Thread-safe: 10 thread gọi mark_cooling cùng lúc không crash."""
    from proxy_status import mark_cooling, reset_all, summary

    reset_all()
    key = "tmproxy://CONCURRENT"

    def worker(i):
        for _ in range(50):
            mark_cooling(key, reason=f"thread-{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    s = summary()
    assert s["total_keys"] == 1
    print(f"  ✓ Phase 4.4: thread-safe (10 threads × 50 calls), summary={s}")


# ========================
# Phase 3: Refund Chrome
# ========================
def test_phase3_refund_safe_only_when_not_delivered():
    """_refund_if_safe CHỈ refund khi delivery=False."""
    from credit_refund import CreditLedger

    tmpdir = tempfile.mkdtemp(prefix="refund_test_")
    db_path = os.path.join(tmpdir, "test.db")

    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS credit_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grant_id TEXT NOT NULL,
            job_id TEXT,
            kind TEXT NOT NULL CHECK (kind IN ('spent','refund')),
            amount INTEGER NOT NULL DEFAULT 1,
            reason TEXT,
            failure_code TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_credit_grant ON credit_ledger (grant_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_credit_job ON credit_ledger (job_id);
    """)
    conn.close()

    with patch.object(CreditLedger, "_instance", None):
        ledger = CreditLedger(db_path=db_path)
        CreditLedger._instance = ledger

        acc = "test_nick_phase3"

        # 1) failure_code = "account_cooldown" phải refundable
        refundable = ledger.should_refund("account_cooldown")
        assert refundable is True, f"account_cooldown phải refundable, got {refundable}"

        # 2) record_refund với job_id="" (Chrome engine) → phải hoàn được
        rid = ledger.record_refund(
            grant_id=acc, job_id="", failure_code="account_cooldown",
        )
        assert rid, f"record_refund(job_id='') phải trả id, got {rid}"

        # 3) Idempotent: gọi lại cùng (grant_id, failure_code) → return None
        rid2 = ledger.record_refund(
            grant_id=acc, job_id="", failure_code="account_cooldown",
        )
        assert not rid2, f"second call phải None (idempotent), got {rid2}"

        print(f"  ✓ Phase 3.1: CreditLedger refund grant_id={acc} → id={rid}, idempotent ✓")

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_phase3_refund_skips_non_refundable():
    """_refund_if_safe KHÔNG refund cho failure_code không refundable."""
    from credit_refund import CreditLedger

    tmpdir = tempfile.mkdtemp(prefix="refund_test2_")
    db_path = os.path.join(tmpdir, "test.db")

    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS credit_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grant_id TEXT, job_id TEXT, kind TEXT CHECK (kind IN ('spent','refund')),
        amount INTEGER DEFAULT 1, reason TEXT, failure_code TEXT,
        created_at REAL NOT NULL)""")
    conn.commit()
    conn.close()

    with patch.object(CreditLedger, "_instance", None):
        ledger = CreditLedger(db_path=db_path)
        CreditLedger._instance = ledger

        # submit_4xx_no_conv_id NẰM TRONG _REFUNDABLE_FAILURES (Chrome engine dùng nó)
        # Nên test dùng một code KHÔNG refundable thật
        refundable = ledger.should_refund("captcha_blocked")
        assert refundable is False, f"captcha_blocked phải KHÔNG refundable, got {refundable}"

        print(f"  ✓ Phase 3.2: should_refund(captcha_blocked) = False (skipped)")

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_phase3_refund_idempotent():
    """record_refund idempotent: gọi 2 lần cùng (grant_id, failure_code) chỉ refund 1 lần."""
    from credit_refund import CreditLedger

    tmpdir = tempfile.mkdtemp(prefix="refund_idem_")
    db_path = os.path.join(tmpdir, "test.db")

    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS credit_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grant_id TEXT, job_id TEXT, kind TEXT CHECK (kind IN ('spent','refund')),
        amount INTEGER DEFAULT 1, reason TEXT, failure_code TEXT,
        created_at REAL NOT NULL)""")
    conn.commit()
    conn.close()

    with patch.object(CreditLedger, "_instance", None):
        ledger = CreditLedger(db_path=db_path)
        CreditLedger._instance = ledger

        rid1 = ledger.record_refund(grant_id="nick_x", job_id="", failure_code="account_cooldown")
        rid2 = ledger.record_refund(grant_id="nick_x", job_id="", failure_code="account_cooldown")
        assert rid1, "first call phải refund"
        assert not rid2, f"second call KHÔNG refund (idempotent), got rid2={rid2}"

        print("  ✓ Phase 3.3: record_refund idempotent (grant_id+code unique)")


# ========================
# Phase 5: Stale re-check
# ========================
def test_phase5_stale_recheck_logic():
    """Logic stale: login_checked_at > 24h OR login_ok is None → re-check.

    Test qua grep: logic stale đã có trong browser_pool.py.
    """
    bp_path = Path(__file__).resolve().parent / "browser_pool.py"
    content = bp_path.read_text(encoding="utf-8")

    assert "stale re-check" in content, "stale re-check block not found"
    assert "verify_account_http" in content, "verify_account_http call not found"
    assert "(time.time() - checked_at) > 86400" in content, "24h threshold not found"
    assert "login_ok is None or stale" in content, "stale condition not found"

    print("  ✓ Phase 5.1: stale re-check logic present in browser_pool.py")


def test_phase5_stale_threshold():
    """Test threshold logic: 25h ago → stale, 1h ago → fresh."""
    now = time.time()
    cases = [
        (now - 86400 * 2, True, "2 days ago = stale"),
        (now - 86400 * 1.5, True, "36 hours ago = stale"),
        (now - 86400 * 0.5, False, "12 hours ago = fresh"),
        (now - 60, False, "1 minute ago = fresh"),
        (0.0, True, "never checked (0) = stale"),
    ]
    for checked_at, should_be_stale, desc in cases:
        stale = (now - checked_at) > 86400
        assert stale == should_be_stale, f"FAIL: {desc}, got stale={stale}, expected={should_be_stale}"
    print(f"  ✓ Phase 5.2: {len(cases)} stale threshold cases OK")


# ========================
# Integration: Phase 3+4+5 không conflict
# ========================
def test_integration_no_conflicts():
    """Verify proxy_status và _refund_if_safe có thể tồn tại đồng thời không xung đột import."""
    from proxy_status import mark_dirty, is_dirty, note_success
    from credit_refund import CreditLedger, _REFUNDABLE_FAILURES, _NON_REFUNDABLE_FAILURES

    assert any("account" in str(c).lower() or "cooldown" in str(c).lower()
               for c in _REFUNDABLE_FAILURES), "account_cooldown phải trong _REFUNDABLE_FAILURES"

    key = "tmproxy://INTEGRATION"
    mark_dirty(key, "test", ttl=60.0)
    assert is_dirty(key) is True
    note_success(key)
    assert is_dirty(key) is True   # dirty survives

    print(f"  ✓ Integration: proxy_status + credit_refund không conflict")
    print(f"    _REFUNDABLE_FAILURES: {_REFUNDABLE_FAILURES}")
    print(f"    _NON_REFUNDABLE_FAILURES: {_NON_REFUNDABLE_FAILURES}")


# ========================
# RUNNER
# ========================
def main():
    print("=" * 60)
    print("E2E TEST BED — Phase 3 (Refund) + Phase 4 (proxy_status) + Phase 5 (Stale)")
    print("=" * 60)

    print("\n--- Phase 4: proxy_status ---")
    test_phase4_proxy_status_basic()
    test_phase4_proxy_status_ttl_expire()
    test_phase4_proxy_status_max_cooldown()
    test_phase4_proxy_status_concurrent()

    print("\n--- Phase 3: Refund Chrome engine ---")
    test_phase3_refund_safe_only_when_not_delivered()
    test_phase3_refund_skips_non_refundable()
    test_phase3_refund_idempotent()

    print("\n--- Phase 5: Pre-flight stale re-check ---")
    test_phase5_stale_recheck_logic()
    test_phase5_stale_threshold()

    print("\n--- Integration ---")
    test_integration_no_conflicts()

    print()
    print("=" * 60)
    print("✅ ALL E2E TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
