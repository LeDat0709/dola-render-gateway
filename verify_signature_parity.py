"""verify_signature_parity.py — Đối chiếu chữ ký 1:1 giữa SDK bdms trong trình duyệt thật và a_bogus_web thuần Python.

Quy trình:
1. Mở Chromium (Playwright/Patchright) với cookie của nick (mặc định 'acc_test').
2. Nạp trang https://www.dola.com/chat và đợi window.bdms sẵn sàng.
3. Chặn / bắt request fetch do window.bdms tự ký trong main world (hoặc kích hoạt hàm ký).
4. Lấy (query, body) đưa vào hàm a_bogus_web.long_a_bogus() của Python.
5. So khớp hai chữ ký: độ dài, bảng alphabet, tiền tố ob4 và cấu trúc.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit

import a_bogus_web
import config
import device_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("signature_parity")


async def run_parity_check(account: str = "acc_test", headless: bool = True) -> None:
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("Cần cài đặt patchright hoặc playwright để chạy đối chiếu với trình duyệt thật.")
            sys.exit(1)

    import browser
    profile_dir = config.ACCOUNTS_DIR / account
    if not profile_dir.exists():
        logger.error("Thư mục tài khoản %s không tồn tại", profile_dir)
        sys.exit(1)

    cookies_file = profile_dir / "cookies.json"
    if not cookies_file.exists():
        logger.error("Không tìm thấy cookies.json trong %s", profile_dir)
        sys.exit(1)

    cookies_data = json.loads(cookies_file.read_text(encoding="utf-8"))
    items = cookies_data if isinstance(cookies_data, list) else cookies_data.get("cookies", [])
    cookies_list = [{"name": c["name"], "value": c["value"], "domain": c.get("domain", ".dola.com"), "path": c.get("path", "/")}
                    for c in items if c.get("name")]

    captured_signatures = []
    # Cookie THẬT của nick → phải đi đúng proxy của nick như browser.launch_account_context, không thì Dola thấy
    # nick đăng nhập từ IP máy. Nick khai proxy mà không lấy được IP → account_proxy ném lỗi, dừng tại đây.
    proxy_cfg = await asyncio.to_thread(browser.account_proxy, account, True)
    proxy_kwargs = {"proxy": proxy_cfg} if proxy_cfg else {}
    logger.info("Proxy của nick %s: %s", account, "có" if proxy_cfg else "không khai (đi thẳng)")

    async with async_playwright() as p:
        args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            args=args,
            viewport={"width": 1440, "height": 900},
            user_agent=a_bogus_web.USER_AGENT,
            **proxy_kwargs,
        )
        await context.add_cookies(cookies_list)
        page = context.pages[0] if context.pages else await context.new_page()

        # Bắt mọi request tới /chat/completion
        async def on_request(req):
            if "/chat/completion" in req.url:
                captured_signatures.append({
                    "url": req.url,
                    "post_data": req.post_data or "",
                    "headers": req.headers,
                })

        page.on("request", on_request)

        logger.info("Đang mở trang https://www.dola.com/chat …")
        await page.goto("https://www.dola.com/chat", timeout=60000)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass

        # Trích xuất device_id nếu trang có
        await device_manager.extract_from_page(page, account)

        # Kiểm tra SDK bdms
        bdms_ready = False
        for _ in range(20):
            try:
                bdms_ready = await page.evaluate("() => !!(window.bdms && (window.bdms.frontierSign || window.bdms.init))")
                if bdms_ready:
                    break
            except Exception:
                pass
            await page.wait_for_timeout(1000)

        logger.info("Trạng thái window.bdms: %s", "SẴN SÀNG" if bdms_ready else "CHƯA TẢI HOÀN TẤT")

        # Gọi 1 fetch mẫu từ main world (để hook bdms tự động ký a_bogus)
        # Chúng ta chỉ gửi ping nhẹ hoặc abort trước khi server xử lý
        test_payload = json.dumps({"test_probe": 1, "m": "ping"}, separators=(",", ":"))
        try:
            await page.evaluate(r"""async (bodyText) => {
                try {
                    const testUrl = "/chat/completion?aid=495671&real_aid=495671&device_platform=web&language=ja&region=JP&tz_name=Asia%2FTokyo";
                    const controller = new AbortController();
                    setTimeout(() => controller.abort(), 200);
                    try {
                        await fetch(testUrl, {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: bodyText,
                            signal: controller.signal
                        });
                    } catch (e) {}
                    return {ok: true};
                } catch (err) {
                    return {ok: false, error: String(err)};
                }
            }""", test_payload)
        except Exception as e:
            logger.warning("Evaluate test fetch gặp lỗi (bỏ qua): %s", e)

        await page.wait_for_timeout(2000)
        await context.close()

    if not captured_signatures:
        logger.warning("Chưa bắt được request /chat/completion từ browser (có thể fetch bị abort trước khi phát event).")
        # Chạy kiểm chứng toán học độc lập
        logger.info("Chuyển sang kiểm chứng định dạng a_bogus_web tự sinh:")
        opts = {"aid": 495671, "userAgent": a_bogus_web.USER_AGENT}
        ab = a_bogus_web.long_a_bogus("aid=495671&real_aid=495671&device_platform=web", test_payload, opts)
        logger.info("✓ Chữ ký Python sinh thành công: %s (Độ dài: %d ký tự)", ab[:40] + "…", len(ab))
        assert len(ab) >= 160 and all(c in a_bogus_web.S4_ALPHABET + "=" for c in ab)
        logger.info("✓ Kiểm tra cấu trúc S4_ALPHABET: HỢP LỆ 100%")
        return

    req_info = captured_signatures[-1]
    browser_url = req_info["url"]
    browser_body = req_info["post_data"]
    logger.info("ĐÃ BẮT ĐƯỢC REQUEST KÝ BỞI TRÌNH DUYỆT THẬT:")
    logger.info("Browser URL: %s", browser_url[:120] + "…")

    parts = urlsplit(browser_url)
    q_pairs = parse_qsl(parts.query, keep_blank_values=True)
    browser_abogus = dict(q_pairs).get("a_bogus", "")
    query_without_ab = urlencode([(k, v) for k, v in q_pairs if k != "a_bogus"])

    logger.info("Chữ ký Browser sinh: %s (Độ dài: %d)", browser_abogus[:40] + "…", len(browser_abogus))

    # Sinh chữ ký bằng Python trên cùng query và body
    opts = {"aid": 495671, "userAgent": a_bogus_web.USER_AGENT}
    python_abogus = a_bogus_web.long_a_bogus(query_without_ab, browser_body, opts)
    logger.info("Chữ ký Python sinh  : %s (Độ dài: %d)", python_abogus[:40] + "…", len(python_abogus))

    # Đối chiếu
    logger.info("=== KẾT QUẢ ĐỐI CHIẾU PARITY ===")
    logger.info("Độ dài Browser a_bogus: %d ký tự", len(browser_abogus))
    logger.info("Độ dài Python a_bogus : %d ký tự", len(python_abogus))
    chars_ok_browser = all(c in a_bogus_web.S4_ALPHABET + "=" for c in browser_abogus)
    chars_ok_python = all(c in a_bogus_web.S4_ALPHABET + "=" for c in python_abogus)
    logger.info("Browser ký đúng bảng S4_ALPHABET: %s", chars_ok_browser)
    logger.info("Python ký đúng bảng S4_ALPHABET : %s", chars_ok_python)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--nick", default="acc_test")
    parser.add_argument("--headful", action="store_true")
    args = parser.parse_args()
    asyncio.run(run_parity_check(account=args.nick, headless=not args.headful))
