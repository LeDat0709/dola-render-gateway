"""TMProxy (tmproxy.com): proxy xoay theo API key, dùng như chuỗi proxy `tmproxy://API_KEY`.

Dán `tmproxy://API_KEY` vào proxy.txt của nick hoặc Kho proxy → tool tự lấy IP hiện hành, tự đổi IP
sau mỗi PROXY_ROTATE_EVERY video (rotate_proxy_session) và ngay khi Dola báo 710022002.

API (docs.tmproxy.com → TMProxy APIs, đọc 13/09/2026):
  POST https://tmproxy.com/api/proxy/get-current-proxy   {"api_key": K}
  POST https://tmproxy.com/api/proxy/get-new-proxy       {"api_key": K, "id_location": 0, "id_isp": 0}
  → {"code": 0, "message": "...", "data": {"https": "host:port", "socks5": "...", "username": "",
      "password": "", "public_ip": "...", "ip_allow": "...", "timeout": <giây còn hạn>,
      "next_request": <giây phải chờ trước lần đổi tiếp>, "expired_at": "..."}}
Xác thực: whitelist IP (ip_allow, đặt trên tmproxy.com) hoặc username/password nếu TMProxy cấp.
Mỗi key = 1 IP tại một thời điểm → 1 key cho 1 nick (hoặc vài nick cùng key, chia bằng Kho proxy).
"""
import json
import threading
import time
import urllib.error
import urllib.request

import config

PREFIX = "tmproxy://"
_API = "https://tmproxy.com/api/proxy/"
_MARGIN_SEC = 30          # coi proxy hết hạn sớm 30s để không đổi IP giữa lúc đang gửi/poll
_lock = threading.Lock()  # ponytail: một khoá cho mọi key — vài chục key, đủ dùng
_cache: dict[str, dict] = {}   # key -> {"https","username","password","public_ip","exp","next_ok"}


class TMProxyError(RuntimeError):
    pass


def is_tmproxy(raw: str | None) -> bool:
    return (raw or "").strip().lower().startswith(PREFIX)


def key_of(raw: str | None) -> str:
    return (raw or "").strip()[len(PREFIX):].strip()


def mask(raw: str | None) -> str:
    k = key_of(raw)
    return f"{PREFIX}{k[:6]}…" if k else PREFIX


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        _API + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:   # tmproxy.com trả ~1s; 8s đủ, không treo dài
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise TMProxyError(f"không tới được tmproxy.com: {str(exc)[:80]}") from exc


def _call(path: str, body: dict) -> dict:
    res = _post(path, body)
    if str(res.get("code")) != "0":
        raise TMProxyError(f"{path}: {res.get('message') or res}")
    return res.get("data") or {}


def _store(key: str, data: dict, now: float) -> dict:
    https = (data or {}).get("https") or ""
    if not https:
        raise TMProxyError("TMProxy trả về không có proxy https")
    ent = {
        "https": https,
        "username": data.get("username") or "",
        "password": data.get("password") or "",
        "public_ip": data.get("public_ip") or "",
        "exp": now + max(0, int(data.get("timeout") or 0)) - _MARGIN_SEC,
        "next_ok": now + max(0, int(data.get("next_request") or 0)),
    }
    _cache[key] = ent
    return ent


def _new_body(key: str) -> dict:
    return {"api_key": key, "id_location": config.TMPROXY_ID_LOCATION, "id_isp": config.TMPROXY_ID_ISP}


def current(key: str) -> dict:
    """Proxy hiện hành của key, cache tới khi gần hết hạn. Chưa có/hết hạn → get-current; rỗng → get-new."""
    now = time.time()
    with _lock:
        ent = _cache.get(key)
        if ent and now < ent["exp"]:
            return ent
        try:
            return _store(key, _call("get-current-proxy", {"api_key": key}), now)
        except TMProxyError:
            return _store(key, _call("get-new-proxy", _new_body(key)), now)


def rotate(key: str) -> dict:
    """Xin IP mới (get-new-proxy). Chưa tới next_request → giữ IP cũ, không lỗi."""
    now = time.time()
    with _lock:
        ent = _cache.get(key)
        if ent and now < ent["next_ok"]:
            return ent
        return _store(key, _call("get-new-proxy", _new_body(key)), now)


def resolve_dict(raw: str) -> dict | None:
    """`tmproxy://KEY` → dict proxy cho patchright {server, username?, password?}. Lỗi → None (đã log)."""
    key = key_of(raw)
    if not key:
        return None
    try:
        ent = current(key)
    except TMProxyError as exc:
        print(f"[tmproxy] {mask(raw)}: {exc}", flush=True)
        return None
    out = {"server": "http://" + ent["https"]}
    if ent["username"]:
        out["username"] = ent["username"]
    if ent["password"]:
        out["password"] = ent["password"]
    return out
