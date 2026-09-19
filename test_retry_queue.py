"""Test RetryQueue: PriorityQueue với NEW vs RETRY priority."""
import asyncio
import pytest

from retry_queue import RetryQueue, reset_default


@pytest.fixture
def queue():
    return RetryQueue(name="test", max_size=100)


async def test_enqueue_new_sequential_priority(queue: RetryQueue):
    """Job mới có sequence tăng dần (FIFO)."""
    seq1 = await queue.enqueue_new({"id": "a", "task_id": "a"})
    seq2 = await queue.enqueue_new({"id": "b", "task_id": "b"})
    seq3 = await queue.enqueue_new({"id": "c", "task_id": "c"})
    assert seq1 < seq2 < seq3
    assert seq1 == 1.0
    assert seq3 == 3.0


async def test_retry_jumps_ahead_of_new(queue: RetryQueue):
    """Job retry phải lên trước job mới trong heap."""
    await queue.enqueue_new({"id": "a", "task_id": "a"})
    await queue.enqueue_new({"id": "b", "task_id": "b"})
    await queue.enqueue_new({"id": "c", "task_id": "c"})
    retry_seq = await queue.enqueue_retry({"id": "a", "task_id": "a"})
    assert retry_seq < 0, f"retry seq phải âm, got {retry_seq}"
    first = await queue.pull(timeout=0.1)
    assert first is not None
    assert first[1]["id"] == "a", f"expected 'a', got {first[1]['id']}"
    second = await queue.pull(timeout=0.1)
    assert second[1]["id"] == "b"
    third = await queue.pull(timeout=0.1)
    assert third[1]["id"] == "c"


async def test_cancel_marks_lazy_delete(queue: RetryQueue):
    """Cancel phải lazy-delete (job không pull ra)."""
    await queue.enqueue_new({"id": "x", "task_id": "x"})
    await queue.enqueue_new({"id": "y", "task_id": "y"})
    ok = await queue.cancel("x")
    assert ok is True
    first = await queue.pull(timeout=0.1)
    assert first is not None
    assert first[1]["id"] == "y"
    ok2 = await queue.cancel("nonexistent")
    assert ok2 is False


async def test_pull_blocks_until_enqueue(queue: RetryQueue):
    """Pull với timeout=None phải block cho tới khi có job."""
    async def _pull_later():
        await asyncio.sleep(0.05)
        return await queue.pull(timeout=1.0)
    async def _enqueue_later():
        await asyncio.sleep(0.1)
        return await queue.enqueue_new({"id": "late", "task_id": "late"})
    pull_task = asyncio.create_task(_pull_later())
    enqueue_task = asyncio.create_task(_enqueue_later())
    seq = await enqueue_task
    pulled = await pull_task
    assert pulled[1]["id"] == "late"
    assert seq == 1.0


async def test_pull_timeout_returns_none(queue: RetryQueue):
    """Pull với timeout=0.05 khi queue trống → None."""
    result = await queue.pull(timeout=0.05)
    assert result is None


async def test_stats_track_operations(queue: RetryQueue):
    """Stats phải đếm đúng."""
    await queue.enqueue_new({"id": "1", "task_id": "1"})
    await queue.enqueue_new({"id": "2", "task_id": "2"})
    await queue.enqueue_retry({"id": "3", "task_id": "3"})
    await queue.pull(timeout=0.1)
    await queue.pull(timeout=0.1)
    s = queue.stats()
    assert s["enqueued_new"] == 2
    assert s["enqueued_retry"] == 1
    assert s["pulled"] == 2
    assert s["current_depth"] == 1
    assert s["name"] == "test"


async def test_admin_retry_higher_priority(queue: RetryQueue):
    """Admin retry (bonus_priority=-1) phải cao hơn retry thường."""
    await queue.enqueue_retry(
        {"id": "normal", "task_id": "normal"}, bonus_priority=0
    )
    await queue.enqueue_retry(
        {"id": "admin", "task_id": "admin"}, bonus_priority=-1
    )
    first = await queue.pull(timeout=0.1)
    second = await queue.pull(timeout=0.1)
    assert first[1]["id"] == "admin"
    assert second[1]["id"] == "normal"


async def test_qsize_peek(queue: RetryQueue):
    """qsize() trả số job trong queue (peek, non-blocking)."""
    assert queue.qsize() == 0
    await queue.enqueue_new({"id": "a", "task_id": "a"})
    await queue.enqueue_new({"id": "b", "task_id": "b"})
    assert queue.qsize() == 2
    await queue.pull(timeout=0.1)
    assert queue.qsize() == 1


def test_singleton():
    """get_default() phải trả về cùng instance."""
    import retry_queue as rq
    rq.reset_default()
    q1 = rq.get_default()
    q2 = rq.get_default()
    assert q1 is q2
    rq.reset_default()


def test_singleton_after_reset():
    """reset_default() phải tạo instance mới."""
    import retry_queue as rq
    rq.reset_default()
    q1 = rq.get_default()
    rq.reset_default()
    q2 = rq.get_default()
    assert q1 is not q2
    rq.reset_default()


async def test_enqueue_new_rejects_when_full():
    """max_size trước đây chỉ nằm trong chữ ký: hàng đợi phình vô hạn khi nộp dồn."""
    q = RetryQueue(name="cap", max_size=2)
    await q.enqueue_new({"id": "a"})
    await q.enqueue_new({"id": "b"})
    with pytest.raises(asyncio.QueueFull):
        await q.enqueue_new({"id": "c"})
    assert q.qsize() == 2 and "c" not in q._by_id


async def test_retry_still_accepted_when_full():
    """Retry là job ĐÃ có trong hệ thống (vừa dính 710022002), không phải việc mới — không được đánh rơi."""
    q = RetryQueue(name="cap", max_size=1)
    await q.enqueue_new({"id": "a"})
    await q.enqueue_retry({"id": "a"})
    first = await q.pull(timeout=0.1)
    assert first is not None and first[1]["id"] == "a"
