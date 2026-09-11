#!/usr/bin/env python3
"""Clear all cookies from accounts/<account> so it returns to a clean logged-out
state (the profile itself is kept). Reset a dead session before logging in again.

Usage:
    python clear_cookies.py <account>
"""
import asyncio
import re
import sys

from cookie_service import clear_account_cookies

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python clear_cookies.py <account>")
    account = sys.argv[1]
    if not _NAME_RE.match(account):
        sys.exit("tên nick chỉ gồm chữ, số, _ hoặc - (1-32 ký tự)")
    res = asyncio.run(clear_account_cookies(account))
    print(f"đã xóa cookie của {account} (còn {res['remaining']} cookie)")
    sys.exit(0 if res["ok"] else 1)
