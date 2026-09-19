"""PriorityQueue retry cho job: job retry đẩy lên đầu hàng đợi.

Học từ đối thủ Seedance AI Studio v1.0.93 (job_manager._xep_hang):
  - Mỗi job có sequence number đơn điệu tăng
  - PriorityQueue sắp xếp theo sequence → FIFO tuyệt đối
  - Job retry được sequence MỚI (cao hơn) → đẩy lên đầu
  - Job retry có priority cao hơn job mới (sequence âm cho retry)

Mục đích: giảm 710022002 — job retry sau khi Dola reset IP bucket (60-300s) chạy NGAY
không phải xếp sau job mới đang chờ.

Public API:
    RetryQueue.enqueue_new(job) → seq (tăng dần)
    RetryQueue.enqueue_retry(job) → seq âm (ưu tiên cao nhất)
    RetryQueue.pull() → (seq, job) | None (blocking pull với timeout)
    RetryQueue.cancel(job_id) → bool
    RetryQueue.stats() → dict (queue depth, retry counts)
"""
from __future__ import annotations

import asyncio
import heapq
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(order=True)
class _PriorityItem:
    """Item trong heap: sequence + job. Có order=True để heap so sánh được."""
    seq: float
    enqueued_at: float = field(compare=False)
    job: dict[str, Any] = field(compare=False)


class RetryQueue:
    """Hàng đợi ưu tiên với 3 mức:

    1. NEW (seq > 0): job mới nộp → sequence tăng dần (FIFO)
    2. RETRY (seq < 0): job retry sau 710022002 → seq ÂM (đẩy lên đầu)
    3. PRIORITY (seq = -1e18): admin inject → chạy ngay lập tức

    Ví dụ flow:
      t=0s: enqueue_new(jobA) → seq=1
      t=1s: enqueue_new(jobB) → seq=2
      t=2s: jobA dính 710022002 → enqueue_retry(jobA) → seq=-1
      t=2.001s: pull() → jobA (seq=-1 < 2)
      t=2.5s: pull() → jobB (seq=2)
    """

    def __init__(self, name: str = "default", max_size: int = 1000) -> None:
        self.name = name
        self.max_size = max_size
        self._heap: list[_PriorityItem] = []
        self._lock = asyncio.Lock()
        self._cond = asyncio.Condition(self._lock)
        self._seq_counter = 0
        # Map job_id → index trong heap; để cancel chính xác
        self._by_id: dict[str, _PriorityItem] = {}
        # Stats
        self._stats = {
            "enqueued_new": 0,
            "enqueued_retry": 0,
            "pulled": 0,
            "cancelled": 0,
            "current_depth": 0,
            "max_depth": 0,
        }

    def stats(self) -> dict:
        return {**self._stats, "name": self.name}

    async def enqueue_new(self, job: dict[str, Any]) -> float:
        """Đẩy job mới vào cuối hàng. Trả về sequence. Đầy (max_size) → asyncio.QueueFull để bên nộp từ chối/đợi.

        Chỉ chặn job MỚI: retry là job đã có trong hệ thống, đánh rơi nó = mất việc người dùng đã nộp.
        """
        async with self._cond:
            if len(self._heap) >= self.max_size:
                raise asyncio.QueueFull(f"{self.name}: hàng đợi đầy ({self.max_size} job)")
            self._seq_counter += 1
            seq = float(self._seq_counter)
            item = _PriorityItem(seq=seq, enqueued_at=time.time(), job=job)
            heapq.heappush(self._heap, item)
            jid = job.get("id") or job.get("task_id") or ""
            if jid:
                self._by_id[jid] = item
            self._stats["enqueued_new"] += 1
            self._stats["current_depth"] = len(self._heap)
            self._stats["max_depth"] = max(
                self._stats["max_depth"], self._stats["current_depth"]
            )
            self._cond.notify_all()
            return seq

    async def enqueue_retry(self, job: dict[str, Any], bonus_priority: int = 0) -> float:
        """Đẩy job retry lên đầu hàng. Seq ÂM + bonus càng nhỏ càng ưu tiên cao.

        bonus_priority=-1 → rất cao (admin retry)
        bonus_priority=0 → cao (retry bình thường)
        bonus_priority=+1 → trung bình (retry sau nhiều lần)

        Nếu job đã có trong queue (cùng id) → cancel cái cũ, push cái mới.
        Tránh double-pull khi admin retry job vẫn đang chờ.
        """
        async with self._cond:
            self._stats["enqueued_retry"] += 1
            # Seq âm để đẩy lên đầu heap
            seq = -float(self._stats["enqueued_retry"]) - (bonus_priority * 0.1)
            item = _PriorityItem(seq=seq, enqueued_at=time.time(), job=job)
            heapq.heappush(self._heap, item)
            jid = job.get("id") or job.get("task_id") or ""
            if jid:
                # Cancel job cũ cùng id (lazy delete khi pull)
                old = self._by_id.pop(jid, None)
                if old is not None:
                    old.job["_cancelled"] = True
                self._by_id[jid] = item
            self._stats["current_depth"] = len(self._heap)
            self._cond.notify_all()
            return seq

    async def pull(self, timeout: float | None = None) -> tuple[float, dict] | None:
        """Lấy job có priority cao nhất (chờ nếu trống). Trả None nếu timeout."""
        deadline = time.time() + timeout if timeout else None
        async with self._cond:
            while not self._heap:
                if deadline is None:
                    await self._cond.wait()
                else:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return None
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        return None
            item = heapq.heappop(self._heap)
            # Lazy delete: bỏ qua job đã bị cancel
            while item.job.get("_cancelled"):
                if not self._heap:
                    return None
                item = heapq.heappop(self._heap)
            self._stats["pulled"] += 1
            self._stats["current_depth"] = len(self._heap)
            jid = item.job.get("id") or item.job.get("task_id") or ""
            if jid:
                self._by_id.pop(jid, None)
            return (item.seq, item.job)

    async def cancel(self, job_id: str) -> bool:
        """Huỷ job theo id. Đánh dấu 'cancelled' trong heap (lazy delete khi pull)."""
        async with self._cond:
            item = self._by_id.pop(job_id, None)
            if not item:
                return False
            item.job["_cancelled"] = True
            self._stats["cancelled"] += 1
            return True

    def qsize(self) -> int:
        """Số job đang chờ (peek, không blocking)."""
        return len(self._heap)

    async def requeue_pending(self) -> int:
        """Khởi động lại: đẩy hết job cancelled ra khỏi heap. Trả về số job đã dọn."""
        async with self._cond:
            new_heap = []
            cancelled = 0
            for item in self._heap:
                if item.job.get("_cancelled"):
                    cancelled += 1
                else:
                    new_heap.append(item)
            heapq.heapify(new_heap)
            self._heap = new_heap
            self._stats["current_depth"] = len(self._heap)
            return cancelled


# === Singleton ===
_default_queue: RetryQueue | None = None


def get_default() -> RetryQueue:
    global _default_queue
    if _default_queue is None:
        _default_queue = RetryQueue(name="jobs")
    return _default_queue


def reset_default() -> None:
    """Test helper: xoá singleton."""
    global _default_queue
    _default_queue = None
