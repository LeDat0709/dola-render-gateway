"""Thăm dò: gửi lệnh Dola qua API app Android (ký X-Gorgon cục bộ) thay vì mở Chrome.

Nguồn thuật toán ký: Linkmail16/DolaAI-API (dolaSign.py) — cổng app Android
`api16-normal-i18n-myb.dola.com`, thay msToken+a_bogus (do bdms trong Chrome tính) bằng
X-Gorgon/X-Khronos/X-Ladon tính bằng ~90 dòng Python.

CHẠY:
  .venv/bin/python test_android_submit.py                      # dry-run: chỉ dựng + in chữ ký, KHÔNG gửi
  .venv/bin/python test_android_submit.py --nick fb...  --send # gửi THẬT (đọc cảnh báo bên dưới)
  .venv/bin/python test_android_submit.py --nick fb... --send --video  # gửi lệnh tạo video 10s (TỐN credit)

CẢNH BÁO trước khi --send (đọc REPORT ở cuối file):
  1. Cổng Android cần X-Tt-Token + device_id/install_id THẬT của app; nick mình đăng nhập qua WEB
     (cookie web) nên KHÔNG có mấy thứ đó — script mượn device_id mẫu của repo. Fingerprint lệch có
     thể bị Dola risk-control cho nick nghỉ (captcha) — chỉ --send trên nick anh chấp nhận rủi ro.
  2. X-Ladon trong dolaSign là CHUỖI CỐ ĐỊNH của máy tác giả. Nếu rải cho mọi nick → mọi nick chung
     một "thiết bị" = đúng kiểu bị khoá cả loạt mà proxy riêng đang tránh. Đây chỉ là probe 1 nick.
"""
import argparse
import asyncio
import hashlib
import json
import struct
import sys
import time
import uuid

import config

# ---------- Ký X-Gorgon/X-Khronos/X-Ladon (port từ Linkmail16/DolaAI-API dolaSign.py) ----------
_SESSION_FIXED = "7OdAQJLu50UcXnEAwvWduFxCCYujlcj1U5wViru05cv8wZXd"  # X-Ladon cố định của repo (device tác giả)
_FLAGS = bytes.fromhex("00050904")


def _rc4_key8(key_len: int, ptr: int) -> bytes:
    return bytes([0x4a, key_len & 0xff, 0x16, (ptr >> 8) & 0xff, 0x47, 0x6c, (key_len >> 8) & 0xff, ptr & 0xff])


def _ksa(key: bytes) -> list:
    S = list(range(256)); j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i], S[j] = S[j], S[i]
    return S


def _prga(S: list, data: bytes) -> bytes:
    S = list(S); out = bytearray(); i = j = 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        out.append(byte ^ S[(S[i] + S[j]) % 256])
    return bytes(out)


def _revbits(b: int) -> int:
    return int(f"{b:08b}"[::-1], 2)


def _transform(buf: bytes) -> bytes:
    n = len(buf); out = bytearray(n)
    for i in range(n):
        ns = ((buf[i] & 0xf) << 4) | ((buf[i] >> 4) & 0xf)
        nxt = buf[i + 1] if i + 1 < n else (out[0] if n > 1 else ns)
        out[i] = (n ^ (~_revbits((ns ^ nxt) & 0xff) & 0xff)) & 0xff
    return bytes(out)


def sign(url: str, timestamp: int | None = None, key_len: int = 0, ptr: int = 0) -> dict:
    ts = int(time.time()) if timestamp is None else timestamp
    signing_string = url.split("?", 1)[1] if "?" in url else ""
    h = hashlib.md5(signing_string.encode()).digest()[:4]
    plain = h + b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00" + _FLAGS + struct.pack(">I", ts & 0xffffffff)
    assert len(plain) == 20
    enc = _prga(_ksa(_rc4_key8(key_len, ptr)), plain)
    header = bytes([0x84, 0x04, ptr & 0xff, (ptr >> 8) & 0xff, key_len & 0xff, (key_len >> 8) & 0xff])
    return {"X-Gorgon": (header + _transform(enc)).hex(), "X-Khronos": str(ts), "X-Ladon": _SESSION_FIXED}


# ---------- Cookie của nick (đăng nhập web) ----------
def load_cookies(nick: str) -> dict:
    f = config.ACCOUNTS_DIR / nick / "cookies.json"
    if not f.exists():
        raise SystemExit(f"Không thấy {f}")
    d = json.loads(f.read_text())
    items = d if isinstance(d, list) else d.get("cookies", d)
    if isinstance(items, dict):
        items = [{"name": k, "value": v} for k, v in items.items()]
    return {c["name"]: c["value"] for c in items if c.get("name")}


_COOKIE_NAMES = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard", "odin_tt", "ttwid",
                 "uid_tt", "uid_tt_ss", "store-idc", "store-country-code", "s_v_web_id", "msToken"}


def load_cookies_file(path: str) -> dict:
    """Đọc file cookie Netscape (kể cả dòng #HttpOnly_ và domain bị bọc markdown): lấy 2 token cuối mỗi dòng."""
    ck = {}
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#") or not line.strip():
            continue
        toks = line.split()
        if len(toks) >= 2 and toks[-2] in _COOKIE_NAMES:
            ck[toks[-2]] = toks[-1]
    if "sessionid" not in ck:
        raise SystemExit(f"Không thấy sessionid trong {path}")
    return ck


ANDROID_HOST = "https://api16-normal-i18n-myb.dola.com"
BOT_ID = "7241547611541340167"
# device_id/install_id/cdid: MẪU của repo — KHÔNG phải của nick mình (nick đăng nhập web, không có app id)
SAMPLE = {"device_id": "7681452747673093652", "install_id": "7681561912517068597",
          "cdid": "ee3c4d85-c8f0-4c57-8027-1ec83293a5d5"}


def base_params(ck: dict) -> str:
    return (
        f"flow_im_arch=v2&is_retry=0&device_platform=android&os=android&ssmix=a"
        f"&cdid={SAMPLE['cdid']}&channel=googleplay&aid=489823&app_name=nova_ai"
        f"&version_code=14080001&version_name=14.8.0&manifest_version_code=14080005"
        f"&update_version_code=14080040&resolution=1080*2186&dpi=420&device_type=SM-A525M"
        f"&device_brand=samsung&language=en&os_api=34&os_version=14&ac=wifi"
        f"&app_language=en&carrier_region={ck.get('store-country-code','us')}&flow_app_variant=cici"
        f"&sys_region=US&tz_name=Asia%2FHo_Chi_Minh&system_language_detail=en-US"
        f"&user_is_login=1&is_new_user=0&region=US&lang=en&pkg_type=release_version"
        f"&iid={SAMPLE['install_id']}&device_id={SAMPLE['device_id']}"
        f"&doubao_update_version_code=14080040&doubao_os_version=14&doubao_device_type=SM-A525M"
        f"&doubao_device_brand=samsung&doubao_device_platform=android&flow_sdk_version=14080040"
    )


def cookie_header(ck: dict) -> str:
    sid = ck.get("sessionid", "")
    return (f"store-idc={ck.get('store-idc','mya')}; store-country-code={ck.get('store-country-code','us')}; "
            f"store-country-code-src=uid; install_id={SAMPLE['install_id']}; "
            f"odin_tt={ck.get('odin_tt','')}; sid_guard={ck.get('sid_guard','')}; "
            f"sessionid={sid}; sessionid_ss={ck.get('sessionid_ss', sid)}; sid_tt={ck.get('sid_tt', sid)}")


def android_headers(ck: dict) -> dict:
    return {
        "Host": "api16-normal-i18n-myb.dola.com", "Connection": "keep-alive",
        "Cookie": cookie_header(ck), "X-OMNI-REQUEST": "1", "sdk-version": "2",
        "Content-Type": "application/json; encoding=utf-8",
        "x-tt-store-region": ck.get("store-country-code", "us"), "x-tt-store-region-src": "uid",
        "X-SS-DP": "489823",
        "User-Agent": ("com.larus.wolf/14080005 (Linux; U; Android 14; en_US; SM-A525M; "
                       "Build/UP1A.231005.007; Cronet/TTNetVersion:f5f9daf9 2026-08-06)"),
    }


def video_body(prompt: str, ratio: str, duration: int) -> dict:
    now_ms = int(time.time() * 1000)
    return {
        "client_meta": {"local_conversation_id": f"local_{now_ms}", "conversation_id": "",
                        "bot_id": BOT_ID, "last_section_id": "", "last_message_index": None},
        "messages": [{"local_message_id": str(uuid.uuid4()), "message_status": 0, "content_block": [{
            "block_type": 10000, "block_id": str(uuid.uuid4()), "parent_id": "", "meta_info": [],
            "append_fields": [], "content": {"text_block": {"text": f"生成影片：{prompt}，{ratio}"}}}]}],
        "chat_ability": {"ability_type": 17, "ability_param": json.dumps(
            {"ratio": ratio, "model": config.MODEL_KEY_SEEDANCE20, "duration": duration})},
        "option": {"need_create_conversation": True, "create_time_ms": now_ms, "unique_key": str(uuid.uuid4())},
        "ext": {"use_deep_think": "0"},
    }


def text_body(prompt: str) -> dict:
    now_ms = int(time.time() * 1000)
    return {
        "client_meta": {"local_conversation_id": f"local_{now_ms}", "conversation_id": "",
                        "bot_id": BOT_ID, "last_section_id": "", "last_message_index": None},
        "messages": [{"local_message_id": str(uuid.uuid4()), "message_status": 0, "content_block": [{
            "block_type": 10000, "block_id": str(uuid.uuid4()), "parent_id": "", "meta_info": [],
            "append_fields": [], "content": {"text_block": {"text": prompt}}}]}],
        "option": {"need_create_conversation": True, "create_time_ms": now_ms, "unique_key": str(uuid.uuid4())},
        "ext": {"use_deep_think": "0"},
    }


async def send(ck: dict, body: dict, proxy: str | None) -> None:
    import aiohttp
    now_ms = int(time.time() * 1000)
    url = f"{ANDROID_HOST}/chat/completion?{base_params(ck)}&_rticket={now_ms}"
    sig = sign(url, timestamp=now_ms // 1000)
    headers = {**android_headers(ck), "X-SS-REQ-TICKET": str(now_ms), **sig}
    print(f"POST {url[:90]}...\n  X-Gorgon={sig['X-Gorgon'][:32]}…  proxy={'có' if proxy else 'không (đi thẳng)'}")
    data = json.dumps(body, ensure_ascii=False).encode()
    async with aiohttp.ClientSession() as s:
        async with s.post(url, data=data, headers=headers, proxy=proxy,
                          timeout=aiohttp.ClientTimeout(total=60)) as r:
            print(f"  HTTP {r.status}")
            text = await r.text()
            low = text.lower()
            if r.status == 200 and ("conversation_id" in text or "sse_ack" in low or "data:" in text):
                print("  → CHỮ KÝ ĐƯỢC CHẤP NHẬN (có phản hồi hợp lệ). Trích 400 ký tự đầu:")
            else:
                print("  → BỊ TỪ CHỐI. Trích 400 ký tự đầu (xem status_code/verify để biết thiếu gì):")
            print("  " + text[:400].replace("\n", "\n  "))


def dry_run() -> None:
    ck = {"sessionid": "SID_demo", "odin_tt": "ODIN", "sid_guard": "GUARD",
          "store-idc": "mya", "store-country-code": "vn"}
    now_ms = int(time.time() * 1000)
    url = f"{ANDROID_HOST}/chat/completion?{base_params(ck)}&_rticket={now_ms}"
    s1 = sign(url, timestamp=1_700_000_000)
    s2 = sign(url, timestamp=1_700_000_000)
    assert s1 == s2, "sign không tất định với cùng input"
    assert len(bytes.fromhex(s1["X-Gorgon"])) == 26, "X-Gorgon phải 26 byte (6 header + 20 thân)"
    assert s1["X-Ladon"] == _SESSION_FIXED
    print("dry-run OK: chữ ký tất định, X-Gorgon 26 byte.")
    print("  X-Gorgon (ts cố định):", s1["X-Gorgon"])
    print("  X-Khronos:", s1["X-Khronos"], "· X-Ladon:", s1["X-Ladon"][:16] + "…")
    print("  Cookie gửi đi (mẫu):", cookie_header(ck)[:90] + "…")
    print("  → Thiếu X-Tt-Token + device_id/install_id THẬT của nick. Chạy --send để xem Dola nhận không.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nick")
    ap.add_argument("--cookie-file", help="file cookie Netscape (thay cho --nick); gửi đi thẳng nếu không có --proxy")
    ap.add_argument("--proxy", help="proxy đi ra (http://... hoặc host:port:user:pass); mặc định proxy nick, hoặc đi thẳng với --cookie-file")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--video", action="store_true", help="gửi lệnh tạo video 10s (TỐN credit); mặc định gửi chat chữ (miễn phí)")
    ap.add_argument("--prompt", default="xin chào")
    ap.add_argument("--ratio", default="9:16")
    a = ap.parse_args()
    if not a.send:
        dry_run(); return
    if a.cookie_file:
        ck = load_cookies_file(a.cookie_file); label = a.cookie_file
        proxy = a.proxy or config.PROXY or None
    elif a.nick:
        from browser import account_proxy_url
        ck = load_cookies(a.nick); label = a.nick
        proxy = a.proxy or account_proxy_url(a.nick) or config.PROXY or None
    else:
        sys.exit("--send cần --nick <tên nick> hoặc --cookie-file <đường dẫn>")
    body = video_body(a.prompt, a.ratio, 10) if a.video else text_body(a.prompt)
    print(f"Gửi {'VIDEO 10s' if a.video else 'chat chữ (miễn phí)'} qua {label} lên cổng Android…")
    asyncio.run(send(ck, body, proxy))


if __name__ == "__main__":
    main()
