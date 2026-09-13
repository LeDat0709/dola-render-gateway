"""Proxy xoay theo LINK KEY (proxyxoay.shop và các nhà bán cùng kiểu "get.php?key=…").

Khác TMProxy: proxy string chính là NGUYÊN LINK nhà bán cấp, ví dụ
    https://proxyxoay.shop/api/get.php?key=ABC&nhamang=random&tinhthanh=0
Gọi GET link đó, nhà bán trả JSON có sẵn proxy hiện hành; cùng một link, tới chu kỳ thì trả IP mới.
Xác thực theo IP MÁY gọi (whitelist trên trang nhà bán) — nên chỉ máy dán key mới dùng được.

Phản hồi mỗi nhà bán mỗi khác nên parser DUNG NẠP nhiều dạng:
  - JSON có "proxyhttp":"ip:port[:user:pass]" (proxyxoay.shop) hoặc "proxy"/"http"/"data.*"
  - status 100/101/success; khi "chưa tới giờ đổi" vẫn thường kèm proxy hiện hành → dùng luôn
  - "message" để hiện thẻ làn + dò số giây phải chờ (nextRequest); "Nha Mang"/"Vi Tri" cho ISP/vị trí
Nếu format lạ, resolve trả None và ghi log — dán 1 link thật để chỉnh cho khớp.

Giữ IP CỐ ĐỊNH suốt một video: current() luôn trả IP đã cache; chỉ rotate() (đầu job / Dola chặn
710022002 / bấm "Đổi IP") mới gọi lại link, và tôn trọng khoảng chờ nhà bán ép (next_ok).
"""
import json
import re
import threading
import time
import urllib.error
import urllib.request

_lock = threading.Lock()
_cache: dict[str, dict] = {}   # link -> {server, username, password, ip, network, location, expiration, next_ok, message}
_PROXY_FIELDS = ("proxyhttp", "proxyHttp", "proxy_http", "http", "proxy", "https")
_NET_FIELDS = ("Nha Mang", "nha_mang", "nhamang", "network", "isp", "carrier")
_LOC_FIELDS = ("Vi Tri", "vi_tri", "location", "tinhthanh", "region", "city")
_EXP_FIELDS = ("Token expiration date", "expired_at", "expiration", "expire", "expiredAt")
_WAIT_FIELDS = ("nextRequest", "next_request", "nextrequest", "timeout", "ttl")
# Sàn hạn cache current(): phải LỚN hơn thời lượng render 1 video để IP không đổi giữa chừng (submit/poll/tải
# cùng IP), nhưng đủ ngắn để bản cache chết được làm mới thay vì phục vụ mãi. 10 phút > video 30s (~2–3 phút).
_CACHE_TTL_FLOOR = 600


class ProxyXoayError(RuntimeError):
    pass


def is_key_link(raw: str | None) -> bool:
    s = (raw or "").strip().lower()
    return s.startswith(("http://", "https://")) and ("get.php" in s or "key=" in s)


def mask(raw: str | None) -> str:
    """Che giá trị key trong link để hiện ra giao diện."""
    return re.sub(r"(key=)[^&\s]+", lambda m: m.group(1) + "••••", (raw or "").strip(), flags=re.I)


def _find(d: dict, keys) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip()
    return ""


def _parse_proxy_value(val: str) -> dict | None:
    """'ip:port' hoặc 'ip:port:user:pass' → {server, username?, password?}. Bỏ scheme nếu có."""
    val = val.strip()
    if "://" in val:
        val = val.split("://", 1)[1]
    parts = val.split(":")
    if len(parts) == 2:
        return {"server": f"http://{parts[0]}:{parts[1]}"}
    if len(parts) >= 4:
        return {"server": f"http://{parts[0]}:{parts[1]}", "username": parts[2], "password": ":".join(parts[3:])}
    return None


def _get(link: str) -> str:
    req = urllib.request.Request(link, headers={"accept": "application/json, text/plain, */*"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise ProxyXoayError(f"không tới được nhà bán proxy: {str(exc)[:80]}") from exc


def _wait_seconds(data: dict, message: str) -> int:
    n = _find(data, _WAIT_FIELDS)
    if n.isdigit():
        return int(n)
    m = re.search(r"(\d+)\s*(giây|giay|s|sec)", message, re.I)   # "chờ 43 giây"
    return int(m.group(1)) if m else 0


def _fetch(link: str, now: float) -> dict:
    body = _get(link).strip()
    try:
        j = json.loads(body)
        data = j if isinstance(j, dict) else {}
    except ValueError:
        data = {}
    src = {**(data.get("data") if isinstance(data.get("data"), dict) else {}), **data}
    proxy = None
    for f in _PROXY_FIELDS:
        v = src.get(f)
        if isinstance(v, str) and re.search(r"\d+\.\d+\.\d+\.\d+:\d+", v):
            proxy = _parse_proxy_value(v)
            break
    if not proxy and not data:   # phản hồi text thuần "ip:port[:user:pass]"
        m = re.search(r"\d+\.\d+\.\d+\.\d+:\d+(?::[^\s]+)?", body)
        proxy = _parse_proxy_value(m.group(0)) if m else None
    message = _find(src, ("message", "msg", "Message")) or body[:120]
    if not proxy:
        raise ProxyXoayError(message or "phản hồi không có proxy")
    ip = proxy["server"].split("://", 1)[1]
    wait = _wait_seconds(src, message)
    ent = {**proxy, "ip": ip, "network": _find(src, _NET_FIELDS), "location": _find(src, _LOC_FIELDS),
           "expiration": _find(src, _EXP_FIELDS), "message": message,
           "next_ok": now + wait, "fetched_at": now, "ttl": max(wait, _CACHE_TTL_FLOOR)}
    _cache[link] = ent
    return ent


def current(link: str) -> dict:
    """IP hiện hành: giữ IP đã cache ỔN ĐỊNH trong 1 video (submit/poll/tải cùng IP), nhưng lấy LẠI khi bản
    cache đã quá hạn (ttl = max(khoảng chờ nhà bán, sàn) tính từ lúc lấy) để không phục vụ IP đã chết mãi.
    Sàn ttl đặt > thời lượng 1 video nên không đổi IP giữa chừng; chưa có cache → gọi link."""
    now = time.time()
    with _lock:
        ent = _cache.get(link)
        if ent and now - ent["fetched_at"] < ent["ttl"]:
            return ent
        return _fetch(link, now)


def rotate(link: str) -> dict:
    """Xin IP mới: gọi lại link nếu đã qua khoảng chờ nhà bán ép; chưa tới giờ thì giữ IP cũ."""
    now = time.time()
    with _lock:
        ent = _cache.get(link)
        if ent and now < ent["next_ok"]:
            return ent
        return _fetch(link, now)


def resolve_dict(raw: str) -> dict | None:
    """Link → dict proxy cho patchright {server, username?, password?}; lỗi → None (đã log)."""
    try:
        ent = current(raw.strip())
    except ProxyXoayError as exc:
        print(f"[proxyxoay] {mask(raw)}: {exc}", flush=True)
        return None
    return {k: ent[k] for k in ("server", "username", "password") if ent.get(k)}


def lane_info(raw: str) -> dict:
    """Cho thẻ làn dưới ô proxy: IP + nhà mạng + vị trí + hạn còn lại (rỗng nếu chưa lấy được)."""
    ent = _cache.get((raw or "").strip())
    if not ent:
        return {}
    return {"ip": ent.get("ip", ""), "network": ent.get("network", ""),
            "location": ent.get("location", ""), "expiration": ent.get("expiration", "")}
