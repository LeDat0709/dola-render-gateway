"""Nạp gói nick (file JSON xuất từ Seedance Studio hoặc Dola Studio) vào gateway qua đúng API mà
giao diện dùng — chạy tay khi cần nạp nhiều nick, nạp theo đợt, hoặc nạp lại phần lỗi.

Mô phỏng desktop/renderer/src/lib/bundle.js (fromSeedance) + lib/api.js (importAccounts):
  - gói Seedance: tai_khoan[] {name "FB <uid>", fb_uid, dola_uid, cookies{tên: giá_trị}, enabled, proxy}
    → khử trùng theo dola_uid (giữ bản created_at mới nhất), tên nick = fb<fb_uid>, note "FB <uid>"
  - gói Dola Studio: accounts[] {name, cookies, proxy, email, note, scheduling} → giữ nguyên
  - từng nick: POST /api/admin/accounts/import-cookie (server nạp cookie vào profile Chrome + kiểm tra phiên),
    rồi PATCH /api/admin/accounts/<name> {note, scheduling}. N nick cùng lúc (mặc định 4, mỗi nick 3–10s).

Ví dụ:
  .venv/bin/python scripts/import_seedance_bundle.py goi.json --dry-run
  .venv/bin/python scripts/import_seedance_bundle.py goi.json --limit 3            # chạy thử 3 nick
  .venv/bin/python scripts/import_seedance_bundle.py goi.json --offset 3 --limit 40 # đợt tiếp theo
Admin key đọc từ .env.local (DOLA_ADMIN_KEY) trong thư mục hiện tại, hoặc --admin-key.
"""
import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
NAME_MAX = 32


def safe_name(raw, fallback):
    n = re.sub(r"[^A-Za-z0-9_-]+", "_", str(raw or "").strip()).strip("_")[:NAME_MAX]
    return n if NAME_RE.match(n) else fallback


def fb_name(t):
    name = str(t.get("name") or "").strip()
    if t.get("fb_uid") and (not name or re.match(r"^FB\s+\d+$", name, re.I)):
        return f"fb{t['fb_uid']}"
    return None


def normalize(bundle: dict) -> list[dict]:
    """Trả danh sách {name, cookies, proxy, email, note, scheduling} như bundle.js normalizeBundle."""
    if bundle.get("kind") == "dola-studio-accounts" and isinstance(bundle.get("accounts"), list):
        return bundle["accounts"]
    items = bundle.get("tai_khoan")
    if not isinstance(items, list):
        raise SystemExit("File không phải gói nick (thiếu tai_khoan[] hoặc accounts[]).")
    by_uid: dict = {}
    for t in items:
        if not isinstance(t, dict):
            continue
        key = t.get("dola_uid") or t.get("fb_uid") or t.get("id")
        prev = by_uid.get(key)
        if not prev or (t.get("created_at") or 0) >= (prev.get("created_at") or 0):
            by_uid[key] = t
    used: set = set()
    out = []
    for i, t in enumerate(by_uid.values()):
        base = fb_name(t) or safe_name(t.get("name"), f"fb_{i + 1}")
        name, k = base, 2
        while name in used:
            name, k = f"{base[:NAME_MAX - 3]}_{k}", k + 1
        used.add(name)
        out.append({"name": name, "cookies": t.get("cookies") or {}, "proxy": t.get("proxy") or "",
                    "email": "", "note": f"FB {t['fb_uid']}" if t.get("fb_uid") else "",
                    "scheduling": t.get("enabled") is not False})
    return out


def admin_key_from_env() -> str:
    f = Path(".env.local")
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("DOLA_ADMIN_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


async def import_one(session, base, headers, a, per_nick_timeout):
    """Trả (ok: True|False|None, message). None = server không kiểm được phiên (cookie vẫn đã lưu)."""
    body = {"name": a["name"], "cookies": json.dumps(a.get("cookies") or []), "proxy": a.get("proxy") or "",
            "email": a.get("email") or ""}
    try:
        async with session.post(f"{base}/api/admin/accounts/import-cookie", json=body, headers=headers,
                                timeout=per_nick_timeout) as r:
            j = await r.json(content_type=None)
            if r.status >= 400:
                return False, f"HTTP {r.status}: {j.get('detail') or j}"
    except Exception as exc:  # noqa: BLE001 — báo gọn cho từng nick, không dừng cả đợt
        return False, f"lỗi mạng: {str(exc)[:80]}"
    if a.get("note") or a.get("scheduling") is False:
        try:
            async with session.patch(f"{base}/api/admin/accounts/{a['name']}", headers=headers,
                                     json={"note": a.get("note") or "", "scheduling": a.get("scheduling") is not False},
                                     timeout=30) as r:
                await r.read()
        except Exception:  # noqa: BLE001 — note/lịch là phụ, cookie đã vào rồi
            pass
    return j.get("ok"), j.get("message") or ""


async def run(args):
    import aiohttp
    bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    accounts = normalize(bundle)
    if args.only:
        wanted = {n.strip() for n in args.only.split(",") if n.strip()}
        accounts = [a for a in accounts if a["name"] in wanted]
    if args.skip_existing:
        # Nạp nốt sau khi bị đứt giữa chừng (gateway tắt): bỏ nick đã có thư mục profile trên máy chủ này.
        acc_dir = Path(args.accounts_dir)
        before = len(accounts)
        accounts = [a for a in accounts if not (acc_dir / a["name"]).is_dir()]
        print(f"bỏ qua {before - len(accounts)} nick đã có trong {acc_dir}/")
    accounts = accounts[args.offset: args.offset + args.limit if args.limit else None]
    print(f"gói: {len(bundle.get('tai_khoan') or bundle.get('accounts') or [])} mục → nạp {len(accounts)} nick "
          f"(offset {args.offset}, {args.parallel} nick cùng lúc)")
    if args.dry_run:
        for a in accounts[:10]:
            print(f"  {a['name']:20} cookie={len(a['cookies'])} note={a['note']!r} bật={a['scheduling']}")
        if len(accounts) > 10:
            print(f"  … và {len(accounts) - 10} nick nữa")
        return 0
    key = args.admin_key or admin_key_from_env()
    headers = {"x-admin-key": key} if key else {}
    sem = asyncio.Semaphore(args.parallel)
    stat = {"ok": 0, "dead": 0, "unverified": 0, "bad": [], "done": 0}
    t0 = time.time()

    async def one(session, a):
        async with sem:
            ok, msg = await import_one(session, args.base, headers, a, aiohttp.ClientTimeout(total=args.timeout))
        stat["done"] += 1
        if ok is True:
            stat["ok"] += 1; tag = "OK"
        elif ok is False and msg.startswith(("HTTP", "lỗi mạng")):
            stat["bad"].append(f"{a['name']}: {msg}"); tag = "LỖI"
        elif ok is False:
            stat["dead"] += 1; tag = "cookie chết"
        else:
            stat["unverified"] += 1; tag = "chưa kiểm được"
        print(f"[{stat['done']}/{len(accounts)}] {a['name']:20} {tag}  {msg[:70]}", flush=True)

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*(one(session, a) for a in accounts))
    print(f"\nXong {stat['done']}/{len(accounts)} nick trong {time.time() - t0:.0f}s: "
          f"{stat['ok']} đăng nhập OK · {stat['dead']} cookie chết · {stat['unverified']} chưa kiểm được · {len(stat['bad'])} lỗi")
    for b in stat["bad"][:20]:
        print("  ", b)
    return 0 if not stat["bad"] else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle", help="file JSON gói nick")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--admin-key", default="")
    ap.add_argument("--parallel", type=int, default=4, help="số nick nạp cùng lúc (mỗi nick 1 Chrome ẩn ~0.4GB)")
    ap.add_argument("--limit", type=int, default=0, help="chỉ nạp N nick đầu (0 = tất cả)")
    ap.add_argument("--offset", type=int, default=0, help="bỏ qua N nick đầu (nạp theo đợt)")
    ap.add_argument("--only", default="", help="chỉ nạp các tên này, cách nhau bằng dấu phẩy")
    ap.add_argument("--timeout", type=int, default=120, help="giây tối đa mỗi nick")
    ap.add_argument("--dry-run", action="store_true", help="chỉ in kế hoạch, không gọi server")
    ap.add_argument("--skip-existing", action="store_true", help="bỏ nick đã có thư mục profile (nạp nốt sau khi đứt)")
    ap.add_argument("--accounts-dir", default="accounts", help="thư mục profile trên máy này (để --skip-existing dò)")
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
