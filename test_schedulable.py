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
        # Lọc 2 tầng: _schedulable lọc THÔ (còn >=1 credit vẫn xếp lịch — video 1 lượt Seedance 2.0 chạy được),
        # _credit_short mới lọc TINH theo từng video (30s 2.5 cần 2 → nick cb=1 bị loại đúng lúc chọn, không mở Chrome).
        assert pool._schedulable(_acc(credit_balance=0)) is False, "hết credit (cb=0) phải loại"
        assert pool._schedulable(_acc(credit_balance=1)) is True, "còn >=1 credit vẫn xếp lịch (credit_short lọc theo video)"
        assert pool._credit_short(_acc(credit_balance=1, name="x"), 2, 30, "seedance-2.5"), "cb=1 mà video cần 2 → credit_short chặn"
        assert pool._schedulable(_acc(rate_limited=True)) is False, "rate limited phải loại"
        print("OK: tất cả assert pass")
    finally:
        os.unlink(db)


if __name__ == "__main__":
    demo()
