"""msToken thật: persist_dola_cookies không được làm MẤT msToken đang có, và wait_for_mstoken chỉ đọc cookie trang ghi.

Nguồn lỗi (19/9): cookies.json chỉ là ảnh chụp context.cookies() lúc import/đăng nhập — chụp trước khi JS Dola kịp ghi
msToken → 7/10 nick thiếu msToken, gửi bằng msToken giả, dễ 710022002. Lần chụp sau (import lại, đăng nhập lại) mà
trang chưa ghi kịp thì còn XOÁ luôn msToken đã có.

Chạy: .venv/bin/python -m pytest -q test_mstoken_refresh.py
"""
import asyncio
import json

import pytest

import browser
import config


@pytest.fixture
def acc_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ACCOUNTS_DIR", tmp_path)
    (tmp_path / "nick").mkdir()
    return tmp_path / "nick"


def _saved(acc_dir):
    data = json.loads((acc_dir / "cookies.json").read_text(encoding="utf-8"))
    return {c["name"]: c["value"] for c in data}


def test_persist_keeps_existing_mstoken_when_snapshot_lacks_it(acc_dir):
    browser.persist_dola_cookies("nick", [{"name": "sessionid", "value": "s1"}, {"name": "msToken", "value": "TOK"}])
    browser.persist_dola_cookies("nick", [{"name": "sessionid", "value": "s2"}])   # trang chưa kịp ghi msToken
    saved = _saved(acc_dir)
    assert saved["sessionid"] == "s2" and saved["msToken"] == "TOK"


def test_persist_keeps_mstoken_from_dict_format_file(acc_dir):
    # cookies.json dạng dict {name: value} (nhập tay / bản cũ) cũng phải giữ được
    (acc_dir / "cookies.json").write_text(json.dumps({"sessionid": "s1", "msToken": "OLD"}), encoding="utf-8")
    browser.persist_dola_cookies("nick", [{"name": "sessionid", "value": "s2"}])
    assert _saved(acc_dir)["msToken"] == "OLD"


def test_persist_new_mstoken_wins_without_duplicate(acc_dir):
    browser.persist_dola_cookies("nick", [{"name": "msToken", "value": "OLD"}])
    browser.persist_dola_cookies("nick", [{"name": "msToken", "value": "NEW"}])
    data = json.loads((acc_dir / "cookies.json").read_text(encoding="utf-8"))
    assert [c["value"] for c in data if c["name"] == "msToken"] == ["NEW"]


def test_persist_never_invents_mstoken(acc_dir):
    browser.persist_dola_cookies("nick", [{"name": "sessionid", "value": "s1"}])
    assert "msToken" not in _saved(acc_dir)


def test_persist_does_not_mutate_caller_list(acc_dir):
    browser.persist_dola_cookies("nick", [{"name": "msToken", "value": "TOK"}])
    snap = [{"name": "sessionid", "value": "s2"}]
    browser.persist_dola_cookies("nick", snap)
    assert snap == [{"name": "sessionid", "value": "s2"}]


class _FakeCtx:
    """Giả BrowserContext: msToken xuất hiện sau `after` lần đọc (như JS trang ghi trễ)."""

    def __init__(self, after):
        self.after, self.reads = after, 0

    async def cookies(self, url=None):
        self.reads += 1
        base = [{"name": "sessionid", "value": "s"}]
        if self.after is not None and self.reads > self.after:
            return base + [{"name": "msToken", "value": "PAGE"}]
        return base


def test_wait_for_mstoken_returns_when_page_writes_it():
    ctx = _FakeCtx(after=2)
    assert asyncio.run(browser.wait_for_mstoken(ctx, seconds=2.0, poll=0.01)) is True
    assert ctx.reads == 3


def test_wait_for_mstoken_gives_up_after_deadline():
    ctx = _FakeCtx(after=None)
    assert asyncio.run(browser.wait_for_mstoken(ctx, seconds=0.05, poll=0.01)) is False


# ---------- server: nút "Làm mới msToken" + tự làm mới sau import/đăng nhập ----------
class _FakePool:
    def __init__(self, busy=()):
        self.accounts, self.busy = ["n1", "n2"], set(busy)

    def assert_idle(self, name):
        if name in self.busy:
            raise RuntimeError(f"Nick {name} đang render")


@pytest.fixture
def srv(monkeypatch):
    import server
    calls = []

    async def fake_refresh(name, *a, **k):
        calls.append(name)
        return True
    monkeypatch.setattr(server, "_admin_auth", lambda k: None)
    monkeypatch.setattr(browser, "refresh_mstoken", fake_refresh)
    server.calls = calls
    yield server
    del server.calls


def test_refresh_endpoint_returns_has_mstoken(srv, monkeypatch):
    monkeypatch.setattr(srv, "pool", _FakePool())
    assert asyncio.run(srv.admin_account_refresh_mstoken("n1", x_admin_key=None)) == {"ok": True, "has_mstoken": True}
    assert srv.calls == ["n1"]


def test_refresh_endpoint_refuses_busy_nick_without_opening_profile(srv, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(srv, "pool", _FakePool(busy={"n1"}))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(srv.admin_account_refresh_mstoken("n1", x_admin_key=None))
    assert ei.value.status_code == 409 and srv.calls == []


def test_refresh_endpoint_unknown_nick_404(srv, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(srv, "pool", _FakePool())
    with pytest.raises(HTTPException) as ei:
        asyncio.run(srv.admin_account_refresh_mstoken("khong-co", x_admin_key=None))
    assert ei.value.status_code == 404


def test_auto_refresh_skips_nick_that_already_has_mstoken(srv, monkeypatch):
    import submit_http
    monkeypatch.setattr(srv, "pool", _FakePool())
    monkeypatch.setattr(submit_http, "has_real_mstoken", lambda n: True)
    asyncio.run(srv._refresh_mstoken_if_missing("n1"))
    assert srv.calls == []


def test_auto_refresh_never_raises_and_skips_busy_nick(srv, monkeypatch):
    import submit_http
    monkeypatch.setattr(srv, "pool", _FakePool(busy={"n1"}))
    monkeypatch.setattr(submit_http, "has_real_mstoken", lambda n: False)
    asyncio.run(srv._refresh_mstoken_if_missing("n1"))   # không được ném lỗi làm hỏng luồng thêm nick
    assert srv.calls == []
    asyncio.run(srv._refresh_mstoken_if_missing("n2"))
    assert srv.calls == ["n2"]
