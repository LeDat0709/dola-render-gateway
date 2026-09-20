"""Nick bị đánh dấu "đã đăng xuất" (login_ok=0) mà cookies.json được ghi MỚI HƠN lúc đánh dấu (đăng nhập lại / nhập
cookie bằng đường không đi qua server: app desktop → import_cookies.py) phải được kiểm lại bằng passport HTTP,
nếu không pool bỏ qua nick login_ok=0 mãi mãi và cờ không bao giờ tự khỏi (log 20/09 12:14).
Chạy: .venv/bin/python test_relogin_reverify.py
"""
import asyncio
import os
import tempfile
import time
from pathlib import Path

from browser_pool import BrowserPool


def _pool(tmp: str) -> BrowserPool:
    return BrowserPool(accounts_dir=str(Path(tmp) / "accounts"), db_path=str(Path(tmp) / "pool.db"))


def _nick(pool: BrowserPool, tmp: str, name: str, *, ok, checked_ago: float, cookies_ago: float | None) -> None:
    d = Path(tmp) / "accounts" / name
    d.mkdir(parents=True)
    if cookies_ago is not None:
        f = d / "cookies.json"
        f.write_text("[]")
        t = time.time() - cookies_ago
        os.utime(f, (t, t))
    pool._ensure_meta(name)
    pool.set_login_status(name, ok)
    pool._conn.execute("UPDATE accounts_meta SET login_checked_at=? WHERE name=?", (time.time() - checked_ago, name))
    pool._conn.commit()


def test_only_relogged_dead_nicks_are_rechecked():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        _nick(pool, tmp, "relogged", ok=False, checked_ago=600, cookies_ago=60)    # cookie mới hơn cờ → kiểm lại
        _nick(pool, tmp, "still_dead", ok=False, checked_ago=60, cookies_ago=600)  # cookie cũ hơn cờ → giữ nguyên
        _nick(pool, tmp, "alive", ok=True, checked_ago=600, cookies_ago=60)        # sống → không đụng
        _nick(pool, tmp, "no_file", ok=False, checked_ago=600, cookies_ago=None)   # không có cookies.json → bỏ qua
        seen: list[str] = []

        async def fake_verify(name):
            seen.append(name)
            pool.set_login_status(name, True)
            return True

        pool.verify_account_http = fake_verify
        accts = asyncio.run(pool._reverify_relogged(pool.list_accounts()))
        assert seen == ["relogged"], f"chỉ nick vừa đăng nhập lại được kiểm, thực tế {seen}"
        by = {a["name"]: a["login_ok"] for a in accts}
        assert by["relogged"] == 1, "danh sách trả về phải phản ánh cờ mới (đọc lại sau khi kiểm)"
        assert by["still_dead"] == 0 and by["no_file"] == 0, "nick không có cookie mới giữ nguyên cờ chết"


def test_verify_error_keeps_flag_and_does_not_raise():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        _nick(pool, tmp, "relogged", ok=False, checked_ago=600, cookies_ago=60)

        async def boom(name):
            raise RuntimeError("proxy chết")

        pool.verify_account_http = boom
        accts = asyncio.run(pool._reverify_relogged(pool.list_accounts()))
        assert accts[0]["login_ok"] == 0, "kiểm lỗi → giữ cờ cũ, không làm job nổ"


if __name__ == "__main__":
    test_only_relogged_dead_nicks_are_rechecked()
    test_verify_error_keeps_flag_and_does_not_raise()
    print("OK: relogin reverify")
