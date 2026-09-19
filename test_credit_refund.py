"""Test CreditLedger: spent/refund tracking với SQLite."""
import sqlite3
from pathlib import Path

import pytest

from credit_refund import CreditLedger


@pytest.fixture
def ledger(tmp_path):
    db_path = tmp_path / "test.db"
    CreditLedger.reset_for_tests()
    return CreditLedger(db_path)


def test_record_spent_creates_row(ledger: CreditLedger):
    """record_spent phải tạo row với kind='spent'."""
    ledger_id = ledger.record_spent("grant-1", "job-1", amount=1, reason="submit")
    assert ledger_id > 0
    conn = sqlite3.connect(str(ledger._db_path))
    cur = conn.execute(
        "SELECT kind, amount, reason, job_id FROM credit_ledger WHERE id=?",
        (ledger_id,),
    )
    row = cur.fetchone()
    conn.close()
    assert row == ("spent", 1, "submit", "job-1")


def test_record_refund_creates_row(ledger: CreditLedger):
    """record_refund cho failure_code refundable -> tao row 'refund'."""
    ledger.record_spent("grant-1", "job-1", amount=1)
    refund_id = ledger.record_refund(
        "grant-1", "job-1", "submit_4xx_no_conv_id"
    )
    assert refund_id is not None
    assert refund_id > 0
    conn = sqlite3.connect(str(ledger._db_path))
    cur = conn.execute(
        "SELECT kind, amount, failure_code FROM credit_ledger WHERE id=?",
        (refund_id,),
    )
    row = cur.fetchone()
    conn.close()
    assert row == ("refund", 1, "submit_4xx_no_conv_id")


def test_refund_blocked_when_not_refundable(ledger: CreditLedger):
    """Failure_code khong refundable -> refund_id = None."""
    ledger.record_spent("grant-1", "job-1", amount=1)
    refund_id = ledger.record_refund("grant-1", "job-1", "captcha_blocked")
    assert refund_id is None


def test_refund_blocked_when_already_refunded(ledger: CreditLedger):
    """Da refund roi -> refund lan 2 = None."""
    ledger.record_spent("grant-1", "job-1", amount=1)
    r1 = ledger.record_refund("grant-1", "job-1", "submit_4xx_no_conv_id")
    r2 = ledger.record_refund("grant-1", "job-1", "submit_4xx_no_conv_id")
    assert r1 is not None
    assert r2 is None


def test_refund_blocked_without_spent(ledger: CreditLedger):
    """Refund khi chua spent -> None (chong gian lan)."""
    r = ledger.record_refund("grant-1", "job-99", "submit_4xx_no_conv_id")
    assert r is None


def test_balance_subtracts_refunds(ledger: CreditLedger):
    """Balance = spent - refund."""
    ledger.record_spent("g", "j1")
    ledger.record_spent("g", "j2")
    ledger.record_spent("g", "j3")
    assert ledger.balance("g") == 3
    ledger.record_refund("g", "j2", "submit_4xx_no_conv_id")
    assert ledger.balance("g") == 2
    ledger.record_refund("g", "j3", "rate_limit_retry_exhausted")
    assert ledger.balance("g") == 1


def test_balance_per_grant(ledger: CreditLedger):
    """Balance phai tach theo grant_id."""
    ledger.record_spent("g1", "j1")
    ledger.record_spent("g1", "j2")
    ledger.record_spent("g2", "j3")
    assert ledger.balance("g1") == 2
    assert ledger.balance("g2") == 1


def test_balance_unknown_grant_returns_zero(ledger: CreditLedger):
    """Grant khong ton tai -> balance = 0."""
    assert ledger.balance("nonexistent") == 0


def test_summary_aggregates(ledger: CreditLedger):
    """summary() tra tong spent/refund/net/grants/jobs."""
    ledger.record_spent("g1", "j1")
    ledger.record_spent("g1", "j2")
    ledger.record_spent("g2", "j3")
    ledger.record_refund("g1", "j1", "submit_4xx_no_conv_id")
    s = ledger.summary()
    assert s["total_rows"] == 4
    assert s["total_spent"] == 3
    assert s["total_refunded"] == 1
    assert s["net_spent"] == 2
    assert s["grants"] == 2
    assert s["jobs"] == 3


def test_should_refund_classification():
    """should_refund cho tung failure_code."""
    assert CreditLedger.should_refund("submit_4xx_no_conv_id") is True
    assert CreditLedger.should_refund("submit_rate_limited") is True
    assert CreditLedger.should_refund("a_bogus_signature_failed") is True
    assert CreditLedger.should_refund("proxy_unreachable") is True
    assert CreditLedger.should_refund("chrome_open_failed") is True
    assert CreditLedger.should_refund("captcha_blocked") is False
    assert CreditLedger.should_refund("video_already_built") is False
    assert CreditLedger.should_refund("user_cancelled") is False
    assert CreditLedger.should_refund("unknown_failure") is False
    assert CreditLedger.should_refund("") is False


def test_register_refundable_runtime(ledger: CreditLedger):
    """register_refundable cho phep them failure_code luc chay."""
    CreditLedger.register_refundable("custom_new_failure")
    assert CreditLedger.should_refund("custom_new_failure") is True
    ledger.record_spent("g", "j")
    r = ledger.record_refund("g", "j", "custom_new_failure")
    assert r is not None


def test_register_non_refundable_runtime(ledger: CreditLedger):
    """register_non_refundable cho phep KHOA failure_code."""
    # Cleanup global state từ test khác trước khi test
    from credit_refund import _REFUNDABLE_FAILURES, _NON_REFUNDABLE_FAILURES
    _REFUNDABLE_FAILURES.add("submit_4xx_no_conv_id")
    _NON_REFUNDABLE_FAILURES.discard("submit_4xx_no_conv_id")
    try:
        CreditLedger.register_non_refundable("submit_4xx_no_conv_id")
        assert CreditLedger.should_refund("submit_4xx_no_conv_id") is False
        # Cleanup: trả lại trạng thái refundable
        _NON_REFUNDABLE_FAILURES.discard("submit_4xx_no_conv_id")
    finally:
        _NON_REFUNDABLE_FAILURES.discard("submit_4xx_no_conv_id")


def test_list_refunds_returns_dicts(ledger: CreditLedger):
    """list_refunds() tra danh sach dict."""
    # Cleanup global state từ test khác
    from credit_refund import _NON_REFUNDABLE_FAILURES
    _NON_REFUNDABLE_FAILURES.discard("submit_4xx_no_conv_id")
    _NON_REFUNDABLE_FAILURES.discard("rate_limit_retry_exhausted")
    ledger.record_spent("g1", "j1")
    ledger.record_refund("g1", "j1", "submit_4xx_no_conv_id")
    ledger.record_spent("g1", "j2")
    ledger.record_refund("g1", "j2", "rate_limit_retry_exhausted")
    refunds = ledger.list_refunds()
    assert len(refunds) == 2
    assert refunds[0]["kind"] == "refund"
    assert refunds[0]["failure_code"] in (
        "submit_4xx_no_conv_id",
        "rate_limit_retry_exhausted",
    )


def test_singleton_persistence(tmp_path):
    """Singleton phai persist giua cac lan goi."""
    db = tmp_path / "singleton.db"
    CreditLedger.reset_for_tests()
    l1 = CreditLedger.instance(db)
    l1.record_spent("g", "j")
    l2 = CreditLedger.instance(db)
    assert l1 is l2
    assert l2.balance("g") == 1
    CreditLedger.reset_for_tests()


def test_chrome_refund_each_attempt_refunds_once(ledger: CreditLedger):
    """Chrome engine (job_id=""): mỗi lần gọi pool (reason khác nhau) hoàn riêng; cùng lần gọi thì chỉ 1 lần.
    Trước đây khoá chống trùng là (nick, mã lỗi) → nick chỉ được hoàn ĐÚNG 1 lần trong đời cho mỗi mã lỗi."""
    code = "submit_4xx_no_conv_id"
    first = ledger.record_refund("nick-a", "", code, reason="chrome:att-1")
    assert first
    assert ledger.record_refund("nick-a", "", code, reason="chrome:att-1") is None   # cùng lần gọi → không hoàn 2 lần
    assert ledger.record_refund("nick-a", "", code, reason="chrome:att-2")           # job sau, cùng lỗi → vẫn hoàn


def test_chrome_refund_without_reason_keeps_old_dedup(ledger: CreditLedger):
    """Nơi gọi cũ không truyền reason: giữ hành vi cũ (chống trùng theo nick + mã lỗi)."""
    code = "submit_4xx_no_conv_id"
    assert ledger.record_refund("nick-b", "", code)
    assert ledger.record_refund("nick-b", "", code) is None
