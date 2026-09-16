#!/usr/bin/env python3
"""
Test video generation with imported Dola cookie.
"""
import asyncio
import json
import sys
from pathlib import Path

from cookie_service import apply_cookies_to_account
from standalone_video_generator import DolaVideoGenerator

ACCOUNT_NAME = "acc_test"
COOKIE_FILE = "cookies_dola.txt"

async def main():
    print("=" * 60)
    print(f"1. Nạp cookie vào profile '{ACCOUNT_NAME}'...")
    print("=" * 60)
    
    if not Path(COOKIE_FILE).exists():
        print(f"[✗] Không tìm thấy file {COOKIE_FILE}")
        return
        
    cookie_raw = Path(COOKIE_FILE).read_text(encoding="utf-8")
    
    # Import cookie vào accounts/acc_test
    try:
        import_res = await apply_cookies_to_account(ACCOUNT_NAME, cookie_raw, ui_lang="ja")
        print(f"Kết quả nạp cookie: {import_res.get('message')}")
        if not import_res.get("ok"):
            print("[✗] Cookie không kích hoạt được phiên Dola hợp lệ!")
            return
        print(f"[✓] Đã kích hoạt phiên thành công cho {ACCOUNT_NAME}!")
    except Exception as e:
        print(f"[✗] Lỗi nạp cookie: {e}")
        return

    print("\n" + "=" * 60)
    print("2. Tiến hành thử nghiệm tạo video với Seedance 2.0...")
    print("=" * 60)
    
    prompt = sys.argv[1] if len(sys.argv) > 1 else "A golden retriever puppy chasing a glowing butterfly in a sunlit meadow, 4k cinematic"
    model = "seedance-2.0"
    duration = 10
    ratio = "16:9"
    
    print(f"Prompt: {prompt}")
    print(f"Model: {model} | Duration: {duration}s | Ratio: {ratio}")
    
    generator = DolaVideoGenerator(
        profile_dir=f"accounts/{ACCOUNT_NAME}",
        headless=False,
        download_dir="downloads"
    )
    
    try:
        result = await generator.generate_video(
            prompt=prompt,
            model=model,
            duration=duration,
            ratio=ratio,
            timeout=600
        )
        print("\n" + "=" * 60)
        print("🎉 TẠO VIDEO THÀNH CÔNG!")
        print("=" * 60)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"\n[✗] Lỗi trong quá trình tạo video: {e}")

if __name__ == "__main__":
    # CHỐT: file này GỬI VIDEO THẬT lên Dola bằng cookies_dola.txt (tốn lượt, mở Chrome có cửa sổ). Tên test_*.py nên
    # vòng "chạy toàn bộ test" (for t in test_*.py) từng kéo nó chạy theo → mỗi lần chạy test là một hội thoại tạo
    # video thật trên tài khoản (16/09). Giờ phải gõ --send mới chạy, như test_web_submit.py / test_android_submit.py.
    if "--send" not in sys.argv:
        print("[bỏ qua] test_video_with_cookie.py gửi video THẬT (tốn lượt). Chạy: "
              ".venv/bin/python test_video_with_cookie.py --send [prompt]")
        sys.exit(0)
    sys.argv.remove("--send")
    asyncio.run(main())
