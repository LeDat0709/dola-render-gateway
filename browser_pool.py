"""Browser Account Pool: Manages accounts/ profiles with concurrency control and daily limits."""
import asyncio
import contextlib
import os
import random
import shutil
import sqlite3
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from dola_client import CreditError
from dola_error_codes import classify_dola_error, DolaErrorKind
from browser import RegionBlockedError, account_proxy_raw, mask_proxy, normalize_proxy_input
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
# Job chọn đích danh nick ĐANG BẬN: chờ nick rảnh (không giữ slot Chrome) tối đa ngần này, thay vì báo lỗi "Nick đang bận"
# ngay (đối thủ v1.0.88: job CHỜ tài nguyên, không gửi bừa). Một video 30s dựng 9–35 phút.
PINNED_BUSY_WAIT_SEC = int(os.getenv("DOLA_PINNED_BUSY_WAIT", "2700"))
PINNED_BUSY_POLL_SEC = 3.0
IP_DRAIN_POLL_SEC = 2.0   # nhịp kiểm "IP chung đã hết job chạy chưa" trước khi đổi IP lô mới
# Cách ly nick (đối thủ v1.0.88: SPAM_SO_IP → "ACC SPAM CHỜ XỬ LÝ"): lỗi trước khi gửi trên ngần này IP KHÁC NHAU trong
# SPAM_WINDOW_SEC → lỗi ở NICK (cookie/nick bị hạn chế), không phải proxy → nghỉ dài, khỏi đốt lượt mở Chrome/đổi IP.
SPAM_IP_COUNT = int(os.getenv("DOLA_SPAM_IP_COUNT", "3"))
SPAM_WINDOW_SEC = 6 * 3600
SPAM_COOLDOWN_SEC = 6 * 3600
# Lỗi của CODE/tham số (nick nào cũng gặp y hệt) → nổi lên ngay, không đốt MAX_ROTATE lượt mở Chrome + cho nick nghỉ oan.
_NOT_NICK_ERRORS = (AttributeError, NameError, TypeError, KeyError, ImportError, AssertionError, ValueError, sqlite3.Error)


def _rotation_order(a: dict) -> tuple:
    """Thứ tự xoay nick: scoring nhiều chiều để giảm captcha/risk control.

    (1) Còn NHIỀU điểm trước (15/09);
    (2) Nick lâu chưa dùng trước — rải đều, hành vi tự nhiên hơn dồn 1 nick;
    (3) Nick vừa bị cooldown (captcha/risk) → đẩy xuống cuối (penalty).

    Sort key = (-score, last_used_at). Score = remaining * 0.6 + idle_bonus * 0.4 - cooldown_penalty.
    """
    remaining = a.get("remaining") or 0
    last_used = a.get("last_used_at") or 0
    now = time.time()
    # idle_bonus: mỗi 10 phút nghỉ = +1 điểm (trần 10 = nghỉ > 100 phút)
    idle_minutes = max(0, (now - last_used) / 60) if last_used else 60
    idle_bonus = min(10, idle_minutes / 10)
    # penalty: nick vừa thoát cooldown gần đây → hành vi Dola vẫn đang soi
    cooldown_until = a.get("cooldown_until") or 0
    # Nếu cooldown đã hết nhưng mới hết < 10 phút → penalty giảm dần
    if cooldown_until > 0 and cooldown_until <= now:
        mins_since_cooldown = (now - cooldown_until) / 60
        penalty = max(0, 5 - mins_since_cooldown / 2)   # hết dần trong 10 phút
    elif cooldown_until > now:
        penalty = 10   # đang cooldown → penalty cao nhất
    else:
        penalty = 0
    score = remaining * 0.6 + idle_bonus * 0.4 - penalty
    return (-score, last_used)


class PreSubmitStallError(RuntimeError):
    """Worker treo TRƯỚC khi gửi lệnh tới Dola (chưa trừ credit) → xoay nick an toàn."""


async def _presubmit_guard(worker, account, *args, on_submitted, on_browser_hold=None, **kwargs):
    """Hủy worker nếu một pha trước khi gửi quá PRESUBMIT_TIMEOUT_SEC. on_submitted(True) → tắt đồng hồ VĨNH VIỄN
    (lệnh có thể đã tới Dola; render 30s 9–35 phút không được cắt). False khi chưa từng True (worker chắc chắn lệnh
    chưa tới Dola, sắp thử fetch lần 2 / UI) → đặt lại hạn cho pha mới.

    CHỜ SLOT CHROME KHÔNG PHẢI LÀ TREO: engine HTTP bị từ chối thì worker xin lại slot (on_browser_hold) và có thể
    xếp hàng sau các nick khác. Tính cả lúc xếp hàng vào đồng hồ thì càng tăng luồng càng nhiều nick bị báo
    "treo quá 300s" rồi bị xoay oan — đúng cảnh "tăng luồng là lỗi". Có slot rồi mới bấm giờ lại cho pha mở Chrome."""
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

    async def _hold():
        if not sent:
            cm.reschedule(None)              # tạm dừng đồng hồ TRƯỚC khi chờ slot
        if on_browser_hold:
            await on_browser_hold()
        if not sent:
            cm.reschedule(loop.time() + budget)  # có slot rồi mới bấm giờ lại

    try:
        async with cm:
            return await worker(account, *args, on_submitted=_mark,
                                on_browser_hold=_hold if on_browser_hold else None, **kwargs)
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
    return _rate_state.setdefault(key, {
        "until": 0.0, "pause": 0.0, "boost": 0.0, "boost_at": 0.0, "slot": 0.0,
        # Adaptive pacing: phần PHẠT tự học CỘNG THÊM lên config.SUBMIT_GAP_SEC (không phải gap tuyệt đối).
        # Mỗi 710022002 → +1s (trần _ADAPTIVE_EXTRA_MAX); mỗi 5 job OK liên tiếp → -0.5s, sàn 0.
        # Lưu phần phạt chứ KHÔNG lưu gap tuyệt đối: gap tuyệt đối chụp config.SUBMIT_GAP_SEC một lần lúc
        # khoá proxy được tạo, nên /api/admin/submit-gap (đổi nhịp lúc đang chạy) mất tác dụng với mọi proxy
        # đã gửi — đúng cái núm người ta với tay tới khi đang bị 710022002.
        "learned_extra": 0.0,
        "streak": 0.0,   # số job thành công liên tiếp (reset khi dính rate limit)
    })


def _reset_rate_state() -> None:
    """Xoá toàn bộ rate state. CHÚ Ý: gọi khi KHÔNG có job đang chạy (test/admin reset)."""
    _rate_state.clear()
    _PACE_LOCKS.clear()


_RATE_STATE_MAX_ENTRIES = 500


def _gc_rate_state(now=None) -> None:
    """Dọn proxy key đã 'êm' để giữ _rate_state không phình vô hạn."""
    if len(_rate_state) <= _RATE_STATE_MAX_ENTRIES:
        return
    now = time.monotonic() if now is None else now
    stale = [k for k, s in _rate_state.items()
             if s["until"] <= now and s["boost"] <= 0 and s.get("learned_extra", 0) <= 0]
    for k in stale[:max(0, len(_rate_state) - _RATE_STATE_MAX_ENTRIES)]:
        _rate_state.pop(k, None)
        _PACE_LOCKS.pop(k, None)

# Chỉ dừng-rồi-chạy-lại ở cùng nhịp thì hết dừng là dồn vào IP y như cũ → dính tiếp (vòng leo thang
# 90→180→…→900s như log). Nên mỗi lần bị chặn còn GIÃN THÊM nhịp gửi, rồi tự giảm dần khi êm. Đây là
# giảm nhẹ trên MỘT IP; muốn chạy 100+ nick thì phải chia proxy (mỗi IP vài nick).
RATE_LIMIT_GAP_STEP = 5.0    # mỗi lần dính thêm, giãn nhịp gửi thêm ngần này (giây)
RATE_LIMIT_GAP_MAX = 40.0


def _effective_gap_boost(now: float, key: str = "") -> float:
    """Giãn nhịp thêm còn lại của proxy `key`: giảm RATE_LIMIT_GAP_STEP mỗi phút êm kể từ lần chặn gần nhất.
    Hàm NÀY CHỈ ĐỌC, KHÔNG ghi — gọi bao nhiêu lần cũng ra cùng kết quả (cùng `now`)."""
    s = _rs(key)
    if s["boost"] <= 0:
        return 0.0
    decay = ((now - s["boost_at"]) / 60.0) * RATE_LIMIT_GAP_STEP
    return max(0.0, s["boost"] - max(0.0, decay))


def note_rate_limited(now: float | None = None, key: str = "") -> float:
    """Ghi nhận proxy `key` bị Dola báo gửi quá dày; trả số giây còn phải dừng. 10 job cùng dính một đợt = một lần dừng.
    Chỉ dừng gửi qua ĐÚNG proxy đó — nick trên proxy khác vẫn chạy.

    TIPEES hotfix B: gộp `learned_extra` và `boost` để bỏ double-counting. Trước đây mỗi lần dính
    `learned_extra += 1` + `boost += 5s` → gap hiệu dụng +6s/lần (log 17/09: nick ăn 2 phạt chồng).
    Nay: chỉ `learned_extra += 2`, `boost = 0`. Mỗi 5 job OK: `learned_extra -= 0.5`. Trần vẫn _ADAPTIVE_EXTRA_MAX.
    Test test_browser_pool_fixes.py kiểm _effective_gap_boost trả đúng sau hotfix."""
    s = _rs(key)
    now = time.monotonic() if now is None else now
    if now < s["until"]:
        return s["until"] - now
    recent = now - s["until"] < RATE_LIMIT_WINDOW     # dính lại sớm sau khi hết dừng → gấp đôi
    s["pause"] = min(RATE_LIMIT_PAUSE_MAX, s["pause"] * 2 if recent else RATE_LIMIT_PAUSE_SEC)
    s["until"] = now + s["pause"]
    # TIPEES hotfix B: BỎ boost (không dùng nữa), tăng learned_extra gấp đôi để bù lại phần boost cũ.
    s["learned_extra"] = min(_ADAPTIVE_EXTRA_MAX, s.get("learned_extra", 0.0) + 2.0)
    s["streak"] = 0.0
    s["boost"] = 0.0
    s["boost_at"] = 0.0
    # Mirror sang proxy_status (key-level cooling — gate trước khi gửi qua key này)
    if key:
        try:
            import proxy_status as _ps
            _ps.mark_cooling(key, reason="rate_limited", ttl=min(RATE_LIMIT_PAUSE_MAX, 300))
        except Exception:   # noqa: BLE001 — wrap không được fail logic chính
            pass
    return s["pause"]


# Trần phần PHẠT cộng thêm. Sàn là 0 — tức nhịp không bao giờ xuống dưới mức người dùng đặt, và cũng không
# bao giờ tự vượt config + trần này.
_ADAPTIVE_EXTRA_MAX = 12.0
_ADAPTIVE_STREAK_THRESHOLD = 5   # số job thành công liên tiếp để bớt phạt


def note_submit_ok(key: str = "") -> None:
    """Ghi nhận proxy `key` gửi THÀNH CÔNG: tăng streak, bớt phạt nếu đủ điều kiện."""
    s = _rs(key)
    s["streak"] = s.get("streak", 0) + 1
    if s["streak"] >= _ADAPTIVE_STREAK_THRESHOLD:
        old = s.get("learned_extra", 0.0)
        s["learned_extra"] = max(0.0, old - 0.5)
        s["streak"] = 0   # reset streak, bắt đầu đếm lại
        if s["learned_extra"] < old:
            print(f"[pace] proxy {key[:30] or 'chung'}: 5 job OK liên tiếp → bớt phạt nhịp {old:.1f}s → "
                  f"{s['learned_extra']:.1f}s (nhịp = {config.SUBMIT_GAP_SEC:.1f}s + phạt)", flush=True)
    # Mirror sang proxy_status (note_success → auto-clean cooling khi streak ≥ 5)
    if key:
        try:
            import proxy_status as _ps
            _ps.note_success(key)
        except Exception:   # noqa: BLE001
            pass


# Cổng giãn nhịp: mỗi lần gửi lệnh lấy một "khe" cách khe trước >= SUBMIT_GAP + ngẫu nhiên. Khoá chỉ giữ lúc
# tính khe, ngủ ở ngoài → N job song song tự xếp so le thay vì bắn cùng một giây.
# Khoá THEO PROXY: job trên proxy A không chặn job trên proxy B.
_PACE_LOCKS: dict[str, asyncio.Lock] = {}

# CHỜ CHỖ TRÊN IP (học từ đối thủ v1.0.88: "Đang chờ chỗ trên IP chung — còn …", "1 key = 1 IP sống"): tối đa
# config.MAX_JOBS_PER_IP job CHẠY CÙNG LÚC trên một IP ra — proxy riêng của nick, proxy chung, hoặc IP máy (khoá "").
# Giữ chỗ suốt job (gửi → dựng → tải). Hết chỗ thì CHỜ, không mở nick. _pace chỉ giãn lúc BẮT ĐẦU job; trần này mới
# chặn được cảnh 10 job cùng dội vào một IP (log: 27 nick không proxy, 10 job song song → 710022002 hàng loạt).
_egress_busy: dict[str, int] = {}
EGRESS_POLL_SEC = 0.5
# TIPEES hotfix A: nick chờ chỗ trên IP bận KHÔNG được kẹt cứng khi proxy đó đang rate-limit (s["until"]>now).
# Trước đây _egress_slot chỉ nhìn thấy _egress_busy → 4 nick đứng chờ 5+ phút trong khi nick giữ slot đang
# dính 710022002 và IP-level pause 90-900s. Grace = số giây chờ tối đa, quá thì raise DirtyIpWaitTimeout để
# flow ngoài xử lý (xoay nick hoặc proxy khác).
EGRESS_RATE_LIMIT_GRACE_SEC = float(os.getenv("DOLA_EGRESS_RATE_GRACE_SEC", "30"))

# TIPEES P1 (sửa 18/09): thay busy-poll bằng Event-based wait. Mỗi key (proxy/IP) có 1 Event,
# set khi _egress_busy[key] GIẢM (có slot vừa rảnh), clear khi job mới bắt đầu chờ. Trước đây
# mỗi waiter sleep 0.5s → 10 job chờ cùng IP = 20 wake-up/giây không cần thiết. Giờ chỉ wake đúng
# lúc slot release, hoặc sau EGRESS_POLL_SEC timeout (an toàn nếu notify bị miss).
#
# Cảnh báo event-loop: asyncio.Event() BỊ RÀNG BUỘC với loop lúc tạo. Test/loop ngắn hạn (pytest mỗi test = 1
# loop mới) sẽ nổ "bound to a different event loop" nếu cache cross-loop. Cách an toàn: KHÔNG cache Event — tạo
# mới mỗi lần _egress_slot cần, dùng asyncio.Event hiện tại. Notify bằng cách: tạo Event MỚI ở loop hiện tại,
# set ngay, rồi gán vào dict — waiter đang chờ ở loop hiện tại đọc dict sẽ thấy Event đã set. (Cách cũ là set
# Event cũ; với cross-loop là lỗi. Cách mới: set Event mới trong loop hiện tại là idempotent — waiter chỉ cần
# thấy dict trỏ đến Event đã set.)
_EGRESS_EVENTS: dict[str, asyncio.Event] = {}


def _get_egress_event(key: str) -> asyncio.Event:
    """Lấy Event cho key trong loop hiện tại. Luôn tạo mới để tránh 'bound to different event loop'."""
    ev = asyncio.Event()
    ev.set()   # set sẵn: nếu không có waiter, check vẫn đúng
    _EGRESS_EVENTS[key] = ev
    return ev


def _notify_egress(key: str) -> None:
    """Đánh thức tất cả waiter đang chờ slot trên key. Tạo Event mới + set ngay trong loop hiện tại."""
    _get_egress_event(key)   # đã set sẵn


def _egress_key(account: str) -> str:
    return normalize_proxy_input(account_proxy_raw(account) or config.PROXY)


DIRTY_IP_POLL_SEC = 15


class DirtyIpWaitTimeout(RuntimeError):
    """Chờ hết DIRTY_IP_WAIT_SEC mà IP proxy vẫn bẩn + chưa đổi được. Lệnh CHƯA gửi → không mất lượt."""


async def _wait_clean_ip(account: str, sleep=asyncio.sleep, clock=time.time) -> None:
    """TRƯỚC khi mở nick: IP proxy xoay của nick vừa bị Dola chặn (bẩn) mà còn job khác đang dựng trên key (không đổi IP
    được, đổi sẽ cắt job đó) → CHỜ, không gửi trên IP bẩn. Hết job trên key → rotate_if_expiring ngay sau đổi IP mới.
    Chờ quá DIRTY_IP_WAIT_SEC → DirtyIpWaitTimeout (chưa gửi). Proxy tĩnh / đi thẳng / IP sạch → trả về ngay.

    TIPEES hotfix Phase 4: thêm check `proxy_status.is_dirty(key)` để block ngay khi key WAF-flag,
    không cần đợi IP-level dirty (proxy_status 24h vs IP-level 24h trùng TTL, nhưng proxy_status còn
    track cooling streak để auto-clean)."""
    from browser import _effective_rotating, ip_dirty, mask_proxy, proxy_busy, rotating_status
    key = _effective_rotating(account)
    if not key or config.DIRTY_IP_WAIT_SEC <= 0:
        return
    # Phase 4: check proxy_status gate TRƯỚC — nếu key bị WAF-flag (24h), raise ngay để xoay proxy
    try:
        import proxy_status as _ps
        if _ps.is_dirty(key):
            raise DirtyIpWaitTimeout(
                f"proxy key {mask_proxy(key)} bị WAF-flag (710022002 / 24h cooldown) — xoay proxy hoặc đợi 24h"
            )
    except DirtyIpWaitTimeout:
        raise
    except Exception:   # noqa: BLE001 — wrap không được fail logic chính
        pass
    deadline = clock() + config.DIRTY_IP_WAIT_SEC
    logged = False
    while ip_dirty(rotating_status(key)) and proxy_busy(key) > 0:
        if clock() >= deadline:
            raise DirtyIpWaitTimeout(
                f"IP proxy của nick {account} ({mask_proxy(key)}) vừa bị Dola chặn vì gửi quá dày (710022002) và chưa đổi "
                f"được — còn {proxy_busy(key)} job đang dựng trên proxy này. Đã chờ {config.DIRTY_IP_WAIT_SEC // 60} phút, "
                "CHƯA gửi lệnh, không mất lượt. Thêm key proxy hoặc giảm 'Nick gửi cùng lúc'.")
        if not logged:
            logged = True
            print(f"[pool] {account}: IP proxy vừa bị Dola chặn, {proxy_busy(key)} job khác đang dựng trên proxy này → "
                  f"CHỜ đổi IP (tối đa {config.DIRTY_IP_WAIT_SEC // 60} phút), không gửi trên IP bẩn", flush=True)
        await sleep(DIRTY_IP_POLL_SEC)


@contextlib.asynccontextmanager
async def _egress_slot(account: str, on_wait=None, early_release=None):
    """Giữ 1 chỗ trên IP ra của nick.

    - on_wait(): gọi MỘT lần khi phải chờ.
    - early_release: dict {"released": bool} chia sẻ với caller — dùng để tránh double-decrement
      (cả _release_egress_early() và finally đều check + set cờ).

    TIPEES hotfix A: nếu slot IP đầy VÀ proxy đó đang rate-limit (s["until"] > now) → chờ thêm tối đa
    EGRESS_RATE_LIMIT_GRACE_SEC. Quá grace → raise DirtyIpWaitTimeout để flow ngoài xử lý (xoay nick/proxy).
    Trước đây nick chờ kẹt cứng 5+ phút trong khi nick giữ slot dính 710022002 (log 17/09 21:08–21:09).

    TIPEES P1 (sửa 18/09): chờ bằng Event thay vì busy-poll 0.5s. Khi job khác nhả slot → set Event cho key.
    Có fallback timeout EGRESS_POLL_SEC đề phòng notify miss (defensive).
    """
    key = _egress_key(account)
    waited = False
    rl_deadline: float | None = None  # set khi phát hiện rate-limit, đếm grace
    if early_release is None:
        early_release = {"released": False}
    while 0 < config.MAX_JOBS_PER_IP <= _egress_busy.get(key, 0):
        # TIPEES hotfix A: kiểm tra rate-limit THEO PROXY để không kẹt cứng
        s = _rs(key)
        now = time.monotonic()
        if now < s["until"]:
            if rl_deadline is None:
                rl_deadline = now + EGRESS_RATE_LIMIT_GRACE_SEC
                print(f"[pool] {account}: slot IP {mask_proxy(key) or 'IP máy'} đầy + proxy đang rate-limit "
                      f"({s['until'] - now:.0f}s) — chờ tối đa {EGRESS_RATE_LIMIT_GRACE_SEC:.0f}s rồi nhường "
                      f"(tránh kẹt cứng log 17/09)", flush=True)
            if now >= rl_deadline:
                raise DirtyIpWaitTimeout(
                    f"Proxy {mask_proxy(key) or 'IP máy'} rate-limit + slot IP đầy — chờ {EGRESS_RATE_LIMIT_GRACE_SEC:.0f}s "
                    f"vượt grace. Nick {account} nhường slot, xoay nick/proxy khác."
                )
        else:
            rl_deadline = None  # rate-limit đã hết, reset grace
        if not waited:
            waited = True
            where = mask_proxy(key) if key else "IP máy (không proxy)"
            print(f"[pool] {account}: chờ chỗ trên {where} — đang có {_egress_busy.get(key, 0)}/"
                  f"{config.MAX_JOBS_PER_IP} job chạy", flush=True)
            if on_wait:
                on_wait()
        # P1: chờ bằng Event — wake ngay khi job khác nhả slot (notify bên dưới trong finally).
        # Fallback timeout EGRESS_POLL_SEC phòng trường hợp notify miss (race với GC dict, v.v.).
        ev = _get_egress_event(key)
        ev.clear()
        try:
            await asyncio.wait_for(ev.wait(), timeout=EGRESS_POLL_SEC)
        except asyncio.TimeoutError:
            pass  # kiểm tra lại điều kiện vòng while
    _egress_busy[key] = _egress_busy.get(key, 0) + 1
    try:
        yield
    finally:
        if early_release.get("released"):
            # P1: nhảng rồi thì vẫn notify (early release cũng cần đánh thức waiter)
            _notify_egress(key)
            return
        early_release["released"] = True
        cur = _egress_busy.get(key)
        if cur is not None and cur > 0:
            new = cur - 1
            if new > 0:
                _egress_busy[key] = new
            else:
                _egress_busy.pop(key, None)
        # P1: đánh thức waiter cùng key để chúng retry điều kiện
        _notify_egress(key)


async def _pace(key: str = "") -> None:
    """Giãn nhịp gửi cho proxy `key`: mỗi khe cách khe trước >= SUBMIT_GAP (+giãn nếu vừa bị chặn), và chờ
    qua lệnh tạm dừng của ĐÚNG proxy đó. Proxy khác có khe riêng nên không bị một IP quá tải kéo theo.

    TIPEES P3 (sửa 18/09): thay asyncio.Lock() (serialize 2 job cùng key) bằng atomic compare-and-set
    trên _rate_state[key]["slot"]. Trước đây 2 job cùng proxy phải xếp hàng qua 1 lock → job B đợi job
    A pace xong (~30-60s) TRƯỚC KHI biết mình cần pace bao nhiêu. Nay: mỗi job tự tính base/now,
    commit slot mới nhất (CAS Python: GIL đảm bảo atomic cho dict[key] = value). Job đến sau sẽ thấy
    slot đã được commit bởi job đến trước → wait tương ứng. Vẫn serial VỀ MẶT NHỊP (khe B nối tiếp
    khe A, không bao giờ overlap), nhưng không cần lock → waiter không nằm chờ lock để "biết mình cần chờ".
    """
    s = _rs(key)
    now = time.monotonic()
    base = max(now, s["slot"], s["until"])
    wait = base - now
    # Nhịp = mức người dùng đặt (đọc MỖI LẦN, để /api/admin/submit-gap ăn ngay) + phạt tự học + giãn
    # tạm sau 710022002.
    gap = config.SUBMIT_GAP_SEC + s.get("learned_extra", 0.0) + _effective_gap_boost(now, key)
    new_slot = base + gap + random.uniform(0, config.SUBMIT_JITTER_SEC)
    # CAS: nếu ai vừa commit slot xa hơn (job song song cùng key), GIỮ slot xa hơn (an toàn).
    cur_slot = s["slot"]
    if new_slot > cur_slot:
        s["slot"] = new_slot
    # GC rate_state nếu quá lớn (chỉ khi thực sự đông)
    if len(_rate_state) > _RATE_STATE_MAX_ENTRIES:
        _gc_rate_state(now)
    # Slot ĐÃ commit trước sleep → nếu task cancel giữa sleep, job SAU vẫn thấy đúng nhịp
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
        self._one_nick = asyncio.Semaphore(1)   # XOAY IP THEO LÔ: tối đa K job (config.PARALLEL_PER_IP) cùng lúc trên IP chung
        self._one_nick_size = 1
        self._ip_lock = asyncio.Lock()           # quyết định "lô IP" (đếm/đổi IP) từng job một
        self._ip_used = 0                        # số nick đã dùng IP proxy xoay hiện tại (đổi IP sau mỗi N nick)
        # Đếm lượt THEO TỪNG KHOÁ proxy xoay — cho trường hợp mỗi nick một proxy riêng (thực tế: ~5 khoá chia
        # cho 37 nick). Một số đếm CHUNG như self._ip_used chỉ đúng khi cả kho dùng MỘT proxy chung; với proxy
        # riêng nó cộng lẫn các khoá vào nhau nên xoay sai khoá, sai lúc. Khoá dict = browser._effective_rotating.
        self._ip_used_by_key: dict[str, int] = {}
        # Khoá THEO KHOÁ proxy (khuôn _PACE_LOCKS): job trên khoá A không chặn quyết định của khoá B. Một lock
        # chung giữ qua lời gọi nhà bán (proxyxoay tới ~17s) là cả kho đứng im.
        self._ip_locks: dict[str, asyncio.Lock] = {}
        self._fail_ips: dict[str, dict[str, float]] = {}   # nick -> {đường ra (IP proxy/host/"direct"): lúc lỗi gần nhất}
        self._fail_lock: asyncio.Lock = asyncio.Lock()   # serialize _rest_after_presubmit_fail
        self._quarantine: dict[str, str] = {}            # nick -> lý do cách ly (hiện ở blocked_reason)
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
            # TIPEES hotfix C: ghi lý do cách ly nick (kết hợp cooldown_until) để UI/audit biết vì sao nick nghỉ
            ("quarantine_reason", "TEXT DEFAULT ''"),
        ):
            try:
                self._conn.execute(f"ALTER TABLE accounts_meta ADD COLUMN {column} {definition}")
                self._conn.commit()
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise

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

    async def _rest_after_presubmit_fail(self, account: str, err: Exception) -> None:
        """Nick lỗi/treo TRƯỚC khi gửi (chưa tốn credit) mà pool sắp xoay → nghỉ ngắn để job sau khỏi đâm vào nó trước.
        Tắt tự xoay thì không nghỉ: người dùng sửa proxy/cookie xong chạy lại ngay được.

        Async + _fail_lock: trước đây method này sync, nhiều job fail đồng thời trên cùng nick →
        race condition ghi _fail_ips và đặt cooldown. Giờ serialize qua asyncio.Lock."""
        if not config.AUTO_RETRY:
            return
        async with self._fail_lock:
            now = time.time()
            seen = {ip: t for ip, t in self._fail_ips.get(account, {}).items() if now - t < SPAM_WINDOW_SEC}
            seen[self._egress_id(account)] = now
            self._fail_ips[account] = seen
            rest, why = PRESUBMIT_FAIL_COOLDOWN_SEC, ""
            self._ensure_meta(account)
            if len(seen) >= SPAM_IP_COUNT:
                rest = SPAM_COOLDOWN_SEC
                why = (f"cách ly: lỗi trước khi gửi trên {len(seen)} IP khác nhau trong {SPAM_WINDOW_SEC // 3600} giờ — lỗi ở nick "
                       f"(cookie/nick bị hạn chế), không phải proxy. Lỗi cuối: {str(err)[:80]}")
                self._quarantine[account] = why
            self._conn.execute(
                "UPDATE accounts_meta SET cooldown_until=? WHERE name=?",
                (now + rest, account),
            )
            self._conn.commit()
        print(f"[pool] {account}: nghỉ {rest // 60} phút vì lỗi trước khi gửi ({why or str(err)[:80]})", flush=True)
        self._conn.execute("UPDATE accounts_meta SET cooldown_until=MAX(cooldown_until, ?) WHERE name=?",
                           (now + rest, account))
        self._conn.commit()

    @staticmethod
    def _egress_id(account: str) -> str:
        """Đường ra mạng của nick lúc này (KHÔNG gọi mạng): IP:cổng proxy xoay đang cache / host proxy tĩnh / "direct"."""
        try:
            from browser import account_proxy_raw, is_rotating_proxy, parse_proxy, rotating_status
            raw = account_proxy_raw(account) or config.PROXY or ""
            if not raw:
                return "direct"
            if is_rotating_proxy(raw):
                return rotating_status(raw).get("endpoint") or raw
            p = parse_proxy(raw)
            return p["server"] if p else raw
        except Exception:  # noqa: BLE001
            return "?"

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
        self._burn_if_enabled(account, "hết điểm")

    def _burn_if_enabled(self, account: str, reason: str) -> None:
        """ĐỐT NICK (bật config.BURN_NICKS): nick dùng hết lượt/điểm → tắt lịch + ghi chú "[ĐÃ ĐỐT dd/mm HH:MM: lý do]" (giữ
        ghi chú cũ), để mai không tự chạy lại. Chạy đích danh trong Studio vẫn tự mở lại (người dùng quyết)."""
        if not config.BURN_NICKS:
            return
        m = self._meta(account)
        if not m or "[ĐÃ ĐỐT" in (m["note"] or ""):
            return
        tag = f"[ĐÃ ĐỐT {time.strftime('%d/%m %H:%M')}: {reason[:40]}]"
        self._conn.execute("UPDATE accounts_meta SET scheduling=0, note=? WHERE name=?",
                           (f"{tag} {m['note'] or ''}".strip(), account))
        self._conn.commit()
        print(f"[pool] {account}: {tag} — tắt lịch (chế độ đốt nick)", flush=True)

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
        self._burn_if_enabled(account, "hết lượt ngày")

    def list_accounts(self) -> list:
        """Dashboard view: combines metadata, quota, and busy status."""
        from submit_http import has_real_mstoken as _has_real_mstoken
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
                "quarantine": self._quarantine.get(a, "") if m and m["cooldown_until"] > now else "",
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
                "has_mstoken": _has_real_mstoken(a),   # thiếu → gửi bằng msToken giả, dễ 710022002 (UI cảnh báo)
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
            if a.get("quarantine"):
                return f"{a['quarantine']} — còn {left} phút; đăng nhập lại nick rồi 'Bỏ nghỉ' nếu muốn chạy ngay"
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
        self._quarantine.pop(name, None)
        self._fail_ips.pop(name, None)

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
        # Cleanup in-memory caches cho nick này (Fix 15: tránh nick mới tạo cùng tên
        # "thừa hưởng" _fail_ips/_quarantine của nick cũ đã bị xoá).
        self._fail_ips.pop(name, None)
        self._quarantine.pop(name, None)
        self._proxy_stamp.pop(name, None)

    async def verify_account(self, name: str) -> bool:
        """Verifies login state in headless mode and updates cache."""
        if name not in self.accounts:
            raise FileNotFoundError(f"Profile does not exist: {name}")
        # Chống rate-limit: mọi đường tới Dola phải qua gate (cả gửi video lẫn login/verify).
        import video_worker_ui as _vw
        await _vw._global_submit_gate(name)
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

        Defensive: nếu result không phải dict (Exception, None, ...) → fallback cost=1 (Fix 21).
        """
        if not isinstance(result, dict):
            try:
                print(f"[pool] _settle nhận result={type(result).__name__} (không phải dict) -> fallback cost=1", flush=True)
            except Exception:
                pass
            self._claim(account, 1)
            self._conn.execute("UPDATE accounts_meta SET last_used_at=? WHERE name=?", (time.time(), account))
            self._conn.commit()
            return
        used = result.get("credits_used")
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
        if cb is None and self.used_today(account) >= DAILY_LIMIT:
            self._burn_if_enabled(account, "dùng hết lượt ngày")

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
        if balance < 1:
            self._burn_if_enabled(account, "hết điểm")

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
            "login_ok": a.get("login_ok"), "login_checked_at": a.get("login_checked_at") or 0,
            "remaining": a.get("remaining"),
            "scheduling": a.get("scheduling", True), "cooling": a.get("cooling", False),
            "cooldown_until": a.get("cooldown_until", 0), "quarantine": a.get("quarantine", ""),
            "busy": a.get("busy", False), "has_mstoken": a.get("has_mstoken", True),
        } for a in self.list_accounts()]

    async def verify_account_http(self, name: str):
        """Kiểm tra đăng nhập NHANH bằng cookies.json (không mở trình duyệt).

        Trả True/False; None khi không có bản sao cookie để kiểm tra.
        """
        from browser import account_proxy_url, verify_cookie_http
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
        # Kiểm qua ĐÚNG proxy của nick (giải cả proxy xoay). Trước đây luôn dùng proxy chung → nick có proxy
        # riêng bị trả None rồi phải mở Chrome để kiểm (3 luồng, tới 30s/nick) = bước "kiểm tra nick" ì ạch.
        via = await asyncio.to_thread(account_proxy_url, name)
        ok, _ = await verify_cookie_http(cookie_str, proxy=via or None)
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

    @staticmethod
    async def _wait_ip_drained(account: str) -> None:
        """Chờ tới khi không còn job nào chạy trên proxy xoay của nick (sổ giữ chỗ = 0) — để đổi IP lô mới an toàn."""
        from browser import _effective_rotating, _proxy_leases
        key = _effective_rotating(account)
        waited = False
        deadline = time.monotonic() + 2400   # tối đa 40 phút (video 30s dựng ~35 phút + dự phòng)
        while key and _proxy_leases.get(key, 0) > 0:
            if time.monotonic() >= deadline:
                print(f"[pool] {account}: chờ drain IP quá 40 phút — bỏ qua, tránh treo vô hạn", flush=True)
                break
            if not waited:
                print(f"[pool] {account}: IP chung đủ lô — chờ {_proxy_leases.get(key, 0)} job đang chạy trên IP này xong rồi đổi IP", flush=True)
                waited = True
            await asyncio.sleep(IP_DRAIN_POLL_SEC)

    async def _wait_pinned_nick(self, account: str, model, duration) -> None:
        """Nick được chọn đang bận (job khác đang dựng trên nó). Bật tự xoay mà có nick KHÁC rảnh + đủ điểm → đi luôn
        (vòng xoay bỏ qua nick bận, chạy trên nick rảnh). Không thì CHỜ nick rảnh, không giữ slot Chrome/cổng lúc chờ."""
        lock = self._locks.setdefault(account, asyncio.Lock())
        if not lock.locked():
            return
        need = self._cost_for(model, duration) or self._default_cost(model, duration)
        if config.AUTO_RETRY and any(
                a["name"] != account and self._schedulable(a) and not a.get("busy")
                and not self._credit_short(a, need, duration, model) for a in self.list_accounts()):
            return
        print(f"[pool] {account} đang bận — job chờ nick rảnh (tối đa {PINNED_BUSY_WAIT_SEC // 60} phút)", flush=True)
        deadline = time.monotonic() + PINNED_BUSY_WAIT_SEC
        while lock.locked():
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Nick '{account}' đang bận tạo video khác quá {PINNED_BUSY_WAIT_SEC // 60} phút — "
                                   "chạy lại sau hoặc chọn nick khác.")
            await asyncio.sleep(PINNED_BUSY_POLL_SEC)

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
        from browser import (is_rotating_proxy, rotate_effective_proxy,
                             _effective_rotating as _eff_rot, rotating_status as _rot_status)
        if account is not None:
            await self._wait_pinned_nick(account, model, duration)   # TRƯỚC khi giữ cổng/slot Chrome
        one_nick_on = config.ONE_NICK and is_rotating_proxy(config.PROXY)
        one_nick_held = False
        browser_held = False

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

        def _refund_if_safe(acc: str, failure_code: str) -> None:
            """Hoàn credit khi Chrome engine fail mà CHẮC CHẮN Dola chưa trừ (delivery=False).

            Dùng account làm grant_id (tracking string, không ảnh hưởng logic refund).
            Refund CHỈ khi delivery["maybe"] == False (chưa gửi thật)."""
            if delivery["maybe"]:
                return   # đã gửi rồi → credit đã trừ → không refund
            try:
                from credit_refund import CreditLedger
                ledger = CreditLedger.instance()
                if ledger.should_refund(failure_code):
                    rid = ledger.record_refund(
                        grant_id=acc,          # dùng account làm grant_id (tracking string)
                        job_id="",             # Chrome engine không có job_id (không cần)
                        failure_code=failure_code,
                    )
                    if rid:
                        print(f"[pool] {acc}: hoàn credit failure={failure_code}", flush=True)
            except Exception as _re:  # noqa: BLE001 — refund fail không chặn rotate
                print(f"[pool] {acc}: refund fail (bỏ qua): {_re}", flush=True)

        def _raise_if_delivered(acc, e):
            if delivery["maybe"] or isinstance(e, _FetchDelivered):   # _FetchDelivered: đã/có thể đã tới Dola dù cờ lỡ False
                print(f"[pool] {acc}: lỗi SAU khi lệnh đã tới Dola → KHÔNG xoay/không gửi lại "
                      f"(tránh trừ lượt 2 lần): {e}", flush=True)
                self._claim_submitted(acc)
                raise e

        async def _run_worker(acc, on_balance, seen, early_release=None):
            from browser import min_life_for, proxy_lease, rotate_if_expiring
            from submit_http import SubmitHttpRejected
            from video_worker_ui import _fetch_model_key as _vui_fetch_model_key
            # ENGINE HTTP: nếu bật + không có reference_image → gửi + poll qua HTTP (mượt, 5s submit, free browser).
            # Dola từ chối chắc chắn (SubmitHttpRejected) → rơi về Chrome fetch (bên dưới). KHÔNG acquire browser slot
            # vì _generate_via_http đã on_browser_free() ngay trong nhánh này (submit không cần Chrome).
            http_engine = (config.SUBMIT_MODE == "http" and not reference_image_paths
                           and _vui_fetch_model_key(model) is not None)
            # Chờ chỗ trên IP TRƯỚC mọi thứ; lúc chờ nhả slot Chrome (_hold_browser ngay dưới xin lại).
            # early_release (từ _run_worker_rl) cho phép nhả chỗ IP khi đang chờ 710022002 — nick khác cùng IP
            # (khoá "" cho IP máy) không kẹt "chờ chỗ trên IP" vô thời hạn trong khi nick này đang hết giờ chặn.
            async with _egress_slot(acc, on_wait=_release_browser, early_release=early_release):
                await _wait_clean_ip(acc)   # IP bẩn + đang có job khác trên key → chờ đổi IP, không gửi trên IP bẩn
                await _pace(account_proxy_raw(acc) or "")
                if http_engine:
                    # Submit + poll + download qua HTTP — KHÔNG mở Chrome. _generate_via_http tự nhả browser slot
                    # và gọi on_opening nếu cần (Dola hỏi lại → mở Chrome ở giai đoạn resume, KHÔNG gửi lại).
                    try:
                        from video_worker_ui import _generate_via_http, _NeedsBrowser, _fetch_model_key
                        timeout = config.VIDEO_TIMEOUT_30S if (duration or 0) == 30 else config.VIDEO_TIMEOUT
                        result = await _generate_via_http(
                            acc, prompt, ratio, duration or 10, _fetch_model_key(model),
                            timeout, on_conversation_id, on_poll, on_balance,
                            on_submitted=_on_submitted,
                            on_browser_free=_release_browser,
                            on_browser_hold=_hold_browser,
                        )
                        # Thoát ngay — KHÔNG vào nhánh Chrome. _settle sẽ chạy ở finally bên ngoài.
                        return result
                    except SubmitHttpRejected as exc:
                        # Dola từ chối chắc chắn (cookie/captcha/a_bogus lệch) → rơi về Chrome fetch, _hold_browser.
                        if on_submitted:
                            _on_submitted(acc, False)   # chưa tới Dola → pool được xoay/thử đường khác
                        print(f"[pool] {acc}: engine HTTP bị từ chối ({exc}); rơi về Chrome fetch", flush=True)
                        await _hold_browser()   # đã nhả slot ở _generate_via_http → xin lại
                    except _NeedsBrowser as ask:
                        # Dola hỏi lại → _generate_via_http ĐÃ tự mở Chrome trả lời (giữ conversation_id).
                        # Kết quả nằm trong ask (trả về dict) — không cần làm gì thêm.
                        pass
                await _hold_browser()
                # IP proxy xoay sắp hết tuổi mà không job nào khác đang dùng → đổi TRƯỚC khi mở nick (không chết giữa lúc gửi).
                await asyncio.to_thread(rotate_if_expiring, acc, min_life_for(duration))   # IP phải đủ sống hết job
                if on_opening:
                    try:
                        on_opening(acc)
                    except Exception as e:  # noqa: BLE001 — chỉ là nhãn hiển thị, không được làm nick bị xoay/nghỉ
                        print(f"[pool] {acc}: ghi trạng thái 'đang mở nick' lỗi (bỏ qua): {e!r}", flush=True)
                with proxy_lease(acc):   # giữ chỗ proxy suốt job: nick khác không tự đổi IP dưới chân job này
                    result = await _presubmit_guard(
                        generate_video, acc, prompt, ratio, duration, model=model,
                        on_conversation_id=on_conversation_id, on_poll=on_poll,
                        on_balance=on_balance, on_submitted=_on_submitted,
                        on_browser_free=_release_browser, on_browser_hold=_hold_browser,
                        reference_image_paths=reference_image_paths)
            try:
                self._settle(acc, result, model, duration, seen["balance"])
                # VIDEO XONG = cookie chắc chắn sống (Studio bỏ qua bước kiểm cho nick này). KHÔNG ghi lúc Dola mới nhận
                # lệnh: nick cookie chết (KHÁCH) vẫn có conversation_id rồi mới bị từ chối (ảnh 15/09 báo sống oan).
                self.set_login_status(acc, True)
                # Adaptive pacing: ghi nhận thành công cho proxy → nếu đủ streak → giảm gap
                note_submit_ok(key=account_proxy_raw(acc) or "")
            except Exception as e:  # noqa: BLE001 — video ĐÃ có: lỗi ghi sổ không được biến job thành lỗi (chạy lại = trừ 2 lần)
                print(f"[pool] {acc}: video xong nhưng ghi lượt/credit lỗi (bỏ qua): {e!r}", flush=True)
            return result

        async def _run_worker_rl(acc, on_balance, seen):
            """Wrapper để handler ngoài (line ~1665) bắt RateLimitedError — burn nick NGAY.

            Trước đây: 710022002 → retry CÙNG nick 2 lần (waits=[15s, 30s]) TRƯỚC rồi mới burn.
            Vấn đề: cùng IP → cùng WAF flag → vẫn 710022002 → lãng phí lượt mở Chrome/proxy.
            Bây giờ: dính 710022002 → raise để handler burn nick NGAY + rotate sang nick khác.
            Áp dụng cho CẢ HTTP engine lẫn Chrome engine (Phase 1+2 học từ Seedance).

            Học từ Seedance _la_loi_kich_hoat: nick bị WAF-gắn cờ là RỦI RO → xoay ra ngay,
            không phí lượt thử lại trên cùng fingerprint."""
            early_release = {"released": False}
            return await _run_worker(acc, on_balance, seen, early_release=early_release)

        try:
            # MỖI LẦN MỘT NICK: giữ cổng SUỐT job (submit + render) → 1 nick/lần. Acquire TRƯỚC
            # browser-sema để thứ tự khoá luôn one_nick→browser. NẰM TRONG try...finally để
            # CancelledError giữa 2 lần acquire không leak lock (BUG-01).
            if one_nick_on:
                want = max(1, config.PARALLEL_PER_IP)
                if want != self._one_nick_size:
                    resize_semaphore(self._one_nick, want - self._one_nick_size)
                    self._one_nick_size = want
                await self._one_nick.acquire()
                one_nick_held = True
            # Semaphore = số Chrome chạy cùng lúc (RAM), KHÔNG phải số video cùng lúc.
            await self.semaphore.acquire()
            browser_held = True

            last_err = None
            pinned = account is not None
            soft_pin = False   # ghim MỀM: vẫn ưu tiên + báo lý do thật của nick thẻ, nhưng cho xoay sang nick khác
            tried: set[str] = set()
            need = self._cost_for(model, duration) or self._default_cost(model, duration)
            if account is not None:
                match = next((a for a in self.list_accounts() if a["name"] == account), None)
                if match is None:
                    raise RuntimeError(f"Nick '{account}' không tồn tại")
                if not config.AUTO_RETRY and self._locks.setdefault(account, asyncio.Lock()).locked():
                    # Đã chờ ở _wait_pinned_nick mà vừa bị job khác giành lại (hiếm) → ghim cứng thì báo, không xoay.
                    raise RuntimeError(
                        f"Nick '{account}' đang bận tạo video khác — chờ video hiện tại xong rồi chạy tiếp.")
                if config.AUTO_RETRY:
                    # GHIM MỀM ("Tự thử lại/xoay nick khi lỗi" BẬT): ưu tiên nick của thẻ; nếu nó hết lượt/chết/
                    # bị chặn IP thì TỰ XOAY sang nick khác còn chạy được — đây mới là "xoay nick" thật trong Studio.
                    # Giữ pinned=True để khi KHÔNG còn nick nào chạy được vẫn báo LÝ DO THẬT của nick thẻ (không bọc).
                    soft_pin = True
                    # Xoay ƯU TIÊN nick còn NHIỀU điểm nhất → rải đều, né dồn 1 nick, tận dụng tối đa lượt/ngày.
                    others = sorted((a for a in self.list_accounts() if a["name"] != account),
                                    key=_rotation_order)
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
                candidates = sorted(self.list_accounts(), key=_rotation_order)
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
                    # ── Pre-flight checks (TRƯỚC khi tốn Chrome) ──────
                    # 1) Cookie check: kiểm HTTP nhanh, cookie chết → bỏ qua nick
                    try:
                        from browser import quick_cookie_check, ensure_proxy_alive
                        cookie_alive = await quick_cookie_check(account)
                        if cookie_alive is False:
                            self.set_login_status(account, False)
                            last_err = RuntimeError(f"Nick '{account}' cookie chết (pre-flight)")
                            continue
                    except Exception as _pf:  # noqa: BLE001
                        pass  # không kết luận được → để Chrome kiểm
                    # 1b) Stale re-check: cookie CHƯA check bao giờ (NULL) HOẶC check quá cũ (> 24h)
                    #     → verify_account_http (HTTP nhanh ~1s) trước khi tốn Chrome.
                    #     Seedance học: cookie "có thể chết" lúc nào cũng không hay biết → verify mỗi 24h.
                    try:
                        m = self._meta(account)
                        if m:
                            checked_at = m.get("login_checked_at") or 0.0
                            login_ok = m.get("login_ok")
                            stale = (time.time() - checked_at) > 86400
                            if login_ok is None or stale:
                                ok_http = await self.verify_account_http(account)
                                if ok_http is False:
                                    self.set_login_status(account, False)
                                    last_err = RuntimeError(f"Nick '{account}' cookie chết (stale re-check)")
                                    continue
                                self.set_login_status(account, True)
                    except Exception as _st:  # noqa: BLE001 — stale re-check fail không chặn flow
                        pass
                    # 2) Proxy warmup: kiểm TCP nhanh, proxy chết → bỏ qua nick
                    try:
                        await ensure_proxy_alive(account)
                    except RuntimeError as _pw:
                        last_err = _pw
                        await self._rest_after_presubmit_fail(account, _pw)
                        continue
                    except Exception:  # noqa: BLE001
                        pass  # timeout/lỗi lạ → để Chrome xử
                    # MỖI LẦN MỘT NICK: cổng đã giữ ở ĐẦU hàm. Mỗi IP dùng cho ĐÚNG N nick (config.NICKS_PER_IP)
                    # rồi mới xoay → không phí nhịp xoay, vẫn hạn chế trùng IP. N=1 = 1 nick/IP (an toàn nhất).
                    if one_nick_on:
                        n = max(1, config.NICKS_PER_IP)
                        async with self._ip_lock:
                            if self._ip_used >= n:
                                # IP hiện tại đã đủ N job → lô mới. Còn job chạy trên IP này thì CHỜ chúng xong rồi
                                # mới đổi; nhả slot Chrome trong lúc chờ để job đang chạy xin lại được.
                                _release_browser()
                                await self._wait_ip_drained(account)
                                await _hold_browser()
                                self._ip_used = 0
                            # Nhánh RIÊNG, không gộp vào nhánh drain ở trên: sổ = 0 cũng gồm LẦN ĐẦU sau khi bật
                            # server. Gộp vào thì job đầu tiên không xin IP mới, lô đầu chạy trên IP có sẵn (có thể
                            # là IP nick trước vừa dùng / vừa bị Dola chặn). Cùng luật với nhánh proxy riêng bên
                            # dưới: "sổ rỗng = coi như đủ lô = xoay thật".
                            if self._ip_used == 0:
                                try:
                                    await asyncio.to_thread(rotate_effective_proxy, account)   # xin IP mới cho lô N nick
                                except Exception as _e:  # noqa: BLE001 — đổi IP lỗi thì chạy tiếp IP cũ
                                    print(f"[pool] N nick/IP: đổi IP lỗi (chạy tiếp IP cũ): {_e}", flush=True)
                            self._ip_used += 1
                            used = self._ip_used
                        print(f"[pool] {account}: dùng IP proxy xoay — lượt {used}/{n} của IP này "
                              f"(tối đa {self._one_nick_size} job song song)", flush=True)
                        self._stamp_proxy(account, used=used, per=n, fresh=(used == 1))
                    elif config.XOAY_THEO_LUOT and (ip_key := _eff_rot(account)):
                        # N LƯỢT/IP CHO PROXY RIÊNG TỪNG NICK (bật bằng DOLA_XOAY_THEO_LUOT=1). Khác
                        # hẳn one_nick_on ở trên (one_nick_on = proxy CHUNG xoay, khoá 1 job/lần). Trước đây
                        # cả khối N-nick/IP nằm trong `if one_nick_on`, mà one_nick_on = ONE_NICK and
                        # is_rotating_proxy(config.PROXY) → PROXY rỗng là DOLA_NICKS_PER_IP vô tác dụng hoàn
                        # toàn. Khoá = chuỗi proxy xoay nick đang đi; proxy tĩnh/nối thẳng trả "" → rơi xuống else.
                        n = max(1, config.NICKS_PER_IP)
                        rotated = False
                        async with self._ip_locks.setdefault(ip_key, asyncio.Lock()):
                            # Chưa có trong sổ = VỪA BẬT LẠI SERVER: coi như ĐỦ LÔ, xin IP mới. Mặc định 0 thì
                            # job đầu của mỗi khoá chạy tiếp IP mà phiên trước đã dùng cạn (có thể đã bị Dola
                            # chặn) mà giao diện vẫn khoe "IP mới". Giá: mỗi khoá đúng 1 lời gọi nhà bán/lần bật.
                            used = self._ip_used_by_key.get(ip_key, n)
                            if used >= n:
                                # KHÔNG chờ drain: mỗi khoá đang gánh nhiều nick, chờ rỗng = treo luồng (người
                                # dùng vừa yêu cầu mở max luồng). An toàn vì _rotate_raw TỪ CHỐI đổi khi sổ giữ
                                # chỗ của khoá này > 0 → không cắt IP dưới chân job đã gửi (đã trừ lượt).
                                truoc = _rot_status(ip_key).get("endpoint", "")
                                try:
                                    ok = await asyncio.to_thread(rotate_effective_proxy, account)
                                except Exception as _e:  # noqa: BLE001 — đổi IP lỗi thì chạy tiếp IP cũ
                                    print(f"[pool] {account}: xin IP mới lỗi (chạy tiếp IP cũ): {_e}", flush=True)
                                    ok = False
                                # So IP TRƯỚC/SAU chứ không tin mỗi giá trị trả về: còn cooldown nhà bán thì
                                # rotate() trả NGUYÊN IP CŨ mà _rotate_raw vẫn True → reset số đếm trong khi IP
                                # không hề đổi, tưởng đã sang lô mới.
                                rotated = bool(ok) and _rot_status(ip_key).get("endpoint", "") != truoc
                                if rotated:
                                    used = 0
                            # Xoay hỏng/bị từ chối → KHÔNG reset: để số đếm leo quá n ("lượt 9/2") làm dấu hiệu
                            # nợ xoay đang treo, nhìn một dòng log là biết.
                            self._ip_used_by_key[ip_key] = used + 1
                            used = self._ip_used_by_key[ip_key]
                        print(f"[pool] {account}: proxy riêng — lượt {used}/{n} của IP này"
                              + ("" if used <= n else " — NỢ XOAY treo: chưa đổi được IP (còn job chạy trên "
                                                      "proxy này, hoặc nhà bán còn hạn chờ đổi)"), flush=True)
                        # fresh = ĐÃ ĐỔI IP THẬT, không phải "lượt 1": cờ này hiện ra giao diện là "IP mới
                        # (vừa xoay)", gắn bừa là nói dối người dùng.
                        self._stamp_proxy(account, used=used, per=n, fresh=rotated)
                    else:
                        self._stamp_proxy(account)   # proxy tĩnh/nối thẳng: chỉ hiện IP + nhà cung cấp (không có lượt)
                    try:
                        seen = {"balance": False}

                        def on_balance(balance, source=""):
                            seen["balance"] = True
                            self._set_credit_balance(account, balance, source)

                        from browser import rotate_proxy_session
                        rotate_proxy_session(account, config.PROXY_ROTATE_EVERY)   # sticky: đổi IP sau mỗi N video
                        return await _run_worker_rl(account, on_balance, seen)   # 710022002: chờ rồi thử lại cùng nick
                    except (ContentPolicyViolationError, PortraitProtectionError, PromptUnclearError) as e:
                        # Prompt/image problem, not an account problem: no rotation helps.
                        print(f"[pool] {account} rejected due to content policy: {e}", flush=True)
                        raise
                    except LoggedOutError as e:
                        print(f"[pool] {account} logged out (session invalid), disabling until re-login: {e}", flush=True)
                        self.set_login_status(account, False)
                        if getattr(e, "not_charged", False):
                            # Dola từ chối vì nick là KHÁCH: không có credit để trừ → chắc chắn chưa tốn lượt, xoay an toàn.
                            _on_submitted(account, False)
                            _refund_if_safe(account, "account_cooldown")   # hoàn credit vì nick là khách → chưa trừ
                        else:
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
                        # IP này Dola chặn theo VÙNG → đánh dấu bẩn + xin IP mới ngay, y như khi dính 710022002.
                        # Trước đây chỉ xoay nick: nick sau dễ nhận lại đúng IP vừa bị chặn, tốn thêm ~1 phút/nick.
                        from browser import mark_ip_dirty, rotate_tmproxy_now
                        mark_ip_dirty(account, f"chặn vùng: {e}")
                        if rotate_tmproxy_now(account):
                            print(f"[pool] {account}: xin IP mới do bị chặn VÙNG (khác 710022002)", flush=True)
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
                            await self._rest_after_presubmit_fail(account, e2)
                            last_err = e2
                            continue
                    except RateLimitedError as e:
                        from browser import mark_ip_dirty, rotate_tmproxy_now, account_proxy_raw as _apr, rotate_effective_proxy
                        err_text = str(e)
                        info = classify_dola_error(err_text)
                        if info.kind == DolaErrorKind.NICK_VERIFICATION:
                            # 710022002 → ByteDance WAF gắn cờ nick & IP → BURN NICK NGAY (Seedance _la_loi_kich_hoat)
                            # KHÔNG retry cùng nick: cùng IP → lại 710022002 → lãng phí lượt mở Chrome/proxy.
                            # Burn LUÔN, không check config.BURN_NICKS (lỗi này = nick đã bị Dola gắn cờ rủi ro).
                            m = self._meta(account)
                            already = bool(m and "[ĐÃ ĐỐT" in (m.get("note") or ""))
                            if not already:
                                tag = f"[ĐÃ ĐỐT {time.strftime('%d/%m %H:%M')}: {info.code} — {err_text[:40]}]"
                                self._conn.execute(
                                    "UPDATE accounts_meta SET scheduling=0, is_burned=1, burned_at=?, burn_reason=?, note=? WHERE name=?",
                                    (time.time(), f"{info.code}: {err_text[:80]}", f"{tag} {m.get('note') or ''}".strip() if m else tag, account))
                                self._conn.commit()
                                print(f"[pool] {account}: ĐỐT NICK vĩnh viễn (710022002, proxy chuyển nick khác)", flush=True)
                            else:
                                print(f"[pool] {account}: đã đốt rồi, chuyển nick", flush=True)
                            mark_ip_dirty(account, err_text)   # đánh dấu IP bẩn luôn
                            _refund_if_safe(account, "submit_4xx_no_conv_id")   # 710022002 = chưa trừ → hoàn credit
                            _raise_if_delivered(account, e)
                            last_err = e
                            continue   # rotate sang nick khác NGAY
                        # Regular rate limit (không phải verify): pause + cooldown + rotate
                        mark_ip_dirty(account, err_text)   # IP này Dola vừa chặn → nick sau không nhận lại trong 24 giờ
                        if _apr(account):
                            rotate_tmproxy_now(account)   # nick dùng proxy RIÊNG → xin IP mới cho key đó
                        else:
                            rotate_effective_proxy(account)   # proxy CHUNG → xoay IP chung (an toàn vì ONE_NICK = 1 nick/lần)
                        if config.NO_COOLDOWN:
                            print(f"[pool] {account}: rate limit — NO_COOLDOWN bật, không nghỉ, xoay ngay: {e}", flush=True)
                        else:
                            pause = note_rate_limited(key=account_proxy_raw(account) or "")
                            print(f"[pool] {account}: Dola báo rate limit — dừng gửi qua proxy này {pause:.0f}s, "
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
                        # Hoàn credit nếu CHƯA gửi (delivery["maybe"]=False) — refund an toàn, không chặn rotate
                        _refund_if_safe(account, "submit_4xx_no_conv_id")
                        print(f"[pool] {account} lỗi trước khi gửi lệnh{'' if pinned and not soft_pin else ', xoay nick'}: {e!r}", flush=True)
                        await self._rest_after_presubmit_fail(account, e)
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
            if one_nick_held:
                self._one_nick.release()   # MỖI LẦN MỘT NICK: hết job (xong/lỗi) → mở cổng cho nick sau