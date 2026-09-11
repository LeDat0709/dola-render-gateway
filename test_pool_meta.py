"""Self-check: ghi trạng thái cho nick CHƯA có dòng meta không được rơi vào khoảng không.

Máy VN đẩy cookie nick mới lên VPS qua /api/admin/accounts/import-cookie → server gọi
set_login_status ngay sau khi tạo thư mục profile, trước khi ai đọc pool.accounts.
Chạy: .venv/bin/python test_pool_meta.py
"""
import asyncio
import tempfile
from pathlib import Path

import browser_pool
from browser_pool import BrowserPool
from video_worker_ui import AccountLimitedError, TransientDolaError


def _pool(tmp: str) -> BrowserPool:
    return BrowserPool(accounts_dir=str(Path(tmp) / "accounts"), db_path=str(Path(tmp) / "pool.db"))


def test_status_for_unknown_nick_is_kept():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        pool.set_login_status("moi", True)
        pool.set_email("moi", "moi@example.com")
        meta = pool._meta("moi")
        assert meta is not None and meta["login_ok"] == 1, "login_ok của nick mới bị mất"
        assert meta["email"] == "moi@example.com", "email của nick mới bị mất"


def test_new_profile_dir_shows_up_without_restart():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        assert pool.accounts == []
        (Path(tmp) / "accounts" / "nick2").mkdir(parents=True)
        assert pool.accounts == ["nick2"], "nick tạo sau khi pool khởi động phải hiện ngay"


def _fake_gen(*outcomes):
    calls = []
    async def gen(account, *a, **kw):
        calls.append(account)
        r = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(r, Exception):
            raise r
        return r
    gen.calls = calls
    return gen


def test_timeout_in_retry_does_not_rotate():
    """Log 11/9 11:09: lỗi tạm thời → thử lại → quá giờ → 'xoay nick' = gửi lần 2 trong khi Dola vẫn dựng video cũ."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        for n in ("n1", "n2"):
            (Path(tmp) / "accounts" / n).mkdir(parents=True)
        gen = _fake_gen(TransientDolaError("エラーが発生しました"), TimeoutError("Hết 237s chưa ra video"))
        browser_pool.generate_video = gen
        try:
            asyncio.run(pool.generate_video("p", "9:16", 10, account="n1"))
            assert False, "phải ném TimeoutError"
        except TimeoutError:
            pass
        assert gen.calls == ["n1", "n1"], gen.calls     # thử lại 1 lần trên chính nick, KHÔNG sang n2
        assert pool.used_today("n1") == 1                # video vẫn đang dựng trên Dola → tính lượt


def test_pinned_nick_reports_real_reason():
    """Ảnh 11/9 15:57: nick 'sẵn sàng' mà job báo 'Hết nick chạy được' — lý do thật bị bọc mất."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        (Path(tmp) / "accounts" / "n1").mkdir(parents=True)
        browser_pool.generate_video = _fake_gen(AccountLimitedError("Hết lượt tạo video hôm nay. Dola: 上限"))
        try:
            asyncio.run(pool.generate_video("p", "9:16", 10, account="n1"))
            assert False, "phải ném lỗi"
        except AccountLimitedError as e:
            assert "Hết lượt" in str(e)
        except RuntimeError as e:
            assert False, f"lý do thật bị bọc thành: {e}"


if __name__ == "__main__":
    test_status_for_unknown_nick_is_kept()
    test_new_profile_dir_shows_up_without_restart()
    test_timeout_in_retry_does_not_rotate()
    test_pinned_nick_reports_real_reason()
    print("OK")
