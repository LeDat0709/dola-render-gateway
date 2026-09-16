"""submit_http.py — GỬI lệnh tạo video Dola bằng HTTP THUẦN (curl_cffi), KHÔNG mở Chrome. [Giai đoạn 2]

Port trung thực body /chat/completion từ SUBMIT_JS (video_worker.py) — GỒM payload 30s Khan (2.5) — sang Python,
ký a_bogus qua signer.py (web thuần / chrome dự phòng), gửi bằng curl_cffi (giả TLS fingerprint Chrome).
Cookie lấy từ accounts/<nick>/cookies.json (không cần trình duyệt). Trả conversation_id để bàn giao cho
poll_conversation_http + _download (đã HTTP sẵn).

CHƯA nối pool — bật qua cờ DOLA_SUBMIT_ENGINE=http ở Giai đoạn 2b. Verify thật bằng test_web_submit --send.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Optional

import config
import signer

WEB_URL = "https://www.dola.com/chat/completion"
BOT_ID = "7339470689562525703"                       # khớp SUBMIT_JS client_meta.bot_id
REPLY_FORMAT = "生成された動画：%s"
UA = signer.DEFAULT_UA
_ORIENT = {"9:16": "縦", "3:4": "縦", "16:9": "横", "4:3": "横", "1:1": "正方形"}


def load_account_cookies(account: str) -> dict[str, str]:
    """Cookie nick từ accounts/<nick>/cookies.json (list [{name,value}] hoặc dict) — KHÔNG mở Chrome."""
    f = config.ACCOUNTS_DIR / account / "cookies.json"
    if not f.exists():
        raise FileNotFoundError(f"Không thấy cookies.json của {account} ({f}) — nick chưa lưu cookie no-browser")
    d = json.loads(f.read_text(encoding="utf-8"))
    items = d if isinstance(d, list) else d.get("cookies", d)
    if isinstance(items, dict):
        return {k: v for k, v in items.items()}
    return {c["name"]: c["value"] for c in items if c.get("name")}


def _khan_extra(model: str) -> dict:
    """Payload 30s Khan cho model 2.5 (đo thực 13/09): Dola tính 30s = 2 credit + nhận cho nick free."""
    if "2.5" not in str(model or ""):
        return {}
    return {"allow_free_queue": True, "accept_queue": True, "credits": 1, "cost": 1,
            "audio": False, "generate_audio": False, "with_audio": False, "has_audio": False,
            "bgm": False, "sound": False, "no_watermark": True, "remove_logo": True, "quality": "max"}


def build_video_body(prompt: str, ratio: str | None, duration: int, model: str) -> dict:
    """Port trung thực body SUBMIT_JS (Khan-aware). Giữ đúng key/vị trí — server đọc credit/queue nhiều chỗ."""
    now_ms = int(time.time() * 1000)
    now_sec = now_ms // 1000
    local_conv = f"local_{now_ms}{str(int(uuid.uuid4().int % 1000)).zfill(3)}"
    khan = _khan_extra(model)
    khan_mode = bool(khan)

    ability = {"model": model, "duration": int(duration),
               "input_box_content": {"user_input_content": prompt, "reply_message_format": REPLY_FORMAT}}
    if ratio:
        ability["ratio"] = ratio
    ability.update(khan)
    init_opt = {"need_ack_conversation": True, **khan}

    # Text tin nhắn: Khan KHÔNG nhét "30秒" (kẻo trợ lý Dola đọc thấy rồi hỏi lại/hạ 15s); ngoài Khan mới ép spec.
    if khan_mode:
        text = REPLY_FORMAT.replace("%s", prompt + (("、" + ratio) if ratio else ""))
    else:
        orient = _ORIENT.get(ratio or "", "")
        spec = ((f"{int(duration)}秒" if duration else "")
                + (f"・アスペクト比{ratio}" + (f"（{orient}）" if orient else "") if ratio else ""))
        directive = (f"【この仕様で直接生成してください（{spec}）。長さ・比率は変更せず、追加の確認は不要です】\n"
                     if spec else "")
        text = directive + prompt

    body = {
        "client_meta": {
            "local_conversation_id": local_conv, "conversation_id": "", "bot_id": BOT_ID,
            "last_section_id": "", "last_message_index": None,
            "local_permissions": [
                {"permission_name": "ACCESS_COARSE_LOCATION", "status": 3},
                {"permission_name": "ACCESS_FINE_LOCATION", "status": 3},
                {"permission_name": "ACCESS_BACKGROUND_LOCATION", "status": 3},
            ],
        },
        "messages": [{
            "local_message_id": str(uuid.uuid4()),
            "content_block": [{
                "block_type": 10000,
                "content": {"text_block": {"text": text, "icon_url": "", "icon_url_dark": "", "summary": ""},
                            "pc_event_block": ""},
                "block_id": str(uuid.uuid4()), "parent_id": "", "meta_info": [], "append_fields": [],
            }],
            "message_status": 0,
        }],
        "option": {
            "send_message_scene": "", "create_time_ms": now_ms, "collect_id": "", "is_audio": False,
            "answer_with_suggest": False, "tts_switch": False, "need_deep_think": 0, "click_clear_context": False,
            "from_suggest": False, "is_regen": False, "is_replace": False, "is_from_click_option": False,
            "is_from_click_softlink": False, "disable_sse_cache": False, "select_text_action": "",
            "is_select_text": False, "resend_for_regen": False, "scene_type": 0, "unique_key": str(uuid.uuid4()),
            "start_seq": 0, "need_create_conversation": True, "conversation_init_option": init_opt,
            "regen_query_id": [], "edit_query_id": [], "regen_instruction": "", "no_replace_for_regen": False,
            "message_from": 0, "shared_app_name": "", "shared_app_id": "",
            "sse_recv_event_options": {"support_chunk_delta": True}, "is_ai_playground": False, "is_old_user": False,
            "recovery_option": {"is_recovery": (not khan_mode), "req_create_time_sec": now_sec,
                                "append_sse_event_scene": 0},
            "message_storage_type": 0, "related_deleted_message_ids": {}, "connector_info_list": [],
            "model_config": {"model_item_key": "", "model_extra_params": {}},
            "aggregate_params": {"conversation_mode": "", "mode_id": "", "model_item_key": "", "agent_mode": "",
                                 "reasoning_effort": "", "provider_id": ""},
        },
        "chat_ability": {"ability_type": 17, "ability_param": json.dumps(ability, ensure_ascii=False), **khan},
        "user_context": [],
        "ext": {"answer_with_suggest": "0", "sub_conv_firstmet_type": "1", "collection_id": "", "is_finish": "1",
                "conversation_init_option": json.dumps(init_opt, ensure_ascii=False),
                "commerce_credit_config_enable": "0"},
    }
    body.update(khan)   # Khan rải extras ở cả root body (diff toàn thân 13/09: 13 khoá)
    return body


def build_query(cookies: dict[str, str]) -> dict[str, str]:
    """Query cho /chat/completion (no-Chrome): params cố định + msToken/fp từ cookie. device_id để trống —
    verify --send sẽ cho biết Dola có chấp nhận không có device_id của trình duyệt hay không."""
    from a_bogus_web import fake_mstoken
    return {
        "aid": str(signer.DEFAULT_AID), "real_aid": str(signer.DEFAULT_AID), "device_platform": "web",
        "language": "ja", "region": "JP", "sys_region": "JP", "samantha_web": "1", "web_platform": "browser",
        "use-olympus-account": "1", "version_code": "20800", "pkg_type": "release_version",
        "pc_version": "3.32.62", "doubao_device_platform": "web", "doubao_pc_version": "3.32.62",
        "msToken": cookies.get("msToken") or fake_mstoken(),
        "fp": cookies.get("s_v_web_id", ""), "tz_name": "Asia/Tokyo",
        "web_tab_id": str(uuid.uuid4()),
    }


def _extract_conversation_id(text: str) -> str:
    """Lấy conversation_id (số) từ luồng SSE trả về. Ưu tiên id dạng số dài như video conv."""
    for m in re.finditer(r'"conversation_id"\s*:\s*"?(\d{6,})"?', text):
        return m.group(1)
    return ""


def build_signed(cookies: dict[str, str], req_body: dict) -> tuple[str, str]:
    from urllib.parse import urlencode
    query = urlencode(build_query(cookies))
    body_json = json.dumps(req_body, ensure_ascii=False, separators=(",", ":"))
    a_bogus = signer.sign(query, body_json, aid=signer.DEFAULT_AID, user_agent=UA)
    return f"{WEB_URL}?{query}&a_bogus={a_bogus}", body_json


class SubmitHttpRejected(RuntimeError):
    """Dola CHẮC CHẮN chưa nhận lệnh (4xx / captcha / thiếu cookie) — an toàn thử lại đường khác, chưa trừ lượt."""


# Mã lỗi libcurl xảy ra TRƯỚC khi byte đầu tiên rời máy → chắc chắn Dola chưa nhận, gửi lại không mất lượt.
# Cố ý KHÔNG có 28 (hết giờ), 55/56 (đứt lúc gửi/nhận), 18/52 (nhận dở) — những cái đó Dola có thể đã nhận.
_NEVER_SENT_CURL_CODES = frozenset({5, 6, 7, 35, 97})   # RESOLVE_PROXY, RESOLVE_HOST, CONNECT, SSL_CONNECT, PROXY


def _never_left_machine(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code in _NEVER_SENT_CURL_CODES
    return False   # không rõ mã → coi như CÓ THỂ đã gửi (an toàn cho lượt)


# Mã lỗi libcurl xảy ra TRƯỚC khi byte đầu tiên rời máy → chắc chắn Dola chưa nhận, gửi lại không mất lượt.
# Cố ý KHÔNG có 28 (hết giờ), 55/56 (đứt lúc gửi/nhận), 18/52 (nhận dở) — những cái đó Dola có thể đã nhận.
_NEVER_SENT_CURL_CODES = frozenset({5, 6, 7, 35, 97})   # RESOLVE_PROXY, RESOLVE_HOST, CONNECT, SSL_CONNECT, PROXY


def _never_left_machine(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code in _NEVER_SENT_CURL_CODES
    return False   # không rõ mã → coi như CÓ THỂ đã gửi (an toàn cho lượt)


async def submit_via_http(account: str, prompt: str, ratio: str | None, duration: int,
                          model: str | None = None, proxy: str | None = None, on_submitted=None) -> str:
    """Gửi 1 lệnh video qua HTTP thuần (curl_cffi), trả conversation_id. KHÔNG mở Chrome.

    on_submitted(account, True) gọi NGAY TRƯỚC POST (từ đây có thể đã trừ lượt). Ném SubmitHttpRejected khi Dola
    chắc chắn CHƯA nhận (4xx/captcha/thiếu cookie → gọi on_submitted(False)); RuntimeError khác = không rõ (đừng gửi lại).
    """
    import asyncio
    from curl_cffi import requests as creq

    model = model or config.MODEL_KEY_SEEDANCE25
    try:
        cookies = load_account_cookies(account)
    except FileNotFoundError as e:
        raise SubmitHttpRejected(str(e)) from e
    if "sessionid" not in cookies:
        raise SubmitHttpRejected(f"Nick {account}: cookies.json thiếu sessionid (đăng nhập lại nick)")
    body = build_video_body(prompt, ratio, duration, model)
    url, body_json = build_signed(cookies, body)
    headers = {"Content-Type": "application/json", "agw-js-conv": "str, str", "Accept": "*/*",
               "User-Agent": UA, "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
               "Referer": "https://www.dola.com/chat/", "Origin": "https://www.dola.com",
               "last-event-id": "undefined"}
    proxies = {"http": proxy, "https": proxy} if proxy else None

    def _post() -> tuple[int, str]:
        r = creq.post(url, data=body_json.encode(), headers=headers, proxies=proxies,
                      impersonate="chrome", timeout=120)
        return r.status_code, r.text

    if on_submitted:
        on_submitted(account, True)   # lệnh sắp rời máy → cổng "đã gửi" của pool chặn xoay/gửi lại
    try:
        status, text = await asyncio.to_thread(_post)
    except Exception as e:   # noqa: BLE001
        # Lỗi mạng KHÔNG đồng nghĩa "Dola chưa nhận". Chỉ lỗi xảy ra TRƯỚC khi gói tin rời máy (không phân giải được
        # tên miền, không bắt tay được TCP/TLS/proxy) mới chắc chắn chưa gửi → an toàn rơi về đường Chrome. Hết giờ
        # chờ trả lời / đứt lúc đang đọc thì Dola CÓ THỂ đã nhận và đã trừ lượt — gửi lại là mất lượt lần 2.
        if _never_left_machine(e):
            if on_submitted:
                on_submitted(account, False)
            raise SubmitHttpRejected(f"không nối được tới Dola (chưa gửi): {str(e)[:160]}") from e
        raise RuntimeError(
            f"Mất kết nối SAU khi đã gửi lệnh tới Dola — KHÔNG gửi lại để tránh trừ lượt 2 lần. "
            f"Xem dola.com của nick, chưa có video thì chạy lại: {str(e)[:140]}") from e
    if 400 <= status < 500 and status != 408:
        if on_submitted:
            on_submitted(account, False)   # WAF/cookie/proxy chặn ở cửa → chưa trừ lượt
        raise SubmitHttpRejected(f"Dola từ chối submit (HTTP {status}): {text[:200]}")
    if status != 200:
        raise RuntimeError(f"Dola từ chối submit (HTTP {status}): {text[:200]}")
    # Lấy conversation_id TRƯỚC: có id nghĩa là Dola ĐÃ nhận việc (đã trừ lượt) — dù trong phần trả lời có lẫn chữ
    # "verify"/"captcha" ở trường khác thì cũng không được coi là bị chặn, vì rơi về đường Chrome sẽ gửi lần 2.
    conv_id = _extract_conversation_id(text)
    if not conv_id:
        low = text.lower()
        if any(w in low for w in ("verify", "slide", "captcha")) or '"a_bogus' in low:
            # a_bogus bị từ chối (ByteDance đổi thuật toán, hoặc IP bẩn) → chưa tạo hội thoại → rơi về Chrome an toàn.
            if on_submitted:
                on_submitted(account, False)
            raise SubmitHttpRejected(f"Bị chặn/nghi (verify/captcha/a_bogus lệch bản) — signer.last_backend={signer.last_backend()}: {text[:200]}")
        raise RuntimeError(f"Submit gửi được nhưng không lấy được conversation_id: {text[:200]}")
    print(f"[{account}] submit_via_http OK conversation_id={conv_id} (ký {signer.last_backend()})", flush=True)
    return conv_id


def _demo() -> None:
    """Dry-run self-check (KHÔNG gửi): body 2.5/30s có đủ payload Khan + ký được a_bogus."""
    body = build_video_body("con mèo lướt sóng", "9:16", 30, config.MODEL_KEY_SEEDANCE25)
    # Khan phải rải ở 3 nơi: ability_param, chat_ability, root body
    ap = json.loads(body["chat_ability"]["ability_param"])
    assert ap.get("credits") == 1 and ap.get("cost") == 1 and ap.get("allow_free_queue") is True, ap
    assert body["chat_ability"].get("allow_free_queue") is True, body["chat_ability"]
    assert body.get("allow_free_queue") is True and body.get("cost") == 1, "Khan phải ở root body"
    assert "30秒" not in body["messages"][0]["content_block"][0]["content"]["text_block"]["text"], "text KHÔNG được ghi 30秒"
    assert body["client_meta"]["bot_id"] == BOT_ID
    # 2.0 KHÔNG có Khan
    body20 = build_video_body("x", "9:16", 10, config.MODEL_KEY_SEEDANCE20)
    assert "allow_free_queue" not in body20, "2.0 không có payload Khan"
    # ký thử (web thuần)
    import os
    os.environ["DOLA_SIGNER_BACKEND"] = "web"
    bj = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    ab = signer.sign("aid=497858", bj)
    assert len(ab) >= 100, ab
    print("submit_http OK: body 2.5/30s đủ Khan (ability_param+chat_ability+root), text sạch '30秒', 2.0 không Khan, ký a_bogus ok.")


def _cli() -> None:
    """CLI verify:
      python submit_http.py                                  # dry-run (không gửi) — self-check body/ký
      python submit_http.py --nick fb... --send              # GỬI THẬT 30s 2.5 (TỐN ~2 credit) → in conversation_id
      python submit_http.py --nick fb... --send --dur 10 --prompt "..."   # tuỳ chỉnh
    """
    import argparse, asyncio
    ap = argparse.ArgumentParser()
    ap.add_argument("--nick"); ap.add_argument("--send", action="store_true")
    ap.add_argument("--prompt", default="con mèo lướt sóng lúc hoàng hôn, máy quay cầm tay rung nhẹ")
    ap.add_argument("--ratio", default="9:16"); ap.add_argument("--dur", type=int, default=30)
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    if not a.send:
        _demo(); return
    if not a.nick:
        raise SystemExit("--send cần --nick")
    from browser import account_proxy_url
    proxy = account_proxy_url(a.nick) or None
    print(f"GỬI THẬT tới Dola: nick={a.nick} model={a.model or config.MODEL_KEY_SEEDANCE25} {a.dur}s {a.ratio} "
          f"proxy={'có' if proxy else 'đi thẳng'} — tốn credit nếu nhận.")
    cid = asyncio.run(submit_via_http(a.nick, a.prompt, a.ratio, a.dur, a.model, proxy))
    print(f"✓ conversation_id={cid} — a_bogus web ĐƯỢC CHẤP NHẬN. Poll bằng poll_conversation_http.")


if __name__ == "__main__":
    _cli()
