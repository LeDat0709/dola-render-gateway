"""Chỉ nhả trình duyệt khi Dola đã hết hỏi lại — bật HTTP poll không được làm chết job 30s."""
import asyncio
import tempfile
from pathlib import Path

import video_worker_ui as vw

CONFIRM = "30秒の動画を生成しますか？よろしいですか"       # Dola hỏi xác nhận thời lượng
VIDEO = {"ok": True, "texts": [], "videos": ["https://x/v.mp4"], "videoModels": [""], "images": []}


class FakePage:
    def __init__(self, polls):
        self.polls = list(polls)
    async def evaluate(self, *a, **kw):
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]


def _run(polls, quiet, answered, shared=None):
    async def main():
        page = FakePage(polls)
        return await vw.poll_conversation("acc1", page, FakeCtx(), "77", timeout=60,
                                          handoff_after=0, answered=shared)

    class FakeCtx:
        async def cookies(self, *a): return []

    vw.HANDOFF_QUIET_SEC = quiet
    vw._reply_yes = _fake_reply(answered)
    return asyncio.run(main())


def _fake_reply(answered):
    async def reply(page, *a, **kw):
        answered.append(True)
        return True
    return reply


def setup_module(_=None):
    real_sleep = asyncio.sleep
    async def fast(_s): await real_sleep(0)
    asyncio.sleep = fast                      # bỏ 5s chờ mỗi vòng poll
    tmp = Path(tempfile.mkdtemp()) / "v.mp4"; tmp.write_bytes(b"0" * 10)
    async def fake_download(url, account): return tmp
    vw._download = fake_download


def test_no_question_hands_off():
    out = _run([{"ok": True, "texts": [], "videos": [], "images": []}], quiet=0, answered=[])
    assert out.get("handoff") is True


def test_pending_question_is_answered_first():
    answered = []
    polls = [{"ok": True, "texts": [CONFIRM], "videos": [], "images": []}, VIDEO]
    out = _run(polls, quiet=30, answered=answered)     # còn trong cửa sổ im tiếng → không nhả
    assert answered, "phải trả lời câu hỏi của Dola trước khi nhả trình duyệt"
    assert not out.get("handoff") and out["conversation_id"] == "77"


# Đúng những câu Dola hỏi lại trong log 09:56 — phải nhận ra là "cần mở lại trình duyệt".
DOLA_ASKS = [
    "30秒の動画生成は現在サポートされていません。最短4秒、最長15秒まで可能です。",
    "30秒の動画生成は現在対応していません。最短4秒、最長15秒まで可能です。",
    "生成可能な案は以下のいずれです：\n\nA. 15秒版：前半の主要対立",
    "30秒の動画を生成しますか？よろしいですか",
]


def test_late_questions_reopen_browser():
    for text in DOLA_ASKS:
        assert vw._question_needs_browser(text), text[:40]
    assert not vw._question_needs_browser("生成中です")      # dòng trạng thái thì bỏ qua
    assert not vw._question_needs_browser("")


# Đúng các câu trong log 09:45–09:54 từng làm job chết oan / vòng vo.
CAP_MSGS = [
    "30秒の動画生成はできません。現在対応しているのは4～15秒です。",
    "30 秒の動画生成はできません。現在対応しているのは 4～15 秒です。",
    "30秒版は直接生成できません。動画生成は4～15秒まで対応しています。",
    "30秒の動画生成は現在サポートしていません。最短4秒、最長15秒まで対応可能です。",
]


def test_duration_cap_is_not_content_policy():
    """Dola hạ thời lượng ≠ chặn nội dung — trước đây job bị đánh chết oan."""
    blocks = lambda t: bool(vw.CONTENT_POLICY_PATTERN.search(t)) and not vw._is_duration_capped(t)
    for m in CAP_MSGS:
        assert vw._is_duration_capped(m), m[:30]
        assert not blocks(m), f"vẫn bị coi là chặn nội dung: {m[:40]}"
    real_block = "ご希望のコンテンツを生成できません。他の内容をお試しください。"
    assert blocks(real_block)                          # chặn nội dung thật thì vẫn phải bắt


def test_reply_uses_dola_cap_not_30s():
    assert vw._capped_seconds("最短4秒、最長15秒まで可能です") == 15
    assert vw._capped_seconds("最長の10秒で生成します") == 10
    assert vw._capped_seconds("生成中です") is None


def test_own_directive_is_ignored():
    own = "【この仕様で直接生成してください（30秒・アスペクト比9:16（縦））。長さ・比率は変更せず、追加の確認は不要です】"
    assert vw._is_own_message(own)                     # tin của chính mình, không phải Dola hỏi
    assert not vw._is_own_message(CAP_MSGS[0])


def test_answered_memory_survives_reopen():
    """Mở lại nick không được trả lời lại câu cũ — đó là vòng lặp đã đốt 20 lần mở nick."""
    answered = set()
    polls = [{"ok": True, "texts": [CONFIRM], "videos": [], "images": []}, VIDEO]
    out = _run(polls, quiet=30, answered=[], shared=answered)
    assert CONFIRM in answered, "phải nhớ là đã trả lời câu này"

    # lần mở lại nick (poll mới, cùng bộ nhớ): câu cũ vẫn còn trong hội thoại → KHÔNG trả lời nữa
    again = []
    out2 = _run([{"ok": True, "texts": [CONFIRM], "videos": [], "images": []}],
                quiet=0, answered=again, shared=answered)
    assert not again, "đã trả lời lại câu cũ → ping-pong"
    assert out2.get("handoff") is True          # hết việc phải làm thì nhả trình duyệt


def test_blocked_reason_says_one_thing():
    """Nick không chạy được thì phải nói ĐÚNG lý do — trước đây liệt kê cả 4 nên hướng dẫn sai."""
    import time as _t
    from browser_pool import BrowserPool
    r = BrowserPool.blocked_reason
    base = {"login_ok": 1, "scheduling": True, "cooling": False, "rate_limited": False,
            "quota_blocked": False, "credit_balance": 4, "used_today": 0, "cooldown_until": 0}
    assert "cookie chết" in r(None, {**base, "login_ok": 0})
    assert "tắt lịch" in r(None, {**base, "scheduling": False})
    assert "nghỉ" in r(None, {**base, "cooling": True, "cooldown_until": _t.time() + 600})
    assert "hết lượt" in r(None, {**base, "rate_limited": True})
    assert "hết điểm" in r(None, {**base, "credit_balance": 0})
    # nick lành thì không được coi là bị chặn
    assert "không rõ" in r(None, base)


if __name__ == "__main__":
    setup_module(); test_no_question_hands_off(); test_pending_question_is_answered_first()
    test_late_questions_reopen_browser(); test_duration_cap_is_not_content_policy()
    test_reply_uses_dola_cap_not_30s(); test_own_directive_is_ignored()
    test_answered_memory_survives_reopen(); test_blocked_reason_says_one_thing(); print("OK")
