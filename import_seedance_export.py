#!/usr/bin/env python3
"""Bulk-import a "seedance-accounts" JSON export (from another tool) into the account pool.

The export shape:
    {"loai":"seedance-accounts", "tai_khoan":[
        {"id":..., "name":"FB 61594...", "fb_uid":"61594...", "cookies":{<name>:<value>, ...}, ...}, ...]}

Each account's `cookies` map is the full Dola passport set (sessionid, sid_guard, sid_ucp_v1,
uid_tt, s_v_web_id, ...). We inject them into accounts/<nick>, verify the session, and pin
the session cookies with a far-future expiry (so a session-only sessionid survives restarts).

Usage:
    python import_seedance_export.py <export.json> [--prefix acc] [--no-verify]
"""
import asyncio
import json
import re
import sys
from pathlib import Path

from cookie_service import apply_cookies_to_account
import config

_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _nick_name(acc: dict, index: int, prefix: str) -> str:
    """Safe 1-32 char nick from fb_uid / name / index."""
    base = str(acc.get("fb_uid") or acc.get("name") or f"{prefix}{index}").strip()
    base = _NAME_RE.sub("_", base).strip("_") or f"{prefix}{index}"
    return base[:32]


def _cookie_json(cookies: dict) -> str:
    """Build a JSON array of Dola cookie objects (robust — no string-splitting fragility)."""
    out = []
    for name, value in cookies.items():
        if not name or value is None:
            continue
        out.append({"name": str(name), "value": str(value), "domain": ".dola.com",
                    "path": "/", "secure": True,
                    "httpOnly": name in ("sessionid", "sessionid_ss", "sid_guard", "sid_tt",
                                         "uid_tt", "sid_ucp_v1", "ssid_ucp_v1")})
    return json.dumps(out, ensure_ascii=False)


async def import_export(path: str, prefix: str = "acc", verify: bool = True) -> None:
    f = Path(path)
    if not f.exists():
        sys.exit(
            f"Không thấy file '{path}'.\n"
            "Hãy lưu TOÀN BỘ nội dung JSON export (dạng {\"tai_khoan\":[...]}) ra 1 file rồi chạy lại, ví dụ:\n"
            f"  1) Tạo file:  nano {path}   (dán JSON, lưu bằng Ctrl+O, thoát Ctrl+X)\n"
            f"  2) Chạy:      .venv/bin/python import_seedance_export.py {path}"
        )
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        sys.exit(f"File '{path}' không phải JSON hợp lệ (có thể bị dán thiếu/đứt): {e}")
    accounts = data.get("tai_khoan") or data.get("accounts") or []
    if not accounts:
        sys.exit("Không thấy 'tai_khoan' trong file export.")
    print(f"Tìm thấy {len(accounts)} tài khoản trong export.", flush=True)
    slots = asyncio.Semaphore(config.LOGIN_CONCURRENCY)   # mỗi nick = 1 Chrome → chạy song song có trần

    async def load_one(i: int, acc: dict) -> bool:
        cookies = acc.get("cookies") or {}
        if not cookies.get("sessionid"):
            print(f"[{i}/{len(accounts)}] bỏ qua (không có sessionid)", flush=True)
            return False
        nick = _nick_name(acc, i, prefix)
        lang = cookies.get("i18next") or "ja"
        proxy = (acc.get("proxy") or "").strip()
        if proxy:
            from browser import set_account_proxy
            set_account_proxy(nick, proxy)
        async with slots:
            try:
                if verify:
                    res = await apply_cookies_to_account(nick, _cookie_json(cookies), ui_lang=lang)
                    status = "OK ✓" if res.get("ok") else f"cookie chết ({res.get('message', '')[:50]})"
                else:
                    # inject-only: write cookies into the profile without launching a browser
                    from browser import launch_account_context, pin_session_cookies
                    from patchright.async_api import async_playwright
                    (config.ACCOUNTS_DIR / nick).mkdir(parents=True, exist_ok=True)
                    async with async_playwright() as p:
                        ctx = await launch_account_context(p, nick, headless=True)
                        try:
                            await ctx.add_cookies(json.loads(_cookie_json(cookies)))
                            await pin_session_cookies(ctx)
                        finally:
                            await ctx.close()
                    status = "đã nạp (chưa verify)"
                print(f"[{i}/{len(accounts)}] {nick} <- {acc.get('name', '')}: {status}", flush=True)
                return True
            except Exception as e:
                print(f"[{i}/{len(accounts)}] {nick}: LỖI {str(e)[:100]}", flush=True)
                return False


    done = await asyncio.gather(*(load_one(i, acc) for i, acc in enumerate(accounts, 1)))
    print(f"Xong: {sum(done)}/{len(accounts)} nick nạp thành công.", flush=True)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("usage: python import_seedance_export.py <export.json> [--prefix acc] [--no-verify]")
    prefix = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--prefix=")), "acc")
    verify = "--no-verify" not in sys.argv
    asyncio.run(import_export(args[0], prefix=prefix, verify=verify))
