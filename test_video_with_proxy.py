"""Test tạo video thật với proxy (proxyxoay HTTP tĩnh).

Chạy:
  .venv/bin/python test_video_with_proxy.py --nick fb61593994871849 --proxy "http://user:pass@host:port"
"""
import argparse
import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Đảm bảo proxy được biết trước khi import BrowserPool
import config
import browser_pool
from browser_pool import BrowserPool


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nick", required=True)
    ap.add_argument("--proxy", required=True, help="http://user:pass@host:port")
    ap.add_argument("--prompt", default="a cat playing with yarn")
    ap.add_argument("--ratio", default="9:16")
    ap.add_argument("--duration", type=int, default=10)
    args = ap.parse_args()

    nick = args.nick
    proxy_url = args.proxy

    # 1. Tạo temp dir chứa accounts/<nick>/proxy.txt + copy cookies.json
    tmp = Path(tempfile.mkdtemp(prefix="dola_test_"))
    nick_dir = tmp / "accounts" / nick
    nick_dir.mkdir(parents=True)

    src = config.ACCOUNTS_DIR / nick
    if not (src / "cookies.json").exists():
        sys.exit(f"Không thấy cookies.json ở {src}")

    # Copy cần thiết
    for fn in ("cookies.json", "device_id.json"):
        s = src / fn
        if s.exists():
            shutil.copy(s, nick_dir / fn)

    (nick_dir / "proxy.txt").write_text(proxy_url, encoding="utf-8")
    print(f"[setup] nick_dir={nick_dir}")
    print(f"[setup] proxy.txt = {proxy_url[:60]}...")

    # 2. Save state, override config
    saved = (browser_pool.config.ACCOUNTS_DIR, browser_pool.config.PROXY,
             browser_pool.config.SUBMIT_MODE, browser_pool.config.MAX_JOBS_PER_IP)
    browser_pool.config.ACCOUNTS_DIR = tmp / "accounts"
    browser_pool.config.PROXY = ""
    browser_pool.config.SUBMIT_MODE = "fetch"   # chạy qua curl_cffi (HTTP), không mở Chrome
    browser_pool.config.MAX_JOBS_PER_IP = 1     # an toàn

    db_path = str(tmp / "test.db")

    # 3. Tạo pool và chạy
    pool = BrowserPool(accounts_dir=str(tmp / "accounts"), db_path=db_path, max_concurrency=2)

    # State tracking
    events = []

    def on_opening(acct):
        events.append(("opening", acct))

    def on_conversation_id(acct, cid):
        events.append(("conversation_id", acct, cid[:20]))

    def on_submitted(acct, ok):
        events.append(("submitted", acct, ok))

    def on_poll(acct, status, elapsed):
        events.append(("poll", acct, status, f"{elapsed:.0f}s"))

    def on_balance(acct, remaining):
        events.append(("balance", acct, remaining))

    try:
        t0 = time.time()
        print(f"\n[run] Submitting: prompt='{args.prompt[:40]}' ratio={args.ratio} dur={args.duration}s")
        result = await pool.generate_video(
            args.prompt, args.ratio, args.duration,
            account=nick,
            on_opening=on_opening,
            on_conversation_id=on_conversation_id,
            on_submitted=on_submitted,
            on_poll=on_poll,
            on_balance=on_balance,
        )
        elapsed = time.time() - t0
        print(f"\n✅ DONE in {elapsed:.1f}s")
        print(f"   local_path: {result.get('local_path', '?')}")
        print(f"   conversation_id: {result.get('conversation_id', '?')}")
        print(f"   account: {result.get('account', '?')}")
    except Exception as e:
        print(f"\n❌ FAIL: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Restore config
        (browser_pool.config.ACCOUNTS_DIR, browser_pool.config.PROXY,
         browser_pool.config.SUBMIT_MODE, browser_pool.config.MAX_JOBS_PER_IP) = saved
        shutil.rmtree(tmp, ignore_errors=True)

    # 4. In events timeline
    print("\n=== Events ===")
    for ev in events:
        print(f"  {ev}")


if __name__ == "__main__":
    asyncio.run(main())
