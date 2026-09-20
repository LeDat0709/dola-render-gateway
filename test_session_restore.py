"""Jar Chrome mất phiên thì nạp lại từ accounts/<nick>/cookies.json.

20/09: cả cụm nick chết với "Bị đăng xuất khỏi Dola giữa chừng". pin_session_cookies chỉ ghim lại cookie
ĐANG CÓ trong jar — jar mất sessionid là hết đường, dù file cookies.json vẫn còn phiên (33/69 nick có).
Đối thủ Seedance Unlimited coi file JSON của mình là nguồn sự thật và nạp lại ở MỌI lần mở
(xem SEEDANCE_UNLIMITED_ANALYSIS.md mục 6) — đây là bản port có kiểm chứng.

Nguyên tắc: jar CÒN phiên thì KHÔNG đụng (bản trong jar mới hơn file); chỉ cứu khi jar đã mất."""
import asyncio
import json

import browser

FAR = 4102444800   # 2100-01-01


def _c(name, value="v", **kw):
    return {"name": name, "value": value, "domain": ".dola.com", "path": "/",
            "expires": kw.get("expires", FAR), "httpOnly": False, "secure": True, "sameSite": "Lax"}


class _Ctx:
    def __init__(self, jar):
        self._jar = jar
        self.added = []

    async def cookies(self, _url=None):
        return self._jar

    async def add_cookies(self, cookies):
        self.added.extend(cookies)


def test_jar_still_has_session_so_nothing_is_touched():
    jar = [_c("sessionid", "moi"), _c("i18next")]
    saved = [_c("sessionid", "cu-hon")]
    assert browser._session_cookies_to_restore(jar, saved) == []


def test_restores_session_when_jar_lost_it():
    jar = [_c("i18next")]                       # jar còn cookie linh tinh nhưng mất phiên
    saved = [_c("sessionid", "abc"), _c("sid_tt", "d"), _c("i18next", "x")]
    out = browser._session_cookies_to_restore(jar, saved)
    names = {c["name"] for c in out}
    assert "sessionid" in names and "sid_tt" in names
    assert "i18next" not in names               # chỉ cứu cookie phiên, không dội lại cả file


def test_nothing_to_restore_when_file_has_no_session():
    assert browser._session_cookies_to_restore([], [_c("i18next")]) == []
    assert browser._session_cookies_to_restore([], []) == []


def test_restored_cookies_get_far_future_expiry():
    """Nạp thiếu expires là cookie phiên — Chromium bỏ khi đóng context, lần sau lại mất."""
    import time
    out = browser._session_cookies_to_restore([], [dict(_c("sessionid"), expires=-1)])
    assert out and all(c["expires"] == browser._FAR_FUTURE for c in out)
    assert browser._FAR_FUTURE > time.time() + 365 * 86400      # còn hạn ít nhất 1 năm nữa
    assert all(c["domain"] and c["path"] for c in out)


def test_empty_values_are_skipped():
    assert browser._session_cookies_to_restore([], [_c("sessionid", "")]) == []


def test_async_wrapper_adds_to_context(tmp_path, monkeypatch):
    monkeypatch.setattr(browser.config, "ACCOUNTS_DIR", tmp_path, raising=False)
    (tmp_path / "n1").mkdir()
    (tmp_path / "n1" / "cookies.json").write_text(json.dumps([_c("sessionid", "abc")]), encoding="utf-8")
    ctx = _Ctx(jar=[_c("i18next")])
    assert asyncio.run(browser.restore_session_cookies(ctx, "n1")) is True
    assert [c["name"] for c in ctx.added] == ["sessionid"]


def test_async_wrapper_is_quiet_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(browser.config, "ACCOUNTS_DIR", tmp_path, raising=False)
    (tmp_path / "n2").mkdir()
    ctx = _Ctx(jar=[])
    assert asyncio.run(browser.restore_session_cookies(ctx, "n2")) is False
    assert ctx.added == []


def test_async_wrapper_survives_broken_json(tmp_path, monkeypatch):
    """File hỏng không được làm chết job — mở nick vẫn phải chạy tiếp."""
    monkeypatch.setattr(browser.config, "ACCOUNTS_DIR", tmp_path, raising=False)
    (tmp_path / "n3").mkdir()
    (tmp_path / "n3" / "cookies.json").write_text("{khong-phai-json", encoding="utf-8")
    ctx = _Ctx(jar=[])
    assert asyncio.run(browser.restore_session_cookies(ctx, "n3")) is False
