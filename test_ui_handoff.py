"""Chế độ UI cũng phải NHẢ Chrome sau khi gửi, rồi theo dõi tiếp bằng cookie (HTTP).

20/09: đường fetch có bàn giao (video_worker_ui:1459) nhưng đường UI gọi thẳng poll_conversation, nên
DOLA_HTTP_POLL_AFTER=60 vô hiệu — Chrome bị giữ suốt cả lượt dựng (~370s đo thật), tốn RAM, và Chrome chết
giữa chừng là mất lượt. Bộ test này chốt: UI dùng chung đúng một đường bàn giao với fetch."""
import asyncio
import time

import video_worker_ui as vw


class _Ctx:
    def __init__(self):
        self.closed = False

    async def cookies(self, _url):
        return [{"name": "sessionid", "value": "abc"}, {"name": "msToken", "value": "t"}]

    async def close(self):
        self.closed = True


def _patch(monkeypatch, poll_result, http_result=None, http_exc=None):
    state = {"freed": 0, "held": 0, "http_called": False, "handoff_after": "chua-goi"}

    async def fake_poll(account, page, context, conv_id, timeout, on_poll, on_balance, ratio, duration,
                        handoff_after=None, answered=None, prompt=""):
        state["handoff_after"] = handoff_after
        return poll_result

    async def fake_http(account, cookie, ms_token, fp, conv_id, timeout, on_poll=None, on_balance=None,
                        answered=None, prompt=""):
        state["http_called"] = True
        state["cookie"] = cookie
        if http_exc:
            raise http_exc
        return http_result

    async def fake_persist(context, account):
        return None

    monkeypatch.setattr(vw, "poll_conversation", fake_poll)
    monkeypatch.setattr(vw, "poll_conversation_http", fake_http)
    monkeypatch.setattr(vw, "_persist_before_close", fake_persist)
    return state


def _run(monkeypatch, poll_result, **kw):
    state = _patch(monkeypatch, poll_result, **kw)
    ctx = _Ctx()
    closed = {"v": False}

    def freed():
        state["freed"] += 1

    async def held():
        state["held"] += 1

    out = asyncio.run(vw._poll_then_handoff(
        account="n1", page=object(), context=ctx, conv_id="c1", timeout=900,
        deadline=time.time() + 900, ms_token="t", fp="f", on_poll=None, on_balance=None,
        ratio="9:16", duration=30, prompt="p", on_browser_free=freed, on_browser_hold=held,
        mark_closed=lambda: closed.__setitem__("v", True)))
    return out, state, ctx, closed


def test_video_done_before_handoff_keeps_browser(monkeypatch):
    monkeypatch.setattr(vw.config, "HTTP_POLL", True, raising=False)
    out, state, ctx, closed = _run(monkeypatch, {"path": "/x.mp4"})
    assert out == {"path": "/x.mp4"}
    assert state["http_called"] is False
    assert ctx.closed is False and state["freed"] == 0    # xong sớm → không cần đóng sớm


def test_handoff_closes_browser_and_polls_with_cookie(monkeypatch):
    monkeypatch.setattr(vw.config, "HTTP_POLL", True, raising=False)
    monkeypatch.setattr(vw.config, "HTTP_POLL_AFTER_SEC", 60, raising=False)
    out, state, ctx, closed = _run(monkeypatch, {"handoff": True}, http_result={"path": "/y.mp4"})
    assert out == {"path": "/y.mp4"}
    assert state["handoff_after"] == 60                    # có truyền mốc bàn giao
    assert ctx.closed is True and closed["v"] is True       # Chrome đã đóng
    assert state["freed"] == 1                             # slot Chrome đã trả cho nick khác
    assert "sessionid=abc" in state["cookie"]              # theo dõi tiếp bằng cookie


def test_http_poll_off_holds_browser_like_before(monkeypatch):
    monkeypatch.setattr(vw.config, "HTTP_POLL", False, raising=False)
    out, state, ctx, _ = _run(monkeypatch, {"path": "/z.mp4"})
    assert out == {"path": "/z.mp4"}
    assert state["handoff_after"] is None                  # tắt cờ → giữ nguyên cách cũ
    assert ctx.closed is False


def test_late_question_reopens_browser(monkeypatch):
    """Dola hỏi lại sau khi đã nhả Chrome → xin lại slot rồi mở nick trả lời, không được bỏ job."""
    monkeypatch.setattr(vw.config, "HTTP_POLL", True, raising=False)
    monkeypatch.setattr(vw.config, "HTTP_POLL_AFTER_SEC", 60, raising=False)

    async def fake_resume(account, conv_id, left, **kw):
        return {"path": "/resumed.mp4"}

    monkeypatch.setattr(vw, "resume_video", fake_resume)
    out, state, ctx, _ = _run(monkeypatch, {"handoff": True}, http_exc=vw._NeedsBrowser("15秒でいい？"))
    assert out == {"path": "/resumed.mp4"}
    assert state["held"] == 1                              # đã xin lại slot Chrome trước khi mở
