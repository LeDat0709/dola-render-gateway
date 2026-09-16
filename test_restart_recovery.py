"""Tắt server = tắt job: job dở dang không sống lại, không có job nào kẹt 'đang chạy'."""
import tempfile, time
from pathlib import Path

from server import RESTART_ERROR, _hard_timeout, _task_stage
from store import TaskStore
import config


def test_boot_sweep_kills_unfinished_only():
    with tempfile.TemporaryDirectory() as d:
        store = TaskStore(str(Path(d) / "t.db"))
        store.create("q", "seedance-2.5", "p", "9:16", 30)                       # queued
        store.create("r", "seedance-2.5", "p", "9:16", 30)
        store.update("r", status="processing", started_at=time.time(), conversation_id="77")
        store.create("done", "seedance-2.5", "p", "9:16", 30)
        store.update("done", status="completed", video_url="u", finished_at=time.time())

        assert store.fail_unfinished(RESTART_ERROR) == 2
        assert store.get("q")["status"] == "failed" and store.get("r")["status"] == "failed"
        assert store.get("r")["error"] == RESTART_ERROR
        assert store.get("done")["status"] == "completed"                        # job đã xong giữ nguyên
        assert store.pending_task_count() == 0
        assert store.fail_unfinished(RESTART_ERROR) == 0                         # bật lại lần nữa: sạch rồi


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
    test_boot_sweep_kills_unfinished_only(); test_hard_timeout_covers_render_plus_overhead()
    test_stage_shown_in_ui(); test_client_id_dedupe(); print("OK")
