#!/usr/bin/env python3
"""Đẩy nick từ máy này lên gateway VPS — "broker" như Seedance nhưng nick và proxy là của bạn.

Đọc accounts/<nick>/cookies.json → POST /api/admin/accounts/import-cookie. Nếu đưa file proxy
(mỗi dòng một proxy: http://user:pass@host:port, socks5://…, host:port:user:pass) thì chia nick
lên proxy theo vòng tròn, tối đa --per-ip nick một IP, và ghi proxy riêng cho từng nick
(POST /api/admin/accounts/<nick>/proxy). Nhiều nick chung một IP là lý do tool đối thủ sập.

Mỗi nick nhập mất 10–30 giây (VPS mở Chrome kiểm tra phiên) — 42 nick khoảng 10 phút.

Chạy từ thư mục repo trên máy có nick:
  .venv/bin/python deploy/push_nicks.py http://IP:8010 ADMIN_KEY --proxies proxies.txt --per-ip 5 --dry-run
  .venv/bin/python deploy/push_nicks.py http://IP:8010 ADMIN_KEY --proxies proxies.txt --per-ip 5
  .venv/bin/python deploy/push_nicks.py --selftest
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _post(base: str, admin_key: str, path: str, body: dict):
    req = urllib.request.Request(base.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Admin-Key": admin_key})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.status, json.loads(r.read() or b"{}")


def read_proxies(path: str | None) -> list[str]:
    if not path:
        return []
    lines = [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines()]
    return [l for l in lines if l and not l.startswith("#")]


def plan(accounts_dir: str, proxies: list[str], per_ip: int, only: set | None = None) -> list[tuple]:
    """[(nick, cookie_json, proxy|None)]. Nick không có cookies.json bị bỏ qua. Proxy chia vòng tròn."""
    names = sorted(p.parent.name for p in Path(accounts_dir).glob("*/cookies.json"))
    if only:
        names = [n for n in names if n in only]
    if proxies and len(names) > per_ip * len(proxies):
        raise SystemExit(f"{len(names)} nick mà chỉ {len(proxies)} proxy × {per_ip} nick/IP = "
                         f"{per_ip * len(proxies)} chỗ — thêm proxy hoặc tăng --per-ip")
    return [(n, (Path(accounts_dir) / n / "cookies.json").read_text(encoding="utf-8"),
             proxies[i % len(proxies)] if proxies else None) for i, n in enumerate(names)]


def push(base: str, admin_key: str, items: list[tuple], post=_post, dry_run: bool = False) -> int:
    ok = 0
    for n, raw, proxy in items:
        tag = f"{n:24s} → {proxy or '(proxy chung của VPS)'}"
        if dry_run:
            print("  [thử] " + tag)
            continue
        try:
            # Proxy TRƯỚC, cookie SAU: bước nhập cookie mở Chrome của nick để kiểm tra phiên, phải đi
            # qua đúng proxy riêng của nick (server tự tạo thư mục nick khi ghi proxy.txt).
            if proxy:
                post(base, admin_key, f"/api/admin/accounts/{n}/proxy", {"proxy": proxy})
            _, res = post(base, admin_key, "/api/admin/accounts/import-cookie", {"name": n, "cookies": raw})
            ok += 1
            print(f"  ✅ {tag} login_ok={res.get('ok')}")
        except urllib.error.HTTPError as e:
            print(f"  ❌ {tag} HTTP {e.code}: {e.read()[:200].decode(errors='replace')}")
        except Exception as e:
            print(f"  ❌ {tag} {e}")
    return ok


def _selftest():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    for n in ("n1", "n2", "n3", "khong-cookie"):
        (tmp / n).mkdir()
    for n in ("n1", "n2", "n3"):
        (tmp / n / "cookies.json").write_text('[{"name":"sessionid","value":"x"}]')
    items = plan(str(tmp), ["http://p1:1", "http://p2:2"], per_ip=2)
    assert [(n, p) for n, _, p in items] == [("n1", "http://p1:1"), ("n2", "http://p2:2"), ("n3", "http://p1:1")]
    try:
        plan(str(tmp), ["http://p1:1"], per_ip=2)
        assert False, "3 nick / 1 proxy × 2 phải bị chặn"
    except SystemExit:
        pass
    calls = []
    def fake(base, key, path, body):
        calls.append((path, body.get("proxy")))
        return 200, {"ok": True}
    assert push("http://x", "k", items, post=fake) == 3
    assert calls[0] == ("/api/admin/accounts/n1/proxy", "http://p1:1") and calls[1] == ("/api/admin/accounts/import-cookie", None)
    assert len(calls) == 6                                    # 3 nick × (đặt proxy rồi nhập cookie)
    assert push("http://x", "k", items, post=fake, dry_run=True) == 0 and len(calls) == 6
    print("OK")


def main():
    if "--selftest" in sys.argv:
        return _selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", help="http://IP:8010")
    ap.add_argument("admin_key")
    ap.add_argument("--accounts", default="accounts", help="thư mục nick trên máy này")
    ap.add_argument("--proxies", help="file proxy, mỗi dòng một proxy")
    ap.add_argument("--per-ip", type=int, default=5, help="tối đa bao nhiêu nick chung một proxy")
    ap.add_argument("--only", help="chỉ đẩy các nick này, cách nhau bằng dấu phẩy")
    ap.add_argument("--dry-run", action="store_true", help="chỉ in kế hoạch, không gửi")
    a = ap.parse_args()
    items = plan(a.accounts, read_proxies(a.proxies), a.per_ip, set(a.only.split(",")) if a.only else None)
    print(f"{len(items)} nick → {a.base}" + (f" qua {len(read_proxies(a.proxies))} proxy" if a.proxies else ""))
    n = push(a.base, a.admin_key, items, dry_run=a.dry_run)
    if not a.dry_run:
        print(f"Xong {n}/{len(items)} nick. Mở app → Kho tài khoản (server từ xa) để xem trạng thái đăng nhập.")


if __name__ == "__main__":
    main()
