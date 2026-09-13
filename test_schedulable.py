"""Self-check cho _schedulable: account đã logout (login_ok==0) phải bị loại,
login_ok None (chưa rõ) và 1 (OK) vẫn được chạy nếu các điều kiện khác đạt."""
import tempfile, os
from browser_pool import BrowserPool, DAILY_LIMIT


def _acc(**over):
    base = dict(scheduling=True, cooling=False, rate_limited=False, quota_blocked=False,
                used_today=0, login_ok=1, credit_balance=None)
    base.update(over)
    return base


def demo():
    db = tempfile.mktemp(suffix=".db")
    pool = BrowserPool(accounts_dir="/nonexistent", db_path=db)
    try:
        assert pool._schedulable(_acc(login_ok=1)) is True, "login_ok=1 phải chạy"
        assert pool._schedulable(_acc(login_ok=None)) is True, "login_ok=None (chưa rõ) phải chạy"
        assert pool._schedulable(_acc(login_ok=0)) is False, "login_ok=0 (logout) phải bị loại"
        # gate cũ vẫn đúng
        assert pool._schedulable(_acc(used_today=DAILY_LIMIT)) is False, "hết quota ngày phải loại"
        assert pool._schedulable(_acc(credit_balance=1)) is False, "credit<2 phải loại"
        assert pool._schedulable(_acc(rate_limited=True)) is False, "rate limited phải loại"
        print("OK: tất cả assert pass")
    finally:
        os.unlink(db)


if __name__ == "__main__":
    demo()
