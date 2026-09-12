"""Browser Account Pool: Manages accounts/ profiles with concurrency control and daily limits."""
import asyncio
import random
import shutil
import sqlite3
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from dola_client import CreditError
from video_worker_ui import (
    AccountLimitedError,
    ContentPolicyViolationError,
    CreditInsufficientError,
    LoggedOutError,
    ParameterChangeError,
    PortraitProtectionError,
    PromptUnclearError,
    RiskControlError,
    TransientDolaError,
    generate_video,
    resume_video,
)
import config

DAILY_LIMIT = config.DAILY_LIMIT
COOLDOWN_SEC = 1800  # 30-minute cooldown on risk control

# Cổng giãn nhịp: mỗi lần gửi lệnh lấy một "khe" cách khe trước >= SUBMIT_GAP + ngẫu nhiên. Khoá chỉ giữ lúc
# tính khe, ngủ ở ngoài → N job song song tự xếp so le thay vì bắn cùng một giây.
_PACE_LOCK = asyncio.Lock()
_next_slot = 0.0


async def _pace() -> None:
    global _next_slot
    async with _PACE_LOCK:
        now = time.monotonic()
        wait = max(0.0, _next_slot - now)
        _next_slot = max(now, _next_slot) + config.SUBMIT_GAP_SEC + random.uniform(0, config.SUBMIT_JITTER_SEC)
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
            (account, date.today().isoformat()),
        ).fetchone()
        return row[0] if row else 0

    def _claim(self, account: str):
        self._conn.execute(
            "INSERT INTO usage(account, day, used) VALUES (?,?,1) "
            "ON CONFLICT(account, day) DO UPDATE SET used=used+1",
            (account, date.today().isoformat()),
        )
        self._conn.commit()

    def _next_limit_reset(self) -> float:
        """Calculates next daily quota reset timestamp."""
        try:
            tz = ZoneInfo(config.LIMIT_RESET_TZ)
        except Exception:
            # Fallback to fixed offset if tzdata is not installed.
            offsets = {"Asia/Tokyo": 9, "Asia/Hong_Kong": 8, "UTC": 0}
            tz = timezone(timedelta(hours=offsets.get(config.LIMIT_RESET_TZ, 9)))
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
            (account, date.today().isoformat(), DAILY_LIMIT),
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
            return "đang tắt lịch — bấm 'Bật lịch tất cả'"
        if a["cooling"]:
            left = max(1, int(((a.get("cooldown_until") or 0) - now) / 60))
            return f"đang nghỉ chống risk-control, còn {left} phút — bấm 'Bỏ nghỉ tất cả' nếu muốn chạy ngay"
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

    def set_login_status(self, name: str, ok: bool):
        self._ensure_meta(name)
        self._conn.execute(
            "UPDATE accounts_meta SET login_ok=?, login_checked_at=? WHERE name=?",
            (1 if ok else 0, time.time(), name),
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
        ok = await check_login_state(name)
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
        """Nick biết credit thật mà ít hơn giá video này → nói trước, khỏi mở Chrome rồi mới bị Dola từ chối."""
        cb = a["credit_balance"]
        if need and cb is not None and cb < need:
            return (f"Nick '{a['name']}' còn {cb} credit, video {duration}s ({model}) cần {need} "
                    f"— chọn nick khác hoặc giảm thời lượng")
        return None

    def _settle(self, account: str, result, model, duration, balance_seen: bool):
        """Video xong: tính lượt, học giá từ câu "N動画クレジットを使用" và trừ credit đã biết của nick.

        Không trừ khi Dola vừa báo số dư ngay trong job này (số đó đã là sau khi trừ).
        ponytail: nếu Dola báo "残り" TRƯỚC khi trừ thì lệch một video; lần thiếu credit kế tiếp
        (ParameterChangeError mang need/left) tự chỉnh lại.
        """
        self._claim(account)
        used = result.get("credits_used") if isinstance(result, dict) else None
        if used and duration and model:
            self._remember_cost(model, duration, used)
        cost = used or self._cost_for(model, duration)
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

        async def check(name: str) -> dict:
            r = None
            try:
                r = await self.verify_account_http(name)      # HTTP thuần: chạy song song thoải mái
                if r is None:
                    async with browser_slots:                 # phải mở Chrome: giới hạn cho khỏi nghẽn
                        r = await self.verify_account(name)
            except Exception as e:
                print(f"[verify] {name} lỗi: {e}", flush=True)
            return {"name": name, "ok": bool(r), "checked": r is not None}

        wanted = list(self.accounts) if names is None else [n for n in self.accounts if n in set(names)]
        return list(await asyncio.gather(*(check(n) for n in wanted)))

    async def resume_video(self, account: str, conversation_id: str, timeout: int,
                           on_poll=None, ratio: str | None = None, duration: int | None = None) -> dict:
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
                                                ratio=ratio, duration=duration,
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
                             account: str | None = None) -> dict:
        """Picks an idle schedulable account; automatically rotates on quota/risk limits.

        account: when set, only that nick is used (no rotation). Raises if it does not
        exist or is not currently usable.
        """
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

        try:
            last_err = None
            pinned = account is not None
            tried: set[str] = set()
            need = self._cost_for(model, duration)
            if account is not None:
                match = next((a for a in self.list_accounts() if a["name"] == account), None)
                if match is None:
                    raise RuntimeError(f"Nick '{account}' không tồn tại")
                if not self._schedulable(match):
                    raise RuntimeError(f"Nick '{account}' không chạy được: {self.blocked_reason(match)}")
                short = self._credit_short(match, need, duration, model)
                if short:
                    raise RuntimeError(short)
                if self._locks.setdefault(account, asyncio.Lock()).locked():
                    raise RuntimeError(
                        f"Nick '{account}' đang bận tạo video khác — chờ video hiện tại xong rồi chạy tiếp.")
                candidates = [match]
            else:
                candidates = self.list_accounts()
            for a in candidates:
                if not config.AUTO_RETRY and last_err is not None:
                    raise last_err   # người dùng tắt xoay nick: nick đầu hỏng là dừng, không thử nick khác
                if not pinned and len(tried) >= config.MAX_ROTATE:
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
                    try:
                        seen = {"balance": False}

                        def on_balance(balance, source=""):
                            seen["balance"] = True
                            self._set_credit_balance(account, balance, source)

                        await _pace()
                        await _hold_browser()
                        result = await generate_video(
                            account, prompt, ratio, duration, model=model,
                            on_conversation_id=on_conversation_id, on_poll=on_poll,
                            on_balance=on_balance, on_submitted=on_submitted,
                            on_browser_free=_release_browser, on_browser_hold=_hold_browser,
                            reference_image_paths=reference_image_paths)
                        self._settle(account, result, model, duration, seen["balance"])
                        return result
                    except (ContentPolicyViolationError, PortraitProtectionError, PromptUnclearError) as e:
                        # Prompt/image problem, not an account problem: no rotation helps.
                        print(f"[pool] {account} rejected due to content policy: {e}", flush=True)
                        raise
                    except LoggedOutError as e:
                        print(f"[pool] {account} logged out (session invalid), disabling until re-login: {e}", flush=True)
                        self.set_login_status(account, False)
                        last_err = e
                        continue
                    except CreditInsufficientError as e:
                        # Pre-flight balance too low for any generation: block until reset, rotate.
                        print(f"[pool] {account} insufficient points, skipping: {e}", flush=True)
                        self._mark_quota_blocked(account, str(e))
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
                        # This specific video costs more credits than remain (e.g. 3 needed, 2 left).
                        # The account can still make SHORTER videos today, so do NOT block it — and
                        # rotating to other free nicks (same low credits) just wastes launches.
                        # Surface Dola's clear "reduce duration" message straight to the caller.
                        print(f"[pool] {account} needs more credits for this video size (not blocking): {e}", flush=True)
                        raise
                    except AccountLimitedError as e:
                        print(f"[pool] {account} reached daily limit, rotating: {e}", flush=True)
                        self._mark_daily_limit(account, str(e))
                        last_err = e
                        continue
                    except CreditError as e:
                        print(f"[pool] {account} out of quota, rotating: {e}", flush=True)
                        self._claim(account)
                        last_err = e
                        continue
                    except TransientDolaError as e:
                        # Dola lỗi tạm thời (không phải lỗi tài khoản, thường không trừ lượt) → thử lại
                        # chính nick này 1 lần; vẫn lỗi thì xoay sang nick khác.
                        if not config.AUTO_RETRY:
                            raise   # người dùng tắt tự thử lại: không gửi lần 2 (không tạo thêm cuộc trò chuyện)
                        print(f"[pool] {account} Dola lỗi tạm thời, thử lại 1 lần: {e}", flush=True)
                        try:
                            await asyncio.sleep(3)
                            await _pace()
                            await _hold_browser()
                            result = await generate_video(
                                account, prompt, ratio, duration, model=model,
                                on_conversation_id=on_conversation_id, on_poll=on_poll,
                                on_balance=on_balance, on_submitted=on_submitted,
                                on_browser_free=_release_browser, on_browser_hold=_hold_browser,
                                reference_image_paths=reference_image_paths)
                            self._settle(account, result, model, duration, seen["balance"])
                            return result
                        except TimeoutError:
                            # Đã có conversation_id → Dola vẫn đang dựng. Xoay nick ở đây = gửi lần 2 =
                            # trừ lượt 2 lần (log 11/9 11:09, 15:40). Xử lý y như nhánh ngoài.
                            self._claim(account)
                            self._conn.execute(
                                "UPDATE accounts_meta SET last_used_at=? WHERE name=?",
                                (time.time(), account))
                            self._conn.commit()
                            raise
                        except (ContentPolicyViolationError, PortraitProtectionError, PromptUnclearError, ParameterChangeError):
                            raise   # lỗi của prompt / kích cỡ video này, không phải của nick → xoay vô ích
                        except Exception as e2:
                            # Job ghim nick thì không có nick nào để xoay — nói đúng để người dùng khỏi hiểu nhầm.
                            print(f"[pool] {account} vẫn lỗi sau khi thử lại{'' if pinned else ', xoay nick'}: {e2}", flush=True)
                            last_err = e2
                            continue
                    except RiskControlError as e:
                        print(f"[pool] {account} risk control triggered (30m cooldown), rotating: {e}", flush=True)
                        self._conn.execute(
                            "UPDATE accounts_meta SET cooldown_until=? WHERE name=?",
                            (time.time() + COOLDOWN_SEC, account))
                        self._conn.commit()
                        last_err = e
                        continue
                    except TimeoutError as e:
                        # Once conversation_id is assigned, task continues on Dola side;
                        # do not re-submit to prevent duplicate credit consumption.
                        self._claim(account)
                        self._conn.execute(
                            "UPDATE accounts_meta SET last_used_at=? WHERE name=?",
                            (time.time(), account))
                        self._conn.commit()
                        raise
                    except FileNotFoundError as e:
                        print(f"[pool] {account} profile missing, skipping: {e}", flush=True)
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