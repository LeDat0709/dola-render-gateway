"""Browser Account Pool: Manages accounts/ profiles with concurrency control and daily limits."""
import asyncio
import os
import random
import shutil
import sqlite3
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from dola_client import CreditError
from browser import RegionBlockedError, account_proxy_raw
from video_worker_ui import (
    AccountLimitedError,
    ContentPolicyViolationError,
    CreditInsufficientError,
    LoggedOutError,
    ParameterChangeError,
    PortraitProtectionError,
    PromptUnclearError,
    RateLimitedError,
    RiskControlError,
    TransientDolaError,
    _FetchDelivered,
    generate_video,
    resume_video,
)
import config

# Trần cho MỘT pha TRƯỚC khi gửi lệnh (mở Chrome → vào trang → preflight). Các bước có trần riêng cộng lại
# ~280s (proxy 12s, goto 60s×3, device_id 20s, preflight 30s, dò hội thoại 20s) → 300s: chỉ bắt chỗ KHÔNG có trần
# (launch/page.evaluate/cookies treo — ảnh 15/09 "đang mở nick" 22–53 phút giữ hết slot Chrome, job sau xếp hàng
# theo). Hủy trước khi gửi không tốn credit. Máy yếu/proxy rất chậm: tăng DOLA_PRESUBMIT_TIMEOUT.
PRESUBMIT_TIMEOUT_SEC = float(os.getenv("DOLA_PRESUBMIT_TIMEOUT", "300"))
# Nick lỗi/treo TRƯỚC khi gửi thì nghỉ ngắn: không nghỉ, nick hỏng còn nhiều điểm nhất luôn đứng đầu danh sách
# xoay → job nào cũng đâm vào nó trước (≥3 nick như vậy = mọi job "Đã thử 3 nick").
PRESUBMIT_FAIL_COOLDOWN_SEC = 600
# Lỗi của CODE/tham số (nick nào cũng gặp y hệt) → nổi lên ngay, không đốt MAX_ROTATE lượt mở Chrome + cho nick nghỉ oan.
_NOT_NICK_ERRORS = (AttributeError, NameError, TypeError, KeyError, ImportError, AssertionError, ValueError, sqlite3.Error)


class PreSubmitStallError(RuntimeError):
    """Worker treo TRƯỚC khi gửi lệnh tới Dola (chưa trừ credit) → xoay nick an toàn."""


async def _presubmit_guard(worker, account, *args, on_submitted, **kwargs):
    """Hủy worker nếu một pha trước khi gửi quá PRESUBMIT_TIMEOUT_SEC. on_submitted(True) → tắt đồng hồ VĨNH VIỄN
    (lệnh có thể đã tới Dola; render 30s 9–35 phút không được cắt). False khi chưa từng True (worker chắc chắn lệnh
    chưa tới Dola, sắp thử fetch lần 2 / UI) → đặt lại hạn cho pha mới."""
    budget = PRESUBMIT_TIMEOUT_SEC   # đọc lúc gọi, không chốt lúc import
    loop = asyncio.get_running_loop()
    cm = asyncio.timeout(budget)
    sent = False

    def _mark(acc, submitted):
        nonlocal sent
        if submitted:
            sent = True
            cm.reschedule(None)
        elif not sent:
            cm.reschedule(loop.time() + budget)
        on_submitted(acc, submitted)

    try:
        async with cm:
            return await worker(account, *args, on_submitted=_mark, **kwargs)
    except Exception as exc:
        # Chỉ đổi loại lỗi khi CHÍNH đồng hồ cắt và lệnh chưa từng gửi (kể cả khi finally đóng Chrome ném lỗi
        # khác). TimeoutError thật của worker ("Hết 2400s chưa ra video") giữ nguyên → nhánh claim+raise.
        if cm.expired() and not sent:
            raise PreSubmitStallError(
                f"Nick {account} treo quá {budget:.0f}s trước khi gửi lệnh (Chrome/proxy/trang) — "
                "chưa tốn credit, xoay nick") from exc
        raise

DAILY_LIMIT = config.DAILY_LIMIT
COOLDOWN_SEC = 1800  # 30-minute cooldown on risk control (captcha)
# Trần thời gian KIỂM PHIÊN mỗi nick: proxy chết làm bước mở Chrome/HTTP kiểm treo 10+ phút (ảnh 15/09,
# 8 nick kẹt "đang kiểm tra nick"). Hết giờ → bỏ qua nick đó (coi như chưa kiểm), KHÔNG treo cả đợt.
VERIFY_TIMEOUT_SEC = int(os.getenv("DOLA_VERIFY_TIMEOUT", "30"))

# Dola 710022002 "gửi quá dày" tính theo IP trong ngắn hạn: nhiều nick chung IP thì dính cả loạt. Trước đây
# mỗi nick dính là nghỉ 30 phút rồi xoay ngay sang nick khác → cùng IP lại dính → vài phút là bench cả kho.
# Nay: tạm dừng gửi TOÀN BỘ (mọi _pace() chờ), nick dính chỉ nghỉ ngắn; tái diễn sau khi hết dừng (trong
# RATE_LIMIT_WINDOW) thì thời gian dừng gấp đôi, tối đa RATE_LIMIT_PAUSE_MAX.
RATE_LIMIT_PAUSE_SEC = 90.0
RATE_LIMIT_PAUSE_MAX = 900.0
RATE_LIMIT_WINDOW = 600.0
RATE_LIMIT_NICK_SEC = 300
# State giãn/dừng gửi THEO PROXY (khoá = chuỗi proxy của nick, "" = đi thẳng/proxy chung). 710022002 tính
# theo IP thoát, nên một IP dính quá tải KHÔNG được làm dừng nick trên IP khác. Mỗi khoá một bộ mốc riêng.
_rate_state: dict[str, dict[str, float]] = {}


def _rs(key: str) -> dict[str, float]:
    return _rate_state.setdefault(key, {"until": 0.0, "pause": 0.0, "boost": 0.0, "boost_at": 0.0, "slot": 0.0})


def _reset_rate_state() -> None:
    _rate_state.clear()

# Chỉ dừng-rồi-chạy-lại ở cùng nhịp thì hết dừng là dồn vào IP y như cũ → dính tiếp (vòng leo thang
# 90→180→…→900s như log). Nên mỗi lần bị chặn còn GIÃN THÊM nhịp gửi, rồi tự giảm dần khi êm. Đây là
# giảm nhẹ trên MỘT IP; muốn chạy 100+ nick thì phải chia proxy (mỗi IP vài nick).
RATE_LIMIT_GAP_STEP = 5.0    # mỗi lần dính thêm, giãn nhịp gửi thêm ngần này (giây)
RATE_LIMIT_GAP_MAX = 40.0


def _effective_gap_boost(now: float, key: str = "") -> float:
    """Giãn nhịp thêm còn lại của proxy `key`: giảm RATE_LIMIT_GAP_STEP mỗi phút êm kể từ lần chặn gần nhất."""
    s = _rs(key)
    if s["boost"] <= 0:
        return 0.0
    decay = ((now - s["boost_at"]) / 60.0) * RATE_LIMIT_GAP_STEP
    s["boost"] = max(0.0, s["boost"] - max(0.0, decay))
    s["boost_at"] = now
    return s["boost"]


def note_rate_limited(now: float | None = None, key: str = "") -> float:
    """Ghi nhận proxy `key` bị Dola báo gửi quá dày; trả số giây còn phải dừng. 10 job cùng dính một đợt = một lần dừng.
    Chỉ dừng gửi qua ĐÚNG proxy đó — nick trên proxy khác vẫn chạy."""
    s = _rs(key)
    now = time.monotonic() if now is None else now
    if now < s["until"]:
        return s["until"] - now
    recent = now - s["until"] < RATE_LIMIT_WINDOW     # dính lại sớm sau khi hết dừng → gấp đôi
    s["pause"] = min(RATE_LIMIT_PAUSE_MAX, s["pause"] * 2 if recent else RATE_LIMIT_PAUSE_SEC)
    s["until"] = now + s["pause"]
    s["boost"] = min(RATE_LIMIT_GAP_MAX, _effective_gap_boost(now, key) + RATE_LIMIT_GAP_STEP)
    s["boost_at"] = now
    return s["pause"]


# Cổng giãn nhịp: mỗi lần gửi lệnh lấy một "khe" cách khe trước >= SUBMIT_GAP + ngẫu nhiên. Khoá chỉ giữ lúc
# tính khe, ngủ ở ngoài → N job song song tự xếp so le thay vì bắn cùng một giây.
_PACE_LOCK = asyncio.Lock()


async def _pace(key: str = "") -> None:
    """Giãn nhịp gửi cho proxy `key`: mỗi khe cách khe trước >= SUBMIT_GAP (+giãn nếu vừa bị chặn), và chờ
    qua lệnh tạm dừng của ĐÚNG proxy đó. Proxy khác có khe riêng nên không bị một IP quá tải kéo theo."""
    global _rate_state
    async with _PACE_LOCK:
        s = _rs(key)
        now = time.monotonic()
        base = max(now, s["slot"], s["until"])
        wait = base - now
        gap = config.SUBMIT_GAP_SEC + _effective_gap_boost(now, key)
        s["slot"] = base + gap + random.uniform(0, config.SUBMIT_JITTER_SEC)
    if wait > 0:
        await asyncio.sleep(wait)


class AllAccountsLimitedError(RuntimeError):
    """All active schedulable accounts have reached daily video limit."""


class AllAccountsQuotaBlockedError(RuntimeError):
    """All active schedulable accounts are known to have insufficient credits."""


MAX_BROWSER_SLOTS = 24   # trần cứng: mỗi slot là một Chrome thật (~0.4GB)


_PARK_TASKS: set = set()   # giữ tham chiếu, kẻo task bị GC giữa lúc đang giữ bớt permit


def resize_semaphore(sem: asyncio.Semaphore, delta: int) -> None:
    """Đổi trần một semaphore đang chạy: cộng thì nhả thêm permit, trừ thì giữ bớt lại.

    Job đang chạy không bị đụng tới; giảm trần chỉ có hiệu lực khi slot rảnh ra.
    """
    if delta > 0:
        for _ in range(delta):
            sem.release()
    elif delta < 0:
        async def park(n: int):
            for _ in range(n):
                await sem.acquire()
        task = asyncio.create_task(park(-delta))
        _PARK_TASKS.add(task)
        task.add_done_callback(_PARK_TASKS.discard)


class BrowserPool:
    def __init__(self, accounts_dir: str = "accounts", db_path: str = "pool_usage.db",
                 max_concurrency: int = 1):
        self.accounts_dir = Path(accounts_dir)
        self.max_concurrency = max(1, max_concurrency)
        self.semaphore = asyncio.Semaphore(self.max_concurrency)
        self._one_nick = asyncio.Semaphore(1)   # MỖI LẦN MỘT NICK: 1 nick chạy trọn job tại một thời điểm
        self._ip_used = 0                        # số nick đã dùng IP proxy xoay hiện tại (đổi IP sau mỗi N nick)
        self._proxy_stamp: dict[str, dict] = {}  # dấu proxy đang gắn cho job mỗi nick → cột "Proxy" hiện IP/lượt/NCC
        self._locks: dict[str, asyncio.Lock] = {}
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS usage (account TEXT, day TEXT, used INTEGER, "
            "PRIMARY KEY(account, day))"
        )
        # Chi phí credit Dola học được từ câu "N動画クレジットが使用されます" theo (model, giây).
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS credit_cost (model TEXT, duration INTEGER, credits INTEGER, "
            "PRIMARY KEY(model, duration))"
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts_meta (
                name TEXT PRIMARY KEY,
                scheduling INTEGER DEFAULT 1,
                note TEXT DEFAULT '',
                email TEXT DEFAULT '',
                created_at REAL,
                last_used_at REAL DEFAULT 0,
                login_ok INTEGER,
                login_checked_at REAL DEFAULT 0,
                cooldown_until REAL DEFAULT 0,
                rate_limited_until REAL DEFAULT 0,
                limit_reason TEXT DEFAULT '',
                quota_blocked_until REAL DEFAULT 0,
                quota_reason TEXT DEFAULT '',
                credit_balance INTEGER,
                credit_checked_at REAL DEFAULT 0
            )
            """
        )
        self._conn.commit()
        # Legacy migration: add metadata columns
        for column, definition in (
            ("email", "TEXT DEFAULT ''"),
            ("rate_limited_until", "REAL DEFAULT 0"),
            ("limit_reason", "TEXT DEFAULT ''"),
            ("quota_blocked_until", "REAL DEFAULT 0"),
            ("quota_reason", "TEXT DEFAULT ''"),
            ("credit_balance", "INTEGER"),
            ("credit_checked_at", "REAL DEFAULT 0"),
        ):
            try:
                self._conn.execute(f"ALTER TABLE accounts_meta ADD COLUMN {column} {definition}")
                self._conn.commit()
            except sqlite3.OperationalError:
                pass

    # ===== Account Discovery & Metadata =====

    def _ensure_meta(self, name: str):
        self._conn.execute(
            "INSERT OR IGNORE INTO accounts_meta (name, created_at) VALUES (?, ?)",
            (name, time.time()),
        )
        self._conn.commit()

    @property
    def accounts(self) -> list:
        if not self.accounts_dir.exists():
            return []
        names = sorted(d.name for d in self.accounts_dir.iterdir()
                       if d.is_dir() and not d.name.startswith("."))
        for n in names:
            self._ensure_meta(n)
        return names

    def _meta(self, name: str):
        return self._conn.execute(
            "SELECT * FROM accounts_meta WHERE name=?", (name,)).fetchone()

    def used_today(self, account: str) -> int:
        row = self._conn.execute(
            "SELECT used FROM usage WHERE account=? AND day=?",
            (account, self._usage_day()),
        ).fetchone()
        return row[0] if row else 0

    def _claim_submitted(self, account: str) -> None:
        """Lệnh đã tới Dola (có thể đã trừ credit) mà job hỏng → ghi lượt, KHÔNG xoay (log 11/9)."""
        self._claim(account)
        self._conn.execute("UPDATE accounts_meta SET last_used_at=? WHERE name=?", (time.time(), account))
        self._conn.commit()

    def _rest_after_presubmit_fail(self, account: str, err: Exception) -> None:
        """Nick lỗi/treo TRƯỚC khi gửi (chưa tốn credit) mà pool sắp xoay → nghỉ ngắn để job sau khỏi đâm vào nó trước.
        Tắt tự xoay thì không nghỉ: người dùng sửa proxy/cookie xong chạy lại ngay được."""
        if not config.AUTO_RETRY:
            return
        print(f"[pool] {account}: nghỉ {PRESUBMIT_FAIL_COOLDOWN_SEC // 60} phút vì lỗi trước khi gửi ({str(err)[:80]})", flush=True)
        self._conn.execute("UPDATE accounts_meta SET cooldown_until=MAX(cooldown_until, ?) WHERE name=?",
                           (time.time() + PRESUBMIT_FAIL_COOLDOWN_SEC, account))
        self._conn.commit()

    def _claim(self, account: str, cost: int = 1):
        """Cộng CREDIT đã dùng hôm nay (không phải số video): 30s Seedance 2.5 = 2 credit (đo 13/09)."""
        self._conn.execute(
            "INSERT INTO usage(account, day, used) VALUES (?,?,?) "
            "ON CONFLICT(account, day) DO UPDATE SET used=used+excluded.used",
            (account, self._usage_day(), max(1, int(cost or 1))),
        )
        self._conn.commit()

    @staticmethod
    def _default_cost(model, duration) -> int:
        """Giá khi chưa học được từ Dola (đo 13/09): Seedance 2.5 → 30s = 2 credit (payload Khan),
        10s/15s = 4 credit (Dola: 「4動画クレジットが使用されます」); Seedance 2.0 = 1 credit."""
        try:
            d = int(duration or 0)
        except (TypeError, ValueError):
            d = 0
        if "2.5" in str(model or ""):
            return 2 if d >= 30 else 4
        return 1

    @staticmethod
    def _reset_tz():
        """Múi giờ Dola reset lượt (config.LIMIT_RESET_TZ, mặc định Asia/Tokyo)."""
        try:
            return ZoneInfo(config.LIMIT_RESET_TZ)
        except Exception:
            # Fallback to fixed offset if tzdata is not installed.
            offsets = {"Asia/Tokyo": 9, "Asia/Hong_Kong": 8, "UTC": 0}
            return timezone(timedelta(hours=offsets.get(config.LIMIT_RESET_TZ, 9)))

    @classmethod
    def _usage_day(cls, ts: float | None = None) -> str:
        """Khoá ngày của bảng usage theo múi giờ reset của Dola, KHÔNG theo ngày máy.

        Dola reset 0h JST = 22h VN: video làm 22h–24h VN mà ghi theo ngày máy thì sáng hôm sau nick
        hiện 'còn 4' oan (13/09: 2 nick bị Dola báo hết lượt dù pool ghi 0/4).
        """
        return datetime.fromtimestamp(time.time() if ts is None else ts, cls._reset_tz()).date().isoformat()

    def _next_limit_reset(self) -> float:
        """Calculates next daily quota reset timestamp."""
        tz = self._reset_tz()
        now = datetime.now(tz)
        next_day = now.date() + timedelta(days=1)
        return datetime.combine(next_day, dt_time.min, tzinfo=tz).timestamp()

    def _clear_expired_rate_limits(self):
        now = time.time()
        cur = self._conn.execute(
            "UPDATE accounts_meta SET rate_limited_until=0, limit_reason='', "
            "quota_blocked_until=0, quota_reason='' "
            "WHERE (rate_limited_until > 0 AND rate_limited_until <= ?) "
            "OR (quota_blocked_until > 0 AND quota_blocked_until <= ?)", (now, now))
        if cur.rowcount:
            self._conn.commit()

    def _mark_quota_blocked(self, account: str, reason: str = ""):
        self._conn.execute(
            "UPDATE accounts_meta SET quota_blocked_until=?, quota_reason=?, last_used_at=? WHERE name=?",
            (self._next_limit_reset(), reason[:300], time.time(), account),
        )
        self._conn.commit()

    def _mark_daily_limit(self, account: str, reason: str = ""):
        """Marks account as reaching daily limit until next reset."""
        self._conn.execute(
            "INSERT INTO usage(account, day, used) VALUES (?,?,?) "
            "ON CONFLICT(account, day) DO UPDATE SET used=MAX(used, excluded.used)",
            (account, self._usage_day(), DAILY_LIMIT),
        )
        self._conn.execute(
            "UPDATE accounts_meta SET last_used_at=?, rate_limited_until=?, limit_reason=? WHERE name=?",
            (time.time(), self._next_limit_reset(), reason[:300], account),
        )
        self._conn.commit()

    def list_accounts(self) -> list:
        """Dashboard view: combines metadata, quota, and busy status."""
        self._clear_expired_rate_limits()
        now = time.time()
        reset_at = self._next_limit_reset() - 86400   # mốc reset credit gần nhất (0h JST)
        out = []
        for a in self.accounts:
            m = self._meta(a)
            used = self.used_today(a)
            lock = self._locks.get(a)
            # Credit Dola reset theo ngày: số đọc trước mốc reset là số cũ → coi như chưa biết. Không thì
            # nick về 0 credit hôm qua kẹt "hết credit" mãi (_schedulable đòi cb >= 1 khi đã biết cb).
            cb = m["credit_balance"] if (m and m["credit_checked_at"] >= reset_at) else None
            out.append({
                "name": a,
                "scheduling": bool(m["scheduling"]) if m else True,
                "note": m["note"] if m else "",
                "email": m["email"] if m else "",
                "created_at": m["created_at"] if m else 0,
                "last_used_at": m["last_used_at"] if m else 0,
                "login_ok": m["login_ok"] if m else None,
                "login_checked_at": m["login_checked_at"] if m else 0,
                "cooldown_until": m["cooldown_until"] if m else 0,
                "cooling": bool(m and m["cooldown_until"] > now),
                "rate_limited_until": m["rate_limited_until"] if m and m["rate_limited_until"] else 0,
                "rate_limited": bool(m and m["rate_limited_until"] > now),
                "limit_reason": m["limit_reason"] if m else "",
                "quota_blocked_until": m["quota_blocked_until"] if m and m["quota_blocked_until"] else 0,
                "quota_blocked": bool(m and m["quota_blocked_until"] > now),
                "quota_reason": m["quota_reason"] if m else "",
                "credit_balance": cb,
                "credit_checked_at": m["credit_checked_at"] if m else 0,
                "used_today": used,
                "limit": DAILY_LIMIT,
                "remaining": cb if cb is not None else max(0, DAILY_LIMIT - used),
                "busy": bool(lock and lock.locked()),
            })
        return out

    def blocked_reason(self, a: dict) -> str:
        """Vì sao nick này không chạy được — nói ĐÚNG MỘT lý do kèm cách xử lý.

        Trước đây mọi trường hợp đều trả về một câu liệt kê cả 4 nguyên nhân, nên người dùng
        thấy "đang tắt lịch" trong khi thật ra nick đang nghỉ chống risk-control.
        """
        now = time.time()
        if a["login_ok"] == 0:
            return "cookie chết — bấm đăng nhập lại nick"
        if not a["scheduling"]:
            return "đang tạm ngưng — bấm 'Cho chạy lại' ở Kho tài khoản (chạy đích danh trong Studio thì tự mở lại)"
        if a["cooling"]:
            left = max(1, int(((a.get("cooldown_until") or 0) - now) / 60))
            return (f"đang nghỉ (captcha/gửi quá dày/lỗi lúc mở nick), còn {left} phút — "
                    "bấm 'Bỏ nghỉ tất cả' nếu muốn chạy ngay")
        if a["rate_limited"]:
            return "hết lượt hôm nay — mai chạy lại hoặc dùng nick khác"
        if a["quota_blocked"]:
            return "Dola báo hết điểm — chờ reset hoặc dùng nick khác"
        cb = a["credit_balance"]
        if cb is not None and cb < 1:
            return "hết điểm (còn 0 credit)"
        if cb is None and a["used_today"] >= DAILY_LIMIT:
            return f"đã dùng {a['used_today']}/{DAILY_LIMIT} lượt hôm nay"
        return "không rõ lý do — xem tab Kho tài khoản"

    def _stamp_proxy(self, account: str, used=None, per=None, fresh=None) -> None:
        """Ghi IP/nhà cung cấp/lượt đang gắn cho job của nick — cột 'Proxy' hiện như đối thủ (peek cache, không gọi mạng)."""
        try:
            from browser import rotating_ip_info, account_proxy_raw, provider_label
            raw = account_proxy_raw(account) or config.PROXY
            info = rotating_ip_info(raw or "")
            prov = provider_label(raw)   # tmproxy | topproxy | proxyxoay | ""
            self._proxy_stamp[account] = {
                "ip": info.get("ip") or "", "isp": info.get("network") or info.get("location") or "",
                "provider": prov, "used": used, "per": per, "fresh": fresh,
            }
        except Exception:  # noqa: BLE001 — chỉ là thông tin hiển thị
            pass

    def proxy_stamp(self, account: str) -> dict:
        """Dấu proxy đang gắn cho nick (cho /v1/videos poll dựng cột Proxy). {} nếu chưa chạy."""
        return dict(self._proxy_stamp.get(account) or {})

    def clear_cooldown(self, name: str) -> None:
        """Bỏ trạng thái 'đang nghỉ' để chạy lại ngay (người dùng tự quyết định chấp nhận rủi ro)."""
        self._conn.execute("UPDATE accounts_meta SET cooldown_until=0 WHERE name=?", (name,))
        self._conn.commit()

    def set_scheduling(self, name: str, on: bool):
        self._conn.execute(
            "UPDATE accounts_meta SET scheduling=? WHERE name=?", (1 if on else 0, name))
        self._conn.commit()

    # Nick vừa được tạo bởi import cookie chưa có dòng meta (chỉ được tạo khi ai đó đọc .accounts)
    # → UPDATE rơi vào khoảng không, trạng thái đăng nhập vừa kiểm tra bị mất. Đăng ký trước.
    def set_email(self, name: str, email: str):
        self._ensure_meta(name)
        self._conn.execute(
            "UPDATE accounts_meta SET email=? WHERE name=?", (email, name))
        self._conn.commit()

    def set_login_status(self, name: str, ok):
        """True/False = kết luận của Dola; None = không kiểm tra được (mạng/proxy) → để trống,
        KHÔNG ghi 0: nick sống mà bị gắn "cookie chết" chỉ vì proxy mặc định không chạy."""
        self._ensure_meta(name)
        self._conn.execute(
            "UPDATE accounts_meta SET login_ok=?, login_checked_at=? WHERE name=?",
            (None if ok is None else (1 if ok else 0), time.time(), name),
        )
        self._conn.commit()

    def set_note(self, name: str, note: str):
        self._conn.execute(
            "UPDATE accounts_meta SET note=? WHERE name=?", (note, name))
        self._conn.commit()

    def assert_idle(self, name: str):
        """Ném RuntimeError nếu nick đang render (đang giữ khoá pool).

        Phải gọi TRƯỚC mọi thao tác đụng profile từ đường KHÔNG qua khoá pool
        (xoá cookie, mở cửa sổ profile): assert_profile_free sẽ GIẾT tiến trình đang
        giữ profile chứ không từ chối, nên nếu không chặn ở đây thì xoá cookie giữa
        lúc render sẽ giết luôn video đang chạy.
        """
        lock = self._locks.get(name)
        if lock and lock.locked():
            raise RuntimeError(f"Nick '{name}' đang tạo video — chờ xong rồi hãy thao tác.")

    def delete_account(self, name: str):
        lock = self._locks.get(name)
        if lock and lock.locked():
            raise RuntimeError("Account is generating video, cannot delete")
        d = self.accounts_dir / name
        if d.exists():
            shutil.rmtree(d)
        self._conn.execute("DELETE FROM accounts_meta WHERE name=?", (name,))
        self._conn.commit()

    async def verify_account(self, name: str) -> bool:
        """Verifies login state in headless mode and updates cache."""
        if name not in self.accounts:
            raise FileNotFoundError(f"Profile does not exist: {name}")
        lock = self._locks.setdefault(name, asyncio.Lock())
        if lock.locked():
            raise RuntimeError("Account is generating video, please verify later")
        from browser import check_login_state
        ok = await check_login_state(name)   # RegionBlockedError nổi lên nguyên: không ghi "cookie chết" oan
        self._conn.execute(
            "UPDATE accounts_meta SET login_ok=?, login_checked_at=? WHERE name=?",
            (1 if ok else 0, time.time(), name),
        )
        self._conn.commit()
        return ok

    # ===== Scheduling =====

    def _remember_cost(self, model: str, duration: int, credits: int):
        self._conn.execute(
            "INSERT OR REPLACE INTO credit_cost(model, duration, credits) VALUES (?,?,?)",
            (model, int(duration), int(credits)))
        self._conn.commit()

    def _cost_for(self, model: str, duration) -> int | None:
        if not duration:
            return None
        row = self._conn.execute(
            "SELECT credits FROM credit_cost WHERE model=? AND duration=?", (model, int(duration))).fetchone()
        return row[0] if row else None

    def _credit_short(self, a: dict, need, duration, model) -> str | None:
        """Nick không đủ credit cho video này → nói trước, khỏi mở Chrome rồi mới bị Dola từ chối.

        Biết số dư thật (cb) thì so với cb; chưa biết thì so với trần ngày (used_today đếm theo credit).
        Lý do có nhánh sau: 13/09 nick đã tiêu 4/4 credit (2 video 30s) nhưng pool đếm 2 video → vẫn mở
        Chrome rồi Dola báo 「1日あたりの上限に達しました」.
        """
        if not need:
            return None
        cb = a["credit_balance"]
        if cb is not None and cb < need:
            return (f"Nick '{a['name']}' còn {cb} credit, video {duration}s ({model}) cần {need} "
                    f"— {self._credit_advice(model, duration)}")
        if cb is None and a["used_today"] + need > DAILY_LIMIT:
            return (f"Nick '{a['name']}' đã dùng {a['used_today']}/{DAILY_LIMIT} credit hôm nay, video {duration}s "
                    f"({model}) cần {need} — {self._credit_advice(model, duration)}")
        return None

    @staticmethod
    def _credit_advice(model, duration) -> str:
        """Lời khuyên khi thiếu lượt. Seedance 2.5: 30s = 2 lượt (Khan, RẺ NHẤT), 10s/15s = 4 lượt →
        video ngắn thiếu lượt phải TĂNG lên 30s chứ không phải giảm (bug 15/09: tool khuyên 'giảm giây')."""
        try:
            d = int(duration or 0)
        except (TypeError, ValueError):
            d = 0
        if "2.5" in str(model or "") and d < 30:
            return "để 30s (rẻ nhất, chỉ 2 lượt) hoặc đổi nick khác"
        return "đổi nick khác (nick này hết lượt hôm nay)"

    def _settle(self, account: str, result, model, duration, balance_seen: bool):
        """Video xong: tính lượt, học giá từ câu "N動画クレジットを使用" và trừ credit đã biết của nick.

        Không trừ khi Dola vừa báo số dư ngay trong job này (số đó đã là sau khi trừ).
        ponytail: nếu Dola báo "残り" TRƯỚC khi trừ thì lệch một video; lần thiếu credit kế tiếp
        (ParameterChangeError mang need/left) tự chỉnh lại.
        """
        used = result.get("credits_used") if isinstance(result, dict) else None
        if used and duration and model:
            self._remember_cost(model, duration, used)
        cost = used or self._cost_for(model, duration) or self._default_cost(model, duration)
        self._claim(account, cost)
        m = self._meta(account)
        cb = m["credit_balance"] if m else None
        if cost and cb is not None and not balance_seen:
            self._set_credit_balance(account, cb - cost, "after-job")
        self._conn.execute("UPDATE accounts_meta SET last_used_at=? WHERE name=?", (time.time(), account))
        self._conn.commit()

    def _set_credit_balance(self, account: str, balance: int, source: str = ""):
        self._conn.execute(
            "UPDATE accounts_meta SET credit_balance=?, credit_checked_at=? WHERE name=?",
            (max(0, int(balance)), time.time(), account),
        )
        if balance < 2:
            self._conn.execute(
                "UPDATE accounts_meta SET quota_blocked_until=?, quota_reason=? WHERE name=?",
                (self._next_limit_reset(), source[:300] or "Insufficient credits", account),
            )
        self._conn.commit()

    def _credit_available(self, account: str, required: int = 2) -> bool:
        row = self._meta(account)
        return not row or row["credit_balance"] is None or row["credit_balance"] >= required

    def _schedulable(self, a: dict) -> bool:
        cb = a["credit_balance"]
        # Biết credit thật → dùng credit (còn >=1 là chạy được video ngắn); chưa biết → dùng cap ngày.
        credit_ok = (a["used_today"] < DAILY_LIMIT) if cb is None else (cb >= 1)
        return (a["scheduling"] and not a["cooling"] and not a["rate_limited"]
                and not a["quota_blocked"] and a["login_ok"] != 0 and credit_ok)

    @property
    def all_accounts_limited(self) -> bool:
        """Returns True if all active accounts have reached daily limit."""
        candidates = [a for a in self.list_accounts() if a["scheduling"] and not a["cooling"]]
        return bool(candidates) and all(
            a["rate_limited"] or a["used_today"] >= DAILY_LIMIT for a in candidates
        )

    @property
    def all_accounts_quota_blocked(self) -> bool:
        candidates = [a for a in self.list_accounts() if a["scheduling"] and not a["cooling"]]
        return bool(candidates) and all(
            a["quota_blocked"] or a["rate_limited"] or a["used_today"] >= DAILY_LIMIT
            for a in candidates
        ) and any(a["quota_blocked"] for a in candidates)

    @property
    def available(self) -> bool:
        return any(self._schedulable(a) for a in self.list_accounts())

    @property
    def cookie_count(self) -> int:  # /health compatibility
        return len(self.accounts)

    def account_status(self) -> list:
        # scheduling/cooling/busy phải có mặt: thiếu chúng thì tab Studio (đọc /health)
        # vẽ nick đã tắt lịch hoặc đang nghỉ thành "sẵn sàng" và bắn job vào đó.
        return [{
            "account": a["name"], "used_today": a["used_today"], "limit": a["limit"],
            "rate_limited": a["rate_limited"], "rate_limited_until": a["rate_limited_until"],
            "quota_blocked": a["quota_blocked"], "quota_blocked_until": a["quota_blocked_until"],
            "login_ok": a.get("login_ok"), "remaining": a.get("remaining"),
            "scheduling": a.get("scheduling", True), "cooling": a.get("cooling", False),
            "cooldown_until": a.get("cooldown_until", 0),
            "busy": a.get("busy", False),
        } for a in self.list_accounts()]

    async def verify_account_http(self, name: str):
        """Kiểm tra đăng nhập NHANH bằng cookies.json (không mở trình duyệt).

        Trả True/False; None khi không có bản sao cookie để kiểm tra.
        """
        from browser import verify_cookie_http
        import json as _json
        f = self.accounts_dir / name / "cookies.json"
        if not f.exists():
            return None
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in data
                                   if c.get("name") and c.get("value"))
        except Exception:
            return None
        if "sessionid" not in cookie_str and "sid_guard" not in cookie_str:
            return None
        ok, _ = await verify_cookie_http(cookie_str)
        self.set_login_status(name, ok)
        return ok

    def set_max_concurrency(self, limit: int) -> int:
        """Đổi số nick được gửi cùng lúc mà không cần khởi động lại server."""
        limit = max(1, min(int(limit), MAX_BROWSER_SLOTS))
        resize_semaphore(self.semaphore, limit - self.max_concurrency)
        self.max_concurrency = limit
        return limit

    async def verify_all(self, names: list | None = None) -> list:
        """Kiểm tra phiên các nick (ưu tiên HTTP nhanh, thiếu cookie backup thì dùng trình duyệt).

        names=None: kiểm tra tất cả. Truyền danh sách để chỉ kiểm tra đúng mấy nick đang cần.
        """
        browser_slots = asyncio.Semaphore(config.LOGIN_CONCURRENCY)

        async def _check_one(name: str) -> dict:
            r = None
            try:
                r = await self.verify_account_http(name)      # HTTP thuần: chạy song song thoải mái
                if r is None:
                    async with browser_slots:                 # phải mở Chrome: giới hạn cho khỏi nghẽn
                        r = await self.verify_account(name)
            except Exception as e:
                print(f"[verify] {name} lỗi: {e}", flush=True)
            return {"name": name, "ok": bool(r), "checked": r is not None}

        async def check(name: str) -> dict:
            # Proxy chết → mở Chrome/HTTP kiểm phiên treo rất lâu. Cắt ở VERIFY_TIMEOUT_SEC: hết giờ coi như
            # "chưa kiểm" (checked=False) để không treo cả đợt; wait_for hủy task → Chrome tự đóng (async with).
            try:
                return await asyncio.wait_for(_check_one(name), timeout=VERIFY_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                print(f"[verify] {name} quá {VERIFY_TIMEOUT_SEC}s (proxy chết/mạng chậm?) → bỏ qua, không treo", flush=True)
                return {"name": name, "ok": False, "checked": False}

        wanted = list(self.accounts) if names is None else [n for n in self.accounts if n in set(names)]
        return list(await asyncio.gather(*(check(n) for n in wanted)))

    async def resume_video(self, account: str, conversation_id: str, timeout: int,
                           on_poll=None, ratio: str | None = None, duration: int | None = None,
                           prompt: str = "") -> dict:
        """Resumes an accepted session without re-scheduling.

        Slot Chrome (semaphore) nhả ngay khi worker chuyển sang theo dõi HTTP — như generate_video.
        Giữ suốt render thì sau restart, N job khôi phục chiếm hết N slot tới 15 phút.
        """
        await self.semaphore.acquire()
        browser_held = True

        def _release_browser():
            nonlocal browser_held
            if browser_held:
                browser_held = False
                self.semaphore.release()

        async def _hold_browser():
            nonlocal browser_held
            if not browser_held:
                await self.semaphore.acquire()
                browser_held = True

        try:
            lock = self._locks.setdefault(account, asyncio.Lock())
            async with lock:
                seen = {"balance": False}

                def on_balance(balance, source=""):
                    seen["balance"] = True
                    self._set_credit_balance(account, balance, source)
                try:
                    result = await resume_video(account, conversation_id, timeout,
                                                on_poll=on_poll, on_balance=on_balance,
                                                ratio=ratio, duration=duration, prompt=prompt,
                                                on_browser_free=_release_browser, on_browser_hold=_hold_browser)
                    self._settle(account, result, None, duration, seen["balance"])
                    return result
                except TimeoutError:
                    self._claim(account)
                    self._conn.commit()
                    raise
        finally:
            _release_browser()

    async def generate_video(self, prompt: str, ratio: str = None, duration: int = None,
                             model: str = "seedance_v2.0", on_conversation_id=None,
                             on_poll=None, on_balance=None, on_submitted=None,
                             reference_image_paths: list[str] | None = None,
                             account: str | None = None, on_opening=None) -> dict:
        """Picks an idle schedulable account; automatically rotates on quota/risk limits.

        account: when set, only that nick is used (no rotation). Raises if it does not
        exist or is not currently usable.
        on_opening(account): gọi khi đã có slot Chrome + nick và BẮT ĐẦU mở nick — trước đó job chỉ đang chờ
        lượt (UI tách "chờ slot Chrome" khỏi "đang mở nick" để thấy ngay job nào treo thật).
        """
        # MỖI LẦN MỘT NICK: giữ cổng SUỐT job (submit + render) → 1 nick/lần. Acquire TRƯỚC browser-sema để
        # thứ tự khoá luôn one_nick→browser (nếu acquire trong vòng lặp, nhánh retry gọi lại browser-sema khi
        # đang giữ one_nick sẽ khoá chéo với job kia đang giữ browser-sema chờ one_nick). Chỉ khi proxy chung xoay.
        from browser import is_rotating_proxy, rotate_effective_proxy
        one_nick_on = config.ONE_NICK and is_rotating_proxy(config.PROXY)
        if one_nick_on:
            await self._one_nick.acquire()
        # Semaphore = số Chrome chạy cùng lúc (RAM), KHÔNG phải số video cùng lúc: khi bật
        # DOLA_HTTP_POLL worker gọi on_browser_free ngay sau khi gửi xong (~20s) nên slot được trả
        # lại trong lúc video vẫn đang render → nhiều nick chạy song song mà không tốn thêm RAM.
        await self.semaphore.acquire()
        browser_held = True

        def _release_browser():
            nonlocal browser_held
            if browser_held:
                browser_held = False
                self.semaphore.release()

        async def _hold_browser():
            nonlocal browser_held
            if not browser_held:
                await self.semaphore.acquire()
                browser_held = True

        # CỔNG "ĐÃ GỬI": Dola TRỪ LƯỢT ngay khi nhận lệnh, không phải khi video xong. Worker gọi on_submitted(True)
        # ngay TRƯỚC lúc lệnh có thể rời máy, chỉ gọi False khi CHẮC CHẮN Dola không nhận. Lỗi nổ lúc cờ còn True =
        # có thể đã trừ lượt → xoay/thử lại = gửi lần 2 = trừ 2 lần (log 11/9) → phải nổi lỗi. Cờ sống theo JOB
        # (biến cục bộ, không để trên self: job song song sẽ ghi đè nhau), không reset theo nick: True thì mọi nhánh
        # đều raise nên không bao giờ sang nick kế.
        delivery = {"maybe": False}

        def _on_submitted(acc, submitted: bool):
            delivery["maybe"] = submitted          # gán TRƯỚC khi chuyển cho server: callback server lỗi vẫn giữ cờ
            if on_submitted:
                on_submitted(acc, submitted)       # server ghi submitted_at → restart không chạy lại job đã gửi

        def _raise_if_delivered(acc, e):
            if delivery["maybe"] or isinstance(e, _FetchDelivered):   # _FetchDelivered: đã/có thể đã tới Dola dù cờ lỡ False
                print(f"[pool] {acc}: lỗi SAU khi lệnh đã tới Dola → KHÔNG xoay/không gửi lại "
                      f"(tránh trừ lượt 2 lần): {e}", flush=True)
                self._claim_submitted(acc)
                raise e

        async def _run_worker(acc, on_balance, seen):
            await _pace(account_proxy_raw(acc) or "")
            await _hold_browser()
            if on_opening:
                try:
                    on_opening(acc)
                except Exception as e:  # noqa: BLE001 — chỉ là nhãn hiển thị, không được làm nick bị xoay/nghỉ
                    print(f"[pool] {acc}: ghi trạng thái 'đang mở nick' lỗi (bỏ qua): {e!r}", flush=True)
            result = await _presubmit_guard(
                generate_video, acc, prompt, ratio, duration, model=model,
                on_conversation_id=on_conversation_id, on_poll=on_poll,
                on_balance=on_balance, on_submitted=_on_submitted,
                on_browser_free=_release_browser, on_browser_hold=_hold_browser,
                reference_image_paths=reference_image_paths)
            try:
                self._settle(acc, result, model, duration, seen["balance"])
            except Exception as e:  # noqa: BLE001 — video ĐÃ có: lỗi ghi sổ không được biến job thành lỗi (chạy lại = trừ 2 lần)
                print(f"[pool] {acc}: video xong nhưng ghi lượt/credit lỗi (bỏ qua): {e!r}", flush=True)
            return result

        try:
            last_err = None
            pinned = account is not None
            soft_pin = False   # ghim MỀM: vẫn ưu tiên + báo lý do thật của nick thẻ, nhưng cho xoay sang nick khác
            tried: set[str] = set()
            need = self._cost_for(model, duration) or self._default_cost(model, duration)
            if account is not None:
                match = next((a for a in self.list_accounts() if a["name"] == account), None)
                if match is None:
                    raise RuntimeError(f"Nick '{account}' không tồn tại")
                if self._locks.setdefault(account, asyncio.Lock()).locked():
                    # BẬN thì CHỜ, KHÔNG xoay — người dùng đã chọn đúng nick này, video hiện tại xong rồi chạy tiếp.
                    raise RuntimeError(
                        f"Nick '{account}' đang bận tạo video khác — chờ video hiện tại xong rồi chạy tiếp.")
                if config.AUTO_RETRY:
                    # GHIM MỀM ("Tự thử lại/xoay nick khi lỗi" BẬT): ưu tiên nick của thẻ; nếu nó hết lượt/chết/
                    # bị chặn IP thì TỰ XOAY sang nick khác còn chạy được — đây mới là "xoay nick" thật trong Studio.
                    # Giữ pinned=True để khi KHÔNG còn nick nào chạy được vẫn báo LÝ DO THẬT của nick thẻ (không bọc).
                    soft_pin = True
                    # Xoay ƯU TIÊN nick còn NHIỀU điểm nhất → rải đều, né dồn 1 nick, tận dụng tối đa lượt/ngày.
                    others = sorted((a for a in self.list_accounts() if a["name"] != account),
                                    key=lambda a: -(a.get("remaining") or 0))
                    candidates = [match] + others
                else:
                    # GHIM CỨNG (toggle TẮT): nick không chạy được → báo lý do thật, không xoay.
                    if not self._schedulable(match):
                        raise RuntimeError(f"Nick '{account}' không chạy được: {self.blocked_reason(match)}")
                    short = self._credit_short(match, need, duration, model)
                    if short:
                        raise RuntimeError(short)
                    candidates = [match]
            else:
                # Không ghim: cũng ưu tiên nick còn nhiều điểm nhất trước.
                candidates = sorted(self.list_accounts(), key=lambda a: -(a.get("remaining") or 0))
            for a in candidates:
                if not config.AUTO_RETRY and last_err is not None:
                    raise last_err   # người dùng tắt xoay nick: nick đầu hỏng là dừng, không thử nick khác
                if (not pinned or soft_pin) and len(tried) >= config.MAX_ROTATE:
                    raise RuntimeError(
                        f"Đã thử {len(tried)} nick ({', '.join(sorted(tried))}) đều lỗi — dừng để không đốt lượt. "
                        f"Lỗi cuối: {last_err}")
                if not self._schedulable(a):
                    continue
                short = self._credit_short(a, need, duration, model)
                if short:
                    last_err = RuntimeError(short)
                    continue
                account = a["name"]
                lock = self._locks.setdefault(account, asyncio.Lock())
                # Skip busy accounts to prevent concurrent collisions on same profile.
                if lock.locked():
                    continue
                async with lock:
                    if not self._schedulable(next(x for x in self.list_accounts() if x['name'] == account)):
                        continue  # State changed while waiting
                    tried.add(account)
                    # MỖI LẦN MỘT NICK: cổng đã giữ ở ĐẦU hàm. Mỗi IP dùng cho ĐÚNG N nick (config.NICKS_PER_IP)
                    # rồi mới xoay → không phí nhịp xoay, vẫn hạn chế trùng IP. N=1 = 1 nick/IP (an toàn nhất).
                    if one_nick_on:
                        n = max(1, config.NICKS_PER_IP)
                        if self._ip_used >= n:
                            self._ip_used = 0                       # IP hiện tại đã đủ N nick → lô mới, xoay IP
                        if self._ip_used == 0:
                            try:
                                await asyncio.to_thread(rotate_effective_proxy, account)   # xin IP mới cho lô N nick
                            except Exception as _e:  # noqa: BLE001 — đổi IP lỗi thì chạy tiếp IP cũ
                                print(f"[pool] N nick/IP: đổi IP lỗi (chạy tiếp IP cũ): {_e}", flush=True)
                        self._ip_used += 1
                        print(f"[pool] {account}: dùng IP proxy xoay — lượt {self._ip_used}/{n} của IP này", flush=True)
                        self._stamp_proxy(account, used=self._ip_used, per=n, fresh=(self._ip_used == 1))
                    else:
                        self._stamp_proxy(account)   # không bật N nick/IP: vẫn hiện IP + nhà cung cấp (không có lượt)
                    try:
                        seen = {"balance": False}

                        def on_balance(balance, source=""):
                            seen["balance"] = True
                            self._set_credit_balance(account, balance, source)

                        from browser import rotate_proxy_session
                        rotate_proxy_session(account, config.PROXY_ROTATE_EVERY)   # sticky: đổi IP sau mỗi N video
                        return await _run_worker(account, on_balance, seen)
                    except (ContentPolicyViolationError, PortraitProtectionError, PromptUnclearError) as e:
                        # Prompt/image problem, not an account problem: no rotation helps.
                        print(f"[pool] {account} rejected due to content policy: {e}", flush=True)
                        raise
                    except LoggedOutError as e:
                        print(f"[pool] {account} logged out (session invalid), disabling until re-login: {e}", flush=True)
                        self.set_login_status(account, False)
                        _raise_if_delivered(account, e)   # logout lúc poll/resume = sau khi gửi → không xoay
                        last_err = e
                        continue
                    except RegionBlockedError as e:
                        # Màn "Dola không khả dụng ở khu vực này" = lỗi đường ra mạng, không phải nick.
                        # Nick có proxy riêng → proxy đó chết/đổi vùng, nick khác (proxy khác) có thể qua → xoay.
                        # Không có proxy riêng → mọi nick cùng đi một đường → xoay chỉ tốn ~1 phút/nick → dừng ngay.
                        print(f"[pool] {account} bị Dola chặn vùng: {e}", flush=True)
                        if not account_proxy_raw(account):
                            raise
                        _raise_if_delivered(account, e)   # resume_video mở lại Dola sau khi gửi cũng ném lỗi này
                        last_err = e
                        continue
                    except CreditInsufficientError as e:
                        # Pre-flight balance too low for any generation: block until reset, rotate.
                        print(f"[pool] {account} insufficient points, skipping: {e}", flush=True)
                        self._mark_quota_blocked(account, str(e))
                        _raise_if_delivered(account, e)   # preflight ở lần gửi 2/UI: lần 1 có thể đã trừ
                        last_err = e
                        continue
                    except ParameterChangeError as e:
                        # Học từ câu Dola: giá video này (model, giây) và credit còn lại của nick → lần sau
                        # bỏ qua nick không đủ credit ngay từ trước khi mở Chrome.
                        need_now, left_now = getattr(e, "need", None), getattr(e, "left", None)
                        if need_now and duration:
                            self._remember_cost(model, duration, need_now)
                        if left_now is not None:
                            self._set_credit_balance(account, left_now, "param")
                        # Video này đắt hơn số credit còn lại (vd cần 4, còn 2). Nick vẫn làm được video rẻ hơn → KHÔNG khoá.
                        # Hiện Dola chỉ báo câu này TRONG POLL (sau khi gửi) → cổng nổi lỗi, không tạo lại trên nick khác.
                        # Nếu có lúc báo TRƯỚC khi nhận lệnh thì xoay: _credit_short đã học giá, bỏ qua nick thiếu điểm.
                        print(f"[pool] {account} needs more credits for this video size (not blocking): {e}", flush=True)
                        _raise_if_delivered(account, e)
                        last_err = e
                        continue
                    except AccountLimitedError as e:
                        print(f"[pool] {account} reached daily limit, rotating: {e}", flush=True)
                        self._mark_daily_limit(account, str(e))
                        _raise_if_delivered(account, e)   # 上限 trong poll: hội thoại chia 2 bản có thể đã trừ bản 1
                        last_err = e
                        continue
                    except CreditError as e:
                        # Dola từ chối vì hết điểm/quota → KHÔNG trừ credit (không có video), nhưng phải
                        # ghi nick về 0 credit: nếu không, cột nick cứ hiện "còn 4" (lấy từ trần ngày) rồi
                        # lần sau lại chọn đúng nick này → lỗi lại → đốt lượt oan (ảnh 15/09: còn 4 mà báo
                        # hết điểm). cb=0 → không schedulable nữa, UI hiện "còn 0", tự mở lại sau reset 0h JST.
                        print(f"[pool] {account} out of quota, mark 0 credit + rotating: {e}", flush=True)
                        self._set_credit_balance(account, 0, str(e))
                        _raise_if_delivered(account, e)
                        last_err = e
                        continue
                    except TransientDolaError as e:
                        # Dola lỗi tạm thời lúc vào trang (chưa gửi) → thử lại chính nick này 1 lần; vẫn lỗi thì xoay.
                        if not config.AUTO_RETRY:
                            raise   # người dùng tắt tự thử lại: không gửi lần 2 (không tạo thêm cuộc trò chuyện)
                        _raise_if_delivered(account, e)   # lỗi tạm thời trong poll = SAU khi gửi → thử lại = gửi lần 2
                        print(f"[pool] {account} Dola lỗi tạm thời, thử lại 1 lần: {e}", flush=True)
                        try:
                            await asyncio.sleep(3)
                            return await _run_worker(account, on_balance, seen)
                        except TimeoutError:
                            # Đã có conversation_id → Dola vẫn đang dựng. Xoay nick ở đây = gửi lần 2 =
                            # trừ lượt 2 lần (log 11/9 11:09, 15:40). Xử lý y như nhánh ngoài.
                            self._claim_submitted(account)
                            raise
                        except (ContentPolicyViolationError, PortraitProtectionError, PromptUnclearError,
                                ParameterChangeError, *_NOT_NICK_ERRORS):
                            raise   # lỗi của prompt / kích cỡ video / code, không phải của nick → xoay vô ích
                        except Exception as e2:
                            _raise_if_delivered(account, e2)   # lần thử lại ĐÃ gửi rồi mới lỗi → nick khác gửi nữa = trừ lần 2
                            # Job ghim nick thì không có nick nào để xoay — nói đúng để người dùng khỏi hiểu nhầm.
                            print(f"[pool] {account} vẫn lỗi sau khi thử lại{'' if pinned else ', xoay nick'}: {e2}", flush=True)
                            self._rest_after_presubmit_fail(account, e2)
                            last_err = e2
                            continue
                    except RateLimitedError as e:
                        from browser import rotate_tmproxy_now
                        rotate_tmproxy_now(account)   # nick dùng tmproxy://KEY: Dola chặn IP này → xin IP mới ngay
                        if config.NO_COOLDOWN:
                            print(f"[pool] {account}: 710022002 — NO_COOLDOWN bật, không nghỉ/không dừng gửi, xoay ngay: {e}", flush=True)
                        else:
                            pause = note_rate_limited(key=account_proxy_raw(account) or "")
                            print(f"[pool] {account}: Dola báo gửi quá dày (710022002) — dừng gửi qua proxy này {pause:.0f}s, "
                                  f"nick nghỉ {RATE_LIMIT_NICK_SEC // 60} phút rồi xoay: {e}", flush=True)
                            self._conn.execute(
                                "UPDATE accounts_meta SET cooldown_until=? WHERE name=?",
                                (time.time() + RATE_LIMIT_NICK_SEC, account))
                            self._conn.commit()
                        _raise_if_delivered(account, e)   # worker chỉ báo False khi dò đủ không thấy hội thoại mới
                        last_err = e
                        continue
                    except RiskControlError as e:
                        if config.NO_COOLDOWN:
                            print(f"[pool] {account} captcha/risk — NO_COOLDOWN bật, không nghỉ 30 phút, xoay ngay: {e}", flush=True)
                        else:
                            print(f"[pool] {account} risk control triggered (30m cooldown), rotating: {e}", flush=True)
                            self._conn.execute(
                                "UPDATE accounts_meta SET cooldown_until=? WHERE name=?",
                                (time.time() + COOLDOWN_SEC, account))
                            self._conn.commit()
                        _raise_if_delivered(account, e)   # captcha UI sau Enter: không xác nhận được → không xoay
                        last_err = e
                        continue
                    except TimeoutError:
                        # Once conversation_id is assigned, task continues on Dola side;
                        # do not re-submit to prevent duplicate credit consumption.
                        self._claim_submitted(account)
                        raise
                    except _NOT_NICK_ERRORS:
                        raise   # lỗi code/tham số: nick nào cũng gặp y hệt → xoay chỉ đốt lượt mở Chrome + cho nick nghỉ oan
                    except FileNotFoundError as e:
                        print(f"[pool] {account} profile missing, skipping: {e}", flush=True)
                        _raise_if_delivered(account, e)   # stat/mkdir file video sau khi xong
                        last_err = e
                        continue
                    except Exception as e:
                        # Lỗi chưa phân loại (proxy riêng không lấy được IP, Chrome không mở, treo trước khi gửi…):
                        # chỉ xoay khi lệnh CHƯA tới Dola. Sau khi gửi các lỗi này vẫn nổ được (poll/tải/resume) → nổi lỗi.
                        _raise_if_delivered(account, e)
                        print(f"[pool] {account} lỗi trước khi gửi lệnh{'' if pinned and not soft_pin else ', xoay nick'}: {e!r}", flush=True)
                        self._rest_after_presubmit_fail(account, e)
                        last_err = e
                        continue
            if pinned and last_err is not None:
                # Job trỏ đúng 1 nick: nói lý do thật ("Hết lượt hôm nay…"). Bọc thành "No available
                # accounts" thì giao diện dịch ra "Hết nick chạy được" trong khi nick đang "sẵn sàng".
                raise last_err
            if self.all_accounts_quota_blocked:
                raise AllAccountsQuotaBlockedError(
                    f"429: All schedulable accounts have insufficient points: {last_err or 'No accounts'}"
                )
            if self.all_accounts_limited:
                raise AllAccountsLimitedError(
                    f"429: All schedulable accounts have reached Dola daily limit: {last_err or 'No accounts'}"
                )
            raise RuntimeError(f"No available accounts in pool: {last_err or 'No accounts'}")
        finally:
            _release_browser()
            if one_nick_on:
                self._one_nick.release()   # MỖI LẦN MỘT NICK: hết job (xong/lỗi) → mở cổng cho nick sau