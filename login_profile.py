#!/usr/bin/env python3
"""Open accounts/<name> in a HEADED browser so you log into dola.com by hand,
then auto-capture the session cookies into the profile once sessionid appears.

No password/OAuth automation: you do the login in the window; this just waits
for the session and persists it (the persistent profile saves cookies on close).

Usage:
    python login_profile.py <account> [ui_lang]   # ui_lang default "ja"
"""
import asyncio
import re
import sys
from pathlib import Path

from patchright.async_api import async_playwright

from browser import (cookie_value, force_ui_language, launch_account_context, persist_dola_cookies,
                     pin_session_cookies)
import config

LOGIN_URL = "https://www.dola.com/chat"
POLL_SECONDS = 3
LOGIN_TIMEOUT = 300  # 5 minutes to finish the manual login
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


async def login_profile(account: str, ui_lang: str = "ja") -> bool:
    if not _NAME_RE.match(account):
        sys.exit("tên nick chỉ gồm chữ, số, _ hoặc - (1-32 ký tự)")
    # New nick: create the profile dir so Chromium initializes a fresh persistent
    # profile (launch_account_context refuses a missing folder, meant for the worker).
    (config.ACCOUNTS_DIR / account).mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        # use_extension=True: nạp extension Dola30 + vá skill-pack in-page để chip 30s hiện ra.
        # Không có nó, giao diện Dola chỉ cho chọn tới 15s → mở profile xong vẫn không tạo tay được 30s.
        # NHƯNG đăng nhập là đường KHÔI PHỤC nick, phải luôn mở được: extension bị tắt/thiếu thư mục thì
        # launch_account_context ném lỗi → rơi về mở không extension thay vì chết, chỉ mất chip 30s.
        try:
            context = await launch_account_context(p, account, headless=False, use_extension=True)
        except (RuntimeError, FileNotFoundError) as exc:
            print(f"[{account}] (không nạp được extension 30s: {str(exc)[:80]} — mở cửa sổ đăng nhập bình thường)",
                  flush=True)
            context = await launch_account_context(p, account, headless=False)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(LOGIN_URL, timeout=60000, wait_until="domcontentloaded")
            print(f"[{account}] Hãy đăng nhập dola.com trong cửa sổ vừa mở. "
                  f"Đang chờ tối đa {LOGIN_TIMEOUT}s...", flush=True)
            # A stale (dead) sessionid may already sit in the profile, so cookie *presence*
            # is not proof of login. A fresh login rotates sessionid, so wait for the value
            # to CHANGE from this baseline. Read at context level (survives the tab churn of
            # the OAuth flow — a fixed page ref gets closed and would crash).
            baseline_sid = cookie_value(await context.cookies("https://www.dola.com"), "sessionid")
            for _ in range(LOGIN_TIMEOUT // POLL_SECONDS):
                await asyncio.sleep(POLL_SECONDS)
                try:
                    cur_sid = cookie_value(await context.cookies("https://www.dola.com"), "sessionid")
                except Exception:
                    print(f"[{account}] Cửa sổ đã đóng trước khi đăng nhập xong.", flush=True)
                    return False
                if cur_sid and cur_sid != baseline_sid:
                    # Force the Dola UI language so the render worker's selectors match, and
                    # pin the fresh session cookie so it survives the context close below.
                    await force_ui_language(context, ui_lang)
                    await pin_session_cookies(context)
                    # cookies.json là nguồn của verify HTTP + gửi không-Chrome: không ghi thì nó giữ cookie chết cũ
                    # và nick bị báo "đã đăng xuất" mãi dù vừa đăng nhập xong.
                    persist_dola_cookies(account, await context.cookies("https://www.dola.com"))
                    print(f"[{account}] OK: đăng nhập mới thành công, đã lưu vào accounts/{account}.",
                          flush=True)
                    return True
            print(f"[{account}] Hết giờ chờ, sessionid chưa đổi (chưa đăng nhập xong).",
                  flush=True)
            return False
        finally:
            await context.close()  # persistent context flushes cookies to the profile here


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: python login_profile.py <account> [ui_lang]")
    lang = sys.argv[2] if len(sys.argv) == 3 else "ja"
    ok = asyncio.run(login_profile(sys.argv[1], lang))
    sys.exit(0 if ok else 1)
