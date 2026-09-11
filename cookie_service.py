"""Service to parse and inject cookies into Dola (and FB/Google) persistent profiles."""
import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from patchright.async_api import async_playwright
from browser import cookie_value, launch_account_context, persist_dola_cookies, pin_session_cookies, verify_cookie_http
import config

_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HTTPONLY_PREFIX = "#HttpOnly_"


def parse_cookie_input(raw: str, default_domain: str = ".dola.com") -> List[Dict[str, Any]]:
    """Flexibly parses cookies from JSON, Netscape, Pipe-delimited, or key=value strings."""
    raw = raw.strip()
    if not raw:
        return []

    # 1. Try JSON
    if (raw.startswith("[") and raw.endswith("]")) or (raw.startswith("{") and raw.endswith("}")):
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            cookies = []
            for item in data:
                if isinstance(item, dict) and "name" in item and "value" in item:
                    cookies.append({
                        "name": str(item["name"]),
                        "value": str(item["value"]),
                        "domain": item.get("domain", default_domain),
                        "path": item.get("path", "/"),
                        "httpOnly": bool(item.get("httpOnly", False)),
                        "secure": bool(item.get("secure", True)),
                    })
            if cookies:
                return cookies
        except json.JSONDecodeError:
            pass

    # 2. Check for pipe-delimited format (e.g. UID|PASS|2FA|COOKIE|UA)
    if "|" in raw:
        parts = [p.strip() for p in raw.split("|")]
        for part in parts:
            if "sessionid=" in part or "c_user=" in part or "datr=" in part or "xs=" in part:
                raw = part
                break

    # 3. Check for Netscape format (lines with tabs or whitespace, 7 fields)
    lines = raw.splitlines()
    netscape_cookies = []
    is_netscape = False
    for rline in lines:
        line = _LINK.sub(r"\1", rline).strip()
        if not line:
            continue
        http_only = line.startswith(_HTTPONLY_PREFIX)
        if http_only:
            line = line[len(_HTTPONLY_PREFIX):]
        elif line.startswith("#"):
            is_netscape = True
            continue

        # Netscape rows are TAB-delimited with a TRUE/FALSE secure column and a bare
        # domain. Guard against a normal "k=v; k=v; ..." string (>=7 pairs) being
        # mis-split into 7 whitespace tokens and mangled — that drops sessionid.
        fields = line.split("\t") if "\t" in line else line.split()
        if (len(fields) >= 7
                and fields[3].upper() in ("TRUE", "FALSE")
                and "=" not in fields[0]):
            is_netscape = True
            domain, _flag, path_, secure, expiry, name = fields[:6]
            value = " ".join(fields[6:])
            try:
                expires = int(expiry)
            except ValueError:
                expires = -1
            netscape_cookies.append({
                "name": name,
                "value": value,
                "domain": domain,
                "path": path_,
                "expires": expires if expires > 0 else -1,
                "httpOnly": http_only,
                "secure": secure.upper() == "TRUE",
            })

    if is_netscape and netscape_cookies:
        return netscape_cookies

    # 4. Fallback to standard key=value; pairs
    kv_cookies = []
    # Replace newlines with semicolons if user pasted multiple lines of k=v
    semi_raw = raw.replace("\n", ";").replace("\r", ";")
    for item in semi_raw.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, val = item.split("=", 1)
        name = name.strip()
        val = val.strip()
        if not name:
            continue

        # Detect domain
        domain = default_domain
        if name in ("c_user", "xs", "datr", "fr", "sb", "wd"):
            domain = ".facebook.com"
        elif name in ("sessionid", "csrftoken", "i18next"):
            domain = ".dola.com"

        is_http_only = name in ("sessionid", "xs", "datr", "c_user")

        kv_cookies.append({
            "name": name,
            "value": val,
            "domain": domain,
            "path": "/",
            "secure": True,
            "httpOnly": is_http_only,
        })

    return kv_cookies


async def apply_cookies_to_account(
    account: str,
    cookie_raw: str,
    ui_lang: str = "ja",
    check_url: str = "https://www.dola.com/chat"
) -> Dict[str, Any]:
    """Injects cookies into accounts/<account> profile and verifies session."""
    parsed = parse_cookie_input(cookie_raw)
    if not parsed:
        raise ValueError("Không tìm thấy cookie hợp lệ trong dữ liệu nhập vào.")

    # If user provided Facebook cookies (has c_user / xs) without Dola sessionid,
    # automatically perform Facebook OAuth to authenticate Dola.
    has_sid = any(c["name"] == "sessionid" and c["value"] for c in parsed)
    has_fb = any(c["name"] in ("c_user", "xs") and c["value"] for c in parsed)
    if not has_sid and has_fb:
        from facebook_login import add_account_via_facebook
        user_label = await add_account_via_facebook(account, cookie_raw)
        return {
            "ok": True,
            "has_sessionid": True,
            "cookies_count": len(parsed),
            "account": account,
            "sessionid_found": True,
            "message": f"Đăng nhập Dola qua Facebook OAuth thành công! ({user_label or 'Active'})",
            "user_label": user_label,
        }

    # Prepare cookies list
    final_cookies = []
    for c in parsed:
        c_name = c["name"]
        c_val = c["value"]
        domain = c.get("domain", ".dola.com")

        # Normalize domain for Dola
        if "dola.com" in domain:
            final_cookies.append({**c, "domain": ".dola.com"})
            final_cookies.append({**c, "domain": "www.dola.com"})
        else:
            final_cookies.append(c)

    # Add i18next language preference
    final_cookies.append({"name": "i18next", "value": ui_lang, "domain": "www.dola.com", "path": "/"})
    final_cookies.append({"name": "i18next", "value": ui_lang, "domain": ".dola.com", "path": "/"})

    profile_dir = config.ACCOUNTS_DIR / account
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Inject cookies into the profile with NO navigation (fast: ~2-3s vs ~10s), then verify the
    # session over plain HTTP. Skips the slow goto + 4s wait + page.evaluate.
    async with async_playwright() as p:
        context = await launch_account_context(p, account, headless=True)
        try:
            await context.add_cookies(final_cookies)
            await pin_session_cookies(context)  # persist session-only cookies past close
            all_cookies = await context.cookies("https://www.dola.com")
            sid = cookie_value(all_cookies, "sessionid")
            try:
                persist_dola_cookies(account, all_cookies)
            except Exception:
                pass
        finally:
            await context.close()

    cookie_str = "; ".join(f'{c["name"]}={c["value"]}' for c in all_cookies)
    is_authenticated, verify_msg = await verify_cookie_http(cookie_str)
    input_had_sid = any(c["name"] == "sessionid" and c["value"] for c in parsed)
    if is_authenticated:
        message = "Đăng nhập Dola thành công! Phiên đã được lưu."
    elif not input_had_sid:
        message = ("Chuỗi cookie bạn dán KHÔNG chứa sessionid của Dola. "
                   "Hãy export lại cookie từ tab dola.com đang đăng nhập.")
    else:
        message = f"Cookie không đăng nhập được: {verify_msg}"
    return {
        "ok": is_authenticated,
        "has_sessionid": bool(sid),
        "cookies_count": len(parsed),
        "account": account,
        "sessionid_found": bool(sid),
        "message": message,
    }


async def clear_account_cookies(account: str) -> Dict[str, Any]:
    """Wipes all cookies from accounts/<account> so it returns to a clean logged-out
    state (profile is kept). Use to reset a dead session before logging in again."""
    async with async_playwright() as p:
        context = await launch_account_context(p, account, headless=True)
        try:
            await context.clear_cookies()
            remaining = len(await context.cookies())
            return {"ok": True, "account": account, "remaining": remaining}
        finally:
            # Persistent context flushes the emptied cookie store to disk on close.
            await context.close()
