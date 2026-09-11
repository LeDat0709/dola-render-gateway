#!/usr/bin/env python3
"""Import Netscape cookies into an accounts/<name> profile to restore Dola login.

A manual alternative to add_account.py's Google OAuth: paste the cookies you
exported from a logged-in dola.com browser, and this writes them into the
persistent profile so the pool sees a live session.

Usage:
    python import_cookies.py <account> <cookies_file>

Export cookies from Chrome: log into https://www.dola.com, use a
"Cookie-Editor" extension -> Export -> Netscape, save to a .txt file.
"""
import asyncio
import re
import sys
from pathlib import Path

from patchright.async_api import async_playwright

from browser import cookie_value, launch_account_context, persist_dola_cookies, pin_session_cookies, verify_cookie_http
import config

# Undo markdown-link mangling (e.g. "[www.dola.com](https://www.dola.com)" -> "www.dola.com")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HTTPONLY_PREFIX = "#HttpOnly_"


def parse_netscape(path: str, ui_lang: str | None = None) -> list[dict]:
    """Parses a Netscape cookie file into Playwright add_cookies() dicts.

    ui_lang: when set, overrides the i18next cookie value so Dola renders that
    UI language (the worker's selectors expect Japanese; pass "ja").
    """
    cookies = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = _LINK.sub(r"\1", raw).strip()
        if not line:
            continue
        http_only = line.startswith(_HTTPONLY_PREFIX)
        if http_only:
            line = line[len(_HTTPONLY_PREFIX):]
        elif line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 7:
            continue
        domain, _flag, path_, secure, expiry, name = fields[:6]
        value = " ".join(fields[6:])
        try:
            expires = int(expiry)
        except ValueError:
            expires = 0
        if ui_lang and name == "i18next":
            value = ui_lang
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": path_,
            "expires": expires if expires > 0 else -1,
            "httpOnly": http_only,
            "secure": secure.upper() == "TRUE",
        })
    return cookies


from cookie_service import parse_cookie_input

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


async def import_cookies(account: str, path: str, ui_lang: str | None = None) -> None:
    if not _NAME_RE.match(account):
        sys.exit("tên nick chỉ gồm chữ, số, _ hoặc - (1-32 ký tự)")
    text = Path(path).read_text(encoding="utf-8")
    cookies = parse_cookie_input(text)
    if not cookies:
        sys.exit("Không tìm thấy cookie hợp lệ trong file!")

    # Paste-cookie flow creates the profile on demand — no add_account.py / OAuth needed.
    # (launch_account_context refuses a missing profile to protect the worker path.)
    (config.ACCOUNTS_DIR / account).mkdir(parents=True, exist_ok=True)

    # Set UI lang if needed
    if ui_lang:
        cookies.append({"name": "i18next", "value": ui_lang, "domain": "www.dola.com", "path": "/"})
        cookies.append({"name": "i18next", "value": ui_lang, "domain": ".dola.com", "path": "/"})

    async with async_playwright() as p:
        context = await launch_account_context(p, account)
        try:
            await context.add_cookies(cookies)
            # Fast: no navigation. Verify the session over plain HTTP after injecting.
            live = await context.cookies("https://www.dola.com")
            sid = cookie_value(live, "sessionid")
            input_had_sid = any(c["name"] == "sessionid" and c["value"] for c in cookies)
            if sid:
                # A harvested/pasted sessionid is often a session cookie (no expiry) and would
                # vanish when this context closes; pin it so the nick stays logged in.
                await pin_session_cookies(context)
                try:
                    persist_dola_cookies(account, await context.cookies("https://www.dola.com"))
                except Exception:
                    pass
                status = "sessionid=OK ✓ (đăng nhập thành công)"
            elif not input_had_sid:
                status = ("sessionid=MISSING — chuỗi cookie KHÔNG có sessionid; "
                          "export lại từ tab dola.com đang đăng nhập")
            else:
                status = ("sessionid=MISSING — cookie có sessionid nhưng dola.com đã xóa "
                          "khi tải trang (hết hạn/không hợp lệ); cần lấy cookie mới")
            print(f"imported {len(cookies)} cookies into accounts/{account}; {status}")
        finally:
            await context.close()  # persistent context flushes cookies to the profile on close


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit("usage: python import_cookies.py <account> <cookies_file> [ui_lang]")
    lang = sys.argv[3] if len(sys.argv) == 4 else None
    asyncio.run(import_cookies(sys.argv[1], sys.argv[2], lang))
