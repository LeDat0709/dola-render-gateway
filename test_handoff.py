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
    assert vw._answer_key(CONFIRM) in answered, "phải nhớ là đã trả lời câu này"

    # lần mở lại nick (poll mới, cùng bộ nhớ): câu cũ vẫn còn trong hội thoại → KHÔNG trả lời nữa
    again = []
    out2 = _run([{"ok": True, "texts": [CONFIRM], "videos": [], "images": []}],
                quiet=0, answered=again, shared=answered)
    assert not again, "đã trả lời lại câu cũ → ping-pong"
    assert out2.get("handoff") is True          # hết việc phải làm thì nhả trình duyệt


# Nguyên văn Dola trong log 11:14–11:16 (fb61593690226090): cùng một câu bị trả lời 3 lần vì
# tin nhắn được đọc lúc còn đang stream nên chuỗi lưu lại NGẮN HƠN chuỗi đọc lần sau.
STREAM_SHORT = "リクエストされた秒数 30 秒は対応範囲外です。動画生成は 4～15 秒まで可能です。"
STREAM_GROWN = STREAM_SHORT + "ご希望の秒数を教えてください。"
STREAM_MENU = STREAM_SHORT + "以下からお選びください：\n- A. 15 秒版（フル構成）\n- B. 10 秒版\n- C. 4 秒版"


def _run_answers(polls, shared, ratio=None, duration=30):
    """Chạy 1 lượt poll, trả về (số lần 'はい', các câu trả lời dạng chữ)."""
    yes, texts = [], []
    async def reply_yes(page, *a, **kw):
        yes.append(True); return True
    async def reply_text(page, ans, *a, **kw):
        texts.append(ans); return True

    class FakeCtx:
        async def cookies(self, *a): return []

    vw._reply_yes = reply_yes
    vw._reply_text = reply_text
    vw.HANDOFF_QUIET_SEC = 30
    asyncio.run(vw.poll_conversation("acc1", FakePage(polls), FakeCtx(), "77", timeout=60,
                                     handoff_after=0, answered=shared, ratio=ratio,
                                     duration=duration))
    return yes, texts


def test_streaming_message_is_answered_once():
    """Tin nhắn dài thêm trong lúc stream không được tính là câu hỏi MỚI."""
    shared = set()
    yes, _ = _run_answers([{"ok": True, "texts": [STREAM_SHORT], "videos": [], "images": []}, VIDEO], shared)
    assert len(yes) == 1, "phải trả lời câu Dola hỏi"
    yes2, _ = _run_answers([{"ok": True, "texts": [STREAM_GROWN], "videos": [], "images": []}, VIDEO], shared)
    assert not yes2, "cùng một câu (chỉ dài thêm) mà trả lời lại → ping-pong như log 11:16"


def test_option_list_gets_a_letter_not_yes():
    """Dola liệt kê A/B/C thì 'はい' vô nghĩa — phải đáp chữ cái, và theo mức giây Dola cho."""
    shared = set()
    yes, texts = _run_answers([{"ok": True, "texts": [STREAM_MENU], "videos": [], "images": []}, VIDEO], shared)
    assert not yes, "trả 'はい' cho menu A/B/C là câu trả lời sai → Dola hỏi lại mãi"
    assert texts, "phải chọn một phương án trong menu"
    assert "30" not in texts[0], f"đòi lại 30s sau khi Dola nói tối đa 15s: {texts[0]}"
    assert texts[0].startswith("A"), f"phải gọi đúng chữ cái phương án 15 giây: {texts[0]}"
    # hỏi lại y nguyên → im lặng; và HTTP poll cũng phải nhận ra để không mở lại nick vô ích
    yes2, texts2 = _run_answers([{"ok": True, "texts": [STREAM_MENU], "videos": [], "images": []}, VIDEO], shared)
    assert not yes2 and not texts2, "trả lời lại menu cũ → ping-pong"
    assert vw._answer_key(vw._NeedsBrowser(STREAM_MENU).full) in shared


def _http_poll(texts, shared):
    """poll_conversation_http với aiohttp giả: lượt 1 chỉ có `texts`, lượt 2 có thêm video."""
    import json as _j

    def msg(text=None, video=None):
        blocks = []
        if text:
            blocks.append({"content": {"text_block": {"text": text}}})
        if video:
            blocks.append({"block_type": 2074, "content": {"creation_block": {"creations": [
                {"type": 2, "video": {"download_url": video, "video_model": ""}}]}}})
        return {"content": _j.dumps(blocks)}

    def page(*msgs):
        return {"downlink_body": {"pull_singe_chain_downlink_body": {"messages": list(msgs)}}}

    pages = [page(*[msg(t) for t in texts]),
             page(*[msg(t) for t in texts], msg(video="https://x/v.mp4"))]

    class Resp:
        status = 200
        def __init__(self, data): self.data = data
        async def json(self, **_): return self.data
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class Session:
        def post(self, *a, **k): return Resp(pages.pop(0) if len(pages) > 1 else pages[0])
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    real = vw.aiohttp.ClientSession
    vw.aiohttp.ClientSession = Session
    try:
        return asyncio.run(vw.poll_conversation_http("acc1", "c=1", "", "", "77", 60, answered=shared))
    finally:
        vw.aiohttp.ClientSession = real


def test_http_poll_skips_answered_question():
    """Sau khi nhả nick, câu ĐÃ trả lời vẫn nằm trong 20 tin gần nhất — HTTP poll phải bỏ qua và
    chờ video, không được ném _NeedsBrowser (→ "hỏi đi hỏi lại", job chết oan sau 60s)."""
    out = _http_poll([CONFIRM], {vw._answer_key(CONFIRM)})
    assert out["conversation_id"] == "77" and out.get("local_path"), "câu cũ đã trả lời mà vẫn đòi mở lại nick"
    try:                                   # câu MỚI (chưa trả lời) thì vẫn phải xin mở lại nick
        _http_poll([CONFIRM], set())
        assert False, "câu chưa trả lời phải ném _NeedsBrowser"
    except vw._NeedsBrowser:
        pass


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
    test_answered_memory_survives_reopen(); test_streaming_message_is_answered_once()
    test_option_list_gets_a_letter_not_yes(); test_http_poll_skips_answered_question()
    test_blocked_reason_says_one_thing(); print("OK")
