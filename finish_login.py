"""Mở profile accounts/<name> ở chế độ có giao diện để hoàn tất đăng nhập bằng tay.

Dùng khi add_account.py kẹt ở một bước cần bạn tự quyết định (chấp nhận điều khoản,
xác minh danh tính, chọn tài khoản...).

Cách dùng: python finish_login.py acc2
Script tự thoát khi phát hiện sessionid của dola.com. Ctrl+C để dừng sớm.
"""
import asyncio
import sys
import time

from patchright.async_api import async_playwright

from browser import cookie_value, launch_account_context

WAIT_LIMIT_SEC = 900
POLL_INTERVAL_SEC = 3


async def wait_for_session(context, account: str) -> bool:
    deadline = time.time() + WAIT_LIMIT_SEC
    start = time.time()
    last_report = 0.0
    while time.time() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        cookies = await context.cookies("https://www.dola.com")
        if cookie_value(cookies, "sessionid"):
            return True
        elapsed = time.time() - start
        if elapsed - last_report >= 30:
            last_report = elapsed
            print(f"  ...dang cho sessionid ({int(elapsed)}s, {len(cookies)} cookie dola.com)")
    return False


async def main():
    account = sys.argv[1] if len(sys.argv) > 1 else "acc2"
    async with async_playwright() as p:
        context = await launch_account_context(p, account, headless=False)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.dola.com/chat", timeout=60000,
                            wait_until="domcontentloaded")
            print("=" * 62)
            print(f"  Cua so da mo cho profile: {account}")
            print("  Lam LAN LUOT trong cua so do:")
            print("    1. Bam nut dang nhap  (log-in / login)")
            print("    2. Chon  'Google'")
            print("    3. Chon tai khoan neu Google hoi")
            print("    4. Trang dieu khoan Workspace -> bam nut xanh")
            print("    5. Cho tu quay ve dola.com")
            print("  Script tu thoat khi thay sessionid. Toi da 15 phut.")
            print("=" * 62)
            if await wait_for_session(context, account):
                print(f"[{account}] OK - sessionid da luu vao accounts/{account}")
                await page.wait_for_timeout(3000)
                return
            print(f"[{account}] Het thoi gian cho, chua thay sessionid.")
            sys.exit(1)
        finally:
            await context.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDa dung.")
