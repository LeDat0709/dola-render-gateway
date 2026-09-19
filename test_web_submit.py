"""Thăm dò: gửi lệnh Dola tới CỔNG WEB (www.dola.com/chat/completion) với a_bogus tính CỤC BỘ (a_bogus_web),
không mở Chrome. Đây là đường đúng để bỏ Chrome khỏi bước gửi (khác cổng app Android đã chết vì CAPTCHA).

CHẠY:
  .venv/bin/python test_web_submit.py                                  # dry-run: dựng URL + a_bogus, KHÔNG gửi
  .venv/bin/python test_web_submit.py --nick fb... --send              # gửi chat chữ (miễn phí)
  .venv/bin/python test_web_submit.py --cookie-file c.txt --send       # dùng cookie rời
  .venv/bin/python test_web_submit.py --nick fb... --send --video      # gửi video 10s (TỐN credit)

Đọc kết quả:
  - SSE_ACK / conversation_id  → a_bogus CỤC BỘ ĐƯỢC CHẤP NHẬN ⇒ bỏ được Chrome khỏi submit.
  - verify/slide, param error, a_bogus invalid → thuật toán đã lệch bản (ByteDance xoay mỗi quý) ⇒ cần cập nhật.
  - 710022002 → rate-limit/nhu cầu cao, thử lại sau.
"""
import argparse
import json
import sys
import time
import uuid

import config
from a_bogus_web import long_a_bogus, fake_mstoken

WEB_URL = "https://www.dola.com/chat/completion"
DOLA_AID = "495671"
DOLA_BOT_ID = "7339470689562525703"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
_COOKIE_NAMES = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard", "odin_tt", "ttwid",
                 "uid_tt", "uid_tt_ss", "store-idc", "store-country-code", "s_v_web_id", "msToken"}


def load_cookies(nick: str) -> dict:
    f = config.ACCOUNTS_DIR / nick / "cookies.json"
    if not f.exists():
        raise SystemExit(f"Không thấy {f}")
    d = json.loads(f.read_text())
    items = d if isinstance(d, list) else d.get("cookies", d)
    if isinstance(items, dict):
        items = [{"name": k, "value": v} for k, v in items.items()]
    return {c["name"]: c["value"] for c in items if c.get("name")}


def load_cookies_file(path: str) -> dict:
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


def cookie_header(ck: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in ck.items())


def web_params(ck: dict) -> dict:
    return {
        "aid": DOLA_AID, "real_aid": DOLA_AID, "device_platform": "web", "language": "ja",
        "region": "JP", "sys_region": "JP", "samantha_web": "1", "web_platform": "browser",
        "use-olympus-account": "1", "version_code": "20800", "pkg_type": "release_version",
        "pc_version": "3.36.11", "doubao_device_platform": "web", "doubao_pc_version": "3.36.11",
        "msToken": ck.get("msToken") or fake_mstoken(),
        "fp": ck.get("s_v_web_id", ""),
        "web_tab_id": str(uuid.uuid4()),
    }


def _msg_block(text: str) -> dict:
    return {"local_message_id": str(uuid.uuid4()), "message_status": 0, "content_block": [{
        "block_type": 10000, "block_id": str(uuid.uuid4()), "parent_id": "", "meta_info": [],
        "append_fields": [], "content": {"text_block": {"text": text, "icon_url": "", "icon_url_dark": "", "summary": ""},
                                         "pc_event_block": ""}}]}


def body(prompt: str, ratio: str, duration: int, video: bool, fp: str) -> dict:
    now_ms = int(time.time() * 1000)
    b = {
        "client_meta": {"local_conversation_id": f"local_{now_ms}", "conversation_id": "",
                        "bot_id": DOLA_BOT_ID, "last_section_id": "", "last_message_index": None},
        "messages": [_msg_block(f"生成影片：{prompt}，{ratio}" if video else prompt)],
        "option": {"need_create_conversation": True, "create_time_ms": now_ms, "unique_key": str(uuid.uuid4()),
                   "conversation_init_option": {"need_ack_conversation": True},
                   "sse_recv_event_options": {"support_chunk_delta": True}},
        "ext": {"fp": fp, "use_deep_think": "0", "conversation_init_option": '{"need_ack_conversation":true}'},
    }
    if video:
        b["chat_ability"] = {"ability_type": 17, "ability_param": json.dumps(
            {"ratio": ratio, "model": config.MODEL_KEY_SEEDANCE20, "duration": duration})}
    return b


def build_signed(ck: dict, req_body: dict) -> tuple[str, str]:
    from urllib.parse import urlencode
    params = web_params(ck)
    query = urlencode(params)
    body_json = json.dumps(req_body, ensure_ascii=False, separators=(",", ":"))
    a_bogus = long_a_bogus(query, body_json, {"aid": int(DOLA_AID), "userAgent": UA})
    return f"{WEB_URL}?{query}&a_bogus={a_bogus}", body_json


def send(ck: dict, req_body: dict, proxy: str | None) -> None:
    # curl_cffi impersonate=chrome: giả TLS/JA3 + thứ tự header của Chrome thật (aiohttp lộ ngay là bot ở tầng TLS)
    from curl_cffi import requests as creq
    url, body_json = build_signed(ck, req_body)
    headers = {"Content-Type": "application/json", "agw-js-conv": "str, str", "Accept": "*/*",
               "User-Agent": UA, "Cookie": cookie_header(ck), "Referer": "https://www.dola.com/chat/",
               "Origin": "https://www.dola.com"}
    proxies = {"http": proxy, "https": proxy} if proxy else None
    print(f"POST {WEB_URL} · a_bogus cục bộ (…{url.split('a_bogus=')[1][:24]}…) · impersonate=chrome · proxy={'có' if proxy else 'không (đi thẳng)'}")
    r = creq.post(url, data=body_json.encode(), headers=headers, proxies=proxies, impersonate="chrome", timeout=60)
    text = r.text
    low = text.lower()
    print(f"  HTTP {r.status_code}")
    if r.status_code == 200 and ("conversation_id" in text or "sse_ack" in low):
        print("  → a_bogus CỤC BỘ ĐƯỢC CHẤP NHẬN. Trích 400 ký tự:")
    elif "verify" in low or "slide" in low or "captcha" in low or "a_bogus" in low:
        print("  → BỊ CHẶN/nghi (verify/captcha/a_bogus) — có thể thuật toán lệch bản. Trích 400 ký tự:")
    else:
        print("  → chưa rõ / rate-limit. Trích 400 ký tự:")
    print("  " + text[:400].replace("\n", "\n  "))


def dry_run() -> None:
    ck = {"sessionid": "SID", "s_v_web_id": "verify_demo", "store-country-code": "vn"}
    url, body_json = build_signed(ck, body("mèo cam ngủ", "9:16", 10, False, "verify_demo"))
    print("dry-run OK — URL đã ký (che a_bogus một phần):")
    print("  " + url[:130] + "…&a_bogus=" + url.split("a_bogus=")[1][:20] + "…")
    print("  body:", body_json[:120], "…")
    print("  → Chạy --send --nick <nick> để xem Dola nhận a_bogus cục bộ không.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nick")
    ap.add_argument("--cookie-file")
    ap.add_argument("--proxy")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--video", action="store_true")
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
        sys.exit("--send cần --nick hoặc --cookie-file")
    req = body(a.prompt, a.ratio, 10, a.video, ck.get("s_v_web_id", ""))
    print(f"Gửi {'VIDEO 10s' if a.video else 'chat chữ (miễn phí)'} qua {label} lên CỔNG WEB (a_bogus cục bộ)…")
    send(ck, req, proxy)


if __name__ == "__main__":
    main()
