"""test_pipeline_payload_shape.py — Snapshot test cho HTTP no-Chrome pipeline payload.

MỤC ĐÍCH: Bắt regress khi Dola / ByteDance đổi format payload / query params.
Khi ByteDance xoay thuật toán hoặc đổi schema, payload shape sẽ lệch → test này FAIL
→ developer biết phải update code ngay, không phải đợi job chạy thật mới phát hiện.

CHẠY:
    ./.venv/bin/python test_pipeline_payload_shape.py

KHÔNG gửi request thật, chỉ test in-memory:
    - submit_http.build_video_body()
    - submit_http.build_query()
    - submit_http.build_signed() (a_bogus signer từ .c — nếu .so load fail sẽ skip)
    - device_manager.generate_deterministic_device_info()
"""
from __future__ import annotations

import json
import re
import sys
import time

# ---- Bắt buộc import được các module (an toàn nếu thiếu) ----
try:
    import config  # noqa: F401
    import submit_http
    import device_manager
except ImportError as e:
    print(f"[FAIL] Không import được module Dola: {e}")
    sys.exit(1)


FAIL = 0
PASS = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global FAIL, PASS
    if condition:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  —  {detail}")


# =============================================================================
# 1. device_manager: format đúng chuẩn ByteDance
# =============================================================================
def test_device_manager() -> None:
    print("\n[1] device_manager — 3 định danh thiết bị")
    info = device_manager.generate_deterministic_device_info("test_acc")
    check("device_id là str 19 chữ số bắt đầu bằng '7'",
          bool(re.fullmatch(r"7\d{18}", info.get("device_id", ""))),
          f"actual={info.get('device_id')!r}")
    check("web_id là str 19 chữ số bắt đầu bằng '7'",
          bool(re.fullmatch(r"7\d{18}", info.get("web_id", ""))),
          f"actual={info.get('web_id')!r}")
    check("tea_uuid không rỗng",
          bool(info.get("tea_uuid")),
          f"actual={info.get('tea_uuid')!r}")

    # Deterministic: gọi 2 lần cùng seed phải ra kết quả giống nhau
    info2 = device_manager.generate_deterministic_device_info("test_acc")
    check("Deterministic — gọi 2 lần cùng nick ra giống nhau",
          info == info2,
          f"diff: {set(info.items()) ^ set(info2.items())}")


# =============================================================================
# 2. submit_http.build_query(): query params khớp schema
# =============================================================================
def test_build_query() -> None:
    print("\n[2] submit_http.build_query — query string schema")
    cookies = {
        "sessionid": "fake_session_id",
        "msToken": "fake_mstoken_value",
        "s_v_web_id": "fake_fp_value",
    }
    q = submit_http.build_query(cookies)

    # Required keys (device_id/web_id/tea_uuid KHÔNG bắt buộc: chưa có nguồn ID thật từ profile)
    required = ["aid", "real_aid",
                "msToken", "fp", "tz_name", "web_tab_id", "version_code",
                "pc_version", "doubao_pc_version", "language", "region",
                "sys_region", "samantha_web", "web_platform",
                "use-olympus-account", "pkg_type", "device_platform"]
    for k in required:
        check(f"query có key '{k}'", k in q, f"keys={list(q.keys())}")

    # aid phải là 495671 (đã verify trong signer.c:6490)
    check("aid == '495671' (Dola web, KHÔNG phải 497858)",
          q.get("aid") == "495671", f"actual={q.get('aid')}")

    # tz_name = Asia/Tokyo (khớp khu vực Dola)
    check("tz_name == 'Asia/Tokyo'", q.get("tz_name") == "Asia/Tokyo",
          f"actual={q.get('tz_name')}")

    # Anti-regress: KHÔNG có 'app_name' (một số báo cáo ghi nhầm)
    check("KHÔNG có 'app_name' (samantha_web dùng dạng '1' không phải app_name)",
          "app_name" not in q, "có app_name → có khả năng copy nhầm từ báo cáo sai")

    # Region JP
    check("region == 'JP'", q.get("region") == "JP")
    check("sys_region == 'JP'", q.get("sys_region") == "JP")

    # msToken từ cookie (không rỗng)
    check("msToken từ cookie lấy ra được", q.get("msToken") == "fake_mstoken_value",
          f"actual={q.get('msToken')!r}")

    # Thiếu msToken → bỏ hẳn tham số, không tự bịa giá trị giả
    q_none = submit_http.build_query({"sessionid": "x", "s_v_web_id": "fp"})
    check("thiếu msToken → không có key 'msToken'", "msToken" not in q_none,
          f"actual={q_none.get('msToken')!r}")

    # Không có cookie msToken nhưng có xmst (localStorage) → dùng xmst
    q_xmst = submit_http.build_query({"sessionid": "x", "xmst": "xmst_value"})
    check("có xmst, không có msToken → msToken = xmst", q_xmst.get("msToken") == "xmst_value",
          f"actual={q_xmst.get('msToken')!r}")

    # pc_version khớp trang Dola hiện tại
    check("pc_version == '3.36.11'", q.get("pc_version") == "3.36.11" and q.get("doubao_pc_version") == "3.36.11",
          f"actual={q.get('pc_version')}/{q.get('doubao_pc_version')}")


# =============================================================================
# 3. submit_http.build_video_body(): body shape
# =============================================================================
def test_build_video_body() -> None:
    print("\n[3] submit_http.build_video_body — body shape")
    body = submit_http.build_video_body(
        prompt="một chú mèo vàng ngủ trên bãi cỏ",
        ratio="16:9", duration=5, model="seedance-2.0",
    )

    # Top-level keys
    check("body có 'client_meta'", "client_meta" in body)
    check("body có 'messages'", "messages" in body)
    check("body có 'option'", "option" in body)
    check("body có 'chat_ability'", "chat_ability" in body)
    check("body có 'ext'", "ext" in body)

    # client_meta shape
    cm = body.get("client_meta", {})
    check("client_meta.bot_id != rỗng", bool(cm.get("bot_id")))
    check("client_meta.conversation_id == '' (tạo mới)", cm.get("conversation_id") == "",
          f"actual={cm.get('conversation_id')!r}")
    check("client_meta.local_conversation_id bắt đầu bằng 'local_'",
          str(cm.get("local_conversation_id", "")).startswith("local_"),
          f"actual={cm.get('local_conversation_id')!r}")

    # option shape
    opt = body.get("option", {})
    check("option.need_create_conversation == True", opt.get("need_create_conversation") is True)
    check("option có 'conversation_init_option'", "conversation_init_option" in opt)
    check("option có 'recovery_option'", "recovery_option" in opt)

    # chat_ability shape
    ca = body.get("chat_ability", {})
    check("chat_ability.ability_type == 17 (video mode)", ca.get("ability_type") == 17,
          f"actual={ca.get('ability_type')}")
    ap = json.loads(ca.get("ability_param", "{}"))
    check("chat_ability.ability_param.model == 'seedance-2.0'",
          ap.get("model") == "seedance-2.0", f"actual={ap.get('model')}")
    check("chat_ability.ability_param.duration == 5",
          ap.get("duration") == 5, f"actual={ap.get('duration')}")
    check("chat_ability.ability_param.ratio == '16:9'",
          ap.get("ratio") == "16:9", f"actual={ap.get('ratio')}")

    # Directive injection (anti-clarifying) — KHÔNG có cho Khan (2.5)
    text = body["messages"][0]["content_block"][0]["content"]["text_block"]["text"]
    has_directive = "【この仕様で直接生成してください" in text
    check("Mode 2.0 có directive 【...】 chống clarifying", has_directive,
          f"text={text[:80]!r}")

    # ANTI-HALLUCINATION CHECK — những thứ báo cáo từng nhầm, code KHÔNG có:
    body_str = json.dumps(body, ensure_ascii=False)
    for fake_str in [
        "slow steadycam",
        "cinematic fluid motion",
        "24fps",  # nếu xuất hiện → code đã tự augment smooth-motion, đáng ngờ
    ]:
        check(f"KHÔNG có '{fake_str}' trong body (claim ảo giác, code không augment)",
              fake_str not in body_str.lower(),
              f"text chứa '{fake_str}' → code đã tự thêm keyword")


# =============================================================================
# 4. submit_http.build_video_body() cho Khan mode (2.5) — directive KHÔNG có
# =============================================================================
def test_build_video_body_khan() -> None:
    print("\n[4] submit_http.build_video_body — Khan mode (2.5)")
    body = submit_http.build_video_body(
        prompt="test prompt khan", ratio="16:9", duration=30, model="seedance-2.5",
    )
    text = body["messages"][0]["content_block"][0]["content"]["text_block"]["text"]

    # Khan mode KHÔNG có directive (tránh Dola hỏi lại 30s)
    has_directive = "【この仕様で直接生成してください" in text
    check("Khan mode KHÔNG có directive 【...】 (để Dola không hỏi lại 30s)",
          not has_directive,
          f"text={text[:100]!r}")

    # Khan có extras
    ca = body.get("chat_ability", {})
    ap = json.loads(ca.get("ability_param", "{}"))
    check("Khan ability_param.allow_free_queue == True",
          ap.get("allow_free_queue") is True)
    check("Khan ability_param.no_watermark == True",
          ap.get("no_watermark") is True)


# =============================================================================
# 5. build_signed(): a_bogus signature length
# =============================================================================
def test_build_signed() -> None:
    print("\n[5] submit_http.build_signed — a_bogus signature")
    cookies = {"sessionid": "x", "msToken": "y", "s_v_web_id": "z"}
    body = submit_http.build_video_body("test", ratio="16:9", duration=5, model="seedance-2.0")

    try:
        url, body_json = submit_http.build_signed(cookies, body)
    except Exception as e:
        # signer có thể chưa load (.so) — báo skip
        if "a_bogus_web" in str(e) or "signer" in str(e).lower():
            print(f"  [SKIP] Không load được signer native (.so): {e}")
            print("         → OK, test chỉ skip phần signature length.")
            return
        raise

    check("URL bắt đầu bằng 'https://www.dola.com/chat/completion'",
          url.startswith("https://www.dola.com/chat/completion"),
          f"actual={url[:80]}")
    check("URL có query 'aid=495671'", "aid=495671" in url)
    check("URL có query 'device_platform=web'", "device_platform=web" in url)
    check("URL có query 'tz_name=Asia%2FTokyo'", "tz_name=Asia%2FTokyo" in url)

    # Anti-regress: KHÔNG có /im/chain/send
    check("URL KHÔNG chứa '/im/chain/send' (endpoint ma, không tồn tại)",
          "/im/chain/send" not in url)

    # a_bogus length = 176 (theo spec ByteDance)
    m = re.search(r"a_bogus=([^&]+)", url)
    if m:
        ab = m.group(1)
        # Ngưỡng theo signer.py:53 (>=100). Bản web hiện ra 172 ký tự và Dola vẫn nhận (video 10s tạo được 19/09).
        check(f"a_bogus length >= 100 (actual={len(ab)})", len(ab) >= 100,
              f"len={len(ab)}")
    else:
        check("URL có query 'a_bogus=...'", False, "Không thấy a_bogus trong URL")


# =============================================================================
# MAIN
# =============================================================================
def main() -> int:
    print("=" * 72)
    print("TEST: Pipeline payload shape (snapshot test cho no-Chrome HTTP submit)")
    print("=" * 72)
    t0 = time.time()
    test_device_manager()
    test_build_query()
    test_build_video_body()
    test_build_video_body_khan()
    test_build_signed()
    dt = time.time() - t0
    print("\n" + "=" * 72)
    print(f"TỔNG: {PASS} pass, {FAIL} fail — trong {dt:.2f}s")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
