"""Dola Pool: OpenAI-compatible Video API (FastAPI) and Admin Dashboard.

Endpoints (Asynchronous 2-stage):
POST /v1/videos/generations -> Create task (status=queued)
GET  /v1/videos/<id>         -> Query task status (queued/processing/completed/failed)
GET  /videos/<file>          -> Static video download server

Admin Dashboard: GET / -> web/index.html; Admin API /api/admin/*
"""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import re
import shutil
import time
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config

# ── Ghi mọi log ra logs/gateway.log dù khởi động kiểu nào (python server.py hoặc uvicorn server:app).
#    Panel "📄 Log" trong app đọc file này. Có dấu thời gian mỗi dòng, tự cắt vòng ở 5MB.
import os as _os, sys as _sys, time as _time

class _Tee:
    def __init__(self, stream, fh):
        self._stream = stream; self._fh = fh; self._buf = ""
    def write(self, text):
        try: self._stream.write(text)
        except Exception: pass
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            try: self._fh.write(f"[{_time.strftime('%H:%M:%S')}] {line}\n")
            except Exception: pass
    def flush(self):
        try: self._stream.flush()
        except Exception: pass
    def __getattr__(self, k): return getattr(self._stream, k)

def _setup_file_log():
    if getattr(_setup_file_log, "_done", False): return
    _setup_file_log._done = True
    try:
        logs = Path("logs"); logs.mkdir(exist_ok=True)
        f = logs / "gateway.log"
        try:
            if f.exists() and f.stat().st_size > 5 * 1024 * 1024: f.replace(logs / "gateway.log.1")
        except OSError: pass
        fh = open(f, "a", encoding="utf-8", buffering=1)
        fh.write(f"\n[{_time.strftime('%H:%M:%S')}] ===== gateway (pid {_os.getpid()}) khởi động =====\n")
        _sys.stdout = _Tee(_sys.stdout, fh)
        _sys.stderr = _Tee(_sys.stderr, fh)
    except Exception:
        pass

_setup_file_log()
from add_account import add_account_flow
from browser_pool import (AllAccountsLimitedError, AllAccountsQuotaBlockedError, BrowserPool,
                          MAX_BROWSER_SLOTS, resize_semaphore)
from cookie_service import apply_cookies_to_account, clear_account_cookies
from media import download_reference_images, validate_reference_urls
from store import PendingTaskLimitExceeded, TaskQuotaExceeded, TaskStore

Path(config.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
WEB_DIR = Path(__file__).resolve().parent / "web"   # cwd = thư mục dữ liệu, web/ nằm cạnh mã nguồn
WEB_DIR.mkdir(parents=True, exist_ok=True)

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
if not config.ADMIN_KEY and config.HOST not in _LOOPBACK_HOSTS:
    raise RuntimeError(
        "DOLA_ADMIN_KEY is empty while DOLA_HOST is non-loopback. All /api/admin/* routes "
        "would be unauthenticated, including POST /api/admin/accounts (accepts account "
        "email/password/TOTP) and API key management. Set DOLA_ADMIN_KEY, or set "
        "DOLA_HOST=127.0.0.1 for local-only use."
    )

app = FastAPI(title="dola-pool", version="0.4.0")

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = TaskStore(config.DB_PATH)
pool = BrowserPool(max_concurrency=config.MAX_CONCURRENCY, accounts_dir=str(config.ACCOUNTS_DIR))

app.mount("/videos", StaticFiles(directory=config.DOWNLOAD_DIR), name="videos")

# Background jobs (add/verify), in-memory
JOBS: dict[str, dict] = {}

SIZE_TO_RATIO = {
    "1280x720": "16:9", "1920x1080": "16:9",
    "720x1280": "9:16", "1080x1920": "9:16",
    "1024x1024": "1:1", "1440x1080": "4:3", "1080x1440": "3:4",
}
SUPPORTED_DURATIONS = (10, 15, 30)
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class KeyConcurrencyLimiter:
    """Concurrency limits per API Key; 0 = unlimited."""

    def __init__(self):
        self._condition = asyncio.Condition()
        self._active: defaultdict[str, int] = defaultdict(int)

    async def acquire(self, api_key_hash: str | None, limit: int):
        if not api_key_hash or limit <= 0:
            return
        async with self._condition:
            while self._active[api_key_hash] >= limit:
                await self._condition.wait()
            self._active[api_key_hash] += 1

    async def release(self, api_key_hash: str | None):
        if not api_key_hash:
            return
        async with self._condition:
            if self._active[api_key_hash] > 0:
                self._active[api_key_hash] -= 1
            if self._active[api_key_hash] == 0:
                self._active.pop(api_key_hash, None)
            self._condition.notify_all()



key_limiter = KeyConcurrencyLimiter()

# asyncio KHÔNG giữ tham chiếu tới task nền: task có thể bị GC giữa đường (cảnh báo trong tài
# liệu asyncio.create_task). Job video biến mất im lặng, dòng DB kẹt 'processing' → nick treo.
_BG_TASKS: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task

# Thêm/đăng nhập nhiều nick cùng lúc, nhưng có trần: mỗi nick là một Chrome thật.
MAX_LOGIN_SLOTS = 12
login_concurrency = config.LOGIN_CONCURRENCY
login_slots = asyncio.Semaphore(login_concurrency)


# ===== Authentication =====


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _anonymous_client() -> dict:
    return {
        "api_key_hash": None,
        "api_key_name": "Anonymous",
        "daily_limit": 0,
        "concurrency_limit": 0,
        "allowed_durations": list(SUPPORTED_DURATIONS),
    }


def _env_client(key: str) -> dict:
    return {
        "api_key_hash": _hash_key(key),
        "api_key_name": f"Env Key ({key[:8]}…)",
        "daily_limit": 0,
        "concurrency_limit": 0,
        "allowed_durations": list(SUPPORTED_DURATIONS),
    }


def _auth(authorization):
    """Returns client policy for caller; empty key enables dev mode."""
    if not config.API_KEYS and not store.has_enabled_keys():
        return _anonymous_client()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    key = authorization[7:].strip()
    if not key:
        raise HTTPException(401, "missing bearer token")
    if key in config.API_KEYS:
        return _env_client(key)
    record = store.get_key(key)
    if not record or not store.is_key_valid(key):
        raise HTTPException(401, "invalid api key")
    store.touch_key(key)
    return {
        "api_key_hash": _hash_key(key),
        "api_key_name": record["name"] or "Unnamed Client",
        "daily_limit": record["daily_limit"],
        "concurrency_limit": record["concurrency_limit"],
        "allowed_durations": record["allowed_durations"],
    }


def _admin_auth(x_admin_key: str | None):
    if not config.ADMIN_KEY:
        return
    if x_admin_key != config.ADMIN_KEY:
        raise HTTPException(401, "invalid admin key")


def _normalize_allowed_durations(values) -> list[int]:
    if values is None:
        return list(SUPPORTED_DURATIONS)
    try:
        normalized = sorted({int(value) for value in values})
    except (TypeError, ValueError):
        raise HTTPException(422, "allowed_durations must be an array of 10, 15, or 30")
    if not normalized or any(value not in SUPPORTED_DURATIONS for value in normalized):
        raise HTTPException(422, "allowed_durations must contain at least one of 10, 15, 30")
    return normalized


# ===== Client API =====


class VideoGenRequest(BaseModel):
    model: str = "seedance-2.0"
    prompt: str = Field(..., min_length=1)
    size: str | None = None
    ratio: str | None = None
    duration: int | None = Field(None, ge=10, le=30)
    # Accepts durations: 10, 15, 30 seconds.
    reference_images: list[str] = Field(default_factory=list)
    # Optional: force a specific nick. None = auto-pick/rotate across the pool.
    account: str | None = None


class TaskResponse(BaseModel):
    id: str
    status: str
    stage: str | None = None   # opening | submitting | rendering (chi tiết hơn "processing")
    model: str | None = None
    prompt: str | None = None
    video_url: str | None = None
    error: str | None = None


def _task_stage(row: dict) -> str:
    """Giai đoạn thật của job đang chạy — UI hiện "đang gửi" vs "Dola đang render"."""
    if row["status"] != "processing":
        return row["status"]
    if row.get("conversation_id"):
        return "rendering"      # Dola đã nhận việc, trình duyệt đã được trả lại
    if row.get("submitted_at"):
        return "submitting"
    return "opening"            # đang mở nick / kiểm tra điểm


def _resolve_ratio(size, ratio):
    if size and size in SIZE_TO_RATIO:
        return SIZE_TO_RATIO[size]
    return ratio


async def _run_task(task_id, model, prompt, ratio, duration, reference_images, client, account=None):
    api_key_hash = client.get("api_key_hash")
    acquired = False
    reference_root = None
    try:
        await key_limiter.acquire(api_key_hash, client.get("concurrency_limit", 0))
        acquired = True
        store.update(task_id, status="processing", started_at=time.time())

        def on_conversation_id(account, conversation_id, deadline_at):
            store.update(task_id, status="processing", account=account,
                         conversation_id=conversation_id, deadline_at=deadline_at,
                         last_poll_at=time.time())

        def on_poll(now):
            store.update(task_id, last_poll_at=now)

        def on_submitted(account, submitted: bool):
            # submitted=True: lệnh đã tới Dola (có thể đã trừ lượt) → restart KHÔNG được chạy lại.
            store.update(task_id, account=account,
                         submitted_at=time.time() if submitted else None)

        reference_root, reference_paths = await download_reference_images(
            reference_images or [], task_id)
        result = await pool.generate_video(
            prompt, ratio, duration, model,
            on_conversation_id=on_conversation_id, on_poll=on_poll,
            on_submitted=on_submitted,
            reference_image_paths=reference_paths, account=account)
        public_url = _public_video_url(result)
        store.update(task_id, status="completed", video_url=public_url,
                     account=result.get("account"), last_poll_at=time.time(),
                     finished_at=time.time())
    except (AllAccountsLimitedError, AllAccountsQuotaBlockedError) as e:
        store.update(task_id, status="failed", error=str(e)[:500],
                     failure_code="429", finished_at=time.time())
    except Exception as e:
        store.update(task_id, status="failed", error=str(e)[:500],
                     finished_at=time.time())
    finally:
        if reference_root:
            shutil.rmtree(reference_root, ignore_errors=True)
        if acquired:
            await key_limiter.release(api_key_hash)


def _public_video_url(result: dict) -> str:
    """File đã tải về → link /videos/ của gateway; tải hỏng (đã thử lại) → giữ link CDN Dola để tải tay."""
    if result.get("local_path"):
        return f"{config.PUBLIC_BASE}/videos/{Path(result['local_path']).name}"
    print(f"[gateway] video không tải về được, trả link Dola: {result.get('download_error')}", flush=True)
    return result["video_url"]


async def _resume_task(row: dict):
    task_id = row["id"]
    deadline = row.get("deadline_at") or (
        time.time() + (config.VIDEO_TIMEOUT_30S if row.get("duration") == 30 else config.VIDEO_TIMEOUT)
    )
    remaining = max(1, int(deadline - time.time()))
    api_key_hash = row.get("api_key_hash")
    acquired = False
    try:
        await key_limiter.acquire(
            api_key_hash, int(row.get("client_concurrency_limit") or 0)
        )
        acquired = True
        store.update(task_id, status="processing", last_poll_at=time.time(),
                     started_at=row.get("started_at") or time.time())

        def on_poll(now):
            store.update(task_id, last_poll_at=now)

        _rratio = row.get("ratio")
        if _rratio == "default":
            _rratio = None
        result = await pool.resume_video(
            row["account"], row["conversation_id"], remaining, on_poll=on_poll,
            ratio=_rratio, duration=row.get("duration"))
        public_url = _public_video_url(result)
        store.update(task_id, status="completed", video_url=public_url,
                     account=result.get("account"), last_poll_at=time.time(),
                     finished_at=time.time())
    except Exception as e:
        store.update(task_id, status="failed", error=str(e)[:500],
                     finished_at=time.time())
    finally:
        if acquired:
            await key_limiter.release(api_key_hash)


def _task_client(row: dict) -> dict:
    """Restores client context from task snapshot."""
    return {
        "api_key_hash": row.get("api_key_hash"),
        "api_key_name": row.get("api_key_name") or "Historical Task",
        "daily_limit": 0,
        "concurrency_limit": int(row.get("client_concurrency_limit") or 0),
        "allowed_durations": list(SUPPORTED_DURATIONS),
    }


def _task_reference_images(raw) -> list[str]:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return values if isinstance(values, list) else []


MAX_AUTO_REQUEUE = 2          # số lần tự chạy lại một job bị restart cắt ngang
STALE_REQUEUE_SEC = 6 * 3600  # job cũ hơn mức này thì không tự chạy lại nữa (prompt đã lỗi thời)
STALE_RESUME_SEC = 30 * 60  # job "processing" cũ hơn mức này khi khởi động = treo → bỏ; phải > VIDEO_TIMEOUT_30S (1500s) kẻo bỏ oan job 30s đang dựng


def _restart_action(row: dict, now: float) -> tuple[str, str | None]:
    """Quyết định cho job 'processing' mất conversation_id khi server khởi động lại.

    Chỉ tự chạy lại khi chắc chắn prompt CHƯA tới Dola (submitted_at rỗng) — nếu đã gửi thì
    chạy lại sẽ trừ lượt lần 2.
    """
    if row.get("submitted_at"):
        nick = row.get("account") or "nick đang dùng"
        return "failed", (
            f"Đã gửi prompt tới Dola rồi server mới tắt — không tự chạy lại để khỏi trừ lượt "
            f"2 lần. Kiểm tra {nick} trên dola.com; chưa có video thì bấm chạy lại.")
    if int(row.get("attempts") or 0) >= MAX_AUTO_REQUEUE:
        return "failed", (
            f"Đã tự chạy lại {MAX_AUTO_REQUEUE} lần sau khi server khởi động lại mà vẫn dở dang "
            f"— bấm chạy lại thủ công.")
    if now - (row.get("created_at") or now) > STALE_REQUEUE_SEC:
        return "failed", "Job quá cũ (server tắt lâu) — không tự chạy lại, bấm chạy lại nếu còn cần."
    return "requeue", None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Recovers accepted sessions on startup and requeues pending tasks."""
    now = time.time()
    recoverable_ids = set()
    for row in store.recoverable_tasks():
        recoverable_ids.add(row["id"])
        started = row.get("started_at") or 0
        deadline = row.get("deadline_at") or 0
        if (deadline and now > deadline) or (started and now - started > STALE_RESUME_SEC):
            store.update(row["id"], status="failed", finished_at=now,
                         error="Video treo quá lâu (Dola không trả kết quả) — đã bỏ để giải phóng nick.")
            continue
        _spawn(_resume_task(row))
    # Job 'processing' không có conversation_id: server tắt giữa lúc mở trình duyệt / gửi prompt.
    for row in store.orphan_processing_tasks(recoverable_ids):
        action, reason = _restart_action(row, now)
        if action == "requeue":
            store.requeue(row["id"])  # chưa gửi prompt → chạy lại an toàn, không mất lượt
            print(f"[recover] {row['id']} bị restart cắt ngang, tự xếp hàng chạy lại", flush=True)
        else:
            store.update(row["id"], status="failed", finished_at=now, error=reason)
    for row in store.recoverable_queued_tasks():
        ratio = row.get("ratio")
        if ratio == "default":
            ratio = None
        _spawn(_run_task(
            row["id"], row["model"], row["prompt"], ratio, row["duration"],
            _task_reference_images(row.get("reference_images")), _task_client(row),
        ))
    from browser import mask_proxy as _mask, probe_proxy
    print(f"[gateway] proxy chung: {_mask(config.PROXY) or '(không — nối thẳng)'}", flush=True)
    if config.PROXY:
        bad = await probe_proxy(config.PROXY)
        if bad:
            print(f"[gateway] ⚠ proxy chung {_mask(config.PROXY)} KHÔNG nối được ({bad}) — mọi nick không có "
                  "proxy riêng sẽ lỗi. Sửa hoặc xoá trống ở Cài đặt → Proxy chung rồi Tắt/Bật server.", flush=True)
    yield


app.router.lifespan_context = lifespan


@app.post("/v1/videos/generations", response_model=TaskResponse)
async def create_video(req: VideoGenRequest, authorization: str | None = Header(default=None)):
    client = _auth(authorization)
    duration = req.duration or 10
    if duration not in SUPPORTED_DURATIONS:
        raise HTTPException(422, "Currently supports durations of 10s, 15s, and 30s")
    if duration not in client["allowed_durations"]:
        raise HTTPException(422, f"Current API Key is not allowed to generate {duration}s videos")
    model_key = req.model.lower().replace("-", "_")
    if model_key not in (
        "seedance_2.0", "seedance_2.5", "seedance_v2.0", "seedance_v2.5",
        "seedance_20", "seedance_25", "seedance_v20", "seedance_v25",
    ):
        raise HTTPException(422, "Supported models are seedance-2.0 and seedance-2.5")
    try:
        reference_images = await validate_reference_urls(req.reference_images)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    account = req.account.strip() if req.account else None
    if account and account not in pool.accounts:
        raise HTTPException(422, f"Nick '{account}' không tồn tại trong pool")
    # Queue task when accounts are busy; reject only when pool is fully exhausted.
    if not pool.available and pool.all_accounts_limited:
        raise HTTPException(429, "Rate limited: All accounts reached Dola daily video limit, please try again tomorrow")
    if not pool.available and pool.all_accounts_quota_blocked:
        raise HTTPException(429, "Insufficient credits: All accounts lack points, waiting for refresh")
    if not pool.accounts:
        raise HTTPException(503, "no account in pool")
    task_id = "video_" + uuid.uuid4().hex
    ratio = _resolve_ratio(req.size, req.ratio)
    try:
        store.create(
            task_id,
            req.model,
            req.prompt,
            ratio or "default",
            duration,
            reference_images=json.dumps(reference_images, ensure_ascii=False),
            api_key_hash=client["api_key_hash"],
            api_key_name=client["api_key_name"],
            daily_limit=client["daily_limit"],
            concurrency_limit=client["concurrency_limit"],
            max_pending=config.MAX_PENDING_TASKS,
        )
    except TaskQuotaExceeded as exc:
        raise HTTPException(429, str(exc)) from exc
    except PendingTaskLimitExceeded as exc:
        raise HTTPException(429, str(exc)) from exc
    _spawn(_run_task(
        task_id, req.model, req.prompt, ratio, duration, reference_images, client, account
    ))
    return TaskResponse(id=task_id, status="queued", model=req.model, prompt=req.prompt)


@app.get("/v1/videos/{task_id}", response_model=TaskResponse)
async def get_video(task_id: str, authorization: str | None = Header(default=None)):
    client = _auth(authorization)
    row = store.get_for_client(task_id, client["api_key_hash"])
    if not row:
        raise HTTPException(404, "task not found")
    return TaskResponse(
        id=row["id"], status=row["status"], stage=_task_stage(row), model=row["model"],
        prompt=row["prompt"], video_url=row["video_url"], error=row["error"],
    )


# Broker nội bộ kiểu Seedance (không license) — /v1/pubkey · /v1/job-grant · /v1/job · /v1/job/{id} ·
# /v1/job/downloaded. Dùng lại đúng create_video/get_video ở trên (pool nick). Xem broker.py.
from broker import make_router as _make_broker_router
app.include_router(_make_broker_router({
    "create_video": create_video, "get_video": get_video, "VideoGenRequest": VideoGenRequest,
}))

# Kho proxy tập trung — /api/admin/proxies (thêm/liệt kê che mật khẩu/kiểm 8 luồng/chia nick). Xem proxy_pool.py.
from proxy_pool import PoolStore as _PoolStore, make_router as _make_proxy_router
from browser import set_account_proxy as _set_account_proxy
_proxy_store = _PoolStore(config.env_local_path().parent / "proxy_pool.json")
app.include_router(_make_proxy_router({
    "store": _proxy_store, "pool": pool, "set_account_proxy": _set_account_proxy, "admin_auth": _admin_auth,
}))


@app.get("/health")
async def health():
    return {
        "ok": True,
        "accounts": pool.account_status(),
        "available": pool.available,
        "pending_tasks": store.pending_task_count(),
        "max_pending_tasks": config.MAX_PENDING_TASKS,
        "max_concurrency": pool.max_concurrency,
        "login_concurrency": login_concurrency,
        "max_browser_slots": MAX_BROWSER_SLOTS,
        "max_login_slots": MAX_LOGIN_SLOTS,
        "http_poll": config.HTTP_POLL,
        "auto_retry": config.AUTO_RETRY,
    }


@app.get("/api/report")
async def report():
    """Báo cáo video tạo ra + trạng thái đầy đủ từng nick (gộp thống kê DB + trạng thái sống)."""
    rep = store.report()
    live = {a["account"]: a for a in pool.account_status()}
    LIVE_FIELDS = ("remaining", "used_today", "limit", "login_ok", "rate_limited", "quota_blocked")
    seen = set()
    for pa in rep["per_account"]:
        seen.add(pa["account"])
        a = live.get(pa["account"])
        if a:
            for k in LIVE_FIELDS:
                pa[k] = a.get(k)
    # nick chưa có video nào vẫn hiển thị (0 video)
    for name, a in live.items():
        if name not in seen:
            row = {"account": name, "total": 0, "failed": 0, "today": 0, "last_at": None, "avg_dur": None}
            for k in LIVE_FIELDS:
                row[k] = a.get(k)
            rep["per_account"].append(row)
    rep["per_account"].sort(key=lambda r: (-(r["total"] or 0), r["account"]))
    return rep


# ===== Admin Dashboard API =====


class AdminLogin(BaseModel):
    key: str


class AccountPatch(BaseModel):
    scheduling: bool | None = None
    note: str | None = None
    email: str | None = None


class AccountAdd(BaseModel):
    name: str
    email: str
    password: str
    totp: str


class AccountCookieImport(BaseModel):
    name: str
    cookies: str
    email: str | None = None
    ui_lang: str = "ja"
    proxy: str = ""   # proxy riêng, ghi TRƯỚC khi mở Chrome kiểm tra phiên (nick mới chưa có trên máy chủ)


class AccountFacebookAdd(BaseModel):
    name: str
    cookie_line: str
    note: str | None = None
    visible: bool = True   # show the Chrome window while logging in (debuggable)


class KeyCreate(BaseModel):
    name: str = ""
    daily_limit: int = Field(0, ge=0, le=1_000_000)
    concurrency_limit: int = Field(0, ge=0, le=1_000)
    allowed_durations: list[int] = Field(default_factory=lambda: list(SUPPORTED_DURATIONS))
    expires_at: float | None = Field(None, ge=0)


class KeyPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    daily_limit: int | None = Field(None, ge=0, le=1_000_000)
    concurrency_limit: int | None = Field(None, ge=0, le=1_000)
    allowed_durations: list[int] | None = None
    expires_at: float | None = Field(None, ge=0)


@app.post("/api/admin/login")
async def admin_login(body: AdminLogin):
    if not config.ADMIN_KEY:
        return {"ok": True, "auth_required": False}
    if body.key == config.ADMIN_KEY:
        return {"ok": True, "auth_required": True}
    raise HTTPException(401, "wrong admin key")


@app.get("/api/admin/accounts")
async def admin_accounts(x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    from browser import account_proxy_raw
    # Kèm proxy riêng để giao diện khỏi hỏi từng nick (46 nick × 4s = 46 yêu cầu mỗi vòng ở chế độ từ xa).
    return {"accounts": [{**a, "proxy": account_proxy_raw(a["name"])} for a in pool.list_accounts()]}


@app.get("/api/admin/config")
async def admin_config(x_admin_key: str | None = Header(default=None)):
    """Cấu hình đang chạy cho giao diện: máy chủ từ xa không đọc được .env.local trên VPS."""
    _admin_auth(x_admin_key)
    from browser import mask_proxy
    return {"proxy": mask_proxy(config.PROXY), "max_concurrency": pool.max_concurrency,
            "auto_retry": config.AUTO_RETRY, "submit_gap": config.SUBMIT_GAP_SEC,
            "submit_jitter": config.SUBMIT_JITTER_SEC, "max_rotate": config.MAX_ROTATE,
            "video_timeout": config.VIDEO_TIMEOUT, "daily_limit": config.DAILY_LIMIT}


@app.get("/api/admin/accounts/export")
async def admin_accounts_export(x_admin_key: str | None = Header(default=None)):
    """Gói mang nick sang máy khác: cookie đã lưu + proxy riêng + email/ghi chú/lịch.

    Máy nhận đưa từng nick qua import-cookie (nạp vào profile Chrome + kiểm tra phiên), không cần
    chép thư mục profile. File chứa phiên đăng nhập → giữ như mật khẩu.
    """
    _admin_auth(x_admin_key)
    from browser import account_proxy_raw
    out, skipped = [], []
    for a in pool.list_accounts():
        f = config.ACCOUNTS_DIR / a["name"] / "cookies.json"
        try:
            cookies = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        except (OSError, ValueError):
            cookies = []
        if not cookies:
            skipped.append(a["name"])   # chưa đăng nhập lần nào → không có gì để mang đi
            continue
        out.append({"name": a["name"], "cookies": cookies, "proxy": account_proxy_raw(a["name"]),
                    "email": a.get("email") or "", "note": a.get("note") or "",
                    "scheduling": bool(a.get("scheduling", True))})
    return {"kind": "dola-studio-accounts", "version": 1, "exported_at": time.time(),
            "accounts": out, "skipped": skipped}


@app.patch("/api/admin/accounts/{name}")
async def admin_account_patch(name: str, body: AccountPatch,
                              x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    if name not in pool.accounts:
        raise HTTPException(404, "account not found")
    if body.scheduling is not None:
        pool.set_scheduling(name, body.scheduling)
    if body.note is not None:
        pool.set_note(name, body.note)
    if body.email is not None:
        pool.set_email(name, body.email)
    return {"ok": True}


class AccountProxy(BaseModel):
    proxy: str = ""   # "" clears -> back to global DOLA_PROXY


@app.post("/api/admin/accounts/{name}/wake")
async def admin_account_wake(name: str, x_admin_key: str | None = Header(default=None)):
    """Bỏ 'đang nghỉ' (cooldown chống risk-control) để chạy lại ngay."""
    _admin_auth(x_admin_key)
    if name not in pool.accounts:
        raise HTTPException(404, "account not found")
    pool.clear_cooldown(name)
    return {"ok": True}


@app.post("/api/admin/accounts/{name}/proxy")
async def admin_account_proxy(name: str, body: AccountProxy, x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    if name not in pool.accounts:
        raise HTTPException(404, "account not found")
    from browser import set_account_proxy, parse_proxy
    if body.proxy.strip() and not parse_proxy(body.proxy):
        raise HTTPException(422, "proxy không hợp lệ (host:port, user:pass@host:port, hoặc host:port:user:pass)")
    set_account_proxy(name, body.proxy)
    return {"ok": True, "proxy": body.proxy.strip() or "(global)"}


@app.get("/api/admin/accounts/{name}/proxy")
async def admin_account_proxy_get(name: str, x_admin_key: str | None = Header(default=None)):
    """Proxy riêng của nick trên máy chủ — app ở chế độ server từ xa đọc qua đây thay vì file cục bộ."""
    _admin_auth(x_admin_key)
    if name not in pool.accounts:
        raise HTTPException(404, "account not found")
    from browser import account_proxy_raw
    return {"proxy": account_proxy_raw(name)}


@app.get("/api/admin/accounts/{name}/check-proxy")
async def admin_account_check_proxy(name: str, x_admin_key: str | None = Header(default=None)):
    """Kiểm tra proxy ĐÃ áp vào nick chưa: đi ĐÚNG proxy của nick (riêng, thiếu thì proxy chung) ra
    ngoài lấy IP thoát + thử vào dola.com. IP thoát khác IP máy chủ = proxy đã áp thật; tới được Dola
    = proxy dùng được. Chạy TỪ máy chủ (đúng nơi mở Chrome cho nick), nên đúng cả chế độ VPS từ xa."""
    _admin_auth(x_admin_key)
    if name not in pool.accounts:
        raise HTTPException(404, "account not found")
    from browser import account_proxy_url, account_proxy_raw
    import aiohttp
    raw = account_proxy_raw(name)
    url = account_proxy_url(name) or None      # proxy nick, thiếu thì rơi về proxy chung
    out = {"ok": False, "has_own": bool(raw), "via": "riêng" if raw else ("chung" if config.PROXY else "nối thẳng")}
    t0 = time.time()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get("https://api.ipify.org?format=json", proxy=url) as r:
                out["egress_ip"] = (await r.json()).get("ip")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get("https://www.dola.com/", proxy=url) as r:
                out["dola"] = r.status < 500
        out["ok"] = True
    except Exception as e:
        out["error"] = str(e)[:120]
    out["ms"] = int((time.time() - t0) * 1000)
    return out


@app.delete("/api/admin/accounts/{name}")
async def admin_account_delete(name: str, x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    if name not in pool.accounts:
        raise HTTPException(404, "account not found")
    try:
        pool.delete_account(name)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/api/admin/accounts/{name}/clear-cookies")
async def admin_account_clear_cookies(name: str, x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    if name not in pool.accounts:
        raise HTTPException(404, "account not found")
    try:
        pool.assert_idle(name)   # đừng xoá cookie khi nick đang render (sẽ giết video)
        res = await clear_account_cookies(name)
    except RuntimeError as e:  # đang render, hoặc profile đang mở trong cửa sổ Chromium
        raise HTTPException(409, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    pool.set_login_status(name, False)  # no cookies -> logged out; drop from scheduling
    return res


class VerifyRequest(BaseModel):
    names: list[str] | None = None   # None = kiểm tra tất cả


@app.post("/api/admin/verify-all")
async def admin_verify_all(body: VerifyRequest | None = None,
                           x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    return {"ok": True, "results": await pool.verify_all(body.names if body else None)}


@app.post("/api/admin/accounts/{name}/verify")
async def admin_account_verify(name: str, x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    try:
        ok = await pool.verify_account(name)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        message = str(e)
        if "ERR_PROXY_CONNECTION_FAILED" in message or "ERR_TUNNEL_CONNECTION_FAILED" in message:
            from browser import account_proxy_raw, mask_proxy as _mask
            via = _mask(account_proxy_raw(name) or config.PROXY)
            raise HTTPException(502, f"Không vào được dola.com: proxy {via} không nối được (tắt, sai cổng "
                                     "hoặc sai mật khẩu). Sửa hoặc xoá trống ở Cài đặt → Proxy chung "
                                     "(hoặc nút ⚙ của nick), rồi Tắt/Bật server.") from e
        raise HTTPException(502, f"Browser check failed: {message[:300]}") from e
    return {"ok": ok}


async def _run_add_job(name: str, email: str, password: str, totp: str):
    JOBS[name] = {"kind": "add", "status": "running", "step": "Đang chờ lượt mở trình duyệt…",
                  "error": "", "started_at": time.time()}
    try:
        async with login_slots:
            JOBS[name] = {**JOBS[name], "step": "Đang đăng nhập…"}
            await add_account_flow(name, email, password, totp)
        pool.set_email(name, email)
        pool.set_login_status(name, True)
        JOBS[name] = {**JOBS[name], "status": "success", "step": "Đăng nhập thành công!"}
    except Exception as e:
        JOBS[name] = {**JOBS[name], "status": "failed", "step": "Thất bại", "error": str(e)[:300]}


async def _run_facebook_add_job(name: str, cookie_line: str, note: str | None = None, visible: bool = True):
    JOBS[name] = {"kind": "add_facebook", "status": "running", "step": "Đang khởi chạy...", "error": "", "started_at": time.time()}
    from facebook_login import add_account_via_facebook

    def on_step(msg: str):
        if name in JOBS:
            JOBS[name]["step"] = msg

    try:
        if login_slots.locked():
            on_step("Đang xếp hàng chờ lượt mở trình duyệt…")
        async with login_slots:
            user_name = await add_account_via_facebook(name, cookie_line, on_step=on_step, visible=visible)
        display_label = user_name or note or name
        pool.set_email(name, display_label)
        pool.set_login_status(name, True)
        JOBS[name] = {**JOBS[name], "status": "success", "step": "Đăng nhập Dola qua Facebook thành công!"}
    except Exception as e:
        JOBS[name] = {**JOBS[name], "status": "failed", "step": "Thất bại", "error": str(e)[:300]}


@app.post("/api/admin/accounts", status_code=202)
async def admin_account_add(body: AccountAdd, x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    if not NAME_RE.match(body.name):
        raise HTTPException(400, "invalid account name")
    if body.name in pool.accounts:
        raise HTTPException(409, "account exists")
    if JOBS.get(body.name, {}).get("status") == "running":
        raise HTTPException(409, "add job running")
    _spawn(_run_add_job(body.name, body.email, body.password, body.totp))
    return {"ok": True, "job": "running"}


@app.post("/api/admin/accounts/add-facebook", status_code=202)
async def admin_account_add_facebook(body: AccountFacebookAdd, x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    name = body.name.strip()
    if not NAME_RE.match(name):
        raise HTTPException(400, "invalid account name (1-32 chars: letters, numbers, -, _)")
    if body.name in pool.accounts:
        raise HTTPException(409, "account exists")
    if JOBS.get(body.name, {}).get("status") == "running":
        raise HTTPException(409, "job already running for this account")
    _spawn(_run_facebook_add_job(name, body.cookie_line, body.note, body.visible))
    return {"ok": True, "job": "running"}


@app.post("/api/admin/accounts/import-cookie")
async def admin_account_import_cookie(body: AccountCookieImport, x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    name = body.name.strip()
    if not NAME_RE.match(name):
        raise HTTPException(400, "invalid account name (1-32 chars: letters, numbers, -, _)")
    from browser import parse_proxy
    if body.proxy.strip() and not parse_proxy(body.proxy):
        raise HTTPException(422, "proxy không hợp lệ (host:port, user:pass@host:port, hoặc host:port:user:pass)")
    try:
        res = await apply_cookies_to_account(name, body.cookies, ui_lang=body.ui_lang, proxy=body.proxy.strip())
        if body.email:
            pool.set_email(name, body.email.strip())
        pool.set_login_status(name, res["ok"])
        return res
    except Exception as e:
        raise HTTPException(400, f"Import cookie error: {str(e)}")


@app.post("/api/admin/accounts/{name}/open")
async def admin_account_open(name: str, x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    if name not in pool.accounts:
        raise HTTPException(404, "account not found")
    try:
        pool.assert_idle(name)   # mở profile khi đang render cũng giết video
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    import subprocess
    subprocess.Popen([_sys.executable, str(Path(__file__).resolve().with_name("login_profile.py")), name])   # `sys` chỉ được import là _sys → NameError 500
    return {"ok": True, "message": f"Opening browser for {name}..."}


class AutoRetryUpdate(BaseModel):
    auto_retry: bool


@app.post("/api/admin/retry")
async def admin_retry(body: AutoRetryUpdate, x_admin_key: str | None = Header(default=None)):
    """Bật/tắt tự thử lại + xoay nick ngay lúc chạy (không cần khởi động lại)."""
    _admin_auth(x_admin_key)
    config.AUTO_RETRY = body.auto_retry
    print(f"[gateway] tự thử lại / xoay nick: {'BẬT' if config.AUTO_RETRY else 'TẮT'}", flush=True)
    return {"ok": True, "auto_retry": config.AUTO_RETRY}


class GlobalProxyUpdate(BaseModel):
    proxy: str = ""


@app.post("/api/admin/global-proxy")
async def admin_set_global_proxy(body: GlobalProxyUpdate, x_admin_key: str | None = Header(default=None)):
    """Đặt proxy chung của server NGAY lúc chạy (không cần khởi động lại) và ghi vào .env.local.

    config.PROXY được browser/poll/tải video đọc lại mỗi lần chạy nên áp dụng cho job mới liền. Nhờ vậy
    app ở chế độ máy chủ từ xa đặt được proxy chung cho VPS thẳng từ giao diện."""
    _admin_auth(x_admin_key)
    from browser import parse_proxy
    v = (body.proxy or "").strip()
    if v and not parse_proxy(v):
        raise HTTPException(422, "proxy không hợp lệ (host:port, user:pass@host:port, hoặc host:port:user:pass)")
    config.PROXY = v
    config.upsert_env_local("DOLA_PROXY", v)
    print(f"[gateway] proxy chung đổi thành: {v or '(nối thẳng)'}", flush=True)
    return {"ok": True, "proxy": v or "(nối thẳng)"}


class ConcurrencyUpdate(BaseModel):
    max_concurrency: int | None = Field(None, ge=1, le=MAX_BROWSER_SLOTS)
    login_concurrency: int | None = Field(None, ge=1, le=MAX_LOGIN_SLOTS)


@app.post("/api/admin/concurrency")
async def admin_set_concurrency(body: ConcurrencyUpdate, x_admin_key: str | None = Header(default=None)):
    """Đổi số luồng ngay lúc đang chạy (job đang chạy không bị đụng tới)."""
    global login_concurrency
    if body.max_concurrency is not None:
        pool.set_max_concurrency(body.max_concurrency)
    if body.login_concurrency is not None:
        limit = max(1, min(body.login_concurrency, MAX_LOGIN_SLOTS))
        resize_semaphore(login_slots, limit - login_concurrency)
        login_concurrency = limit
    print(f"[config] luồng gửi={pool.max_concurrency} · luồng đăng nhập={login_concurrency}", flush=True)
    return {"ok": True, "max_concurrency": pool.max_concurrency, "login_concurrency": login_concurrency}


@app.get("/api/admin/jobs")
async def admin_jobs(x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    return {"jobs": JOBS}


@app.get("/api/admin/tasks")
async def admin_tasks(limit: int = 50, x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    return {"tasks": store.recent_tasks(min(max(limit, 1), 2000))}   # kho video cần xem xa hơn 200 job


@app.get("/api/admin/stats")
async def admin_stats(x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    st = store.stats()
    accs = pool.list_accounts()
    sched = [a for a in accs if a["scheduling"] and not a["cooling"]]
    st["total_accounts"] = len(accs)
    st["available_accounts"] = sum(1 for a in sched if a["remaining"] > 0)
    st["total_remaining"] = sum(a["remaining"] for a in sched)
    totals = st.pop("per_account_total", {})
    st["per_account"] = [{**a, "completed_total": totals.get(a["name"], 0)} for a in accs]
    return st


@app.get("/api/admin/keys")
async def admin_keys(x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    keys = []
    for key in store.list_keys():
        usage = store.key_usage(store.hash_api_key(key["key"]))
        keys.append({**key, **{
            "today_total": usage["total"],
            "today_completed": usage["completed"],
            "today_failed": usage["failed"],
            "today_active": usage["active"],
            "today_queued": usage["queued"],
        }})
    return {"keys": keys, "env_keys": len(config.API_KEYS)}


@app.post("/api/admin/keys")
async def admin_key_create(body: KeyCreate, x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    allowed = _normalize_allowed_durations(body.allowed_durations)
    return {"created": store.create_key(
        body.name,
        daily_limit=body.daily_limit,
        concurrency_limit=body.concurrency_limit,
        allowed_durations=allowed,
        expires_at=body.expires_at,
    )}


@app.patch("/api/admin/keys/{key}")
async def admin_key_patch(key: str, body: KeyPatch,
                          x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    if not store.get_key(key):
        raise HTTPException(404, "api key not found")
    fields = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.enabled is not None:
        fields["enabled"] = 1 if body.enabled else 0
    if body.daily_limit is not None:
        fields["daily_limit"] = body.daily_limit
    if body.concurrency_limit is not None:
        fields["concurrency_limit"] = body.concurrency_limit
    if body.allowed_durations is not None:
        fields["allowed_durations"] = _normalize_allowed_durations(body.allowed_durations)
    if body.expires_at is not None:
        fields["expires_at"] = body.expires_at
    store.update_key(key, **fields)
    return {"ok": True}


@app.delete("/api/admin/keys/{key}")
async def admin_key_delete(key: str, x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    if not store.get_key(key):
        raise HTTPException(404, "api key not found")
    store.delete_key(key)
    return {"ok": True}


# Dashboard single-file frontend
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    print(f"Starting Dola Pool Gateway at http://{config.HOST}:{config.PORT} (Dashboard: http://127.0.0.1:{config.PORT}/web/index.html)")
    uvicorn.run("server:app", host=config.HOST, port=config.PORT, reload=False)

