"""Khóa admin: chống dò (khoá tạm theo IP) + so khớp không lộ thời gian.

Vì sao cần: README hướng dẫn chạy VPS bằng `uvicorn --host 0.0.0.0`, mà /api/admin/login trước đây cho thử
VÔ HẠN lần → dò ra DOLA_ADMIN_KEY là gọi được /api/admin/accounts/export = lấy TOÀN BỘ cookie nick.
"""
import asyncio
import tempfile
from pathlib import Path

import config

config.ADMIN_KEY = "secret-key-123"   # phải đặt TRƯỚC khi import server (module đọc lúc nạp)

import server                                    # noqa: E402
from fastapi import HTTPException                # noqa: E402
from store import TaskStore                      # noqa: E402


class _Req:
    """Giả Request: _admin_auth chỉ cần request.client.host."""

    def __init__(self, ip):
        self.client = type("C", (), {"host": ip})()


def _status(fn):
    """None = qua được; số = mã lỗi HTTP ném ra."""
    try:
        fn()
        return None
    except HTTPException as exc:
        return exc.status_code


def test_wrong_key_locks_out_after_threshold():
    server._admin_fails.clear()
    ip = _Req("9.9.9.9")
    for _ in range(server.ADMIN_MAX_FAILS):
        assert _status(lambda: server._admin_auth("sai", ip)) == 401
    # Quá ngưỡng: khoá tạm — kẻ dò không thử thêm được nữa
    assert _status(lambda: server._admin_auth("sai", ip)) == 429
    # Đang khoá thì khóa ĐÚNG cũng phải chờ (nếu không, vẫn dò được)
    assert _status(lambda: server._admin_auth("secret-key-123", ip)) == 429


def test_lockout_is_per_ip_and_resets_on_success():
    server._admin_fails.clear()
    assert _status(lambda: server._admin_auth("sai", _Req("9.9.9.9"))) == 401
    # Máy khác không bị vạ lây
    assert _status(lambda: server._admin_auth("secret-key-123", _Req("2.2.2.2"))) is None
    # Nhập đúng thì xoá bộ đếm (gõ nhầm vài lần rồi đúng không bị phạt tích luỹ)
    assert _status(lambda: server._admin_auth("secret-key-123", _Req("9.9.9.9"))) is None
    assert "9.9.9.9" not in server._admin_fails


def test_key_compare_and_open_mode():
    server._admin_fails.clear()
    assert server._admin_key_ok("secret-key-123") is True
    assert server._admin_key_ok("secret-key-124") is False
    assert server._admin_key_ok(None) is False
    assert server._admin_key_ok("") is False
    saved = config.ADMIN_KEY
    try:   # không đặt khóa (chạy localhost) → mở như cũ, không khoá ai
        config.ADMIN_KEY = ""
        assert _status(lambda: server._admin_auth(None, _Req("4.4.4.4"))) is None
    finally:
        config.ADMIN_KEY = saved


def _call(coro):
    """Gọi thẳng route async: None = qua được; số = mã lỗi HTTP."""
    try:
        asyncio.run(coro)
        return None
    except HTTPException as exc:
        return exc.status_code


def test_public_video_routes_count_admin_guesses_toward_lockout(monkeypatch):
    """/v1/videos… nhận X-Admin-Key: dò khóa ở đây trước đây KHÔNG bị khoá → vòng qua được chống dò của /api/admin."""
    monkeypatch.setattr(config, "ADMIN_KEY", "secret-key-123")
    monkeypatch.setattr(config, "API_KEYS", ["bearer-only"])   # sai khóa admin → rơi về bearer → 401, không tạo job
    server._admin_fails.clear()
    ip = _Req("7.7.7.7")
    for _ in range(server.ADMIN_MAX_FAILS):
        assert _call(server.get_video("x", authorization=None, x_admin_key="sai", request=ip)) == 401
    # Quá ngưỡng: khóa ĐÚNG cũng bị chặn, và route tạo video dùng chung bộ đếm
    assert _call(server.get_video("x", authorization=None, x_admin_key="secret-key-123", request=ip)) == 429
    req = server.VideoGenRequest(prompt="p")
    assert _call(server.create_video(req, authorization=None, x_admin_key="sai", request=ip)) == 429


def test_public_video_routes_without_admin_header_never_lock_out(monkeypatch):
    """Khách API chỉ dùng bearer (và broker gọi thẳng hàm, tham số còn là Header(...)) không được bị khoá oan."""
    monkeypatch.setattr(config, "ADMIN_KEY", "secret-key-123")
    monkeypatch.setattr(config, "API_KEYS", ["bearer-only"])
    monkeypatch.setattr(server, "store", TaskStore(str(Path(tempfile.mkdtemp()) / "t.db")))
    server._admin_fails.clear()
    for _ in range(server.ADMIN_MAX_FAILS * 2):
        assert _call(server.get_video("x", authorization=None, x_admin_key=None, request=_Req("6.6.6.6"))) == 401
        assert _call(server.get_video("x", "Bearer sai")) == 401   # kiểu broker.py: gọi thẳng, không request
    assert not server._admin_fails


if __name__ == "__main__":
    test_wrong_key_locks_out_after_threshold()
    test_lockout_is_per_ip_and_resets_on_success()
    test_key_compare_and_open_mode()
    print("OK")
