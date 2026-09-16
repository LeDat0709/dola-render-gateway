"""Tắt server/thoát app = xoá job cũ: bật lên bảng sạch, không thẻ ma, không job kẹt 'đang chạy'."""
import tempfile, time
from pathlib import Path

import server
from server import _hard_timeout, _task_stage
from store import TaskStore
import config


def test_purge_keeps_only_finished_videos():
    with tempfile.TemporaryDirectory() as d:
        store = TaskStore(str(Path(d) / "t.db"))
        store.create("q", "seedance-2.5", "p", "9:16", 30)                        # queued
        store.create("r", "seedance-2.5", "p", "9:16", 30)
        store.update("r", status="processing", started_at=time.time(), conversation_id="77")
        store.create("err", "seedance-2.5", "p", "9:16", 30, client_id="key-n1")
        store.update("err", status="failed", finished_at=time.time(), error="Dola chặn nội dung")
        store.create("done", "seedance-2.5", "p", "9:16", 30)
        store.update("done", status="completed", video_url="u", finished_at=time.time())

        assert store.purge_dead_tasks() == 3                                      # queued + processing + failed
        assert store.get("q") is None and store.get("r") is None and store.get("err") is None
        assert store.get("done")["status"] == "completed"                         # kho video giữ nguyên
        assert store.pending_task_count() == 0
        assert store.live_by_client_id("key-n1", None) is None                    # khóa cũ hết níu → Chạy = job mới
        assert store.purge_dead_tasks() == 0                                      # bật lại lần nữa: sạch rồi


def test_second_instance_must_not_touch_jobs():
    """Bật trùng bản thứ hai (./run.sh + nút Bật server): uvicorn chạy lifespan TRƯỚC khi chiếm cổng, nên
    nếu không có khoá thì bản thừa quét sạch job của bản đang render rồi mới chết vì cổng bận."""
    keep = config.DB_PATH
    with tempfile.TemporaryDirectory() as d:
        config.DB_PATH = str(Path(d) / "t.db")
        try:
            assert server._claim_single_instance() is True          # bản đầu: được dọn job cũ
            held, server._INSTANCE_LOCK = server._INSTANCE_LOCK, None
            assert server._claim_single_instance() is False         # bản thừa: KHÔNG được đụng job
            held.close()                                            # bản đầu thoát → nhả khoá
            assert server._claim_single_instance() is True
            server._INSTANCE_LOCK.close()
        finally:
            config.DB_PATH, server._INSTANCE_LOCK = keep, None


def test_hard_timeout_covers_render_plus_overhead():
    assert _hard_timeout(30) > config.VIDEO_TIMEOUT_30S
    assert _hard_timeout(10) > config.VIDEO_TIMEOUT


def test_stage_shown_in_ui():
    assert _task_stage({"status": "queued"}) == "queued"
    assert _task_stage({"status": "processing"}) == "waiting"                     # chưa có slot Chrome/nick
    assert _task_stage({"status": "processing", "opened_at": 1.0}) == "opening"
    assert _task_stage({"status": "processing", "submitted_at": 1.0}) == "submitting"
    assert _task_stage({"status": "processing", "submitted_at": 1.0, "conversation_id": "77"}) == "rendering"


def test_client_id_dedupe():
    """Studio gửi lại cùng khóa (Dừng→Chạy, POST treo rồi gửi lại, mở lại app): job cũ CHƯA xong → trả job cũ;
    đã xong/lỗi → không còn 'sống' → được tạo job mới. Khóa của api key khác không nhìn thấy nhau."""
    with tempfile.TemporaryDirectory() as d:
        store = TaskStore(str(Path(d) / "t.db"))
        store.create("t1", "seedance-2.5", "p", "9:16", 30, account="n1", api_key_hash="k1", client_id="key-n1")
        assert store.live_by_client_id("key-n1", "k1")["id"] == "t1"          # queued → sống
        store.update("t1", status="processing", started_at=time.time())
        assert store.live_by_client_id("key-n1", "k1")["id"] == "t1"          # processing → vẫn sống
        assert store.live_by_client_id("key-n1", "k2") is None               # api key khác → không thấy
        assert store.live_by_client_id("key-n2", "k1") is None               # khóa khác → không thấy
        # Đã kết thúc mà client CHƯA thấy (Dừng/đóng app/mất mạng) → vẫn trả job cũ để client nối lại xem trạng thái
        # cuối (kể cả "server đã tắt"), rồi nó xoá khóa và lần Chạy sau là job mới.
        store.update("t1", status="failed", finished_at=time.time(), submitted_at=time.time())
        assert store.live_by_client_id("key-n1", "k1")["id"] == "t1"
        store.create("t2", "seedance-2.5", "p", "9:16", 30, account="n1", api_key_hash="k1", client_id="key-n1-moi")
        assert store.live_by_client_id("key-n1-moi", "k1")["id"] == "t2"
        assert store.get("t2")["account"] == "n1"                            # nick ghim ghi ngay lúc xếp hàng


if __name__ == "__main__":
    test_purge_keeps_only_finished_videos(); test_second_instance_must_not_touch_jobs()
    test_hard_timeout_covers_render_plus_overhead()
    test_stage_shown_in_ui(); test_client_id_dedupe(); print("OK")
