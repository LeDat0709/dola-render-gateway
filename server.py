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
import secrets
import shutil
import threading
import os
import signal
import time
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
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


def _canh_tien_trinh_cha():
    """App cha chết kiểu gì thì gateway cũng phải chết theo — nếu không sẽ 'trùng server' ở lần mở sau.

    Electron bị tắt cưỡng bức (End Task, mất điện, Windows Update ép reboot) thì before-quit KHÔNG chạy, tiến
    trình python này sống sót và vẫn giữ cổng. Lần mở app kế tiếp uvicorn mới chết vì "address already in use",
    lặp lại mãi tới khi có người tự vào Task Manager. Ở đây tự canh: cha biến mất thì xin tắt êm (SIGTERM để
    lifespan kịp đóng Chrome + nhả nick), quá hạn thì thoát cứng.
    """
    ppid = int(_os.getenv("DOLA_PARENT_PID") or 0)
    if not ppid:
        return

    def _cha_con_song() -> bool:
        if _sys.platform == "win32":
            import ctypes
            SYNCHRONIZE = 0x00100000
            h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, ppid)
            if not h:
                return False
            still = ctypes.windll.kernel32.WaitForSingleObject(h, 0) != 0   # 0 = WAIT_OBJECT_0 = đã thoát
            ctypes.windll.kernel32.CloseHandle(h)
            return still
        try:
            _os.kill(ppid, 0)
            return True
        except OSError:
            return False

    def _vong():
        while True:
            time.sleep(3)
            if _cha_con_song():
                continue
            print(f"[gateway] app cha (pid {ppid}) đã tắt — gateway tự dừng để không giữ cổng", flush=True)
            try:
                os.kill(os.getpid(), signal.SIGTERM)   # uvicorn tắt êm: lifespan đóng Chrome, nhả nick
            except Exception:
                pass
            time.sleep(15)
            _os._exit(0)          # tắt êm không xong trong 15s thì thoát cứng, đừng giữ cổng nữa

    threading.Thread(target=_vong, daemon=True, name="canh-cha").start()


_canh_tien_trinh_cha()
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


# Dò khóa admin: README hướng dẫn chạy VPS bằng --host 0.0.0.0, mà /api/admin/login trước đây thử được vô hạn
# lần → dò ra ADMIN_KEY là lấy được /api/admin/accounts/export = TOÀN BỘ cookie nick. Đếm số lần sai theo IP,
# quá ngưỡng thì khoá tạm; so khớp bằng compare_digest (không lộ độ dài/vị trí ký tự đúng qua thời gian đáp).
ADMIN_MAX_FAILS = 8
ADMIN_LOCKOUT_SEC = 300.0
# ponytail: dict không bao giờ dọn — mỗi IP sai để lại 1 ô nhỏ, kẻ dò từ hàng vạn IP sẽ làm nó phình.
# Đủ cho máy cá nhân/VPS nội bộ. Cần chặt hơn thì dọn ô hết hạn trong _admin_throttle, hoặc đẩy ra fail2ban.
_admin_fails: dict[str, list] = {}   # ip -> [số lần sai, thời điểm khoá tới]
_admin_fails_cleanup_at = 0.0


def _admin_key_ok(key: str | None) -> bool:
    return bool(key) and secrets.compare_digest(str(key), config.ADMIN_KEY)


def _admin_throttle(ip: str, ok: bool):
    """Gọi SAU khi so khóa. Sai → cộng dồn; đúng → xoá. Đang khoá thì ném 429 trước cả khi so."""
    global _admin_fails_cleanup_at
    now = time.time()
    if now - _admin_fails_cleanup_at > 3600:
        _admin_fails_cleanup_at = now
        _admin_fails.clear()
    st = _admin_fails.setdefault(ip, [0, 0.0])
    if ok:
        _admin_fails.pop(ip, None)
        return
    st[0] += 1
    if st[0] >= ADMIN_MAX_FAILS:
        st[1] = time.time() + ADMIN_LOCKOUT_SEC
        st[0] = 0


def _admin_locked(ip: str) -> float:
    st = _admin_fails.get(ip)
    left = (st[1] - time.time()) if st else 0.0
    return left if left > 0 else 0.0


def _admin_auth(x_admin_key: str | None, request: Request | None = None):
    if not config.ADMIN_KEY:
        return
    ip = (request.client.host if request and request.client else "?")
    left = _admin_locked(ip)
    if left:
        raise HTTPException(429, f"Sai khóa admin quá nhiều lần — thử lại sau {int(left)}s")
    ok = _admin_key_ok(x_admin_key)
    _admin_throttle(ip, ok)
    if not ok:
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
    # Khóa idempotency (Studio: 1 khóa/nick tới khi job xong). Gửi lại cùng khóa khi job cũ CHƯA xong → trả job cũ.
    client_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")


class TaskResponse(BaseModel):
    id: str
    status: str
    stage: str | None = None   # opening | submitting | rendering (chi tiết hơn "processing")
    model: str | None = None
    prompt: str | None = None
    video_url: str | None = None
    error: str | None = None
    account: str | None = None   # nick THẬT đã chạy job (khác nick thẻ nếu đã xoay nick) → UI hiện "chạy trên nick Y"
    charged: bool = False        # lệnh ĐÃ tới Dola (submitted_at) → chạy lại là trừ lượt lần 2; UI phải hỏi trước
    proxy_ip: str | None = None       # IP xoay đang gắn cho nick job này (peek cache, không gọi mạng)
    proxy_isp: str | None = None
    proxy_provider: str | None = None  # tmproxy | proxyxoay | "" (tĩnh/nối thẳng)
    proxy_used: int | None = None      # lượt thứ k của IP hiện tại (N nick/IP); None khi không bật
    proxy_per: int | None = None       # N nick mỗi IP
    proxy_fresh: bool | None = None    # True = IP mới (vừa xoay), False = IP cũ (dùng lại)
    proxy_kind: str | None = None      # rotating | static | direct — để UI khỏi hiện "đang chờ IP" khi nick nối thẳng


def _job_proxy(account: str | None) -> dict:
    """Thông tin proxy đang gắn cho job của nick — cột 'Proxy' hiện IP · IP mới/cũ · lượt k/N · nhà cung cấp (như đối thủ).
    Ưu tiên dấu do pool ghi lúc chạy (chính xác lượt/mới-cũ); chưa chạy thì peek cache. KHÔNG gọi mạng.
    kind = rotating (proxy xoay) | static (proxy tĩnh) | direct (không proxy) → UI hiện đúng, không kẹt 'đang chờ IP'."""
    raw, rotating_ip_info, provider_label = "", None, None
    try:
        from browser import rotating_ip_info, account_proxy_raw, is_rotating_proxy, provider_label
        raw = ((account_proxy_raw(account) if account else "") or config.PROXY or "").strip()
        kind = "rotating" if is_rotating_proxy(raw) else ("static" if raw else "direct")
    except Exception:  # noqa: BLE001
        kind = "direct"
    st = {}
    try:
        st = pool.proxy_stamp(account) if account else {}
    except Exception:  # noqa: BLE001
        st = {}
    ip, isp, prov = st.get("ip") or None, st.get("isp") or None, st.get("provider")
    if not ip and kind == "rotating" and rotating_ip_info:   # proxy xoay mà chưa đóng dấu → peek cache
        try:
            info = rotating_ip_info(raw)
            ip = info.get("ip") or None
            isp = isp or info.get("network") or info.get("location") or None
            prov = prov or (provider_label(raw) if provider_label else None)
        except Exception:  # noqa: BLE001 — chỉ là thông tin hiển thị
            pass
    return {"ip": ip, "isp": isp, "provider": prov or None, "kind": kind,
            "used": st.get("used"), "per": st.get("per"), "fresh": st.get("fresh")}


def _task_stage(row: dict) -> str:
    """Giai đoạn thật của job đang chạy — UI hiện "đang gửi" vs "Dola đang render"."""
    if row["status"] != "processing":
        return row["status"]
    if row.get("conversation_id"):
        return "rendering"      # Dola đã nhận việc, trình duyệt đã được trả lại
    if row.get("submitted_at"):
        return "submitting"
    if row.get("opened_at"):
        return "opening"        # đang mở nick / kiểm tra điểm
    # Chưa tới lượt: chờ slot Chrome ("Nick gửi cùng lúc") / nhịp gửi. Trước đây gộp chung "đang mở nick" →
    # ảnh 15/09: 10 dòng "đang mở nick 22p" mà thật ra chỉ vài nick treo giữ hết slot, còn lại đang chờ.
    return "waiting"


def _resolve_ratio(size, ratio):
    if size and size in SIZE_TO_RATIO:
        return SIZE_TO_RATIO[size]
    return ratio


HARD_TIMEOUT_GRACE = 600   # thời gian ngoài lúc chờ render: mở Chrome, ký, tải video về


def _hard_timeout(duration) -> int:
    """Trần tuyệt đối của một job — quá là bỏ, để không job nào 'đang chạy' mãi mà giữ nick."""
    return (config.VIDEO_TIMEOUT_30S if duration == 30 else config.VIDEO_TIMEOUT) + HARD_TIMEOUT_GRACE



# Job báo "đã gửi, chưa xác nhận" nhưng Dola VẪN dựng xong video — lượt đã trừ rồi mà thẻ vẫn đỏ, người dùng
# phải tự bấm "Video trên Dola của nick" mới nhặt được. Nay tự quét lại hội thoại của nick (KHÔNG tốn lượt) và
# gắn video vào đúng job. Quét trễ vài phút vì lúc job hỏng video thường còn đang dựng.
CUU_VIDEO_SAU = (120, 300, 600, 1200, 2400)   # 2/5/10/20/40 phút




def _chon_video(ds: list, row: dict, sau_khi: float) -> dict | None:
    """Chọn ĐÚNG video của job trong danh sách hội thoại của nick.

    KHÔNG ghép theo tên hội thoại: Dola tự đặt tên tóm tắt (thấy thực tế: "犬舍繁育者视频"), không phải prompt —
    ghép kiểu đó trượt gần hết. Hai cách chắc chắn hơn:
      1. Job đã có conversation_id → khớp thẳng, tuyệt đối đúng.
      2. Chưa có → lấy hội thoại tạo GẦN LÚC GỬI NHẤT trong cửa sổ, và chưa job nào nhận. An toàn vì mỗi nick
         có khoá riêng nên chạy tuần tự từng job (browser_pool self._locks) — không có hai job cùng nick chạy
         chồng nhau để mà lẫn.
    """
    if row.get("conversation_id"):
        return next((v for v in ds if v.get("video_url")
                     and str(v.get("conversation_id")) == str(row["conversation_id"])), None)
    ung = [v for v in ds
           if v.get("video_url")
           and sau_khi - 120 <= (v.get("created_at") or 0) <= sau_khi + config.CUU_VIDEO_CUA_SO_SEC
           and not store.task_by_conversation(str(v.get("conversation_id")))]
    return min(ung, key=lambda v: abs((v.get("created_at") or 0) - sau_khi), default=None)


async def _cuu_video_da_tra_luot(task_id: str, account: str, prompt: str, sau_khi: float,
                                 cac_moc: tuple | None = None):
    from video_worker_ui import scan_account_videos
    from video_worker import _download
    from browser import proxy_lease
    # Đọc CUU_VIDEO_SAU lúc GỌI, không đặt làm giá trị mặc định: mặc định bị chốt lúc định nghĩa hàm nên
    # test (gán server.CUU_VIDEO_SAU = (0, 0) cho khỏi chờ thật) sẽ không còn tác dụng.
    for cho in (CUU_VIDEO_SAU if cac_moc is None else cac_moc):
        await asyncio.sleep(cho)
        row = store.get(task_id)
        if not row or row["status"] == "completed":
            return                                   # người dùng đã tự nhặt, hoặc job đã xong
        # GIỮ CHỖ proxy suốt lúc quét: video này ĐÃ TRỪ LƯỢT, mà đường cứu là chỗ DUY NHẤT đi qua proxy của
        # nick mà KHÔNG nằm trong sổ _proxy_leases (chỗ giữ duy nhất là browser_pool khi chạy job) → job của
        # nick khác cùng khoá proxy xin được IP mới, nhà bán giết cổng cũ, quét đứt giữa chừng.
        # Giữ SAU asyncio.sleep để không khoá IP suốt 40 phút chờ.
        with proxy_lease(account):
            try:
                # 50 (trần của endpoint) chứ không phải 20: tới mốc cứu 40 phút, nick chạy dày đã đẩy hội
                # thoại của job này ra khỏi 20 hội thoại gần nhất → quét mãi không thấy video đã trừ lượt.
                ds = await scan_account_videos(account, 50)
            except Exception:
                continue                             # nick lỗi mạng/cookie → thử lại lần sau
        v = _chon_video(ds, row, sau_khi)
        if v:
            # NHẬN CHỖ NGAY, trước khi tải: tải mất hàng chục giây, mà _chon_video của job khác cùng nick lọc
            # "chưa ai nhận" bằng store.task_by_conversation → ghi sau khi tải xong thì trong lúc tải hội thoại
            # này vẫn trống chỗ, hai job cùng nhặt một video.
            store.update(task_id, conversation_id=v["conversation_id"])
            try:
                with proxy_lease(account):   # tải cũng đi qua proxy nick — xem chú thích ở khối quét
                    local = await _download(v["video_url"], account, prompt)
            except Exception as exc:
                # ĐỪNG bỏ cuộc: link CDN Dola có chữ ký hết hạn, mốc sau quét lại sẽ ra URL MỚI. Trước đây
                # tải hỏng một lần là dừng hẳn, mất luôn video đã trả lượt.
                print(f"[{account}] cứu video job {task_id}: tải hỏng ({str(exc)[:90]}) — thử lại mốc sau",
                      flush=True)
                continue
            # Ghi rõ video được tạo LÚC NÀO và lệch bao nhiêu so với lúc gửi — để người dùng tự kiểm tool có
            # nhặt đúng video của job này không (ghép không theo tên hội thoại được, xem _chon_video).
            tao_luc = int(v.get("created_at") or 0)
            lech = int(tao_luc - sau_khi) if tao_luc else 0
            ghi_chu = (f"Tự nhặt lại từ Dola — video tạo lúc "
                       f"{time.strftime('%H:%M %d/%m', time.localtime(tao_luc)) if tao_luc else '?'}"
                       f" ({'sau' if lech >= 0 else 'trước'} lúc gửi {abs(lech) // 60}p{abs(lech) % 60}s)"
                       f" · hội thoại {str(v.get('conversation_id'))[:10]}")
            store.update(task_id, status="completed", finished_at=time.time(),
                         conversation_id=v["conversation_id"], error=ghi_chu, failure_code=None,
                         video_url=_public_video_url({"local_path": str(local), "video_url": v["video_url"]}))
            print(f"[{account}] CỨU được video job {task_id}: {ghi_chu}", flush=True)
            return


def _cuu_neu_da_gui(task_id: str, account: str | None, prompt: str) -> bool:
    """Job hỏng mà lệnh ĐÃ tới Dola (đã trừ lượt) → tự đi nhặt video về.

    Dùng chung cho MỌI nhánh lỗi — treo quá giờ, hết nick, lỗi khác — vì Dola trừ lượt như nhau ở cả ba;
    trước đây chỉ nhánh `except Exception` cứu, nên job treo quá giờ (ca đáng cứu nhất: Dola VẪN đang dựng)
    lại là ca bị bỏ. Mốc đếm là submitted_at (LÚC GỬI) chứ không phải lúc lỗi: video sinh ra ngay sau lúc gửi,
    lấy mốc lỗi thì cửa sổ ghép trong _chon_video lệch hẳn.
    """
    row = store.get(task_id)
    acc = (row or {}).get("account") or account
    if not (row and row.get("submitted_at") and acc):
        return False
    store.update(task_id, error=((row.get("error") or "")[:240]
                                 + " — lệnh đã tới Dola (lượt đã bị trừ) nên tool đang tự quét lại hội thoại "
                                   "của nick để nhặt video về, KHÔNG tốn thêm lượt."))
    _spawn(_cuu_video_da_tra_luot(task_id, acc, prompt, float(row["submitted_at"])))
    return True


# Cứu lại sau khi BẬT SERVER: video của lần chạy trước gần như chắc chắn đã dựng xong rồi, nên quét ngay
# (10s cho pool kịp nạp cookie) thay vì chờ 2 phút như lúc job vừa hỏng.
CUU_VIDEO_SAU_KHI_BAT = (10, 90, 300, 900, 1800)
CUU_LAI_TOI_DA = 40   # trần số job tự quét lúc bật — mỗi job là một lượt đọc hội thoại của nick


def _cuu_lai_job_da_tra_luot() -> None:
    """Bật server: job lần trước đã trừ lượt mà chưa ra video → xếp lại hàng tự quét.

    Tắt server huỷ sạch task nền, nên không nối lại ở đây thì lượt đã trả mất luôn.
    """
    cho = store.jobs_cho_cuu_video()
    if not cho:
        return
    if len(cho) > CUU_LAI_TOI_DA:
        print(f"[gateway] {len(cho)} job đã trừ lượt chờ cứu — chỉ tự quét {CUU_LAI_TOI_DA} job mới nhất; "
              f"{len(cho) - CUU_LAI_TOI_DA} job cũ hơn phải bấm nút quét video của nick.", flush=True)
        cho = cho[:CUU_LAI_TOI_DA]
    dat = 0
    for row in cho:
        if not row.get("account"):
            continue                       # chưa kịp chọn nick thì không biết quét hội thoại của ai
        _spawn(_cuu_video_da_tra_luot(row["id"], row["account"], row.get("prompt") or "",
                                      float(row["submitted_at"]), CUU_VIDEO_SAU_KHI_BAT))
        dat += 1
    if dat:
        print(f"[gateway] {dat} job đã trừ lượt của lần chạy trước — đang tự quét hội thoại nhặt video về "
              "(không tốn thêm lượt)", flush=True)


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
        def on_opening(account):
            store.update(task_id, account=account, opened_at=time.time())

        result = await asyncio.wait_for(pool.generate_video(
            prompt, ratio, duration, model,
            on_conversation_id=on_conversation_id, on_poll=on_poll,
            on_submitted=on_submitted, on_opening=on_opening,
            reference_image_paths=reference_paths, account=account), _hard_timeout(duration))
        acc_that = result.get("account") or account
        if not result.get("local_path"):
            # Dola dựng XONG (đã trừ lượt) nhưng CDN/proxy làm hỏng lượt tải. Trước đây vẫn ghi "completed" kèm
            # link CDN — người dùng tưởng video đã về máy, tới lúc bấm thì link ký đã hết hạn, mất trắng.
            # Ghi đúng sự thật + tự đi tải lại bằng URL MỚI quét từ hội thoại.
            store.update(task_id, status="failed", failure_code="download_failed",
                         video_url=result.get("video_url"), conversation_id=result.get("conversation_id"),
                         account=acc_that, finished_at=time.time(),
                         error="Dola đã dựng xong nhưng chưa tải được về máy "
                               f"({str(result.get('download_error') or '')[:90]}) — đang tự tải lại, "
                               "không tốn thêm lượt. Hoặc bấm nút quét video của nick.")
            _spawn(_cuu_video_da_tra_luot(task_id, acc_that, prompt, time.time()))
        else:
            store.update(task_id, status="completed", video_url=_public_video_url(result),
                         account=acc_that, last_poll_at=time.time(),
                         finished_at=time.time(), error=_short_video_note(result, duration))
    # Ba nhánh lỗi, MỘT lối cứu (_cuu_neu_da_gui): lệnh đã tới Dola là đã trừ lượt, bất kể lỗi kiểu gì —
    # video nhiều khi vẫn dựng xong. Tự đi nhặt về thay vì để thẻ đỏ và bắt người dùng bấm tay.
    except asyncio.TimeoutError:
        store.update(task_id, status="failed", finished_at=time.time(),
                     error=f"Job treo quá {_hard_timeout(duration) // 60} phút — đã bỏ để giải phóng nick.")
        _cuu_neu_da_gui(task_id, account, prompt)
    except (AllAccountsLimitedError, AllAccountsQuotaBlockedError) as e:
        store.update(task_id, status="failed", error=str(e)[:500],
                     failure_code="429", finished_at=time.time())
        _cuu_neu_da_gui(task_id, account, prompt)
    except Exception as e:
        store.update(task_id, status="failed", error=str(e)[:500],
                     finished_at=time.time())
        _cuu_neu_da_gui(task_id, account, prompt)
    finally:
        if reference_root:
            shutil.rmtree(reference_root, ignore_errors=True)
        if acquired:
            await key_limiter.release(api_key_hash)


# Dola có lúc trừ đủ lượt 30s nhưng trả clip ngắn hơn. Đo file rồi ghi chú vào job (job vẫn "hoàn tất" — video
# dùng được) để người dùng biết ngay trên thẻ, thay vì chỉ phát hiện lúc mở file thì lượt đã mất.
SHORT_VIDEO_TOLERANCE_SEC = 2.0


def _short_video_note(result: dict, want_duration) -> str | None:
    path = result.get("local_path")
    if not path or not want_duration:
        return None
    try:
        from watermark import probe_duration
        got = probe_duration(path)
    except Exception:
        return None
    if got is None or got >= want_duration - SHORT_VIDEO_TOLERANCE_SEC:
        return None
    return (f"⚠ Dola trả video {got:.0f}s trong khi bạn đặt {want_duration}s (vẫn trừ lượt như {want_duration}s). "
            "Video vẫn dùng được; muốn đủ giây thì chạy lại (tốn lượt).")


def _public_video_url(result: dict) -> str:
    """File đã tải về → link /videos/ của gateway; tải hỏng (đã thử lại) → giữ link CDN Dola để tải tay."""
    if result.get("local_path"):
        return f"{config.PUBLIC_BASE}/videos/{Path(result['local_path']).name}"
    print(f"[gateway] video không tải về được, trả link Dola: {result.get('download_error')}", flush=True)
    return result["video_url"]


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


# Tắt server / thoát app = XOÁ job cũ. Không hồi sinh, cũng không để lại thẻ lỗi "Server đã tắt…":
# bật lên là bảng sạch, tool chỉ chạy đúng những job bạn bấm ở lần này. Video đã xong thì giữ nguyên.
STOP_GRACE_SEC = 10   # chờ job đang chạy đóng Chrome + nhả nick rồi mới để tiến trình thoát


_INSTANCE_LOCK = None   # giữ mở suốt đời tiến trình; đóng file = nhả khoá


def _claim_single_instance() -> bool:
    """True khi tiến trình này là gateway DUY NHẤT dùng tasks.db — chỉ khi đó mới được dọn job.

    uvicorn chạy lifespan TRƯỚC lúc chiếm cổng (Server.startup): bật trùng một bản nữa (bấm "Bật server"
    trong khi ./run.sh đang chạy, hoặc mở app hai lần) thì bản thừa vẫn kịp quét sạch job của bản ĐANG
    CHẠY rồi mới chết vì "address already in use" — đúng cảnh job đang render bỗng báo "Server đã tắt".
    """
    global _INSTANCE_LOCK
    f = open(Path(config.DB_PATH).with_suffix(".lock"), "a+")
    try:
        if _os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    _INSTANCE_LOCK = f
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bật: dọn job cũ. Tắt: huỷ job đang chạy rồi đánh dấu hỏng — không job nào ở lại trạng thái dở dang."""
    solo = _claim_single_instance()
    if not solo:
        print("[gateway] ⚠ Đã có một gateway khác đang chạy trên thư mục dữ liệu này — bản thừa này KHÔNG "
              "đụng tới job đang chạy. Tắt bớt một bản (cửa sổ ./run.sh hoặc nút Bật server trong app).",
              flush=True)
    else:
        if (dropped := store.purge_dead_tasks()):
            print(f"[gateway] xoá {dropped} job cũ của lần chạy trước (bật lên là bảng sạch)", flush=True)
        _cuu_lai_job_da_tra_luot()   # job purge CHỪA LẠI vì đã trừ lượt → nối lại việc quét, đừng để mất trắng
    from browser import mask_proxy as _mask, probe_proxy
    print(f"[gateway] proxy chung: {_mask(config.PROXY) or '(không — nối thẳng)'}", flush=True)
    if config.PROXY:
        bad = await probe_proxy(config.PROXY)
        if bad:
            print(f"[gateway] ⚠ proxy chung {_mask(config.PROXY)} KHÔNG nối được ({bad}) — mọi nick không có "
                  "proxy riêng sẽ lỗi. Sửa hoặc xoá trống ở Cài đặt → Proxy chung rồi Tắt/Bật server.", flush=True)
    yield
    if not solo:
        return                                    # bản thừa thoát: job là của bản kia, không được đụng
    for t in list(_BG_TASKS):
        t.cancel()
    if _BG_TASKS:
        await asyncio.wait(list(_BG_TASKS), timeout=STOP_GRACE_SEC)   # để finally đóng Chrome, nhả nick
    store.purge_dead_tasks()


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
    # Cùng khóa mà job cũ chưa xong (client Dừng→Chạy, POST treo rồi gửi lại, mở lại app) → trả job cũ, KHÔNG tạo
    # job thứ hai: Dola trừ lượt lúc gửi, tạo trùng = mất lượt 2 lần. Job cũ đã xong/lỗi → tạo mới bình thường.
    if req.client_id:
        live = store.live_by_client_id(req.client_id, client["api_key_hash"])
        if live:
            return TaskResponse(id=live["id"], status=live["status"], model=live["model"],
                                prompt=live["prompt"], account=live.get("account"),
                                video_url=live.get("video_url"), error=live.get("error"),
                                charged=bool(live.get("submitted_at")))
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
            account=account,   # nick đã ghim ghi ngay từ lúc xếp hàng (trước: NULL tới khi mở nick → UI/khôi phục không thấy)
            reference_images=json.dumps(reference_images, ensure_ascii=False),
            api_key_hash=client["api_key_hash"],
            api_key_name=client["api_key_name"],
            daily_limit=client["daily_limit"],
            concurrency_limit=client["concurrency_limit"],
            max_pending=config.MAX_PENDING_TASKS,
            client_id=req.client_id,
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
    px = _job_proxy(row.get("account"))
    return TaskResponse(
        id=row["id"], status=row["status"], stage=_task_stage(row), model=row["model"],
        prompt=row["prompt"], video_url=row["video_url"], error=row["error"], account=row.get("account"),
        charged=bool(row.get("submitted_at")),
        proxy_ip=px["ip"], proxy_isp=px["isp"], proxy_provider=px["provider"],
        proxy_used=px["used"], proxy_per=px["per"], proxy_fresh=px["fresh"], proxy_kind=px["kind"],
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
        "burn_nicks": config.BURN_NICKS,
        "submit_mode": config.SUBMIT_MODE,
        "one_nick": config.ONE_NICK,
        "nicks_per_ip": config.NICKS_PER_IP,
        "parallel_per_ip": config.PARALLEL_PER_IP,
        "rotating_ip": _rotating_ip_cached(),          # IP xoay đang dùng (đọc cache, không gọi mạng)
        "ip_used": getattr(pool, "_ip_used", 0),       # số nick đã dùng IP hiện tại (lượt k/N)
        "submit_gap_min": config.SUBMIT_GAP_SEC,                                  # chờ ngẫu nhiên tối thiểu giữa lần gửi
        "submit_gap_max": config.SUBMIT_GAP_SEC + config.SUBMIT_JITTER_SEC,       # …tối đa (min + jitter)
        # Điểm/video theo model × giây — ĐÚNG giá pool dùng (học từ Dola, chưa có thì giá đo 13/09). UI tự đoán
        # "10/15s = 1 điểm" cho mọi model → loại nhầm/nhận nhầm nick, bấm chạy mà không gửi job (15/09).
        "credit_cost": {m: {str(d): pool._cost_for(m, d) or pool._default_cost(m, d) for d in SUPPORTED_DURATIONS}
                        for m in ("seedance-2.0", "seedance-2.5")},
    }


def _rotating_ip_cached() -> dict:
    try:
        from browser import rotating_ip_info
        return rotating_ip_info(config.PROXY) or {}
    except Exception:
        return {}


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
async def admin_login(body: AdminLogin, request: Request):
    if not config.ADMIN_KEY:
        return {"ok": True, "auth_required": False}
    ip = request.client.host if request.client else "?"
    left = _admin_locked(ip)
    if left:
        raise HTTPException(429, f"Sai khóa admin quá nhiều lần — thử lại sau {int(left)}s")
    ok = _admin_key_ok(body.key)
    _admin_throttle(ip, ok)
    if not ok:
        raise HTTPException(401, "wrong admin key")
    return {"ok": True, "auth_required": True}


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
    from browser import set_account_proxy, check_proxy_input
    try:
        v = check_proxy_input(body.proxy)   # kiểm DẠNG, không gọi nhà bán (proxy.vn chưa whitelist vẫn lưu được)
    except ValueError as e:
        raise HTTPException(422, str(e))
    set_account_proxy(name, v)
    return {"ok": True, "proxy": v or "(global)"}


@app.get("/api/admin/accounts/{name}/proxy")
async def admin_account_proxy_get(name: str, x_admin_key: str | None = Header(default=None)):
    """Proxy riêng của nick trên máy chủ — app ở chế độ server từ xa đọc qua đây thay vì file cục bộ."""
    _admin_auth(x_admin_key)
    if name not in pool.accounts:
        raise HTTPException(404, "account not found")
    from browser import account_proxy_raw
    return {"proxy": account_proxy_raw(name)}


@app.get("/api/admin/accounts/{name}/proxy/current")
async def admin_account_proxy_current(name: str, x_admin_key: str | None = Header(default=None)):
    """IP HIỆN HÀNH của proxy nick, đã giải sẵn {server, username?, password?} — cho cửa sổ Electron.

    Vì sao cần: cửa sổ đăng nhập Electron không tự gọi nhà bán được (cache IP và IP whitelist nằm ở tiến
    trình này). Không có endpoint này thì cửa sổ nối THẲNG khi nick dùng proxy xoay → nick đăng nhập bằng
    IP máy thật rồi render bằng IP proxy, đúng dấu hiệu chống gian lận soi kỹ nhất.

    Vì sao TỪ CHỐI khi proxy đang bận: account_proxy() đi qua current(), mà current() hết hạn cache là gọi
    lại nhà bán — lấy IP mới thì cổng cũ bị giết, cắt ngang video ĐÃ TRỪ LƯỢT đang dựng. Chờ vài phút rẻ
    hơn mất một video.
    ponytail: bận thì từ chối luôn. Muốn đăng nhập được giữa lúc render thì phải có bản đọc-cache-thuần
    trong proxyxoay.py/tmproxy.py (cached_ip hiện không trả user/pass) — chưa cần.
    """
    _admin_auth(x_admin_key)
    # KHÔNG kiểm pool.accounts như endpoint trên: nick MỚI chưa có thư mục vẫn phải đăng nhập được.
    # NAME_RE vẫn chặn "../" nên không đọc ra ngoài thư mục accounts.
    if not NAME_RE.match(name):
        raise HTTPException(422, "tên nick không hợp lệ")
    from browser import _effective_rotating, account_proxy, proxy_busy
    raw = _effective_rotating(name)
    dang_chay = proxy_busy(raw) if raw else 0
    if dang_chay:
        raise HTTPException(409, f"Proxy của nick này đang có {dang_chay} job chạy — lấy IP lúc này có thể "
                                 "đổi IP và làm hỏng video đã trừ lượt. Chờ job xong rồi đăng nhập.")
    try:
        # urllib trong proxyxoay/tmproxy chặn luồng → to_thread, khỏi treo cả event loop của gateway.
        p = await asyncio.to_thread(account_proxy, name)
    except RuntimeError as e:   # account_proxy ném khi proxy xoay riêng không lấy được IP
        raise HTTPException(502, str(e)[:200])
    return {"proxy": p}


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
    out = {"ok": False, "has_own": bool(raw), "via": "riêng" if raw else ("chung" if config.PROXY else "nối thẳng")}
    try:
        url = account_proxy_url(name) or None      # proxy nick, thiếu thì rơi về proxy chung
    except Exception as e:  # noqa: BLE001 — sai định dạng / proxy xoay không lấy được IP: báo rõ, KHÔNG kiểm bằng IP máy
        out["error"] = str(e)[:200]
        return out
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
    if name in pool.accounts:
        raise HTTPException(409, "account exists")
    if JOBS.get(name, {}).get("status") == "running":
        raise HTTPException(409, "job already running for this account")
    _spawn(_run_facebook_add_job(name, body.cookie_line, body.note, body.visible))
    return {"ok": True, "job": "running"}


@app.post("/api/admin/accounts/import-cookie")
async def admin_account_import_cookie(body: AccountCookieImport, x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    name = body.name.strip()
    if not NAME_RE.match(name):
        raise HTTPException(400, "invalid account name (1-32 chars: letters, numbers, -, _)")
    from browser import check_proxy_input
    try:
        proxy = check_proxy_input(body.proxy)
    except ValueError as e:
        raise HTTPException(422, str(e))
    try:
        res = await apply_cookies_to_account(name, body.cookies, ui_lang=body.ui_lang, proxy=proxy)
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


class SubmitModeUpdate(BaseModel):
    mode: str   # "fetch" (mở Chrome ký, ổn định) | "http" (không Chrome, thử nghiệm)


@app.post("/api/admin/submit-mode")
async def admin_submit_mode(body: SubmitModeUpdate, x_admin_key: str | None = Header(default=None)):
    """Đổi cách GỬI lệnh: 'fetch' = mở Chrome ký (mặc định, ổn định); 'http' = ký bằng Python gửi thẳng, KHÔNG mở Chrome
    (nhẹ RAM, mở nick nhanh; Dola từ chối thì tự rơi về fetch). Nhớ vào .env.local."""
    _admin_auth(x_admin_key)
    mode = (body.mode or "").strip().lower()
    if mode not in ("fetch", "http"):
        raise HTTPException(422, "mode phải là 'fetch' hoặc 'http'")
    config.SUBMIT_MODE = mode
    config.upsert_env_local("DOLA_SUBMIT_MODE", mode)
    print(f"[gateway] cách gửi lệnh: {mode}", flush=True)
    return {"ok": True, "submit_mode": mode}


class BurnNicksUpdate(BaseModel):
    burn_nicks: bool


@app.post("/api/admin/burn-nicks")
async def admin_burn_nicks(body: BurnNicksUpdate, x_admin_key: str | None = Header(default=None)):
    """Bật/tắt ĐỐT NICK (nick dùng hết lượt/điểm → tắt lịch + ghi chú [ĐÃ ĐỐT]) ngay lúc chạy, nhớ vào .env.local."""
    _admin_auth(x_admin_key)
    config.BURN_NICKS = body.burn_nicks
    config.upsert_env_local("DOLA_BURN_NICKS", "1" if body.burn_nicks else "0")
    print(f"[gateway] đốt nick khi hết lượt/điểm: {'BẬT' if config.BURN_NICKS else 'TẮT'}", flush=True)
    return {"ok": True, "burn_nicks": config.BURN_NICKS}


class AutoRetryUpdate(BaseModel):
    auto_retry: bool


@app.post("/api/admin/retry")
async def admin_retry(body: AutoRetryUpdate, x_admin_key: str | None = Header(default=None)):
    """Bật/tắt tự thử lại + xoay nick ngay lúc chạy (không cần khởi động lại)."""
    _admin_auth(x_admin_key)
    config.AUTO_RETRY = body.auto_retry
    print(f"[gateway] tự thử lại / xoay nick: {'BẬT' if config.AUTO_RETRY else 'TẮT'}", flush=True)
    return {"ok": True, "auto_retry": config.AUTO_RETRY}


class OneNickUpdate(BaseModel):
    one_nick: bool
    nicks_per_ip: int | None = Field(None, ge=1, le=50)   # số nick mỗi IP trước khi xoay (None = giữ nguyên)
    parallel_per_ip: int | None = Field(None, ge=1, le=16)   # số job CHẠY SONG SONG trên 1 IP chung (None = giữ nguyên)


@app.post("/api/admin/one-nick")
async def admin_one_nick(body: OneNickUpdate, x_admin_key: str | None = Header(default=None)):
    """Bật/tắt MỖI LẦN MỘT NICK ngay lúc chạy (không cần khởi động lại). Chỉ ăn thua khi proxy chung là
    key/link xoay — chạy tuần tự, mỗi IP dùng cho N nick (nicks_per_ip) rồi mới xoay."""
    _admin_auth(x_admin_key)
    config.ONE_NICK = body.one_nick
    if body.nicks_per_ip is not None:
        config.NICKS_PER_IP = max(1, body.nicks_per_ip)
    if body.parallel_per_ip is not None:
        config.PARALLEL_PER_IP = max(1, body.parallel_per_ip)
    print(f"[gateway] XOAY IP THEO LÔ: {'BẬT' if config.ONE_NICK else 'TẮT'} · {config.NICKS_PER_IP} job/IP · "
          f"{config.PARALLEL_PER_IP} song song", flush=True)
    return {"ok": True, "one_nick": config.ONE_NICK, "nicks_per_ip": config.NICKS_PER_IP, "parallel_per_ip": config.PARALLEL_PER_IP}


class SubmitGapUpdate(BaseModel):
    min_sec: float = Field(ge=0, le=60)
    max_sec: float = Field(ge=0, le=120)


@app.post("/api/admin/submit-gap")
async def admin_submit_gap(body: SubmitGapUpdate, x_admin_key: str | None = Header(default=None)):
    """"Chờ ngẫu nhiên X–Y giây" giữa các lần gửi (chống 710022002 'gửi quá dày') NGAY lúc chạy + ghi .env.local.
    _pace đọc config.SUBMIT_GAP_SEC/SUBMIT_JITTER_SEC mỗi lần nên áp dụng cho job mới liền. Nhịp tính theo TỪNG proxy."""
    _admin_auth(x_admin_key)
    lo = max(0.0, float(body.min_sec))
    hi = max(lo, float(body.max_sec))
    config.SUBMIT_GAP_SEC = lo
    config.SUBMIT_JITTER_SEC = hi - lo
    config.upsert_env_local("DOLA_SUBMIT_GAP", str(lo))
    config.upsert_env_local("DOLA_SUBMIT_JITTER", str(hi - lo))
    print(f"[gateway] chờ ngẫu nhiên giữa lần gửi: {lo:.0f}–{hi:.0f}s (theo từng proxy)", flush=True)
    return {"ok": True, "min_sec": lo, "max_sec": hi}


class GlobalProxyUpdate(BaseModel):
    proxy: str = ""


@app.post("/api/admin/global-proxy")
async def admin_set_global_proxy(body: GlobalProxyUpdate, x_admin_key: str | None = Header(default=None)):
    """Đặt proxy chung của server NGAY lúc chạy (không cần khởi động lại) và ghi vào .env.local.

    config.PROXY được browser/poll/tải video đọc lại mỗi lần chạy nên áp dụng cho job mới liền. Nhờ vậy
    app ở chế độ máy chủ từ xa đặt được proxy chung cho VPS thẳng từ giao diện."""
    _admin_auth(x_admin_key)
    from browser import check_proxy_input
    # Chuẩn hoá + kiểm DẠNG (key trần → tmproxy://KEY, proxyvn://KEY → link get.php), khỏi gọi mạng/tốn 1 lượt
    # xoay lúc lưu — thẻ làn mới thật sự gọi nhà bán. Chặn luôn key rỗng ("tmproxy://").
    try:
        v = check_proxy_input(body.proxy)
    except ValueError as e:
        raise HTTPException(422, str(e))
    config.PROXY = v
    config.upsert_env_local("DOLA_PROXY", v)
    print(f"[gateway] proxy chung đổi thành: {v or '(nối thẳng)'}", flush=True)
    return {"ok": True, "proxy": v or "(nối thẳng)"}


def _lane_fields(ent: dict) -> dict:
    return {k: ent.get(k, "") for k in ("ip", "network", "location", "expiration", "message")}


@app.get("/api/admin/global-proxy/lane")
async def admin_global_proxy_lane(x_admin_key: str | None = Header(default=None)):
    """Thẻ làn cho proxy chung XOAY (TMProxy `tmproxy://KEY`/key trần, hoặc link get.php?key=…): IP/nhà
    mạng/vị trí/hạn hiện hành. Giữ IP đã cache trong phiên (không đổi giữa lúc nick chạy). Proxy chung không
    xoay → {key_link: False}. Gọi nhà bán trong to_thread vì urllib chặn luồng; IP whitelist theo máy chủ."""
    _admin_auth(x_admin_key)
    from browser import is_rotating_proxy, rotating_lane
    raw = (config.PROXY or "").strip()
    if not is_rotating_proxy(raw):
        return {"key_link": False}
    try:
        ent = await asyncio.to_thread(rotating_lane, raw)
    except Exception as e:  # noqa: BLE001 — lỗi nhà bán (TMProxyError/ProxyXoayError…) hiện thẳng ra thẻ làn
        return {"key_link": True, "ok": False, "error": str(e)[:160]}
    return {"key_link": True, "ok": True, **_lane_fields(ent or {})}


@app.post("/api/admin/global-proxy/rotate")
async def admin_global_proxy_rotate(x_admin_key: str | None = Header(default=None)):
    """Nút "Đổi IP": xin IP mới từ nhà bán (TMProxy/proxyxoay), tôn trọng khoảng chờ ép (chưa tới giờ → giữ IP cũ)."""
    _admin_auth(x_admin_key)
    from browser import is_rotating_proxy, rotating_rotate
    raw = (config.PROXY or "").strip()
    if not is_rotating_proxy(raw):
        raise HTTPException(422, "Proxy chung không phải key/link xoay được")
    try:
        ent = await asyncio.to_thread(rotating_rotate, raw)
    except Exception as e:  # noqa: BLE001 — lỗi nhà bán hiện thẳng cho người dùng
        raise HTTPException(502, str(e)[:160])
    return {"ok": True, **_lane_fields(ent or {})}


class ConcurrencyUpdate(BaseModel):
    max_concurrency: int | None = Field(None, ge=1, le=MAX_BROWSER_SLOTS)
    login_concurrency: int | None = Field(None, ge=1, le=MAX_LOGIN_SLOTS)


@app.post("/api/admin/concurrency")
async def admin_set_concurrency(body: ConcurrencyUpdate, x_admin_key: str | None = Header(default=None)):
    """Đổi số luồng ngay lúc đang chạy (job đang chạy không bị đụng tới)."""
    _admin_auth(x_admin_key)   # thiếu dòng này thì ai tới được cổng VPS cũng đổi được luồng
    global login_concurrency
    if body.max_concurrency is not None:
        pool.set_max_concurrency(body.max_concurrency)
    if body.login_concurrency is not None:
        limit = max(1, min(body.login_concurrency, MAX_LOGIN_SLOTS))
        resize_semaphore(login_slots, limit - login_concurrency)
        login_concurrency = limit
        # verify_all (bước "kiểm tra nick" trước khi chạy) đọc config.LOGIN_CONCURRENCY chứ không đọc biến này.
        # Thiếu dòng dưới thì đổi ô "Đăng nhập cùng lúc" chỉ ăn vào lúc đăng nhập, còn kiểm nick vẫn giữ số cũ
        # cho tới khi khởi động lại server — người dùng chỉnh 10 mà vẫn thấy kiểm nick chậm như 3.
        config.LOGIN_CONCURRENCY = limit
    print(f"[config] luồng gửi={pool.max_concurrency} · luồng đăng nhập={login_concurrency}", flush=True)
    return {"ok": True, "max_concurrency": pool.max_concurrency, "login_concurrency": login_concurrency}


@app.get("/api/admin/jobs")
async def admin_jobs(x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    return {"jobs": JOBS}


@app.get("/api/admin/tasks")
async def admin_tasks(limit: int = 50, x_admin_key: str | None = Header(default=None)):
    _admin_auth(x_admin_key)
    # kho video cần xem xa hơn 200 job; kèm stage để Tổng quan vẽ dòng chảy job (chờ slot → mở nick → gửi → dựng)
    return {"tasks": [{**t, "stage": _task_stage(t)} for t in store.recent_tasks(min(max(limit, 1), 2000))]}


class RedownloadReq(BaseModel):
    task_id: str
    url: str          # link CDN Dola của video (video_url hiện tại của task)
    account: str = ""
    prompt: str = ""


@app.get("/api/admin/accounts/{name}/videos")
async def admin_account_videos(name: str, limit: int = 30, x_admin_key: str | None = Header(default=None)):
    """Check Video Nick: video đã dựng xong trên Dola của nick (đọc lịch sử, không tốn lượt) + job tương ứng trong tool
    (đã về máy / chỉ trên Dola / job báo lỗi dù video đã ra) để cứu video của job lỗi, quá giờ, IP chết lúc tải."""
    _admin_auth(x_admin_key)
    if name not in pool.accounts:
        raise HTTPException(404, "account not found")
    from video_worker_ui import scan_account_videos
    try:
        videos = await scan_account_videos(name, max(1, min(limit, 50)))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    by_conv = {t["conversation_id"]: t for t in store.recent_tasks(2000) if t.get("conversation_id")}
    used_conv = set()   # 1 hội thoại nhiều video: chỉ video MỚI NHẤT là của job trong tool, video cũ hơn để "remote"
    for v in videos:
        cid = v["conversation_id"]
        t = ({} if cid in used_conv else by_conv.get(cid)) or {}
        used_conv.add(cid)
        local = "/videos/" in str(t.get("video_url") or "")
        # Prompt lấy từ job trong tool; job đã bị dọn (tắt server) hoặc video tạo ở máy khác thì rơi về TÊN
        # hội thoại — Dola đặt tên theo chính prompt, đủ để gộp nhóm trong Kho video.
        v.update(task_id=t.get("id", ""), task_status=t.get("status", ""), prompt=t.get("prompt") or v.get("prompt_seen") or v.get("name") or "",
                 local_url=t.get("video_url") if local else "",
                 state="local" if local else "failed_but_made" if t.get("status") == "failed" else "remote")
    return {"ok": True, "account": name, "videos": videos}


@app.post("/api/admin/redownload")
async def admin_redownload(body: RedownloadReq, x_admin_key: str | None = Header(default=None)):
    """Tải LẠI video đã xong (còn trên Dola) về máy — cho video 'chỉ trên Dola' (tải lúc chạy bị proxy rớt).
    Tải thẳng link CDN về DOWNLOAD_DIR (fallback trực tiếp nếu proxy hỏng) rồi đổi video_url của task sang /videos/."""
    _admin_auth(x_admin_key)
    if "/videos/" in (body.url or ""):
        return {"ok": True, "already": True, "url": body.url}   # đã là link local rồi
    from video_worker import _download, DownloadError
    try:
        local = await _download(body.url, body.account or "video", body.prompt or "")
    except DownloadError as e:
        raise HTTPException(502, f"Tải lại thất bại: {str(e)[:160]}")
    new_url = _public_video_url({"local_path": str(local), "video_url": body.url})
    task = store.get(body.task_id) if body.task_id else None
    if task and task.get("status") == "failed":
        # Check Video Nick cứu video của job bị báo lỗi (quá giờ / IP chết) mà Dola vẫn dựng xong → job thật ra đã xong.
        store.update(body.task_id, video_url=new_url, status="completed", error=None, failure_code=None,
                     finished_at=task.get("finished_at") or time.time())
    elif body.task_id:
        store.update(body.task_id, video_url=new_url)
    print(f"[gateway] tải lại về máy: {Path(local).name}", flush=True)
    return {"ok": True, "url": new_url, "file": Path(local).name}


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

