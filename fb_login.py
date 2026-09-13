#!/usr/bin/env python3
"""Facebook Auto-Login via Cookie & Session Automation.

Supports both Patchright (anti-detect) and standard Playwright.
Can parse standard format:
    UID|PASSWORD|2FA|COOKIE|UA
or direct cookie strings.
"""

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Default credentials provided by user
DEFAULT_RAW_DATA = ""  # never hardcode account cookies here; pass --data or --file


def parse_account_line(line: str) -> Tuple[Optional[str], Optional[str], Optional[str], str, Optional[str]]:
    """Parses UID|PASS|2FA|COOKIES|||UA or raw cookie lines.

    Returns (uid, password, two_factor, cookie_string, user_agent)
    """
    line = line.strip()
    if not line:
        return None, None, None, "", None

    # Check if pipe-delimited
    if "|" in line:
        parts = [p.strip() for p in line.split("|")]
        uid = parts[0] if len(parts) > 0 and parts[0] else None
        pwd = parts[1] if len(parts) > 1 and parts[1] else None

        # Find cookie part (contains c_user or datr or xs)
        cookie_str = ""
        ua = None
        two_fa = None

        for idx, part in enumerate(parts):
            if "c_user=" in part or "xs=" in part or "datr=" in part:
                cookie_str = part
                # If there was a part between password and cookie, check if 2fa
                if idx == 2 and parts[2] and "c_user=" not in parts[2]:
                    two_fa = parts[2]
            elif "Mozilla" in part or "Chrome" in part or "AppleWebKit" in part:
                ua = part

        # If not found specifically, fallback to index heuristics
        if not cookie_str:
            for p in parts:
                if "=" in p:
                    cookie_str = p
                    break

        return uid, pwd, two_fa, cookie_str, ua

    # Raw cookie string
    return None, None, None, line, None


def parse_cookies(cookie_str: str, domain: str = ".facebook.com") -> List[Dict]:
    """Converts a standard cookie string (k=v; k2=v2) into Playwright cookie objects."""
    cookies = []
    items = cookie_str.split(";")
    for item in items:
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, val = item.split("=", 1)
        name = key.strip()
        value = val.strip()
        if not name:
            continue

        is_http_only = name.lower() in ("xs", "datr", "fr", "sb", "c_user")
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "secure": True,
            "httpOnly": is_http_only,
            "sameSite": "Lax",
        })
    return cookies


async def get_playwright_instance():
    """Tries importing patchright (stealth) first, fallback to standard playwright."""
    try:
        from patchright.async_api import async_playwright
        return async_playwright, "patchright"
    except ImportError:
        try:
            from playwright.async_api import async_playwright
            return async_playwright, "playwright"
        except ImportError:
            sys.exit("Error: Neither 'patchright' nor 'playwright' is installed. Run: pip install patchright")


async def run_fb_session(
    cookie_str: str,
    user_agent: Optional[str] = None,
    profile_dir: Optional[str] = None,
    headless: bool = False,
    proxy: Optional[str] = None,
    target_url: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    keep_open: bool = True,
):
    """Executes Facebook automated login using cookies."""
    async_pw, engine_name = await get_playwright_instance()
    print(f"[*] Engine: {engine_name}")

    cookies = parse_cookies(cookie_str)
    c_user = next((c["value"] for c in cookies if c["name"] == "c_user"), "Unknown")
    print(f"[*] Target Facebook UID: {c_user}")
    print(f"[*] Parsed {len(cookies)} cookies.")

    # Determine mobile vs desktop
    is_mobile = False
    if user_agent and ("Mobile" in user_agent or "Android" in user_agent or "iPhone" in user_agent):
        is_mobile = True

    if not target_url:
        target_url = "https://m.facebook.com" if is_mobile else "https://www.facebook.com"

    async with async_pw() as p:
        # No "--disable-blink-features=AutomationControlled": patchright handles the
        # webdriver flag; re-adding it is itself a detection signal.
        launch_args = [
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
        ]
        # Real Chrome > bundled Chromium for fingerprinting; "" disables (no Chrome installed).
        channel = os.getenv("DOLA_BROWSER_CHANNEL", "chrome").strip()

        context_kwargs = {
            "headless": headless,
            "args": launch_args,
            "locale": "vi-VN",
            "timezone_id": "Asia/Ho_Chi_Minh",
        }

        if proxy:
            context_kwargs["proxy"] = {"server": proxy}

        if user_agent:
            context_kwargs["user_agent"] = user_agent

        if is_mobile:
            context_kwargs["viewport"] = {"width": 412, "height": 915}
            context_kwargs["is_mobile"] = True
            context_kwargs["has_touch"] = True
        else:
            context_kwargs["viewport"] = {"width": 1366, "height": 768}

        # Persistent profile or ephemeral context
        if profile_dir:
            profile_path = Path(profile_dir)
            profile_path.mkdir(parents=True, exist_ok=True)
            print(f"[*] Using persistent profile: {profile_path.resolve()}")
            persistent_kwargs = {**context_kwargs, **({"channel": channel} if channel else {})}
            context = await p.chromium.launch_persistent_context(str(profile_path), **persistent_kwargs)
            browser = None
        else:
            launch_kwargs = {"headless": headless, "args": launch_args, **({"channel": channel} if channel else {})}
            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(**context_kwargs)

        try:
            # Inject cookies into context
            await context.add_cookies(cookies)

            page = context.pages[0] if context.pages else await context.new_page()

            print(f"[*] Navigating to {target_url}...")
            await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)

            # Check status
            curr_url = page.url
            print(f"[*] Current URL: {curr_url}")

            # Analyze page content for login state
            page_text = (await page.evaluate("() => document.body ? document.body.innerText : ''")).lower()

            status = "UNKNOWN"
            if "checkpoint" in curr_url or "checkpoint" in page_text:
                status = "CHECKPOINT (Yêu cầu xác minh danh tính)"
                print(f"[!] Trạng thái: {status}")
            elif any(txt in page_text for txt in ["đăng nhập", "login", "vào facebook", "mật khẩu"]) and not any(feed_txt in page_text for feed_txt in ["bảng tin", "tin", "thông báo", "bạn đang nghĩ gì"]):
                # Check if still asking for password
                status = "LOGIN_REQUIRED (Cookie có thể đã hết hạn hoặc cần đăng nhập lại)"
                print(f"[!] Trạng thái: {status}")
            else:
                status = "LOGGED_IN_SUCCESS (Đã đăng nhập thành công)"
                print(f"[✓] Trạng thái: {status}")

            if screenshot_path:
                await page.screenshot(path=screenshot_path)
                print(f"[*] Đã lưu ảnh màn hình trạng thái tại: {screenshot_path}")

            if keep_open and not headless:
                print("\n[+] Trình duyệt đang mở. Bạn có thể thao tác trực tiếp trên cửa sổ Facebook.")
                print("[+] Nhấn Ctrl+C trong Terminal khi muốn kết thúc phiên làm việc.\n")
                try:
                    while True:
                        await asyncio.sleep(1)
                except (asyncio.CancelledError, KeyboardInterrupt):
                    print("\n[*] Đang đóng phiên làm việc...")
        finally:
            await context.close()
            if browser:
                await browser.close()


def main():
    parser = argparse.ArgumentParser(description="Facebook Cookie Auto Login Automation")
    parser.add_argument(
        "--data", "-d",
        type=str,
        default=DEFAULT_RAW_DATA,
        help="Raw data (UID|PASS|2FA|COOKIE|UA) or raw cookie string."
    )
    parser.add_argument(
        "--file", "-f",
        type=str,
        help="Path to file containing account string or cookies."
    )
    parser.add_argument(
        "--profile", "-p",
        type=str,
        default=None,
        help="Directory to save persistent Chromium browser profile (e.g. ./fb_profiles/acc1)"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run in headless mode (without opening visible browser window)."
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="Proxy server URL (e.g. http://127.0.0.1:8080 or socks5://...)"
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Initial URL to navigate (default: https://m.facebook.com for mobile, https://www.facebook.com for desktop)"
    )
    parser.add_argument(
        "--screenshot",
        type=str,
        default="fb_login_status.png",
        help="Path to save screenshot after loading."
    )
    parser.add_argument(
        "--no-keep-open",
        action="store_true",
        help="Do not keep browser open; exit immediately after verifying."
    )

    args = parser.parse_args()

    raw_input = args.data
    if args.file and os.path.exists(args.file):
        raw_input = Path(args.file).read_text(encoding="utf-8").strip()
    if not raw_input:
        sys.exit("Error: cần --data (chuỗi cookie / dòng tài khoản) hoặc --file <đường dẫn>.")

    uid, pwd, two_fa, cookie_str, ua = parse_account_line(raw_input)

    if not cookie_str:
        sys.exit("Error: Không tìm thấy cookie hợp lệ trong dữ liệu đầu vào!")

    # Auto set profile path by UID if not specified
    profile_dir = args.profile
    if not profile_dir and uid:
        profile_dir = f"accounts_fb/{uid}"

    asyncio.run(
        run_fb_session(
            cookie_str=cookie_str,
            user_agent=ua,
            profile_dir=profile_dir,
            headless=args.headless,
            proxy=args.proxy,
            target_url=args.url,
            screenshot_path=args.screenshot,
            keep_open=not args.no_keep_open,
        )
    )


if __name__ == "__main__":
    main()
