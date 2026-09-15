"""Job bị restart cắt ngang: chạy lại khi chưa gửi prompt, không chạy lại khi đã gửi."""
import tempfile, time
from pathlib import Path

from server import MAX_AUTO_REQUEUE, STALE_REQUEUE_SEC, _restart_action, _task_stage
from store import TaskStore


def test_decision():
    now = time.time()
    assert _restart_action({"created_at": now}, now)[0] == "requeue"
    assert _restart_action({"created_at": now, "submitted_at": now}, now)[0] == "failed"
    assert _restart_action({"created_at": now, "attempts": MAX_AUTO_REQUEUE}, now)[0] == "failed"
    assert _restart_action({"created_at": now - STALE_REQUEUE_SEC - 1}, now)[0] == "failed"


def test_stage_shown_in_ui():
    assert _task_stage({"status": "queued"}) == "queued"
    assert _task_stage({"status": "processing"}) == "waiting"                     # chưa có slot Chrome/nick
    assert _task_stage({"status": "processing", "opened_at": 1.0}) == "opening"
    assert _task_stage({"status": "processing", "submitted_at": 1.0}) == "submitting"
    assert _task_stage({"status": "processing", "submitted_at": 1.0, "conversation_id": "77"}) == "rendering"


def test_requeue_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        store = TaskStore(str(Path(d) / "t.db"))
        store.create("t1", "seedance-2.5", "p", "9:16", 30)
        store.update("t1", status="processing", started_at=time.time())
        orphans = store.orphan_processing_tasks(keep_ids=set())
        assert [r["id"] for r in orphans] == ["t1"]

        store.requeue("t1")
        row = store.get("t1")
        assert row["status"] == "queued" and row["attempts"] == 1
        assert row["started_at"] is None and row["submitted_at"] is None
        assert [r["id"] for r in store.recoverable_queued_tasks()] == ["t1"]  # startup sẽ chạy lại
        assert store.orphan_processing_tasks(keep_ids=set()) == []

        store.update("t1", status="processing", started_at=time.time())
        store.requeue("t1")
        assert store.get("t1")["attempts"] == MAX_AUTO_REQUEUE  # lần 2 là lần cuối


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
        # Đã kết thúc mà client CHƯA thấy (Dừng/đóng app/mất mạng) → vẫn trả job cũ để client nối lại xem kết quả,
        # KHÔNG lặng lẽ tạo job thứ hai (job trước có thể đã gửi Dola = đã trừ lượt).
        store.update("t1", status="failed", finished_at=time.time(), submitted_at=time.time())
        assert store.live_by_client_id("key-n1", "k1")["id"] == "t1"
        # Client xoá khóa khi đã thấy trạng thái cuối → lần Chạy sau mang khóa MỚI = ý định mới → job mới.
        store.create("t2", "seedance-2.5", "p", "9:16", 30, account="n1", api_key_hash="k1", client_id="key-n1-moi")
        assert store.live_by_client_id("key-n1-moi", "k1")["id"] == "t2"
        assert store.get("t2")["account"] == "n1"                            # nick ghim ghi ngay lúc xếp hàng


if __name__ == "__main__":
    test_decision(); test_stage_shown_in_ui(); test_requeue_roundtrip(); test_client_id_dedupe(); print("OK")
