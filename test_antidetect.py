#!/usr/bin/env python3
"""Anti-detect posture checks.

Default: static asserts (no browser needed) — the tells we removed must stay removed.
    python test_antidetect.py

Live probe: launch a real account profile and print the fingerprint signals a site
reads to spot automation (webdriver flag, headless UA, missing window.chrome).
    python test_antidetect.py --live <account>
"""
import asyncio
import sys

import browser
import config


def test_static() -> None:
    """The known tells must not creep back in."""
    assert "--disable-blink-features=AutomationControlled" not in browser.LAUNCH_ARGS, \
        "AutomationControlled flag is a detection signal — patchright handles webdriver itself"
    assert hasattr(config, "BROWSER_CHANNEL"), "channel knob missing from config"
    print("[static] OK: no AutomationControlled flag, BROWSER_CHANNEL knob present")
    print(f"[static] BROWSER_CHANNEL={config.BROWSER_CHANNEL!r}  LAUNCH_ARGS={browser.LAUNCH_ARGS}")

    # #3 fingerprint per-nick: cố định theo nick, khác nhau giữa nick, có đủ override (không random mỗi lần)
    a1, a2 = browser._fingerprint_js("nickA"), browser._fingerprint_js("nickA")
    b1 = browser._fingerprint_js("nickB")
    assert a1 == a2, "fingerprint phải ỔN ĐỊNH cho cùng một nick"
    assert a1 != b1, "fingerprint phải KHÁC nhau giữa các nick"
    assert all(k in a1 for k in ("hardwareConcurrency", "deviceMemory", "37445", "37446")), "thiếu override"
    assert config.FINGERPRINT_PER_NICK is False, "JS fingerprint phải MẶC ĐỊNH TẮT (JS tamper dễ bị bắt)"
    print("[static] OK: fingerprint per-nick ổn định/khác nhau nhưng mặc định TẮT; #1 WebRTC dùng cờ native")


async def probe(account: str) -> None:
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        ctx = await browser.launch_account_context(p, account, headless=config.HEADLESS)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            sig = await page.evaluate(
                """() => ({
                    webdriver: navigator.webdriver,
                    userAgent: navigator.userAgent,
                    hasChrome: !!window.chrome,
                    languages: navigator.languages,
                    platform: navigator.platform,
                })"""
            )
            print(f"[live] navigator.webdriver = {sig['webdriver']}  (want: false/undefined)")
            print(f"[live] window.chrome       = {sig['hasChrome']}  (want: True)")
            headless_ua = "HeadlessChrome" in (sig["userAgent"] or "")
            print(f"[live] HeadlessChrome in UA = {headless_ua}  (want: False)")
            print(f"[live] userAgent = {sig['userAgent']}")
            print(f"[live] languages = {sig['languages']}  platform = {sig['platform']}")
            bad = bool(sig["webdriver"]) or headless_ua or not sig["hasChrome"]
            print("[live] RESULT:", "FLAGGED" if bad else "clean")
        finally:
            await ctx.close()


if __name__ == "__main__":
    test_static()
    if len(sys.argv) >= 3 and sys.argv[1] == "--live":
        asyncio.run(probe(sys.argv[2]))
    else:
        print("[hint] run `python test_antidetect.py --live <account>` for a live fingerprint probe")
