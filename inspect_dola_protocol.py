#!/usr/bin/env python3
"""
inspect_dola_protocol.py — Capture raw request/response để hiểu cơ chế Dola API.

Không tốn credit vì chỉ dùng chat text (free).
Mục đích: xem SSE events, header thật, cơ chế authentication.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import aiohttp
import config
import dola_client


def load_any_cookie():
    """Load cookie đầu tiên có sẵn."""
    for nick_dir in sorted(config.ACCOUNTS_DIR.iterdir()):
        cookie_file = nick_dir / "cookies.json"
        if cookie_file.exists():
            data = json.loads(cookie_file.read_text())
            cookies = data if isinstance(data, list) else data.get("cookies", [])
            if cookies:
                cookie_str = "; ".join(
                    f"{c['name']}={c['value']}" for c in cookies
                )
                return nick_dir.name, cookie_str
    raise SystemExit("Không tìm thấy account nào có cookie")


async def inspect_doubao_cn(nick, cookie):
    """Inspect Doubao CN endpoint chi tiết."""
    print("=" * 70)
    print(f"[{nick}] DOUBAO CN API INSPECTION")
    print("=" * 70)

    client = dola_client.create_client(cookie)
    print(f"\n[INFO]")
    print(f"  Client type: {type(client).__name__}")
    print(f"  Domain: {dola_client.CN_DOMAIN}")
    print(f"  Assistant ID: {dola_client.CN_DEFAULT_ASSISTANT_ID}")
    print(f"  PC version: {dola_client.CN_PC_VERSION}")

    import uuid as uuid_lib
    body = {
        "messages": [{
            "content": json.dumps({"text": "Chào bạn, trả lời ngắn: 1+1=?"}),
            "content_type": 2001,
            "attachments": [],
            "references": [],
        }],
        "completion_option": {
            "is_regen": False,
            "with_suggest": True,
            "need_create_conversation": True,
            "launch_stage": 1,
            "is_replace": False,
            "is_delete": False,
            "message_from": 0,
            "action_bar_skill_id": 0,
            "use_deep_think": False,
            "use_auto_cot": False,
            "resend_for_regen": False,
            "enable_commerce_credit": False,
            "event_id": "0",
        },
        "evaluate_option": {"web_ab_params": ""},
        "section_id": "26" + dola_client._random_numeric(16),
        "conversation_id": "0",
        "local_conversation_id": "local_16" + dola_client._random_numeric(14),
        "local_message_id": str(uuid_lib.uuid4()),
    }

    headers = {
        **dola_client.CN_FAKE_HEADERS,
        "Cookie": client.cookie,
        "X-Flow-Trace": f"04-test-{uuid_lib.uuid4().hex[:16]}-01",
        "Referer": f"https://{dola_client.CN_DOMAIN}/chat/",
        "agw-js-conv": "str, str",
    }
    params = client._build_params({
        "msToken": dola_client._fake_ms_token(),
        "a_bogus": dola_client._fake_a_bogus()
    })

    print(f"\n[REQUEST]")
    print(f"  URL: https://{dola_client.CN_DOMAIN}/samantha/chat/completion")
    print(f"  Method: POST")
    print(f"\n  [Headers] (che cookie)")
    for k, v in headers.items():
        if k == "Cookie":
            print(f"    {k}: sessionid=<redacted>; ...")
        else:
            v_str = str(v)[:80] if len(str(v)) > 80 else v
            print(f"    {k}: {v_str}")
    print(f"\n  [Query Params]")
    for k, v in params.items():
        if k in ("msToken", "a_bogus"):
            print(f"    {k}: {str(v)[:30]}...")
        else:
            print(f"    {k}: {v}")
    print(f"\n  [Body]")
    print(json.dumps(body, indent=2, ensure_ascii=False)[:600])

    print(f"\n[RESPONSE]")
    url = f"https://{dola_client.CN_DOMAIN}/samantha/chat/completion"
    # Request mang cookie THẬT của nick → đi đúng proxy của nick như submit_http; proxy khai mà hỏng thì ném lỗi.
    from browser import account_proxy_url
    proxy = account_proxy_url(nick) or None
    print(f"  Proxy của nick: {'có' if proxy else 'không khai (đi thẳng)'}")
    async with aiohttp.ClientSession(trust_env=True) as session:
        try:
            async with session.post(url, params=params, json=body, headers=headers, proxy=proxy,
                                    timeout=aiohttp.ClientTimeout(total=60)) as resp:
                print(f"  HTTP Status: {resp.status}")
                print(f"  Content-Type: {resp.headers.get('Content-Type')}")
                print(f"\n  [Response Headers]")
                for k, v in resp.headers.items():
                    v_str = str(v)[:80] if len(str(v)) > 80 else v
                    print(f"    {k}: {v_str}")

                raw = b""
                async for chunk in resp.content.iter_chunked(4096):
                    raw += chunk
                    if len(raw) > 50000:
                        break
                print(f"\n  [Raw SSE Body] (first 3000 chars):")
                text = raw.decode("utf-8", errors="replace")
                print(text[:3000])
                print(f"\n  ... (total {len(raw)} bytes)")

                print(f"\n  [Parsed SSE Events]:")
                events_raw = text.split("\n\n")
                for i, ev in enumerate(events_raw[:10]):
                    if not ev.strip():
                        continue
                    lines = ev.split("\n")
                    event_name = ""
                    data_lines = []
                    for line in lines:
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].strip())
                    data_str = "\n".join(data_lines)
                    try:
                        data_obj = json.loads(data_str) if data_str else {}
                    except json.JSONDecodeError:
                        data_obj = {"_raw": data_str[:200]}
                    print(f"    Event #{i}: name='{event_name}'")
                    print(f"      data preview: {json.dumps(data_obj, ensure_ascii=False)[:200]}")
        except Exception as e:
            print(f"  LỖI: {e}")
            import traceback
            traceback.print_exc()


async def inspect_dola_global_format():
    """In ra body format mà Dola global sẽ gửi."""
    print("\n" + "=" * 70)
    print("DOLA GLOBAL (in format tham khảo - không gửi)")
    print("=" * 70)

    print(f"\n[CONSTANTS]")
    print(f"  DOLA_AID: {dola_client.DOLA_AID}")
    print(f"  DOLA_BOT_ID: {dola_client.DOLA_BOT_ID}")
    print(f"  VERSION_CODE: {dola_client.VERSION_CODE}")
    print(f"  API base: https://www.dola.com")
    print(f"\n[KEY HEADERS]")
    print(f"  agw-js-conv: str, str  (CHAT - SSE)")
    print(f"  agw-js-conv: str       (POLL - non-stream)")
    print(f"\n[ABILITY_TYPES]")
    print(f"  VIDEO: 17")
    print(f"  IMAGE: 16")
    print(f"  CHAT:  0")
    print(f"\n[BLOCK_TYPES]")
    print(f"  TEXT PROMPT: 10000")
    print(f"  IMAGE ATTACHMENT: 10052")
    print(f"  RESULT (image/video): 2074")
    print(f"\n[RESULT SUB-TYPES]")
    print(f"  IMAGE (type=1)")
    print(f"  VIDEO (type=2)")


async def main():
    nick, cookie = load_any_cookie()
    await inspect_doubao_cn(nick, cookie)
    await inspect_dola_global_format()


if __name__ == "__main__":
    asyncio.run(main())
