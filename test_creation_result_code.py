"""Tin KẾT QUẢ dựng (send_scene 77) mang ai_creation_res_code: 0 = ra video, ≠0 = dựng xong mà không trả video.
Hình dạng lấy từ im/chain/single đọc thật 17/9 (một hội thoại giấu bản quyền, một hội thoại ra video).
Chạy: .venv/bin/python test_creation_result_code.py"""
import asyncio
import json

import video_worker_ui as vw


def msg(text="", scene="5", user_type=2, ext=None, video=None, at=0):
    blocks = [{"block_type": 10000, "content": {"text_block": {"text": text}}}] if text else []
    if video:
        blocks.append({"block_type": 2074, "content": {"creation_block": {"creations": [
            {"type": 2, "video": {"download_url": video, "video_model": ""}}]}}})
    return {"content": json.dumps(blocks), "send_scene": scene, "user_type": user_type,
            "ext": json.dumps(ext or {}), "tts_content": text, "create_time": str(at)}


def page(*msgs):
    return {"downlink_body": {"pull_singe_chain_downlink_body": {"messages": list(msgs)}}}


COST = msg("動画は2クレジットを使用して、15分後に完成します。", ext={"force_submit_review": "1"})
ASK = msg("Video generation currently supports durations from 4 to 15 seconds.",
          ext={"ai_creation_res_code": "710082041", "is_creation_clarifying": "1"})
HIDDEN = msg("著作権を保護するため、生成された動画を表示できません。", scene="77",
             ext={"ai_creation_res_code": "710082022", "block_meta": {"items": ["txt"]}})
DONE = msg("動画が生成されました。", scene="77", ext={"ai_creation_res_code": "0"}, video="https://x/v.mp4")


def test_parse():
    assert vw._parse_single(page(HIDDEN, COST))["creation_fail"]["code"] == 710082022
    assert vw._parse_single(page(DONE, COST))["creation_fail"] is None, "ra video không được coi là thua"
    # 710082041 ở câu hỏi/câu "bắt đầu dựng" KHÔNG phải kết quả dựng → không được báo lỗi (job đang dựng bình thường)
    assert vw._parse_single(page(ASK, COST))["creation_fail"] is None
    assert vw._parse_single(page(COST))["creation_fail"] is None
    # Chỉ tin kết quả MỚI NHẤT quyết định: lượt cũ thua, lượt mới ra video → không lỗi
    assert vw._parse_single(page(DONE, HIDDEN))["creation_fail"] is None
    # Tin người dùng (user_type 1) mang scene 77 cũng không tính
    assert vw._parse_single(page(msg("はい", scene="77", user_type=1, ext={"ai_creation_res_code": "5"})))["creation_fail"] is None


def test_error_kinds():
    e = vw._creation_fail_error({"code": 710082022, "text": "著作権"})
    assert isinstance(e, vw.ContentPolicyViolationError) and "710082022" in str(e)
    e = vw._creation_fail_error({"code": 123, "text": "?"})
    assert isinstance(e, RuntimeError) and "KHÔNG trả video" in str(e)


def _poll(pages):
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
        return asyncio.run(vw.poll_conversation_http("acc1", "c=1", "", "", "77", 60, answered=set()))
    finally:
        vw.aiohttp.ClientSession = real


def test_poll_fails_fast_on_hidden_video():
    """Trước đây câu giấu video không khớp mẫu nào → job treo tới hết 40 phút. Giờ lượt đọc đầu tiên là dừng."""
    # Câu LẠ (không khớp mẫu chữ nào) — chứng minh dừng nhờ MÃ, không nhờ câu chữ
    unknown = msg("[カード]", scene="77", ext={"ai_creation_res_code": "710082022"})
    try:
        _poll([page(unknown, COST)])
        assert False, "tin kết quả mã 710082022 phải dừng job ngay"
    except vw.ContentPolicyViolationError as e:
        assert "710082022" in str(e)


# Hội thoại thật 17/9: cùng câu "Only two videos… I'll start by generating…" — job A KHÔNG có force_submit_review (kẹt
# "đang dựng" 40 phút, không có gì dựng), job B CÓ (ra video sau 409s). Phân biệt bằng CỜ, không bằng câu chữ.
PROMPT = msg("生成された動画：\"15s …\"、9:16", user_type=1, at=1000)
TWO_ONLY_CLAR = msg("Only two videos can be generated at a time. I'll start by generating the 15-second version.",
                    ext={"is_creation_clarifying": "1", "ai_creation_res_code": "710082041"}, at=1001)
TWO_ONLY_STARTED = msg("Only two videos can be generated at a time. I'll start by generating the 15-second version.",
                       ext={"force_submit_review": "1"}, at=1001)


def test_not_started_detection():
    stuck = vw._parse_single(page(TWO_ONLY_CLAR, PROMPT))
    assert stuck["render_started"] is False and stuck["last_bot_clarifying"] is True
    vw._check_not_started(stuck, 1001 + 60)          # mới im 60s → vẫn chờ (Dola có thể đang trả lời tiếp)
    try:
        vw._check_not_started(stuck, 1001 + vw.NOT_STARTED_QUIET_SEC + 1, "Only two videos…")
        assert False, "Dola chỉ trả lời chữ, im quá lâu → phải báo lỗi"
    except vw.DolaNotStartedError as e:
        assert "chưa dựng" in str(e) and "Only two videos" in str(e)

    started = vw._parse_single(page(TWO_ONLY_STARTED, PROMPT))
    assert started["render_started"] is True
    vw._check_not_started(started, 1001 + 3600)      # đã nhận dựng → chờ video dù lâu, KHÔNG báo lỗi
    # Nhận dựng rồi, tool trả "はい" thừa → Dola đáp chữ (clarifying): vẫn KHÔNG được báo lỗi
    later = vw._parse_single(page(msg("承知いたしました。生成中です。", ext={"is_creation_clarifying": "1"}, at=1033),
                                  msg("はい", user_type=1, at=1031), TWO_ONLY_STARTED, PROMPT))
    assert later["render_started"] is True
    vw._check_not_started(later, 1033 + 3600)
    # Tin kết quả (scene 77) cũng là đã dựng
    assert vw._parse_single(page(msg("[カード]", scene="77", ext={"ai_creation_res_code": "0"}, at=1400), TWO_ONLY_CLAR, PROMPT))["render_started"]
    # Tool vừa trả lời (tin mới) → mốc im lặng tính lại từ câu trả lời
    answered = vw._parse_single(page(msg("A、15秒でお願いします。", user_type=1, at=1500), TWO_ONLY_CLAR, PROMPT))
    vw._check_not_started(answered, 1500 + 60)
    # Thiếu cờ (bản POLL_JS cũ) → không làm gì
    vw._check_not_started({"texts": [], "videos": []}, 10 ** 10)


# Ảnh 17/9: menu có "D. 9:16 ではなく別のアスペクト比" — tool chọn nhầm D (vì chứa chữ 9:16) → Dola hỏi lại mãi, không dựng.
MENU = """直接生成できません。現在の動画生成は最長 15 秒まで対応しています。

以下のいずれかで進めますか？

A. 15 秒版に圧縮して生成
B. 15 秒のフック部分のみ生成
C. 前半 15 秒と後半 15 秒の 2 回に分けて生成
D. 9:16 ではなく別のアスペクト比に変更して生成"""


def test_menu_picks_compress_not_negated_ratio():
    assert vw._spec_menu_answer(MENU, "9:16", 30) == "A、15秒、9:16（縦向き）でお願いします。", "job 30s → hạ về 15s, chọn A"
    assert vw._spec_menu_answer(MENU, "9:16", 15).startswith("A、15秒")
    assert vw._spec_menu_answer(MENU, "9:16", 10).startswith("A、10秒"), "job 10s không bị nâng lên 15s"
    for bad in ("B", "C", "D"):
        assert not vw._spec_menu_answer(MENU, "9:16", 30).startswith(bad)
    assert vw._spec_menu_answer("アスペクト比を選んでください：\nA. 16:9\nB. 9:16", "9:16", 15) == "B", "menu tỉ lệ thường vẫn đúng"
    assert vw._spec_menu_answer("A. 9:16 ではなく 16:9\nB. 9:16 縦向き", "9:16", None) == "B", "không chọn phương án phủ định"


if __name__ == "__main__":
    test_parse()
    test_error_kinds()
    test_poll_fails_fast_on_hidden_video()
    test_not_started_detection()
    test_menu_picks_compress_not_negated_ratio()
    print("ALL PASS")
