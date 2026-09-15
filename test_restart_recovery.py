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


if __name__ == "__main__":
    test_decision(); test_stage_shown_in_ui(); test_requeue_roundtrip(); print("OK")
