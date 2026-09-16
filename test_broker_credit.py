"""Broker /v1/job: khóa chống tạo trùng + credit của grant phải chặn ngay lúc TẠO job.

Hai lỗi được khoá ở đây (đều làm mất lượt Dola thật):
  1. Trước đây JobRequest không có client_id → client gửi lại (hết giờ chờ, mạng đứt, bấm 2 lần) tạo job THỨ HAI
     cho cùng một video → nick bị trừ lượt 2 lần.
  2. Credit của grant chỉ bị trừ ở /v1/job/downloaded → client không bao giờ gọi /downloaded thì gửi được VÔ SỐ
     job với một grant "2 credit". Dola trừ lượt lúc nhận lệnh, nên phải chặn ngay lúc tạo job.
"""
import asyncio

from fastapi import HTTPException
from pydantic import BaseModel

import broker


class _VideoGenRequest(BaseModel):
    """Bản rút gọn của VideoGenRequest trong server.py (chỉ các trường broker dùng)."""

    model: str = "x"
    prompt: str = ""
    duration: int | None = None
    ratio: str | None = None
    account: str | None = None
    client_id: str | None = None


class _Resp:
    def __init__(self, job_id):
        self.id = job_id
        self.status = "queued"
        self.video_url = None
        self.error = None


def _harness():
    """Trả (hàm /v1/job, hàm /v1/job/downloaded, danh sách job THẬT đã tạo)."""
    created = []
    by_key = {}

    async def create_video(req, auth):
        # Giả đúng hành vi server.py: cùng client_id mà job cũ còn sống thì trả job cũ
        if req.client_id and req.client_id in by_key:
            return _Resp(by_key[req.client_id])
        job_id = f"job{len(created) + 1}"
        created.append(req)
        if req.client_id:
            by_key[req.client_id] = job_id
        return _Resp(job_id)

    async def get_video(job_id, auth):
        return _Resp(job_id)

    router = broker.make_router({"create_video": create_video, "get_video": get_video,
                                 "VideoGenRequest": _VideoGenRequest})
    job = next(r.endpoint for r in router.routes if r.path == "/v1/job" and "POST" in r.methods)
    downloaded = next(r.endpoint for r in router.routes if r.path == "/v1/job/downloaded")
    broker._spent.clear()
    broker._counted.clear()
    return job, downloaded, created


def test_same_client_id_reuses_job_and_charges_once():
    job, _, created = _harness()
    grant = broker.sign_grant("acme", 5, 3600)
    first = asyncio.run(job(broker.JobRequest(prompt="p", client_id="req-1"), grant))
    again = asyncio.run(job(broker.JobRequest(prompt="p", client_id="req-1"), grant))
    assert first["job"] == again["job"], "gửi lại cùng khóa phải trả job cũ"
    assert len(created) == 1, f"không được tạo job thứ hai, đã tạo {len(created)}"
    # _spent giờ khoá theo grant_id = "client:exp" (BUG-02 fix)
    assert sum(broker._spent.values()) == 1, f"chỉ trừ 1 credit, got {broker._spent}"


def test_credits_gate_at_creation_not_at_download():
    job, downloaded, _ = _harness()
    grant = broker.sign_grant("acme", 2, 3600)
    asyncio.run(job(broker.JobRequest(prompt="p", client_id="req-1"), grant))
    second = asyncio.run(job(broker.JobRequest(prompt="p", client_id="req-2"), grant))
    assert second["remaining"] == 0
    # Hết credit: phải chặn dù client CHƯA từng gọi /downloaded lần nào
    try:
        asyncio.run(job(broker.JobRequest(prompt="p", client_id="req-3"), grant))
        raise AssertionError("phải chặn 402 khi hết credit")
    except HTTPException as exc:
        assert exc.status_code == 402
    # /downloaded chỉ báo số dư, không được cộng trùng
    assert asyncio.run(downloaded(broker.DownloadedRequest(job="job1"), grant))["remaining"] == 0
    assert sum(broker._spent.values()) == 2


def test_client_id_is_namespaced_per_client():
    job, _, created = _harness()
    asyncio.run(job(broker.JobRequest(prompt="p", client_id="req-1"), broker.sign_grant("acme", 5, 3600)))
    other = asyncio.run(job(broker.JobRequest(prompt="p", client_id="req-1"), broker.sign_grant("other", 5, 3600)))
    assert other["job"] != "job1", "hai client dùng trùng chuỗi khóa không được thấy job của nhau"
    assert len(created) == 2


if __name__ == "__main__":
    test_same_client_id_reuses_job_and_charges_once()
    test_credits_gate_at_creation_not_at_download()
    test_client_id_is_namespaced_per_client()
    print("OK")
