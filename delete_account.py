#!/usr/bin/env python3
"""Delete a nick entirely: remove its accounts/<name> profile and its pool metadata.

Refuses while the profile is open in a live Chromium window (close it first).

Usage:
    python delete_account.py <account>
"""
import re
import shutil
import sqlite3
import sys
from pathlib import Path

from browser import assert_profile_free
import config

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_POOL_DB = "pool_usage.db"


def delete_account(name: str) -> None:
    if not _NAME_RE.match(name):
        sys.exit("ten nick chi gom chu, so, _ hoac - (1-32 ky tu)")
    profile_dir = config.ACCOUNTS_DIR / name
    if not profile_dir.exists():
        sys.exit(f"nick {name} khong ton tai")
    # Fails fast if a live Chromium still holds the profile (window open / generating).
    assert_profile_free(profile_dir)
    shutil.rmtree(profile_dir)
    # Best-effort cleanup of pool metadata so a re-added nick starts clean; the running
    # gateway keeps its own connection, so use a busy timeout and don't fail on lock.
    try:
        conn = sqlite3.connect(_POOL_DB, timeout=5)
        conn.execute("DELETE FROM accounts_meta WHERE name=?", (name,))
        conn.execute("DELETE FROM usage WHERE account=?", (name,))
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print(f"da xoa profile {name}; canh bao: chua don duoc metadata pool ({e})")
        return
    print(f"da xoa nick {name} (profile + metadata)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python delete_account.py <account>")
    delete_account(sys.argv[1])
