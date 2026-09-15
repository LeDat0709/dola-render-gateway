"""Kho proxy tập trung cho gateway (giống broker đối thủ), gắn vào server.py.

Tính năng:
  - Nhận proxy dạng host:port:user:pass, host:port, scheme://user:pass@host:port, socks5://…
  - Kiểm tra tự động: 8 luồng song song, timeout 15s/proxy, tự lọc proxy chết.
  - Che mật khẩu ở MỌI API admin (không lộ kho gốc).
  - Tự cấp proxy cho nick chưa có proxy riêng (chia vòng tròn, tối đa N nick/IP).

Lưu ở DATA_DIR/proxy_pool.json. Endpoint (mount vào server.py):
  GET    /api/admin/proxies         -> {ok, proxies:[{id, proxy(che), scheme, alive, last_check, nicks}], stats}
  POST   /api/admin/proxies         -> thêm (body {text} nhiều dòng)  -> {ok, added, total}
  POST   /api/admin/proxies/check   -> kiểm tra tất cả (8 luồng/15s)  -> {ok, alive, dead}
  POST   /api/admin/proxies/prune   -> xoá proxy chết                 -> {ok, removed}
  POST   /api/admin/proxies/assign  -> chia proxy sống cho nick chưa có (body {per_ip, scope}) -> {ok, assigned}
  POST   /api/admin/proxies/{id}/rotate -> đổi IP proxy xoay rồi kiểm lại -> {ok, proxy}
  DELETE /api/admin/proxies/{id}    -> xoá 1 proxy khỏi kho

Tự kiểm (không cần mạng): python proxy_pool.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import aiohttp
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

CHECK_CONCURRENCY = int(os.getenv("PROXY_CHECK_THREADS", "8"))     # 8 luồng song song
CHECK_TIMEOUT = float(os.getenv("PROXY_CHECK_TIMEOUT", "15"))      # 15 giây/proxy
CHECK_URL = os.getenv("PROXY_CHECK_URL", "https://www.dola.com/")  # proxy phải với tới Dola mới coi là sống
# Hỏi IP RA thật qua proxy (IP + nhà mạng + tỉnh) để Kho proxy hiện "IP ra 116.107.x.x · viettel HungYen" như tool đối thủ.
IPINFO_URL = os.getenv("PROXY_IPINFO_URL", "http://ip-api.com/json/?fields=status,query,isp,city,regionName")
DEFAULT_PER_IP = int(os.getenv("PROXY_PER_IP", "5"))


def _proxy_id(raw: str) -> str:
    return hashlib.sha1(raw.strip().encode()).hexdigest()[:12]


def mask_proxy(raw: str) -> str:
    """Che mật khẩu: host:port:user:pass -> host:port:user:***; scheme://user:pass@host -> scheme://user:***@host."""
    from browser import parse_proxy
    if (raw or "").strip().lower().startswith("tmproxy://"):
        import tmproxy
        return tmproxy.mask(raw)
    import proxyxoay
    if proxyxoay.is_key_link(raw):
        return proxyxoay.mask(raw)
    p = parse_proxy(raw)
    if not p:
        return "(sai định dạng)"
    server = p["server"]                       # scheme://host:port
    user = p.get("username")
    if not user:
        return server
    scheme, host = server.split("://", 1)
    return f"{scheme}://{user}:***@{host}"


def _scheme(raw: str) -> str:
    from browser import parse_proxy
    low = (raw or "").strip().lower()
    if low.startswith("tmproxy://"):
        return "tmproxy"
    import proxyxoay
    if proxyxoay.is_key_link(raw):
        return "proxyxoay"
    p = parse_proxy(raw)
    return (p["server"].split("://", 1)[0] if p else "?")


def _aiohttp_url(raw: str) -> str | None:
    """Chuỗi proxy -> URL cho aiohttp (kèm user:pass). aiohttp chỉ đỡ http proxy; socks bỏ qua khi kiểm."""
    from browser import parse_proxy
    from urllib.parse import quote
    p = parse_proxy(raw)
    if not p:
        return None
    server = p["server"]
    user, pw = p.get("username"), p.get("password")
    if not user:
        return server
    scheme, host = server.split("://", 1)
    cred = quote(user, safe="") + ((":" + quote(pw, safe="")) if pw else "")
    return f"{scheme}://{cred}@{host}"


class PoolStore:
    """Kho proxy lưu JSON: {id: {raw, alive, last_check}}. raw (có mật khẩu) KHÔNG bao giờ trả ra API."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.items: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self):
        try:
            if self.path.exists():
                self.items = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = {}

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def add_many(self, raws: list[str]) -> int:
        from browser import check_proxy_input
        added = 0
        for raw in raws:
            if raw.strip().startswith("#"):
                continue
            # Chuẩn hoá + kiểm theo ĐỊNH DẠNG (cùng luật với proxy riêng nick & proxy chung), ĐỪNG gọi mạng lấy IP
            # lúc thêm: proxyxoay/proxy.vn xác thực theo IP whitelist → lấy IP sẽ FAIL khi chưa whitelist (trước đây
            # proxy.vn không thêm được vào kho). Kiểm sống để nút "Kiểm tra kho" (check_all).
            try:
                raw = check_proxy_input(raw)
            except ValueError:
                continue
            if not raw:
                continue
            pid = _proxy_id(raw)
            if pid not in self.items:
                self.items[pid] = {"raw": raw, "alive": None, "last_check": 0}
                added += 1
        if added:
            self._save()
        return added

    def remove(self, pid: str) -> bool:
        if pid in self.items:
            del self.items[pid]
            self._save()
            return True
        return False

    def prune_dead(self) -> int:
        dead = [pid for pid, it in self.items.items() if it.get("alive") is False]
        for pid in dead:
            del self.items[pid]
        if dead:
            self._save()
        return len(dead)

    def alive_raws(self) -> list[str]:
        """Proxy còn sống (hoặc chưa kiểm), theo thứ tự ổn định — để chia cho nick."""
        return [it["raw"] for _, it in sorted(self.items.items()) if it.get("alive") is not False]

    def public(self, nick_count: Callable[[str], int] | None = None) -> list[dict]:
        """Danh sách CHE mật khẩu cho API admin + thông tin hiển thị (KHÔNG gọi mạng): IP ra/nhà mạng/độ trễ lần
        kiểm gần nhất, lý do chết, và với proxy xoay: đuôi key, endpoint, tuổi IP, chờ đổi, số lần đổi hôm nay."""
        from browser import is_rotating_proxy, parse_proxy, rotating_status
        out = []
        for pid, it in sorted(self.items.items()):
            raw = it["raw"]
            rot = rotating_status(raw) if is_rotating_proxy(raw) else {}
            if rot:
                endpoint = rot.get("endpoint", "")
            else:
                p = parse_proxy(raw)   # proxy tĩnh: chỉ tách chuỗi
                endpoint = p["server"].split("://", 1)[-1] if p else ""
            out.append({
                "id": pid, "proxy": mask_proxy(raw), "scheme": _scheme(raw),
                "alive": it.get("alive"), "last_check": it.get("last_check", 0),
                "nicks": nick_count(raw) if nick_count else None,
                "endpoint": endpoint, "exit_ip": it.get("exit_ip") or rot.get("exit_ip", ""),
                "isp": it.get("isp") or rot.get("network", ""), "city": it.get("city") or rot.get("location", ""),
                "latency_ms": it.get("latency_ms"), "error": it.get("error") or rot.get("error", ""),
                "rot": rot or None,
            })
        return out

    async def _check_one(self, pid: str, sem: asyncio.Semaphore) -> None:
        """Kiểm 1 proxy: với tới Dola qua proxy = sống (+độ trễ); sống thì hỏi IP ra/nhà mạng; chết thì ghi lý do."""
        it = self.items.get(pid)
        if not it:
            return
        raw = it["raw"]
        info = {"alive": False, "last_check": time.time(), "error": "", "exit_ip": "", "isp": "", "city": "",
                "latency_ms": None}
        url = await asyncio.to_thread(_aiohttp_url, raw)   # tmproxy://KEY / get.php → gọi API nhà bán (đồng bộ) ngoài vòng lặp
        if not url:
            from browser import is_rotating_proxy, rotating_last_error
            info["error"] = (rotating_last_error(raw) if is_rotating_proxy(raw) else "") or "không lấy được proxy"
        else:
            try:
                async with sem, aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=CHECK_TIMEOUT)) as sess:
                    t0 = time.monotonic()
                    async with sess.get(CHECK_URL, proxy=url) as resp:
                        info["alive"] = resp.status < 500      # có phản hồi từ Dola qua proxy = sống
                        info["latency_ms"] = int((time.monotonic() - t0) * 1000)
                        if not info["alive"]:
                            info["error"] = f"Dola trả HTTP {resp.status} qua proxy"
                    if info["alive"]:
                        try:
                            async with sess.get(IPINFO_URL, proxy=url, timeout=aiohttp.ClientTimeout(total=8)) as r2:
                                j = await r2.json(content_type=None)
                            info.update(exit_ip=str(j.get("query") or ""), isp=str(j.get("isp") or ""),
                                        city=str(j.get("city") or j.get("regionName") or ""))
                        except Exception:  # noqa: BLE001 — chỉ là thông tin hiển thị
                            pass
            except Exception as exc:  # noqa: BLE001
                msg = str(exc) or type(exc).__name__
                info["error"] = re.sub(r"//[^@/\s]+@", "//***@", msg)[:140]   # không lộ user:pass trong lỗi
        if pid in self.items:
            self.items[pid].update(info)

    async def check_ids(self, pids: list[str]) -> None:
        sem = asyncio.Semaphore(CHECK_CONCURRENCY)
        await asyncio.gather(*(self._check_one(pid, sem) for pid in pids))
        self._save()

    async def check_all(self) -> tuple[int, int]:
        """Kiểm 8 luồng/15s; đánh dấu alive True/False. Trả (số sống, số chết)."""
        await self.check_ids(list(self.items))
        alive = sum(1 for it in self.items.values() if it.get("alive"))
        return alive, len(self.items) - alive


class AddProxies(BaseModel):
    text: str = Field(..., min_length=1)
    provider: str = ""   # "tmproxy" | "topproxy" | "proxyvn" | "proxyxoay" | "shoplike" | "" (auto: dán nguyên link / host:port)


class AssignReq(BaseModel):
    per_ip: int = Field(DEFAULT_PER_IP, ge=1, le=50)
    scope: str = "noproxy"      # noproxy = chỉ nick chưa có proxy riêng; all = mọi nick


def make_router(ctx: dict[str, Any]) -> APIRouter:
    """ctx: store (PoolStore), pool (account pool), set_account_proxy(name, raw), admin_auth(x_admin_key)."""
    store: PoolStore = ctx["store"]
    pool = ctx["pool"]
    set_account_proxy = ctx["set_account_proxy"]
    admin_auth = ctx["admin_auth"]
    r = APIRouter()

    def _nick_count(raw: str) -> int:
        from browser import account_proxy_raw
        return sum(1 for a in pool.accounts if account_proxy_raw(a) == raw)

    @r.get("/api/admin/proxies")
    async def list_proxies(x_admin_key: str | None = Header(default=None)):
        admin_auth(x_admin_key)
        items = store.public(_nick_count)
        alive = sum(1 for i in items if i["alive"])
        dead = sum(1 for i in items if i["alive"] is False)
        return {"ok": True, "proxies": items,
                "stats": {"total": len(items), "alive": alive, "dead": dead, "unchecked": len(items) - alive - dead}}

    @r.post("/api/admin/proxies")
    async def add_proxies(body: AddProxies, x_admin_key: str | None = Header(default=None)):
        admin_auth(x_admin_key)
        raws = [l.strip() for l in body.text.splitlines() if l.strip()]
        prov = (body.provider or "").strip().lower()   # người dùng chọn loại → key TRẦN tự thành scheme đúng
        if prov in ("tmproxy", "topproxy", "proxyvn", "proxyxoay", "shoplike"):
            raws = [l if ("://" in l or "/" in l or l.lower().startswith(prov + ":")) else f"{prov}:{l}" for l in raws]
        # tmproxy://KEY được kiểm bằng cách gọi API TMProxy (đồng bộ, ~1s/key) → chạy ở thread kẻo dán 18 key
        # một lúc là vòng lặp gateway đứng ~18s (poll video, /health cùng khựng).
        added = await asyncio.to_thread(store.add_many, raws)
        return {"ok": True, "added": added, "total": len(store.items)}

    @r.post("/api/admin/proxies/check")
    async def check_proxies(x_admin_key: str | None = Header(default=None)):
        admin_auth(x_admin_key)
        alive, dead = await store.check_all()
        return {"ok": True, "alive": alive, "dead": dead, "threads": CHECK_CONCURRENCY, "timeout": CHECK_TIMEOUT}

    @r.post("/api/admin/proxies/prune")
    async def prune_proxies(x_admin_key: str | None = Header(default=None)):
        admin_auth(x_admin_key)
        return {"ok": True, "removed": store.prune_dead(), "total": len(store.items)}

    @r.post("/api/admin/proxies/assign")
    async def assign_proxies(body: AssignReq, x_admin_key: str | None = Header(default=None)):
        admin_auth(x_admin_key)
        from browser import account_proxy_raw
        proxies = store.alive_raws()
        if not proxies:
            raise HTTPException(400, "kho không có proxy sống — thêm và kiểm tra proxy trước")
        targets = [a for a in pool.accounts if body.scope == "all" or not account_proxy_raw(a)]
        cap = body.per_ip * len(proxies)
        if len(targets) > cap:
            raise HTTPException(422, f"{len(targets)} nick mà chỉ {len(proxies)} proxy × {body.per_ip} = {cap} chỗ — thêm proxy")
        assigned = 0
        for i, name in enumerate(targets):
            set_account_proxy(name, proxies[i % len(proxies)])
            assigned += 1
        return {"ok": True, "assigned": assigned, "proxies_used": len(proxies), "per_ip": body.per_ip}

    @r.post("/api/admin/proxies/{pid}/rotate")
    async def rotate_proxy(pid: str, x_admin_key: str | None = Header(default=None)):
        """Đổi IP 1 proxy xoay (nút "Đổi IP" như tool đối thủ) rồi kiểm lại ngay để hiện IP ra mới."""
        admin_auth(x_admin_key)
        it = store.items.get(pid)
        if not it:
            raise HTTPException(404, "không có proxy này trong kho")
        from browser import is_rotating_proxy, rotating_rotate
        if not is_rotating_proxy(it["raw"]):
            raise HTTPException(422, "proxy tĩnh không đổi IP được")
        try:
            await asyncio.to_thread(rotating_rotate, it["raw"])   # chưa tới giờ nhà bán cho đổi → giữ IP cũ, không lỗi
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"nhà bán proxy báo lỗi: {str(exc)[:160]}")
        await store.check_ids([pid])
        return {"ok": True, "proxy": next(p for p in store.public(_nick_count) if p["id"] == pid)}

    @r.delete("/api/admin/proxies/{pid}")
    async def del_proxy(pid: str, x_admin_key: str | None = Header(default=None)):
        admin_auth(x_admin_key)
        return {"ok": store.remove(pid), "total": len(store.items)}

    return r


def demo() -> None:
    """Tự kiểm không cần mạng: thêm/bỏ trùng, che mật khẩu, chia vòng tròn."""
    import tempfile
    store = PoolStore(Path(tempfile.mkdtemp()) / "pool.json")
    n = store.add_many([
        "1.2.3.4:8080:user:secretpass",
        "http://u2:p2@5.6.7.8:3128",
        "9.9.9.9:1080",
        "1.2.3.4:8080:user:secretpass",   # trùng → không thêm
        "rác không phải proxy",
    ])
    assert n == 3, n
    assert store.add_many(["1.2.3.4:8080:user:secretpass"]) == 0, "trùng phải bỏ"

    from browser import normalize_proxy_input, is_rotating_proxy   # string-only, không gọi mạng
    assert normalize_proxy_input("a" * 32) == "tmproxy://" + "a" * 32
    assert normalize_proxy_input("1.2.3.4:8080") == "1.2.3.4:8080"
    # điều kiện bật MỖI LẦN MỘT NICK: chỉ khi proxy chung là key/link xoay
    assert is_rotating_proxy("a" * 32) and is_rotating_proxy("https://x/get.php?key=1")
    assert not is_rotating_proxy("1.2.3.4:8080") and not is_rotating_proxy("")

    pub = store.public()
    joined = json.dumps(pub, ensure_ascii=False)
    assert "secretpass" not in joined and "p2" not in joined, "mật khẩu bị lộ!"
    assert any(i["proxy"] == "http://user:***@1.2.3.4:8080" for i in pub), pub

    # chia vòng tròn: đánh dấu 9.9.9.9 chết, còn 2 proxy sống
    store.items[list(store.items)[2]]["alive"] = False
    alive = store.alive_raws()
    assert len(alive) == 2 and "9.9.9.9:1080" not in alive, alive
    assert store.prune_dead() == 1 and len(store.items) == 2

    print("proxy_pool demo: OK")


if __name__ == "__main__":
    demo()
