"""Nick dùng proxy TĨNH (ip:port) mà proxy chết: bỏ qua nick đó, chạy nick khác — KHÔNG văng lỗi.

Trước đây nhánh bắt lỗi proxy gọi self._record_presubmit_failure (hàm không tồn tại) → job văng AttributeError
ngay tại nick đầu, không hề thử nick khác dù nick khác chạy được.
Chạy: .venv/bin/python test_dead_static_proxy.py
"""
import asyncio
import tempfile
from pathlib import Path

import browser
import browser_pool
from browser_pool import BrowserPool

browser_pool.config.SUBMIT_GAP_SEC = 0
browser_pool.config.SUBMIT_JITTER_SEC = 0
browser_pool.config.AUTO_RETRY = True


def test_dead_static_proxy_skips_to_next_nick():
    saved = (browser_pool.config.ACCOUNTS_DIR, browser_pool.config.PROXY, browser.probe_proxy,
             getattr(browser, "quick_cookie_check", None), browser_pool.generate_video)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            acc = Path(tmp) / "accounts"
            for n in ("n1", "n2"):
                (acc / n).mkdir(parents=True)
            (acc / "n1" / "proxy.txt").write_text("10.255.255.1:3128", encoding="utf-8")   # n1: proxy tĩnh chết
            browser_pool.config.ACCOUNTS_DIR, browser_pool.config.PROXY = acc, ""
            pool = BrowserPool(accounts_dir=str(acc), db_path=str(Path(tmp) / "p.db"), max_concurrency=2)

            async def dead_probe(raw, timeout=3.0):
                return "Connection refused"
            browser.probe_proxy = dead_probe
            browser._proxy_probe_cache.clear()

            async def cookie_ok(account):   # cho qua bước kiểm cookie để tới bước kiểm proxy
                return True
            browser.quick_cookie_check = cookie_ok

            calls = []

            async def gen(account, *a, on_submitted=None, **kw):
                calls.append(account)
                return {"video_url": "u", "account": account}
            browser_pool.generate_video = gen

            r = asyncio.run(pool.generate_video("p", "9:16", 10, account="n1"))
            assert r["account"] == "n2", r
            assert calls == ["n2"], f"nick proxy chết không được mở, nick kia phải chạy: {calls}"
    finally:
        (browser_pool.config.ACCOUNTS_DIR, browser_pool.config.PROXY, browser.probe_proxy,
         qc, browser_pool.generate_video) = saved
        if qc is not None:
            browser.quick_cookie_check = qc


if __name__ == "__main__":
    test_dead_static_proxy_skips_to_next_nick()
    print("OK")
