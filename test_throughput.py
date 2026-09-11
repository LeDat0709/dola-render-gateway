"""1 slot trình duyệt vẫn chạy được nhiều video song song: slot trả lại ngay sau khi gửi xong."""
import asyncio, tempfile, time
from pathlib import Path

import browser_pool
from browser_pool import BrowserPool, resize_semaphore


def _pool(tmp: str, nicks=("acc1", "acc2")) -> BrowserPool:
    accounts = Path(tmp) / "accounts"
    for n in nicks:
        (accounts / n).mkdir(parents=True)
    p = BrowserPool(accounts_dir=str(accounts), db_path=str(Path(tmp) / "pool.db"), max_concurrency=1)
    for n in nicks:
        p.set_login_status(n, True)
    return p


async def _scenario(tmp: str) -> float:
    pool = _pool(tmp)
    submitting = 0

    async def fake_generate(account, prompt, *a, on_browser_free=None, **kw):
        nonlocal submitting
        submitting += 1
        assert submitting <= 1, "vượt trần Chrome cùng lúc"
        await asyncio.sleep(0.2)          # giai đoạn mở trình duyệt + gửi prompt
        submitting -= 1
        if on_browser_free:
            on_browser_free()             # trình duyệt đã đóng → trả slot
        await asyncio.sleep(0.6)          # Dola render, theo dõi bằng HTTP
        return {"local_path": f"/tmp/{account}.mp4", "account": account}

    browser_pool.generate_video = fake_generate
    start = time.monotonic()
    await asyncio.gather(pool.generate_video("p1"), pool.generate_video("p2"))
    return time.monotonic() - start


def test_render_overlaps():
    with tempfile.TemporaryDirectory() as tmp:
        elapsed = asyncio.run(_scenario(tmp))
    # Nối tiếp = 1.6s; song song = ~0.8s (gửi lần lượt, render chồng nhau).
    assert elapsed < 1.2, f"slot trình duyệt chưa được trả sớm ({elapsed:.2f}s)"


def test_resize_limit_live():
    """Đổi trần lúc đang chạy: tăng có slot ngay, giảm chỉ ăn vào slot rảnh."""
    async def main():
        sem = asyncio.Semaphore(1)
        await sem.acquire()
        resize_semaphore(sem, 2)                 # 1 -> 3
        await sem.acquire(); await sem.acquire()  # thêm 2 slot dùng được ngay
        assert sem.locked()

        resize_semaphore(sem, -2)                 # 3 -> 1, chờ slot rảnh
        for _ in range(3):
            sem.release()
        await asyncio.sleep(0.05)                 # để task "park" giữ lại 2 permit
        await sem.acquire()
        try:
            await asyncio.wait_for(sem.acquire(), 0.1)
            raise AssertionError("giảm trần không có tác dụng")
        except asyncio.TimeoutError:
            pass
    asyncio.run(main())


if __name__ == "__main__":
    test_render_overlaps(); test_resize_limit_live(); print("OK")
