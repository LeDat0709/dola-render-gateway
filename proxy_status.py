"""proxy_status.py — Track trạng thái THEO PROXY KEY (dirty/clean/cooling/success).

Học từ Seedance AI Studio v1.0.93 (proxy_xoay.trang_thai_me, _me_can_xoay, _me_con_cho):
  - Proxy xoay có 3 trạng thái:
    1. RANH (idle/clean) — proxy xanh, sẵn dùng
    2. CHỜ (cooling) — Dola vừa chặn (rate limit / risk control), đợi X giây
    3. BẨN (dirty) — Dola gắn cờ WAF (710022002), burn cả key + đợi 24h

Khác với `_rate_state` của browser_pool (chỉ track pause/boost của pacing):
  - `_rate_state` → điều chỉnh NHỊP gửi (gap giữa 2 lần)
  - `proxy_status` → quyết định CÓ DÙNG proxy không (gate trước khi gửi)

API:
  mark_dirty(key, reason, ttl=86400) — proxy bị WAF gắn cờ (24h mặc định)
  mark_clean(key) — proxy vừa xoay IP mới thành công
  mark_cooling(key, ttl=300) — proxy rate limit (5 phút)
  is_dirty(key) — proxy có WAF-flag không
  is_cooling(key) — proxy có rate-limit cooldown không
  cooldown_left(key) — giây còn lại
  note_success(key) — ghi nhận job OK, tăng streak (giảm cooling)
  status(key) — dict toàn trạng thái (debug)

Thread-safe: dùng threading.Lock (giống _rate_state).
"""
from __future__ import annotations

import threading
import time

# Trạng thái per proxy key (chuỗi proxy của nick: tmproxy://KEY, proxyxoay://..., IP:port, etc.)
# key = chuỗi proxy đã chuẩn hoá (khoá chính của pool), không phải từng IP thoát.
_status: dict[str, dict] = {}
_lock = threading.Lock()

# TTL mặc định
DEFAULT_DIRTY_TTL_SEC = 86400     # 24h — Dola gắn cờ WAF = 24h
DEFAULT_COOLING_TTL_SEC = 300    # 5 phút — rate limit pause
# Streak để auto-clean: 5 job OK liên tiếp qua proxy = bỏ cooling (chỉ dùng cho cooling, không cho dirty)
_SUCCESS_STREAK_TO_CLEAN = 5
# GC: dọn key đã "êm" để dict không phình
_GC_MAX_ENTRIES = 200


def _now() -> float:
    return time.time()


def _new_entry() -> dict:
    """Entry mặc định cho 1 proxy key."""
    return {
        "dirty": False,
        "dirty_at": 0.0,
        "dirty_reason": "",
        "dirty_until": 0.0,
        "cooling_until": 0.0,
        "cooling_reason": "",
        "success_streak": 0,
        "last_success_at": 0.0,
        "last_used_at": 0.0,
        "total_success": 0,
        "total_dirty": 0,
    }


def _entry(key: str) -> dict:
    """Lấy entry, tạo mới nếu chưa có. Thread-safe."""
    with _lock:
        e = _status.get(key)
        if e is None:
            e = _new_entry()
            _status[key] = e
        return e


def _gc() -> None:
    """Dọn entry đã hết dirty + cooling + không dùng lâu."""
    if len(_status) <= _GC_MAX_ENTRIES:
        return
    now = _now()
    stale = [
        k for k, e in _status.items()
        if not e["dirty"]
        and e["cooling_until"] <= now
        and e["last_used_at"] < now - 3600
    ]
    for k in stale[:max(0, len(_status) - _GC_MAX_ENTRIES)]:
        _status.pop(k, None)


# === Public API ===

def mark_dirty(key: str, reason: str = "", ttl: float = DEFAULT_DIRTY_TTL_SEC) -> None:
    """Đánh dấu proxy BẨN: Dola WAF gắn cờ (710022002). TTL mặc định 24h.

    Sau 24h mới được thử lại — Dola lưu fingerprint IP+nick trong thời gian này."""
    if not key:
        return
    with _lock:
        e = _status.setdefault(key, _new_entry())
        e["dirty"] = True
        e["dirty_at"] = _now()
        e["dirty_reason"] = reason[:120]
        e["dirty_until"] = _now() + ttl
        e["total_dirty"] = int(e.get("total_dirty", 0)) + 1
        e["last_used_at"] = _now()
        _gc()


def mark_clean(key: str) -> None:
    """Đánh dấu proxy SẠCH: vừa xoay IP mới thành công, hoặc burn xong + IP mới.

    Reset dirty + cooling + streak (stale streak không được phép trigger auto-clean cooling ngay lần kế tiếp)."""
    if not key:
        return
    with _lock:
        e = _status.setdefault(key, _new_entry())
        e["dirty"] = False
        e["dirty_at"] = 0.0
        e["dirty_reason"] = ""
        e["dirty_until"] = 0.0
        e["cooling_until"] = 0.0
        e["cooling_reason"] = ""
        e["success_streak"] = 0
        e["last_used_at"] = _now()


def mark_cooling(key: str, reason: str = "", ttl: float = DEFAULT_COOLING_TTL_SEC) -> None:
    """Đánh dấu proxy đang COOLING: Dola rate limit. TTL mặc định 5 phút.

    note_success() đủ streak có thể tự bỏ cooling."""
    if not key:
        return
    with _lock:
        e = _status.setdefault(key, _new_entry())
        e["cooling_until"] = _now() + ttl
        e["cooling_reason"] = reason[:120]
        e["last_used_at"] = _now()
        _gc()


def is_dirty(key: str) -> bool:
    """Proxy có WAF-flag (burn) không? Dùng gate trước khi gửi."""
    if not key:
        return False
    with _lock:
        e = _status.get(key)
        if not e or not e["dirty"]:
            return False
        if e.get("dirty_until", 0.0) <= _now():
            e["dirty"] = False
            return False
        return True


def is_cooling(key: str) -> bool:
    """Proxy có rate-limit cooldown không?"""
    if not key:
        return False
    with _lock:
        e = _status.get(key)
        if not e:
            return False
        return e["cooling_until"] > _now()


def cooldown_left(key: str) -> float:
    """Số giây còn lại (cooling hoặc dirty). Trả 0 nếu sạch."""
    if not key:
        return 0.0
    with _lock:
        e = _status.get(key)
        if not e:
            return 0.0
        now = _now()
        cooling_left = max(0.0, e["cooling_until"] - now)
        dirty_left = max(0.0, e.get("dirty_until", 0.0) - now) if e["dirty"] else 0.0
        return max(cooling_left, dirty_left)


def note_success(key: str) -> None:
    """Ghi nhận job OK qua proxy. Đủ streak thì auto-clean cooling.

    KHÔNG tự clean dirty — WAF 24h phải hết TTL mới được thử lại."""
    if not key:
        return
    with _lock:
        e = _status.setdefault(key, _new_entry())
        e["success_streak"] = int(e.get("success_streak", 0)) + 1
        e["total_success"] = int(e.get("total_success", 0)) + 1
        e["last_success_at"] = _now()
        e["last_used_at"] = _now()
        if e["cooling_until"] > _now() and e["success_streak"] >= _SUCCESS_STREAK_TO_CLEAN:
            e["cooling_until"] = 0.0
            e["cooling_reason"] = ""
            e["success_streak"] = 0
            print(
                f"[proxy_status] {key[:30]}: streak → auto-clean cooling",
                flush=True,
            )


def status(key: str) -> dict:
    """Trả dict toàn trạng thái (cho debug/UI)."""
    if not key:
        return {}
    with _lock:
        e = _status.get(key)
        if not e:
            return {
                "key": key[:30],
                "dirty": False,
                "cooling": False,
                "cooldown_left": 0.0,
            }
        now = _now()
        cooling = e["cooling_until"] > now
        dirty = e["dirty"] and e.get("dirty_until", 0.0) > now
        return {
            "key": key[:30],
            "dirty": dirty,
            "dirty_reason": e.get("dirty_reason", "") if dirty else "",
            "dirty_left_sec": max(0, int(e.get("dirty_until", 0.0) - now)) if dirty else 0,
            "cooling": cooling,
            "cooling_reason": e.get("cooling_reason", "") if cooling else "",
            "cooldown_left_sec": max(0, int(e["cooling_until"] - now)) if cooling else 0,
            "success_streak": int(e.get("success_streak", 0)),
            "total_success": int(e.get("total_success", 0)),
            "total_dirty": int(e.get("total_dirty", 0)),
            "last_success_at": e.get("last_success_at", 0.0),
        }


def list_keys() -> list[str]:
    """Liệt kê tất cả proxy key đang track (debug/admin)."""
    with _lock:
        return sorted(_status.keys())


def reset_all() -> None:
    """Reset toàn bộ (test/admin)."""
    with _lock:
        _status.clear()


def summary() -> dict:
    """Tóm tắt (admin debug)."""
    with _lock:
        now = _now()
        dirty = sum(1 for e in _status.values() if e["dirty"] and e.get("dirty_until", 0.0) > now)
        cooling = sum(1 for e in _status.values() if e["cooling_until"] > now)
        return {
            "total_keys": len(_status),
            "dirty_count": dirty,
            "cooling_count": cooling,
        }
