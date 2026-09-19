"""render_6_dramas.py — Render tự động 6 kịch bản Drama 30s 9:16 model 2.5 qua HTTP thuần & Proxy US."""
import asyncio
import os
import sys
import time
from pathlib import Path
import config
import submit_http
import video_worker_ui as vw

PROMPTS = [
    {
        "id": "drama_1_biker_bar",
        "title": "Drama 1 - Biker Bar (1s)",
        "prompt": (
            "A 9-year-old girl walks into a roadside biker bar at midnight wearing one slipper and "
            "holding her toddler brother's backpack, pleading with patrons not to contact her stepfather. "
            "When an enforcer searches the backpack, he uncovers a motel room key marked with today's date "
            "and the name of their own motorcycle club president. Handheld cinematic tracking shot, "
            "gritty rain aesthetic, high contrast drama."
        ),
    },
    {
        "id": "drama_2_principal",
        "title": "Drama 2 - Principal & School (2s)",
        "prompt": (
            "A middle school principal publicly accuses a 12-year-old student of stealing cash from the office, "
            "dumping the boy's backpack onto a desk in front of staff and a School Resource Officer. Rather than "
            "alerting on the spilled money, the officer's K9 ignores the desk and scratches frantically at the "
            "principal's locked supply closet. Body-cam realistic aesthetic, intense emotional confrontation."
        ),
    },
    {
        "id": "drama_3_restaurant",
        "title": "Drama 3 - Restaurant Manager & Waitress (3s)",
        "prompt": (
            "A restaurant manager aggressively pulls a chair out from underneath an eight-months pregnant waitress, "
            "dumping her tip jar into a commercial trash can. Minutes later, a County Health & Safety Inspector arrives, "
            "confronting the manager in front of the dining room regarding three deleted employee incident reports. "
            "Cinematic realistic documentary lighting, high tension."
        ),
    },
    {
        "id": "drama_4_veteran",
        "title": "Drama 4 - Airline Gate & Veteran (4s)",
        "prompt": (
            "An airline gate supervisor publicly tears an elderly Vietnam veteran's boarding pass in half, claiming his "
            "wooden medal case cannot be carried aboard. A flight attendant alerts the cockpit, and the airline Captain "
            "emerges into the terminal, recognizing the veteran and revealing his own father believed the man died in combat. "
            "Documentary style cinematic lighting, powerful emotional climax."
        ),
    },
    {
        "id": "drama_5_nursing_home",
        "title": "Drama 5 - Nursing Home Clock Camera (5s)",
        "prompt": (
            "A nursing home director repeatedly accuses an elderly resident of dementia after he claims a midnight intruder "
            "enters his room. The resident's granddaughter conceals a camera inside a digital clock. The next morning, "
            "the director barges in demanding the clock back, revealing his guilt before anyone mentioned the camera. "
            "Hyper-realistic dramatic lighting, sharp emotional confrontation."
        ),
    },
    {
        "id": "drama_6_warehouse",
        "title": "Drama 6 - Warehouse Forklift (6s)",
        "prompt": (
            "A warehouse foreman fires a quiet forklift driver after falsely accusing him of stealing a fifty-thousand-dollar "
            "cargo shipment. When security reviews surveillance footage, the recording proves the driver was never near the dock, "
            "revealing instead that the foreman unlocked the gate at 3:17 AM for an unmarked van. "
            "Raw facility surveillance realism, intense industrial lighting."
        ),
    },
]

DOWNLOAD_DIR = Path("/Users/dat/Downloads/dola-render-gateway/downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

def get_account_proxy(account: str) -> str | None:
    """Proxy của nick theo ĐÚNG đường production (proxy riêng → proxy chung), đã chuẩn hoá cho aiohttp/curl.

    Trước đây nick thiếu proxy.txt rơi về một proxy có user/pass GHI CỨNG trong file này (repo public → lộ).
    Nick khai proxy mà không lấy được IP thì account_proxy_url ném lỗi → bỏ nick đó, KHÔNG đi IP máy.
    """
    from browser import account_proxy_url
    return account_proxy_url(account) or None

async def render_single(account: str, item: dict) -> str | None:
    try:
        proxy = get_account_proxy(account)
    except Exception as e:  # noqa: BLE001 — proxy khai mà hỏng: bỏ nick, không gửi bằng IP máy
        print(f"  ❌ Không lấy được proxy của nick {account}: {e}")
        return None
    print(f"\n=======================================================")
    print(f"🎬 BẮT ĐẦU: {item['title']} (Nick: {account})")
    print(f"=======================================================")
    
    # 1. Gửi lệnh qua HTTP
    print(f"  [1/3] Gửi lệnh tạo video 30s Seedance 2.5 lên Dola (proxy: {'có' if proxy else 'đi thẳng'})...")
    try:
        conv_id = await submit_http.submit_via_http(
            account=account,
            prompt=item["prompt"],
            ratio="9:16",
            duration=30,
            model=config.MODEL_KEY_SEEDANCE25,
            proxy=proxy
        )
        print(f"  ✓ Gửi thành công! conversation_id = {conv_id}")
    except Exception as e:
        print(f"  ❌ Gửi thất bại: {e}")
        return None

    # 2. Chờ Dola render & download
    print(f"  [2/3] Chờ Dola render video (khoảng 2 - 4 phút)...")
    cookies = submit_http.load_account_cookies(account)
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    ms_token = cookies.get("msToken", "")
    
    def on_poll(now):
        print(f"    ↳ Đang render video trên Dola...", flush=True)

    result = await vw.poll_conversation_http(
        account=account,
        cookie=cookie_str,
        ms_token=ms_token,
        fp="",
        conversation_id=conv_id,
        timeout=1800,
        on_poll=on_poll,
        prompt=item["prompt"],
    )

    # 3. Kiểm tra kết quả
    local_path = result.get("local_path")
    if local_path and Path(local_path).exists():
        final_path = DOWNLOAD_DIR / f"{item['id']}_{conv_id}.mp4"
        Path(local_path).rename(final_path)
        print(f"  ✓ HOÀN THÀNH: {final_path}")
        print(f"  ✓ Dung lượng: {final_path.stat().st_size / 1024:.1f} KB")
        return str(final_path)
    else:
        print(f"  ❌ Không lấy được video: {result}")
        return None

async def main():
    # Tìm danh sách via có sẵn trong accounts/
    available_accs = [
        p.name for p in sorted(config.ACCOUNTS_DIR.iterdir())
        if p.is_dir() and (p / "cookies.json").exists() and p.name.startswith("via_")
    ]
    if not available_accs:
        available_accs = ["diane_williams", "acc_test"]

    print(f"Tìm thấy {len(available_accs)} tài khoản via sẵn sàng: {available_accs[:5]}...")
    
    acc_idx = 0
    for item in PROMPTS:
        success = False
        while acc_idx < len(available_accs) and not success:
            acc = available_accs[acc_idx]
            res = await render_single(acc, item)
            if res:
                success = True
                print(f"✓ Video '{item['title']}' đã hoàn thành mỹ mãn!")
            else:
                print(f"Nick {acc} lỗi hoặc hết quota, tự động đổi nick tiếp theo...")
                acc_idx += 1
                await asyncio.sleep(5)
        
        if not success:
            print("Đã duyệt hết danh sách tài khoản khả dụng.")
            break
        
        await asyncio.sleep(15)  # Giãn cách an toàn giữa các shot

if __name__ == "__main__":
    asyncio.run(main())

