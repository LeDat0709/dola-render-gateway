"""Bộ phát hiện màn "Dola không khả dụng ở khu vực này" — chạy: .venv/bin/python test_region_block.py"""
import asyncio

from patchright.async_api import async_playwright

from browser import page_region_blocked

BLOCK_PAGE = ("data:text/html;charset=utf-8,<div><p>この国または地域ではDolaは利用できません</p>"
              "<button>更新</button><button>ホームページに戻る</button></div>")
CHAT_PAGE = "data:text/html;charset=utf-8,<div><textarea></textarea></div>"
LOGIN_PAGE = "data:text/html;charset=utf-8,<div><button>Googleで続ける</button><button>ログイン</button></div>"
LOADING_PAGE = "data:text/html;charset=utf-8,<div>...</div>"


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        pg = await b.new_page()
        for url, want, wait in ((BLOCK_PAGE, True, 6), (CHAT_PAGE, False, 6), (LOGIN_PAGE, False, 6), (LOADING_PAGE, False, 1)):
            await pg.goto(url)
            t = asyncio.get_event_loop().time()
            got = await page_region_blocked(pg, wait_s=wait)
            dt = asyncio.get_event_loop().time() - t
            assert got is want, (url[:60], got, want)
            if want is False and url != LOADING_PAGE:
                assert dt < 2, f"phải dừng sớm khi thấy khung chat/màn đăng nhập, mất {dt:.1f}s"
        await b.close()
    print("test_region_block: OK")


if __name__ == "__main__":
    asyncio.run(main())
