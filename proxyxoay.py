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
import os
import re
import threading
import time
import urllib.error
import urllib.request

_lock = threading.Lock()
_cache: dict[str, dict] = {}   # link -> {server, username, password, ip, network, location, expiration, next_ok, message}
_last_err: dict[str, str] = {}  # link -> lý do lấy IP hỏng gần nhất (whitelist/hết hạn/không tới được) để đưa lên UI
_PROXY_FIELDS = ("proxyhttp", "proxyHttp", "proxy_http", "http", "proxy", "https")
_NET_FIELDS = ("Nha Mang", "nha_mang", "nhamang", "network", "isp", "carrier")
_LOC_FIELDS = ("Vi Tri", "vi_tri", "location", "tinhthanh", "region", "city")
_EXP_FIELDS = ("Token expiration date", "expired_at", "expiration", "expire", "expiredAt")
_WAIT_FIELDS = ("nextRequest", "next_request", "nextrequest", "timeout", "ttl")
# Sàn hạn cache current(): phải LỚN hơn thời lượng render 1 video để IP không đổi giữa chừng (submit/poll/tải
# cùng IP), nhưng đủ ngắn để bản cache chết được làm mới thay vì phục vụ mãi. 10 phút > video 30s (~2–3 phút).
_CACHE_TTL_FLOOR = 600


# proxyxoay.shop (proxy.vn / topproxy) cho KHAI THÊM IPv4 được dùng ngay trong link: &whitelist=IP (tài liệu nhà bán).
# Máy IP ĐỘNG (đo 15/09: 3–4 IP/phiên) whitelist tay xong đổi IP là proxy "chết" → tool tự khai IP hiện tại của máy mỗi khi
# IP máy đổi. get.php vẫn trả lời máy chưa whitelist (HTTP 200) nên khai được. Tắt: DOLA_PROXY_AUTO_WHITELIST=0.
AUTO_WHITELIST = os.getenv("DOLA_PROXY_AUTO_WHITELIST", "1") != "0"
_PUBLIC_IP_URL = "https://api.ipify.org"   # chỉ trả IPv4 — nhà bán chỉ nhận IPv4
_PUBLIC_IP_TTL = 300                       # hỏi lại IP máy mỗi 5 phút (IP động đổi thì lần gọi sau tự khai lại)
_pub_ip = {"ip": "", "at": 0.0}
_wl_sent: dict[str, str] = {}              # link -> IPv4 đã khai whitelist thành công gần nhất


class ProxyXoayError(RuntimeError):
    pass


def _public_ipv4(now: float) -> str:
    """IPv4 công cộng của MÁY (đi thẳng, không qua proxy); lỗi mạng → IP lần trước (hoặc "")."""
    if _pub_ip["ip"] and now - _pub_ip["at"] < _PUBLIC_IP_TTL:
        return _pub_ip["ip"]
    try:
        with urllib.request.urlopen(_PUBLIC_IP_URL, timeout=5) as r:
            ip = r.read().decode("ascii", "replace").strip()
    except (urllib.error.URLError, OSError):
        return _pub_ip["ip"]
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
        _pub_ip.update(ip=ip, at=now)
    return _pub_ip["ip"]


def _whitelist_url(link: str, now: float) -> tuple[str, str]:
    """(url gọi thật, IP đang khai). Chỉ gắn &whitelist= cho proxyxoay.shop, khi link chưa tự ghi whitelist và IP
    máy khác lần khai thành công trước — link lưu trong kho/cache KHÔNG đổi (khoá cache giữ nguyên)."""
    low = link.lower()
    if not AUTO_WHITELIST or "proxyxoay.shop" not in low or "whitelist=" in low:
        return link, ""
    ip = _public_ipv4(now)
    if not ip or _wl_sent.get(link) == ip:
        return link, ""
    return f"{link}{'&' if '?' in link else '?'}whitelist={ip}", ip


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
    url, wl_ip = _whitelist_url(link, now)
    body = _get(url).strip()
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
    prev = _cache.get(link) or {}
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    changes = (prev.get("changes", 0) if prev.get("day") == day else 0) + (prev.get("ip") != ip)   # lần ĐỔI IP trong ngày
    ent = {**proxy, "ip": ip, "network": _find(src, _NET_FIELDS), "location": _find(src, _LOC_FIELDS),
           "expiration": _find(src, _EXP_FIELDS), "message": message,
           "next_ok": now + wait, "fetched_at": now, "ttl": max(wait, _CACHE_TTL_FLOOR), "day": day, "changes": changes}
    _cache[link] = ent
    if wl_ip:
        _wl_sent[link] = wl_ip   # khai OK (có proxy trả về) → IP máy chưa đổi thì lần sau khỏi gắn lại
        print(f"[proxyxoay] {mask(link)}: đã tự khai whitelist IP máy {wl_ip}", flush=True)
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


def cached_ip(link: str) -> dict:
    """IP + nhà mạng + vị trí đang cache của link (KHÔNG gọi mạng) cho cột/thẻ trạng thái."""
    ent = _cache.get((link or "").strip())
    if not ent:
        return {}
    return {"ip": ent.get("ip", ""), "network": ent.get("network", ""), "location": ent.get("location", "")}


def status(link: str) -> dict:
    """Trạng thái IP đang cache của link (KHÔNG gọi mạng) cho Kho proxy: endpoint ip:port, nhà mạng/vị trí, tuổi IP,
    còn sống bao lâu (theo "die sau Ns" nhà bán báo, không có thì theo hạn cache), bao lâu nữa được đổi, số lần đổi hôm nay."""
    ent = _cache.get((link or "").strip())
    if not ent:
        return {}
    now = time.time()
    life = re.search(r"die\s*sau\s*(\d+)", ent.get("message", ""), re.I)
    dies_at = ent["fetched_at"] + (int(life.group(1)) if life else ent["ttl"])
    return {"endpoint": ent.get("ip", ""), "network": ent.get("network", ""), "location": ent.get("location", ""),
            "age": int(now - ent["fetched_at"]), "expires_in": int(dies_at - now),
            "rotate_in": max(0, int(ent["next_ok"] - now)), "changes": ent.get("changes", 1),
            "whitelist_ip": _wl_sent.get((link or "").strip(), "")}


def resolve_dict(raw: str) -> dict | None:
    """Link → dict proxy cho patchright {server, username?, password?}; lỗi → None (đã log)."""
    link = raw.strip()
    try:
        ent = current(link)
    except ProxyXoayError as exc:
        _last_err[link] = str(exc)
        print(f"[proxyxoay] {mask(raw)}: {exc}", flush=True)
        return None
    _last_err.pop(link, None)
    return {k: ent[k] for k in ("server", "username", "password") if ent.get(k)}


def last_error(raw: str | None) -> str:
    """Lý do lấy IP hỏng gần nhất của link (rỗng nếu chưa hỏng lần nào) — để nick báo lỗi rõ, không chung chung."""
    return _last_err.get((raw or "").strip(), "")


def lane_info(raw: str) -> dict:
    """Cho thẻ làn dưới ô proxy: IP + nhà mạng + vị trí + hạn còn lại (rỗng nếu chưa lấy được)."""
    ent = _cache.get((raw or "").strip())
    if not ent:
        return {}
    return {"ip": ent.get("ip", ""), "network": ent.get("network", ""),
            "location": ent.get("location", ""), "expiration": ent.get("expiration", "")}
