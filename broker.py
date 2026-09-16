"""Broker nội bộ kiểu Seedance Studio — CHO CÔNG TY DÙNG, không có license.

Đặt gateway thành một "broker" có đúng hình dạng endpoint của đối thủ (api.seedancevip.top) nhưng:
  - KHÔNG kích hoạt license (nội bộ, ai trong công ty gọi cũng được).
  - Chống client giả bằng "grant" ký HMAC-SHA256 (thư viện chuẩn, không cần Ed25519/cài thêm gì):
    chỉ broker mới cấp grant được, client không tự bịa grant hợp lệ để gọi /v1/job.
  - Tạo video đi qua pool nick sẵn có (chuyển sang API trả phí sau này chỉ cần đổi ctx.create_video).

Endpoint (mount vào server.py):
  GET  /v1/pubkey          -> {ok, key}     mã khoá (vân tay HMAC) để client biết khi khoá đổi
  POST /v1/job-grant       -> {ok, grant}   cấp vé job có hạn; header X-Broker-Key nếu đặt BROKER_KEY
  POST /v1/job             -> {ok, job}     header X-Grant; body {model, duration, ratio, prompt, account?}
  GET  /v1/job/{id}        -> {ok, status, video_url, ...}   header X-Grant
  POST /v1/job/downloaded  -> {ok, remaining}  body {job}; trừ credit nếu grant có giới hạn

Tự kiểm: python broker.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

GRANT_TTL_SEC = int(os.getenv("BROKER_GRANT_TTL", "3600"))   # vé job sống 1 giờ mặc định
_spent: dict[str, int] = {}                                  # client -> số job ĐÃ TẠO (Dola trừ lượt lúc nhận lệnh)
# ponytail: set chỉ lớn dần, xoá khi khởi động lại (như _spent vốn đã vậy — credit grant là hạn ngắn hạn).
# Vài nghìn job mới đáng kể. Cần bền qua restart thì đưa cả hai vào store.py.
_counted: dict[str, set] = {}                                # client -> job id đã tính, để không trừ trùng


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _secret() -> bytes:
    """Khoá ký HMAC: lấy từ BROKER_SECRET, thiếu thì sinh một lần và lưu cạnh dữ liệu."""
    env = os.getenv("BROKER_SECRET", "").strip()
    if env:
        return env.encode()
    data_dir = Path(os.getenv("DOLA_DATA_DIR") or os.getenv("DOLA_ACCOUNTS_DIR", ".")).parent
    f = data_dir / "broker_secret"
    try:
        if f.exists():
            return f.read_bytes()
        sec = os.urandom(32)
        f.write_bytes(sec)
        return sec
    except OSError:
        return b"dev-broker-secret-change-me"     # chỉ khi không ghi được đĩa (dev)


def key_id() -> str:
    """Vân tay khoá (không lộ khoá) — client so để biết broker đã đổi khoá."""
    return hashlib.sha256(b"kid" + _secret()).hexdigest()[:16]


def sign_grant(client: str, credits: int | None = None, ttl: int = GRANT_TTL_SEC, now: float | None = None) -> str:
    """Grant = base64(payload).base64(hmac). Chỉ ai có khoá (broker) mới ký được."""
    now = time.time() if now is None else now
    payload = {"client": client, "exp": int(now + ttl)}
    if credits is not None:
        payload["credits"] = int(credits)
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_grant(token: str, now: float | None = None) -> dict[str, Any]:
    """Trả payload nếu grant hợp lệ và chưa hết hạn; ném HTTPException nếu giả/hết hạn."""
    now = time.time() if now is None else now
    try:
        body, sig = token.split(".", 1)
    except (ValueError, AttributeError):
        raise HTTPException(401, "grant sai định dạng")
    want = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(want, sig):        # chống client giả: chữ ký sai = từ chối
        raise HTTPException(401, "grant không hợp lệ (chữ ký sai)")
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(401, "grant hỏng")
    if not isinstance(payload, dict):
        raise HTTPException(401, "grant hỏng")
    if float(payload.get("exp", 0)) < now:
        raise HTTPException(401, "grant hết hạn — xin /v1/job-grant mới")
    return payload


class GrantRequest(BaseModel):
    client: str = Field(..., min_length=1, max_length=64)
    credits: int | None = Field(None, ge=1)
    ttl: int | None = Field(None, ge=60, le=86400)


class JobRequest(BaseModel):
    model: str = "seedance-2.0"
    prompt: str = Field(..., min_length=1)
    duration: int | None = Field(None, ge=4, le=30)
    ratio: str | None = None
    account: str | None = None
    # Khóa chống tạo trùng: client gửi lại cùng khóa (hết giờ chờ, mạng đứt, bấm 2 lần) → trả job cũ thay vì
    # tạo job thứ hai. Dola trừ lượt lúc nhận lệnh nên tạo trùng = mất lượt 2 lần. Bỏ trống = không chống được.
    client_id: str | None = Field(default=None, max_length=48, pattern=r"^[A-Za-z0-9_.:-]+$")


class DownloadedRequest(BaseModel):
    job: str = Field(..., min_length=1)


def make_router(ctx: dict[str, Callable]) -> APIRouter:
    """ctx cần: create_video(VideoGenRequest, authorization) -> TaskResponse; get_video(id, authorization);
    VideoGenRequest (lớp Pydantic của gateway). Bearer nội bộ lấy từ DOLA_API_KEYS đầu tiên (nếu có)."""
    create_video = ctx["create_video"]
    get_video = ctx["get_video"]
    VideoGenRequest = ctx["VideoGenRequest"]
    r = APIRouter()

    def _bearer() -> str | None:
        keys = [k.strip() for k in os.getenv("DOLA_API_KEYS", "").split(",") if k.strip()]
        return f"Bearer {keys[0]}" if keys else None

    def _check_broker_key(x_broker_key: str | None) -> None:
        want = os.getenv("BROKER_KEY", "").strip()      # đặt BROKER_KEY để chỉ máy trong công ty xin được grant
        if want and (x_broker_key or "") != want:
            raise HTTPException(401, "thiếu/sai X-Broker-Key")

    @r.get("/v1/pubkey")
    async def pubkey():
        return {"ok": True, "key": key_id(), "alg": "HMAC-SHA256"}

    @r.post("/v1/job-grant")
    async def job_grant(body: GrantRequest, x_broker_key: str | None = Header(default=None)):
        _check_broker_key(x_broker_key)
        ttl = body.ttl or GRANT_TTL_SEC
        grant = sign_grant(body.client, body.credits, ttl)
        return {"ok": True, "grant": grant, "exp": int(time.time() + ttl), "client": body.client}

    @r.post("/v1/job")
    async def job(body: JobRequest, x_grant: str | None = Header(default=None)):
        g = verify_grant(x_grant or "")
        client = g["client"]
        grant_id = f"{client}:{g.get('exp', '')}"
        if g.get("credits") is not None and _spent.get(grant_id, 0) >= g["credits"]:
            raise HTTPException(402, "hết credit của grant — xin grant mới")
        # Khóa phải kèm tên client: hai client khác nhau dùng trùng chuỗi "job-1" không được thấy job của nhau.
        cid = f"{client}:{body.client_id}" if body.client_id else None
        req = VideoGenRequest(model=body.model, prompt=body.prompt, duration=body.duration,
                              ratio=body.ratio, account=body.account, client_id=cid)
        resp = await create_video(req, _bearer())      # dùng lại đường tạo video sẵn có (pool nick)
        # Trừ credit NGAY khi tạo job, không đợi /downloaded: Dola trừ lượt lúc nhận lệnh, nên client không gọi
        # /downloaded vẫn phải tiêu credit — nếu không, một grant "10 credit" gửi được vô số job.
        seen = _counted.setdefault(grant_id, set())
        if resp.id not in seen:                        # job cũ trả về do trùng khóa → không trừ lần nữa
            seen.add(resp.id)
            _spent[grant_id] = _spent.get(grant_id, 0) + 1
        remaining = None if g.get("credits") is None else max(0, g["credits"] - _spent.get(grant_id, 0))
        return {"ok": True, "job": resp.id, "status": resp.status, "client": client, "remaining": remaining}

    @r.get("/v1/job/{job_id}")
    async def job_status(job_id: str, x_grant: str | None = Header(default=None)):
        verify_grant(x_grant or "")
        resp = await get_video(job_id, _bearer())
        return {"ok": True, "job": resp.id, "status": resp.status, "stage": getattr(resp, "stage", None),
                "video_url": resp.video_url, "error": resp.error}

    @r.post("/v1/job/downloaded")
    async def job_downloaded(body: DownloadedRequest, x_grant: str | None = Header(default=None)):
        g = verify_grant(x_grant or "")
        client = g["client"]
        # Credit đã trừ lúc TẠO job (Dola tính tiền ở đó). Endpoint này giữ lại cho client cũ, chỉ báo số dư —
        # cộng thêm ở đây sẽ trừ 2 lần cho 1 video.
        grant_id = f"{client}:{g.get('exp', '')}"
        remaining = None if g.get("credits") is None else max(0, g["credits"] - _spent.get(grant_id, 0))
        return {"ok": True, "remaining": remaining}

    return r


def demo() -> None:
    """Tự kiểm không cần mạng: ký→kiểm khớp, sửa chữ ký→từ chối, hết hạn→từ chối."""
    t = sign_grant("cty-app-1", credits=5, ttl=3600)
    p = verify_grant(t)
    assert p["client"] == "cty-app-1" and p["credits"] == 5, p

    body, sig = t.split(".", 1)
    forged = body + "." + sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB")
    try:
        verify_grant(forged); assert False, "chữ ký giả phải bị từ chối"
    except HTTPException as e:
        assert e.status_code == 401, e.status_code

    # đổi nội dung (thêm credits) mà giữ chữ ký cũ → hỏng chữ ký → từ chối (chống client sửa grant)
    tampered = _b64(b'{"client":"x","exp":9999999999,"credits":999}') + "." + sig
    try:
        verify_grant(tampered); assert False, "grant sửa ruột phải bị từ chối"
    except HTTPException:
        pass

    expired = sign_grant("cty-app-1", ttl=1, now=0.0)   # exp = 1 (1970)
    try:
        verify_grant(expired); assert False, "grant hết hạn phải bị từ chối"
    except HTTPException as e:
        assert e.status_code == 401

    assert key_id() == key_id() and len(key_id()) == 16
    print("broker demo: OK")


if __name__ == "__main__":
    demo()
