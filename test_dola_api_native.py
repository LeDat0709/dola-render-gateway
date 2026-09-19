#!/usr/bin/env python3
"""
test_dola_api_native.py — Test trực tiếp API gốc Dola để tìm hiểu cơ chế.

KHÔNG dùng Chrome automation, KHÔNG qua gateway.
Chỉ gọi thẳng https://www.dola.com/chat/completion + /im/chain/single.

Usage:
    # DRY-RUN (chỉ build body, không gửi):
    python test_dola_api_native.py --nick my_account --dry-run

    # Gửi thật (tốn credit):
    python test_dola_api_native.py --nick my_account --prompt "con mèo lướt sóng"

    # Test với từng model:
    python test_dola_api_native.py --nick my_account --model seedance-2.0 --duration 10
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Thêm thư mục gốc vào path
sys.path.insert(0, str(Path(__file__).parent))

import config
import dola_client
import dola_error_codes


def load_cookie(nick: str) -> str:
    """Load cookie từ file accounts/<nick>/cookies.json."""
    cookie_file = config.ACCOUNTS_DIR / nick / "cookies.json"
    if not cookie_file.exists():
        print(f"[-] Không tìm thấy: {cookie_file}")
        sys.exit(1)
    data = json.loads(cookie_file.read_text())
    if isinstance(data, dict) and "cookies" in data:
        cookies = data["cookies"]
    else:
        cookies = data
    return "; ".join(
        f"{c['name']}={c['value']}"
        for c in cookies
        if c.get("name") in ("sessionid", "msToken", "s_v_web_id")
    )


def parse_args():
    ap = argparse.ArgumentParser(description="Test API gốc Dola video generation")
    ap.add_argument("--nick", required=True, help="Tên nick (thư mục trong accounts/)")
    ap.add_argument("--dry-run", action="store_true", help="Chỉ build body, không gửi")
    ap.add_argument("--prompt", default="một con mèo nhỏ lướt sóng lúc hoàng hôn, ánh nắng vàng, máy quay cầm tay",
                   help="Prompt cho video")
    ap.add_argument("--ratio", default="9:16", choices=["9:16", "16:9", "1:1", "3:4", "4:3"],
                   help="Tỷ lệ video")
    ap.add_argument("--duration", type=int, default=10, choices=[10, 15, 30],
                   help="Thời lượng (giây)")
    ap.add_argument("--model", default="seedance-2.5",
                   choices=["seedance-2.0", "seedance-2.5"],
                   help="Model Seedance")
    ap.add_argument("--timeout", type=int, default=900,
                   help="Timeout poll (giây)")
    return ap.parse_args()


async def run_dry_run(client, prompt, ratio, duration, model):
    """Build body, in ra mà không gửi."""
    print("=" * 60)
    print("DRY-RUN MODE — CHỈ BUILD BODY, KHÔNG GỬI")
    print("=" * 60)

    # 1. Body chat completion
    print("\n[1] REQUEST BODY CHO /chat/completion:")
    body = client._build_video_body(prompt, ratio, duration)
    body["chat_ability"]["ability_param"] = json.dumps({
        "ratio": ratio,
        "model": "seedance_v2.5" if "2.5" in model else "seedance_v2.0",
        "duration": duration,
    })
    print(json.dumps(body, indent=2, ensure_ascii=False))

    # 2. Body polling
    print("\n[2] REQUEST BODY CHO /im/chain/single (POLL):")
    print(json.dumps({
        "cmd": 3100,
        "conversation_id": "<sau khi submit sẽ có>",
        "anchor_index": 0,
        "direction": 1,
        "limit": 20,
    }, indent=2))

    # 3. Query params
    print("\n[3] QUERY PARAMS CHUNG:")
    params = dola_client.build_query_params(client.cookie)
    print(json.dumps(params, indent=2))

    # 4. Headers
    print("\n[4] HEADERS GỬI ĐI:")
    print(json.dumps(dola_client.FAKE_HEADERS, indent=2))
    print("    Cookie: <đã che>")
    print("    Content-Type: application/json")
    print("    agw-js-conv: str, str")
    print("    Referer: https://www.dola.com/chat/")


async def poll_with_log(client, conv_id, timeout, poll_interval=10):
    """Poll với log chi tiết từng lần."""
    import aiohttp
    start = time.time()
    attempt = 0
    while time.time() - start < timeout:
        attempt += 1
        elapsed = time.time() - start
        print(f"  [Poll #{attempt} @ {elapsed:.0f}s] ", end="", flush=True)
        try:
            body = {
                "cmd": 3100,
                "conversation_id": conv_id,
                "anchor_index": 0,
                "direction": 1,
                "limit": 20,
            }
            async with aiohttp.ClientSession(trust_env=True) as session:
                data = await client._request_im_chain(session, body, conv_id)

            dl_body = data.get("downlink_body") or {}
            messages = (dl_body.get("pull_singe_chain_downlink_body") or {}).get("messages") or []
            print(f"code={data.get('code', '?')} | messages={len(messages)}", end="")

            # Check lỗi Dola
            error_code = data.get("data", {}).get("code") or data.get("code")
            if error_code and error_code != 0:
                info = dola_error_codes.tra_cứu(str(error_code))
                print(f" | ERROR={error_code}")
                if info:
                    print(f"      → {info.kind}: {info.action}")
                else:
                    print(f"      → Mã lạ: {error_code}")
                return None

            if not messages:
                print(" | (chưa có message, đợi)")
                await asyncio.sleep(poll_interval)
                continue

            # Tìm video
            for msg in messages:
                content = msg.get("content")
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except json.JSONDecodeError:
                        continue
                if not isinstance(content, list):
                    continue
                for block in content:
                    block_text = (block.get("content") or {}).get("text_block", {}).get("text", "")
                    if block_text:
                        print(f"\n      [TEXT] {block_text[:80]}")
                        if dola_client.CREDIT_FAIL_PATTERN.search(block_text):
                            print("      ⚠️ HẾT CREDIT!")
                            raise dola_client.CreditError(block_text[:60])

                    if block.get("block_type") == 2074:
                        creations = (block.get("content") or {}).get("creation_block", {}).get("creations") or []
                        for cre in creations:
                            if cre.get("type") == 2:  # video
                                url = (cre.get("video") or {}).get("download_url", "")
                                if url:
                                    print(" | ✓ VIDEO READY")
                                    return url
            print(" | (đang render, đợi)")
        except dola_client.CreditError:
            raise
        except Exception as e:
            print(f" | LỖI: {e}")
        await asyncio.sleep(poll_interval)
    return None


async def run_live(client, prompt, ratio, duration, model, timeout):
    """Gửi thật, poll kết quả."""
    print("=" * 60)
    print("LIVE MODE — GỬI THẬT TỚI DOLA (tốn credit)")
    print("=" * 60)
    print(f"  Prompt: {prompt}")
    print(f"  Ratio: {ratio}")
    print(f"  Duration: {duration}s")
    print(f"  Model: {model}")
    print(f"  Cookie (3 trường đầu): {client.cookie[:80]}...")

    start = time.time()

    # Bước 1: Submit
    print("\n[BƯỚC 1] Gọi /chat/completion...")
    try:
        import aiohttp
        body = client._build_video_body(prompt, ratio, duration)
        body["chat_ability"]["ability_param"] = json.dumps({
            "ratio": ratio,
            "model": "seedance_v2.5" if "2.5" in model else "seedance_v2.0",
            "duration": duration,
        })
        events = []
        async with aiohttp.ClientSession(trust_env=True) as session:
            resp = await client._request_chat_completion(session, body, timeout=120)
            async with resp:
                events = await client._read_sse_stream(resp)
        print(f"  → Nhận {len(events)} SSE events")

        conv_id = ""
        for event_name, data in events:
            if event_name == "SSE_ACK":
                conv_id = (data.get("ack_client_meta") or {}).get("conversation_id", "")
                print(f"  → SSE_ACK conversation_id: {conv_id}")
                break
            elif event_name == "ERROR":
                print(f"  → ERROR event: {json.dumps(data, ensure_ascii=False)[:200]}")
                return

        if not conv_id:
            print("[-] Không có conversation_id, không thể poll")
            for i, (e, d) in enumerate(events[:5]):
                print(f"    [{i}] event={e} data_preview={str(d)[:100]}")
            return
    except Exception as e:
        print(f"[-] Lỗi submit: {e}")
        import traceback
        traceback.print_exc()
        return

    # Bước 2: Poll
    print(f"\n[BƯỚC 2] Poll /im/chain/single (timeout={timeout}s)...")
    try:
        result = await poll_with_log(client, conv_id, timeout)
        if result:
            elapsed = time.time() - start
            print(f"\n[+] HOÀN TẤT sau {elapsed:.1f}s")
            print(f"    Video URL: {result}")
        else:
            print(f"\n[-] Timeout sau {timeout}s")
    except dola_client.CreditError as e:
        print(f"\n[-] Hết credit: {e}")
    except Exception as e:
        print(f"\n[-] Lỗi poll: {e}")


async def main():
    args = parse_args()

    # Load cookie
    print(f"[*] Loading cookie cho nick '{args.nick}'...")
    cookie = load_cookie(args.nick)
    print(f"    Cookie length: {len(cookie)} chars")

    # Tạo client
    client = dola_client.create_client(cookie)
    print(f"    Client type: {type(client).__name__}")
    print(f"    aid={getattr(client, 'api_base', 'N/A')}, is_valid={client.is_valid}")

    # Kiểm tra loại client
    if not hasattr(client, "_build_video_body"):
        print(f"[-] Client {type(client).__name__} không hỗ trợ video generation")
        print(f"    Cookie này thuộc Doubao CN, video generation chỉ có ở Dola global")
        print(f"    Cần cookie chứa msToken + s_v_web_id để dùng DolaClient")
        sys.exit(1)

    if args.dry_run:
        await run_dry_run(client, args.prompt, args.ratio, args.duration, args.model)
    else:
        await run_live(client, args.prompt, args.ratio, args.duration, args.model, args.timeout)


if __name__ == "__main__":
    asyncio.run(main())
