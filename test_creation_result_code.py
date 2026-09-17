"""Tin KẾT QUẢ dựng (send_scene 77) mang ai_creation_res_code: 0 = ra video, ≠0 = dựng xong mà không trả video.
Hình dạng lấy từ im/chain/single đọc thật 17/9 (một hội thoại giấu bản quyền, một hội thoại ra video).
Chạy: .venv/bin/python test_creation_result_code.py"""
import asyncio
import json

import video_worker_ui as vw


def msg(text="", scene="5", user_type=2, ext=None, video=None):
    blocks = [{"block_type": 10000, "content": {"text_block": {"text": text}}}] if text else []
    if video:
        blocks.append({"block_type": 2074, "content": {"creation_block": {"creations": [
            {"type": 2, "video": {"download_url": video, "video_model": ""}}]}}})
    return {"content": json.dumps(blocks), "send_scene": scene, "user_type": user_type,
            "ext": json.dumps(ext or {}), "tts_content": text}


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


if __name__ == "__main__":
    test_parse()
    test_error_kinds()
    test_poll_fails_fast_on_hidden_video()
    print("ALL PASS")
