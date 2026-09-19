#!/usr/bin/env python3
"""
test_bypass_rate_limit.py — Test các chiến thuật bypass rate limit Dola.

⚠️ CHỈ chạy với nick TEST, không spam production.

Các chiến thuật test:
  1. Burst (baseline) — đo ngưỡng 710022002
  2. Đổi device_id/web_id/msToken/a_bogus mỗi request
  3. Spoof X-Forwarded-For
  4. TLS fingerprint khác (curl_cffi impersonate)

Imports: aiohttp, config, dola_client, dola_error_codes (đã có).
Affected API: KHÔNG. Chỉ gọi /samantha/chat/completion (chat free).
Data: đọc accounts/<nick>/cookies.json, không ghi file nào.
"""
import argparse
import asyncio
import json
import sys
import time
import uuid as uuid_lib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import aiohttp
import config
import dola_client
import dola_error_codes


def load_cookie(nick: str) -> str:
    cookie_file = config.ACCOUNTS_DIR / nick / "cookies.json"
    if not cookie_file.exists():
        sys.exit(f"Không tìm thấy: {cookie_file}")
    data = json.loads(cookie_file.read_text())
    cookies = data if isinstance(data, list) else data.get("cookies", [])
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def build_chat_body(prompt: str) -> dict:
    """Body chat đơn giản (free, không tốn credit)."""
    return {
        "messages": [{
            "content": json.dumps({"text": prompt}),
            "content_type": 2001,
            "attachments": [],
            "references": [],
        }],
        "completion_option": {
            "is_regen": False, "with_suggest": False,
            "need_create_conversation": True, "launch_stage": 1,
            "is_replace": False, "is_delete": False,
            "message_from": 0, "action_bar_skill_id": 0,
            "use_deep_think": False, "use_auto_cot": False,
            "resend_for_regen": False, "enable_commerce_credit": False,
            "event_id": "0",
        },
        "evaluate_option": {"web_ab_params": ""},
        "section_id": "26" + dola_client._random_numeric(16),
        "conversation_id": "0",
        "local_conversation_id": "local_16" + dola_client._random_numeric(14),
        "local_message_id": str(uuid_lib.uuid4()),
    }


async def send_one(session, url, params, body, headers):
    """Gửi 1 request, trả về (status, elapsed, info, preview)."""
    start = time.time()
    try:
        async with session.post(
            url, params=params, json=body, headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            raw = await resp.read()
            elapsed = time.time() - start
            text = raw.decode("utf-8", errors="replace")
            info = dola_error_codes.classify_dola_error(text, resp.status)
            return resp.status, elapsed, info, text[:200]
    except Exception as e:
        return -1, time.time() - start, None, str(e)


async def baseline_burst(nick: str, cookie: str, count: int):
    """Test 1: Gửi dồn dập, đo ngưỡng rate limit."""
    print(f"\n{'='*70}")
    print(f"[TEST 1] BASELINE BURST — gửi {count} request liên tiếp")
    print(f"{'='*70}")

    client = dola_client.create_client(cookie)
    url = f"https://{dola_client.CN_DOMAIN}/samantha/chat/completion"
    body = build_chat_body("hi")
    headers = {
        **dola_client.CN_FAKE_HEADERS,
        "Cookie": client.cookie,
        "X-Flow-Trace": f"04-{uuid_lib.uuid4().hex[:16]}-01",
        "agw-js-conv": "str, str",
    }
    params = client._build_params({
        "msToken": dola_client._fake_ms_token(),
        "a_bogus": dola_client._fake_a_bogus(),
    })

    async with aiohttp.ClientSession(trust_env=True) as session:
        for i in range(count):
            body["local_message_id"] = str(uuid_lib.uuid4())
            status, elapsed, info, _ = await send_one(
                session, url, params, body, headers
            )
            kind = info.kind if info else "?"
            code = info.code if info else ""
            print(f"  [{i+1:2d}] HTTP {status} | {elapsed*1000:.0f}ms | "
                  f"{kind}({code})")
            if info and info.kind == dola_error_codes.DolaErrorKind.RATE_LIMIT:
                print(f"    ⚠️  BỊ RATE LIMIT tại request #{i+1}")
                return i + 1
            await asyncio.sleep(0.1)
    return count


async def bypass_new_params_per_request(nick: str, cookie: str, count: int):
    """Test 2: Đổi tất cả params mỗi request."""
    print(f"\n{'='*70}")
    print(f"[TEST 2] BYPASS VỚI NEW PARAMS MỖI REQUEST")
    print(f"{'='*70}")

    client = dola_client.create_client(cookie)
    url = f"https://{dola_client.CN_DOMAIN}/samantha/chat/completion"
    body = build_chat_body("hi")
    base_headers = {
        **dola_client.CN_FAKE_HEADERS,
        "Cookie": client.cookie,
        "X-Flow-Trace": f"04-{uuid_lib.uuid4().hex[:16]}-01",
        "agw-js-conv": "str, str",
    }

    async with aiohttp.ClientSession(trust_env=True) as session:
        for i in range(count):
            body["local_message_id"] = str(uuid_lib.uuid4())
            new_params = client._build_params({
                "device_id": "7" + dola_client._random_numeric(18),
                "tea_uuid": "7" + dola_client._random_numeric(18),
                "web_id": "7" + dola_client._random_numeric(18),
                "web_tab_id": str(uuid_lib.uuid4()),
                "msToken": dola_client._fake_ms_token(),
                "a_bogus": dola_client._fake_a_bogus(),
            })

            status, elapsed, info, _ = await send_one(
                session, url, new_params, body, base_headers
            )
            kind = info.kind if info else "?"
            code = info.code if info else ""
            print(f"  [{i+1:2d}] HTTP {status} | {elapsed*1000:.0f}ms | "
                  f"{kind}({code}) | dev={new_params['device_id'][-4:]}")
            if info and info.kind == dola_error_codes.DolaErrorKind.RATE_LIMIT:
                print(f"    ⚠️  VẪN BỊ RATE LIMIT dù đổi params")
                return i + 1
            await asyncio.sleep(0.1)
    return count


async def bypass_xff(nick: str, cookie: str, count: int):
    """Test 3: Spoof X-Forwarded-For."""
    print(f"\n{'='*70}")
    print(f"[TEST 3] BYPASS VỚI X-Forwarded-For")
    print(f"{'='*70}")

    client = dola_client.create_client(cookie)
    url = f"https://{dola_client.CN_DOMAIN}/samantha/chat/completion"
    body = build_chat_body("hi")
    base_headers = {
        **dola_client.CN_FAKE_HEADERS,
        "Cookie": client.cookie,
        "X-Flow-Trace": f"04-{uuid_lib.uuid4().hex[:16]}-01",
        "agw-js-conv": "str, str",
    }
    params = client._build_params({
        "msToken": dola_client._fake_ms_token(),
        "a_bogus": dola_client._fake_a_bogus(),
    })

    async with aiohttp.ClientSession(trust_env=True) as session:
        for i in range(count):
            body["local_message_id"] = str(uuid_lib.uuid4())
            fake_ip = f"10.{i // 255}.{i % 255}.{(i*7) % 255}"
            headers = {**base_headers, "X-Forwarded-For": fake_ip}

            status, elapsed, info, _ = await send_one(
                session, url, params, body, headers
            )
            kind = info.kind if info else "?"
            code = info.code if info else ""
            print(f"  [{i+1:2d}] HTTP {status} | XFF={fake_ip:15s} | "
                  f"{kind}({code})")
            if info and info.kind == dola_error_codes.DolaErrorKind.RATE_LIMIT:
                print(f"    ⚠️  VẪN BỊ RATE LIMIT dù spoof XFF")
                return i + 1
            await asyncio.sleep(0.1)
    return count


async def curl_cffi_impersonate(nick: str, cookie: str, count: int):
    """Test 4: TLS fingerprint khác qua curl_cffi."""
    print(f"\n{'='*70}")
    print(f"[TEST 4] TLS FINGERPRINT BYPASS (curl_cffi)")
    print(f"{'='*70}")

    try:
        from curl_cffi import requests as cc_requests
    except ImportError:
        print("  ⚠️  curl_cffi chưa cài, skip test này")
        return -1

    client = dola_client.create_client(cookie)
    url = f"https://{dola_client.CN_DOMAIN}/samantha/chat/completion"
    body = build_chat_body("hi")
    headers = {
        **dola_client.CN_FAKE_HEADERS,
        "Cookie": client.cookie,
        "X-Flow-Trace": f"04-{uuid_lib.uuid4().hex[:16]}-01",
        "agw-js-conv": "str, str",
    }
    params = client._build_params({
        "msToken": dola_client._fake_ms_token(),
        "a_bogus": dola_client._fake_a_bogus(),
    })

    for browser in ["chrome120", "chrome119", "chrome131", "safari17_0", "firefox123"]:
        print(f"  Browser impersonate: {browser}")
        for i in range(min(3, count)):
            body["local_message_id"] = str(uuid_lib.uuid4())
            try:
                start = time.time()
                resp = cc_requests.post(
                    url, params=params, json=body, headers=headers,
                    impersonate=browser, timeout=30,
                )
                elapsed = time.time() - start
                text = resp.text
                info = dola_error_codes.classify_dola_error(text, resp.status_code)
                kind = info.kind if info else "?"
                print(f"    [{i+1}] HTTP {resp.status_code} | {elapsed*1000:.0f}ms | {kind}")
                if info and info.kind == dola_error_codes.DolaErrorKind.RATE_LIMIT:
                    print(f"      ⚠️  Rate limited với {browser}")
                    break
            except Exception as e:
                print(f"    [{i+1}] Lỗi: {e}")
            await asyncio.sleep(0.2)
    return 0


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nick", required=True)
    ap.add_argument("--count", type=int, default=10)
    args = ap.parse_args()

    cookie = load_cookie(args.nick)
    print(f"[*] Cookie loaded (length {len(cookie)})")

    # 1. Baseline
    print(f"\n[PHASE 1] Baseline — đo ngưỡng rate limit")
    limit_at = await baseline_burst(args.nick, cookie, args.count)
    print(f"\n📊 Rate limit bắt đầu ở request #{limit_at}/{args.count}")

    await asyncio.sleep(60)

    # 2. Bypass new params
    print(f"\n[PHASE 2] Bypass với new params mỗi request")
    limit_at2 = await bypass_new_params_per_request(args.nick, cookie, args.count)
    print(f"\n📊 Rate limit bắt đầu ở request #{limit_at2}/{args.count}")

    await asyncio.sleep(60)

    # 3. Bypass X-Forwarded-For
    print(f"\n[PHASE 3] Bypass với X-Forwarded-For")
    limit_at3 = await bypass_xff(args.nick, cookie, args.count)
    print(f"\n📊 Rate limit bắt đầu ở request #{limit_at3}/{args.count}")

    await asyncio.sleep(60)

    # 4. TLS fingerprint
    print(f"\n[PHASE 4] TLS fingerprint khác (curl_cffi)")
    await curl_cffi_impersonate(args.nick, cookie, args.count)


if __name__ == "__main__":
    asyncio.run(main())
