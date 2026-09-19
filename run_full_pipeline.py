"""run_full_pipeline.py — Submit + Poll + Download, 1 file runner duy nhất.

MỤC ĐÍCH: Thay vì chạy 3 lệnh rời (submit → poll → download), 1 file runner làm hết.
Verify tích hợp cuối cùng của toàn bộ no-Chrome pipeline.

CHẠY:
    ./.venv/bin/python run_full_pipeline.py                # mặc định acc_test, 30s, 9:16, Khan mode
    ./.venv/bin/python run_full_pipeline.py --nick fb... --dur 5 --ratio 16:9 --model 2.0

TỐN CREDIT THẬT — cẩn thận khi chạy nhiều lần trên nick production.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# ---- Import các module của repo ----
try:
    import config
    import submit_http
    from submit_http import submit_via_http
    from video_worker_ui import poll_conversation_http
except ImportError as e:
    print(f"[FATAL] Không import được module: {e}", file=sys.stderr)
    sys.exit(1)


# =============================================================================
# Helpers
# =============================================================================
def _print_banner(title: str) -> None:
    bar = "=" * 72
    print(f"\n{bar}\n  {title}\n{bar}", flush=True)


def _load_cookies_and_creds(account: str) -> tuple[str, str, str]:
    """Đọc cookies.json của nick + tách sessionid/msToken/fp.
    Poll KHÔNG yêu cầu msToken (submit dùng fake_mstoken(), poll accept empty).
    """
    p = Path("accounts") / account / "cookies.json"
    if not p.exists():
        raise SystemExit(f"[FATAL] Không thấy {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    cookies = {c["name"]: c["value"] for c in raw}
    if "sessionid" not in cookies:
        raise SystemExit(f"[FATAL] cookies.json thiếu 'sessionid' (đăng nhập lại nick)")
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    ms_token = cookies.get("msToken", "")          # poll accept rỗng
    fp = cookies.get("s_v_web_id", "")
    if not fp:
        print(f"  [WARN] cookies.json thiếu 's_v_web_id' → poll có thể fail")
    return cookie_str, ms_token, fp


def _print_submit_body(account: str, body: dict) -> None:
    """In tóm tắt body submit (mask prompt dài để dễ đọc)."""
    text = body["messages"][0]["content_block"][0]["content"]["text_block"]["text"]
    ap = json.loads(body["chat_ability"]["ability_param"])
    print(f"  • model           : {ap.get('model')}")
    print(f"  • duration        : {ap.get('duration')}s")
    print(f"  • ratio           : {ap.get('ratio')}")
    print(f"  • text (head 100) : {text[:100]!r}...")
    print(f"  • bot_id          : {body['client_meta']['bot_id']}")
    print(f"  • ability_type    : {body['chat_ability']['ability_type']}")


# =============================================================================
# Step 1: SUBMIT
# =============================================================================
async def step_submit(account: str, prompt: str, ratio: str, duration: int,
                      model_key: str, proxy: str | None) -> str:
    _print_banner(f"STEP 1/3 — SUBMIT ({model_key}, {duration}s, {ratio})")

    # Preview body KHÔNG gửi
    body_preview = submit_http.build_video_body(prompt, ratio, duration, model_key)
    _print_submit_body(account, body_preview)
    print(f"  • proxy           : {'có' if proxy else 'đi thẳng IP máy'}")

    print("\n  >>> Bắn request thật tới Dola... (tốn credit nếu nhận)", flush=True)
    t0 = time.time()
    try:
        conv_id = await submit_via_http(account, prompt, ratio, duration,
                                        model_key, proxy)
    except Exception as e:
        print(f"\n  [FAIL] submit_via_http: {type(e).__name__}: {e}", flush=True)
        raise
    dt = time.time() - t0
    print(f"\n  [OK] conversation_id = {conv_id}  (trong {dt:.2f}s)")
    return conv_id


# =============================================================================
# Step 2: POLL
# =============================================================================
async def step_poll(account: str, conv_id: str, cookie: str, ms_token: str,
                    fp: str, timeout: int) -> dict:
    _print_banner(f"STEP 2/3 — POLL ({conv_id}, timeout {timeout}s)")

    def on_poll(_t):
        print(f"    ↳ vẫn đang chờ Dola render...", flush=True)

    def on_balance(bal, src):
        print(f"    ↳ số dư = {bal}  (nguồn={src})", flush=True)

    t0 = time.time()
    result = await poll_conversation_http(
        account=account, cookie=cookie, ms_token=ms_token, fp=fp,
        conversation_id=conv_id, timeout=timeout,
        on_poll=on_poll, on_balance=on_balance,
    )
    dt = time.time() - t0
    print(f"\n  [OK] poll xong trong {dt:.1f}s")
    return result


# =============================================================================
# Step 3: DOWNLOAD
# =============================================================================
def step_download(result: dict, account: str, conv_id: str) -> str:
    _print_banner("STEP 3/3 — DOWNLOAD")
    # poll_conversation_http đã tự download rồi → check local_path
    local = result.get("local_path")
    if local and Path(local).exists():
        size_kb = Path(local).stat().st_size / 1024
        print(f"  • poll đã tự download: {local}")
        print(f"  • size: {size_kb:.1f} KB")
        print(f"  • credits_used: {result.get('credits_used')}")
        if result.get("download_error"):
            print(f"  [WARN] download_error: {result['download_error'][:200]}")
        return str(local)

    # Fallback: poll chưa download, tự tải từ video_url
    url = result.get("video_url")
    if not url:
        print("  [FAIL] Poll kết thúc nhưng KHÔNG có video_url / local_path.")
        print(f"  result keys: {list(result.keys())}")
        for k, v in result.items():
            print(f"  {k}: {str(v)[:120]}")
        sys.exit(2)

    print(f"  • video_url : {url[:120]}...")

    out_dir = Path("downloads"); out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{account}_{conv_id}.mp4"

    print(f"  >>> Tải về {out} ...", flush=True)
    import curl_cffi
    t0 = time.time()
    r = curl_cffi.get(url, impersonate="chrome124", timeout=300)
    if r.status_code != 200:
        print(f"  [FAIL] HTTP {r.status_code}: {r.text[:200]}")
        sys.exit(3)
    out.write_bytes(r.content)
    dt = time.time() - t0
    size_kb = len(r.content) / 1024
    print(f"\n  [OK] {out}  ({size_kb:.1f} KB, trong {dt:.1f}s)")
    return str(out)


# =============================================================================
# MAIN
# =============================================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nick", default="acc_test",
                    help="Tên nick (folder trong accounts/)")
    ap.add_argument("--prompt", default="con mèo vàng ngủ trên bãi cỏ xanh, ánh nắng chiều êm dịu, camera chậm",
                    help="Prompt tạo video")
    ap.add_argument("--ratio", default="9:16",
                    help="Tỉ lệ: 9:16, 16:9, 1:1")
    ap.add_argument("--dur", type=int, default=30,
                    help="Thời lượng giây (5/10/15/30)")
    ap.add_argument("--model", default="2.5",
                    help="Model: '2.0' (seedance_v2.0) hoặc '2.5' (Khan)")
    ap.add_argument("--timeout", type=int, default=600,
                    help="Poll timeout (giây)")
    ap.add_argument("--no-submit", action="store_true",
                    help="Skip submit (poll/download từ conversation_id có sẵn)")
    ap.add_argument("--conv-id", default="",
                    help="Dùng conversation_id có sẵn (kết hợp --no-submit)")
    args = ap.parse_args()

    # Map model key
    model_key = config.MODEL_KEY_SEEDANCE25 if args.model == "2.5" else config.MODEL_KEY_SEEDANCE20

    # Load cookie + proxy
    cookie_str, ms_token, fp = _load_cookies_and_creds(args.nick)
    from browser import account_proxy_url
    proxy = account_proxy_url(args.nick) or None

    print("=" * 72)
    print("FULL PIPELINE TEST — submit + poll + download")
    print("=" * 72)
    print(f"  nick    : {args.nick}")
    print(f"  model   : {args.model}  (key={model_key})")
    print(f"  duration: {args.dur}s")
    print(f"  ratio   : {args.ratio}")
    print(f"  proxy   : {'có' if proxy else 'đi thẳng'}")

    # ---- Step 1: SUBMIT ----
    if args.no_submit:
        if not args.conv_id:
            raise SystemExit("--no-submit cần --conv-id")
        conv_id = args.conv_id
        print(f"\n  [SKIP SUBMIT] dùng conv_id = {conv_id}")
    else:
        conv_id = asyncio.run(step_submit(
            args.nick, args.prompt, args.ratio, args.dur, model_key, proxy,
        ))

    # ---- Step 2: POLL ----
    result = asyncio.run(step_poll(
        args.nick, conv_id, cookie_str, ms_token, fp, args.timeout,
    ))

    # ---- Step 3: DOWNLOAD ----
    out = step_download(result, args.nick, conv_id)

    print("\n" + "=" * 72)
    print(f"✅ PIPELINE OK — file ở {out}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
