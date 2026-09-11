"""Video generation worker v2: UI automation with OpenCV slider puzzle solver."""
import asyncio
import json
import random
import re
import sys
import time
import uuid
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlsplit

import aiohttp
from patchright.async_api import async_playwright

from gap import find_gap_x

import config
from browser import cookie_value, launch_account_context, pin_session_cookies
from dola_client import CREDIT_FAIL_PATTERN, CreditError
from video_worker import (POLL_JS, SUBMIT_JS, RiskControlError, SubmitDelivered, SubmitRejected,
                          _check_submit, _download, extract_unwatermarked_url)

# Daily limit pattern matching response text (JA / ZH / EN / VI)
DAILY_LIMIT_PATTERN = re.compile(
    r"動画生成の\s*1日あたりの上限|(?:每日|今日|今天)(?:视频|影片)?生成.*(?:上限|限额|额度)|"
    r"daily.*(?:limit|quota)|(?:limit|quota).*per\s*day|"
    r"đạt giới hạn tạo video|giới hạn tạo video (?:hằng|hàng) ngày|giới hạn.*(?:hằng|hàng) ngày",
    re.IGNORECASE,
)

# Content policy & safety violation patterns (from DomixHub-Seedance)
CONTENT_POLICY_PATTERN = re.compile(
    r"動画の生成はできません|生成はできません|生成できません|暴力的な内容|不適切な内容|"
    r"ポリシーに違反|規約に違反|违反(?:社区|内容)*(?:规范|政策|准则)|"
    r"cannot be generated|can'?t be generated|violates? (?:our |the )?polic",
    re.IGNORECASE,
)

# Portrait / Face protection pattern
PORTRAIT_PROTECTION_PATTERN = re.compile(
    r"肖像保護|あなた自身が写っている動画のみ|別の画像を使用してください|肖像权保护|请使用您本人|"
    r"portrait protection|can only generate .*yourself|only generate videos (?:of|where) you",
    re.IGNORECASE,
)

# "Change the parameters and try again": Dola's wording when the remaining credits do not
# cover the requested length (longer videos cost more). Treated as a credit shortfall.
PARAMETER_CHANGE_PATTERN = re.compile(
    r"パラメーターを変更してもう一度|変更してもう一度お試し|更改参数后重试|请更改参数|"
    r"change the parameters and try|please change the parameters",
    re.IGNORECASE,
)
_CREDIT_SHORT_MSG = ("Không đủ lượt cho video này (Dola cần nhiều lượt hơn số còn lại — video dài hơn "
                     "tốn nhiều lượt hơn). Giảm thời lượng hoặc dùng tài khoản khác.\n↳ Dola: ")

# Lỗi TẠM THỜI của Dola (hệ thống báo lỗi, yêu cầu thử lại) — KHÔNG phải lỗi tài khoản/nội dung.
# Neo vào cụm "có lỗi" thật, không neo vào "もう一度お試し" chung chung (tránh nhầm với đổi tham số).
TRANSIENT_ERROR_PATTERN = re.compile(
    r"エラーが発生|システムエラー|問題が発生|不明なエラー|しばらく(たって|して)(から)?(再度|もう一度)|"
    r"出错了|发生(了)?错误|系统错误|请稍后(再试|重试)|稍后(再试|重试)|"
    r"an error (has )?occurred|something went wrong|please try again later|try again later|unknown error|"
    r"(đã|có) xảy ra lỗi|có lỗi xảy ra|lỗi hệ thống|thử lại sau",
    re.IGNORECASE,
)

# Login wall / logged-out signals visible in page text (JA / VI / EN / ZH)
_LOGOUT_TEXTS = (
    "他の機能を利用するにはログイン", "ログインしてください", "Googleで続ける",
    "Continue with Google", "Tiếp tục với Google", "Log In to Unlock",
    "Đăng nhập để", "Log in to", "登录以",
)
_LOGOUT_MSG = ("Bị đăng xuất khỏi Dola giữa chừng (mất phiên đăng nhập) — cookie tài khoản đã chết. "
               "Hãy đăng nhập lại / thêm cookie mới cho tài khoản này.")

# Progress chatter Dola emits while rendering; never a reason to fail fast.
_STATUS_MARKERS = (
    "生成された動画", "動画が生成", "生成中", "作成中", "レンダリング", "処理中", "待って", "生成しています",
    "アップロード", "%", "generating", "rendering", "uploading", "đang tạo", "đang tải", "生成されます",
    "完成します", "完成予定", "お送りします", "完了したら", "クレジットを使用", "分後に", "只需", "分钟后",
    "will be generated", "will complete", "will send", "i'll send", "i'll start", "minutes", "残っています",
)

# Dola asks to confirm a (different) supported length before rendering, e.g.
#   JA: "…最大長は 15 秒です。15 秒で生成してもよろしいですか？"
#   EN: "…supports durations from 4 to 15 seconds. I can generate it at the nearest supported duration…"
# An unanswered question hangs the job until timeout, so poll_conversation answers "はい".
_DURATION_WORDS = ("秒", "長さ", "長")
_DURATION_WORDS_LOW = ("second", "giây", "duration", "length")
_ASK_MARKS = ("？", "?", "よろしい", "でしょうか", "ですか", "確認", "确认", "请确认", "指定", "生成する",
              "生成しても", "はい", "「はい」", "1.", "2.", "1、", "①", "②", "支持", "对应", "対応", "範囲",
              "范围", "最大", "最近")
_ASK_MARKS_LOW = ("confirm", "should i", "proceed", "would you like", "supports", "supported", "nearest",
                  "maximum", "range", "choose", "xác nhận", "tối đa", "phạm vi", "gần nhất", "hỗ trợ")

# Labels of the "add image" control when the composer hides its file input until clicked.
_IMG_TRIGGER_LABELS = ("参考画像", "画像を追加", "画像", "Thêm ảnh", "Ảnh tham chiếu", "Add image",
                       "Reference", "画像をアップロード")
UPLOAD_ATTEMPTS = 3        # whole-upload retries (Dola's uploader flakes on slow networks)
UPLOAD_WINDOW_SEC = 40     # per-attempt wait for TOS upload + thumbnails
FETCH_SUBMIT_TIMEOUT_SEC = 180   # whole SUBMIT_JS call (request + SSE ack)
FETCH_ACK_IDLE_MS = 3000         # after SSE_ACK, stop reading the stream once it idles this long
STALE_POLLS = 4            # same substantive Dola reply for this many polls (5s each) => refusal
IMAGE_ONLY_POLLS = 8       # image creations but no video for this many polls => wrong output type


class AccountLimitedError(Exception):
    """Account reached daily video generation limit."""


class CreditInsufficientError(Exception):
    """Insufficient points prior to generation."""


class LoggedOutError(Exception):
    """Dola invalidated the session server-side; account needs re-login."""


class ContentPolicyViolationError(Exception):
    """Prompt or reference image violated Dola content policy."""


class PortraitProtectionError(Exception):
    """Portrait / identity protection triggered."""


class ParameterChangeError(Exception):
    """Parameters rejected or need changes."""


class TransientDolaError(Exception):
    """Dola returned a transient/system error asking to retry (not an account fault)."""


async def _is_logged_out(page, context) -> bool:
    """True when Dola dropped the session (server-side logout, dead cookie, login wall).

    A stale sessionid can survive a server-side logout, so cookie presence alone is not
    proof; the ?from_logout redirect, a vanished sessionid, or login CTA text all count.
    """
    try:
        if "from_logout" in (page.url or ""):
            return True
    except Exception:
        pass
    try:
        if not cookie_value(await context.cookies("https://www.dola.com"), "sessionid"):
            return True
    except Exception:
        pass
    try:
        body = await page.evaluate("() => document.body.innerText || ''")
    except Exception:
        return False
    return any(t in body for t in _LOGOUT_TEXTS)


async def _persist_before_close(context, account: str) -> None:
    """Re-pin Dola session cookies before the worker context closes.

    Dola rotates `sessionid` (and sid_* ) during activity; a rotated value arrives as a
    *session* cookie with no expiry, so Chromium would drop it when this persistent context
    closes and the nick would look logged out next run. Pinning it with a far-future expiry
    keeps the freshest session on disk. Best-effort: never blocks the close.
    """
    try:
        await pin_session_cookies(context)
    except Exception as exc:
        print(f"[{account}] (re-pin cookie bỏ qua: {str(exc)[:80]})", flush=True)


# video_worker.py chèn directive "【この仕様で直接生成してください（…）】" vào đầu prompt. Poll đọc
# lại chính dòng đó và tưởng Dola đang hỏi → trả lời "đổi sang 30秒" trong khi Dola vừa nói chỉ
# làm được 15s. Nhận diện để bỏ qua.
_OWN_MESSAGE_MARKS = ("この仕様で直接生成してください", "追加の確認は不要")


def _is_own_message(text: str) -> bool:
    return any(k in (text or "") for k in _OWN_MESSAGE_MARKS)


def _capped_seconds(text: str) -> int | None:
    """Số giây tối đa Dola đề nghị trong câu "chỉ làm được tới N giây"."""
    offered = [int(n) for n in re.findall(r"(\d{1,2})\s*秒", text or "") if int(n) in (4, 5, 10, 15)]
    return max(offered) if offered else None


def _is_duration_confirm(text: str) -> bool:
    """True when Dola asks to confirm a different supported video length (see _ASK_MARKS)."""
    t = text or ""
    low = t.lower()
    has_duration = any(k in t for k in _DURATION_WORDS) or any(k in low for k in _DURATION_WORDS_LOW)
    if not has_duration:
        return False
    return any(k in t for k in _ASK_MARKS) or any(k in low for k in _ASK_MARKS_LOW)


def _is_transient_error(text: str) -> bool:
    """True when Dola shows a transient/system error (retry helps; rotate nick)."""
    return bool(TRANSIENT_ERROR_PATTERN.search(text or ""))


_NET_ERRORS = ("ERR_INTERNET_DISCONNECTED", "ERR_NETWORK_CHANGED", "ERR_TIMED_OUT",
               "ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_FAILED",
               "ERR_NAME_NOT_RESOLVED", "ERR_PROXY_CONNECTION_FAILED", "ERR_ADDRESS_UNREACHABLE",
               "ERR_EMPTY_RESPONSE", "ERR_SOCKET_NOT_CONNECTED")


async def _goto_dola(page, url: str, attempts: int = 3):
    """page.goto có thử lại khi lỗi mạng chớp nhoáng (mất mạng/proxy blip) → job không chết vì 1 nhịp rớt."""
    for i in range(1, attempts + 1):
        try:
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            return
        except Exception as e:
            msg = str(e)
            if any(n in msg for n in _NET_ERRORS):
                if i < attempts:
                    print(f"  Mở Dola lỗi mạng ({msg[:50]}), thử lại {i}/{attempts - 1}...", flush=True)
                    await asyncio.sleep(3)
                    continue
                raise RuntimeError(
                    "Mất mạng khi mở Dola (đã thử "
                    f"{attempts} lần) — kiểm tra internet/proxy rồi thử lại.") from e
            raise


async def _reply_text(page, text: str) -> bool:
    """Types `text` into the composer and sends it, so Dola proceeds (UI pinned to JA)."""
    try:
        box = await page.query_selector("textarea") or await page.query_selector('[contenteditable="true"]')
        if not box:
            return False
        try:
            await box.click(timeout=2500)
        except Exception:
            await box.focus()   # khung bị lớp phủ che khi đang tạo video -> focus không cần actionability
        await page.keyboard.insert_text(text)   # dán nguyên khối, tức thì (không gõ từng ký tự)
        await page.wait_for_timeout(300)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2000)
        return True
    except Exception as exc:
        print(f"  (auto-reply failed: {str(exc)[:60]})", flush=True)
        return False


async def _reply_yes(page) -> bool:
    return await _reply_text(page, "はい")


def _is_duration_capped(text: str) -> bool:
    """True khi Dola từ chối thời lượng dài và mời/ép tạo ở mức NGẮN HƠN.

    VD: "30秒は現在サポート外です。最長の15秒で生成してよろしければ、すぐに開始できます。"
    Chấp nhận (trả 'はい') để CÓ video 15s thay vì kẹt/timeout khi ép 30s.
    """
    t = text or ""
    low = t.lower()
    rejects = any(k in t for k in (
        "サポート外", "対応していません", "対応していない", "対応外", "対応しておりません",
        "サポートされていません", "現在サポート", "できません", "できない",
        "まで対応", "しか対応", "のみ対応", "までとなり", "までです")) \
        or any(k in low for k in (
            "not support", "isn't support", "is not support", "can't", "cannot",
            "currently support", "up to", "maximum", "only"))
    offers_short = bool(re.search(r"(?:最長|最大|まで)?\s*(?:4|5|10|15)\s*秒", t)) \
        or bool(re.search(r"\b(?:4|5|10|15)\s*(?:sec|second)", low))
    return rejects and offers_short


# Dola đôi khi trả lời bằng MENU chọn thông số thay vì tạo luôn, ví dụ:
#   "直接生成しますね。動画の仕様を選んでください： アスペクト比：A. 16:9 横向き / B. 9:16 縦向き …"
# Poll không trình duyệt không trả lời được; poll trình duyệt phải chọn đúng option theo yêu cầu.
_SPEC_MENU_MARKS = ("仕様を選", "選んでください", "アスペクト比", "縦横比", "比率", "画面比",
                    "aspect ratio", "chọn tỉ lệ", "chọn tỷ lệ", "please choose", "please select", "選択してください")
_ZEN2HAN = {ord(z): ord(h) for z, h in zip("ＡＢＣＤＥＦＧ０１２３４５６７８９：．）", "ABCDEFG0123456789:.)")}


def _zen2han(s: str) -> str:
    return (s or "").translate(_ZEN2HAN)


def _norm_ratio(r) -> str:
    return re.sub(r"\s", "", str(r or "")).replace("：", ":")


def _lists_options(text: str) -> bool:
    """Dola liệt kê phương án có chữ cái ("- A. 15秒版 / - B. 10秒版") → phải đáp CHỮ CÁI.

    Trả 'はい' cho câu kiểu này là vô nghĩa: Dola hỏi lại y nguyên → vòng ping-pong
    (log 11:14→11:16 của fb61593690226090).
    """
    return bool(re.search(r"[A-G]\s*[.\)、]", _zen2han(text or "")))


def _answer_key(text: str) -> str:
    """Khoá "đã trả lời": <nhánh xử lý>|<mức giây Dola cho phép>.

    KHÔNG dùng nguyên văn câu hỏi: tin nhắn được đọc lúc Dola CÒN ĐANG STREAM rồi dài thêm
    sau đó, nên so khớp cả chuỗi (kể cả tiền tố) luôn trượt và tool trả lời lại đúng câu cũ
    — vòng ping-pong trong log 11:14→11:16 của fb61593690226090. Khoá theo CÂU TRẢ LỜI sẽ
    gửi thì ổn định: cùng loại câu + cùng mức giây = cùng câu trả lời, gửi lần hai vô ích.
    Menu A/B tách riêng để không bị nuốt vì trước đó đã trả 'はい' cho cùng thông báo chặn.
    """
    yes_branch = not _lists_options(text) and (_is_duration_capped(text) or _is_duration_confirm(text))
    return f"{'yes' if yes_branch else 'menu'}|{_capped_seconds(text) or 0}"


def _is_spec_menu(text: str) -> bool:
    """True khi Dola CHỜ mình cho specs: menu A/B, HOẶC hỏi mở 'yêu cầu thêm (độ dài/tỉ lệ) là gì'."""
    t = text or ""
    low = t.lower()
    hit = any(k in t for k in _SPEC_MENU_MARKS) or any(k in low for k in ("aspect ratio", "please choose", "please select"))
    ht = _zen2han(t)
    has_opts = _lists_options(t) or bool(re.search(r"\d+\s*:\s*\d+", ht))
    # Dola hỏi MỞ (không menu): "何か追加の要件（長さ、アスペクト比…）があれば教えてください" / "指定してください"
    spec_word = any(k in t for k in ("要件", "長さ", "アスペクト比", "縦横比", "スタイル", "秒数")) \
                or any(k in low for k in ("aspect ratio", "duration", "length", "requirement"))
    asks = any(k in t for k in ("教えて", "指定して", "ありますか", "あれば", "希望", "ご希望", "ください", "でしょうか", "ですか")) \
           or any(k in low for k in ("let me know", "would you like", "any specific", "please specify", "tell me"))
    # Dola CHỜ xác nhận để tạo (chưa có video): "生成するには…確認が必要", "よろしいですか", "生成を開始しても"
    gen_ctx = any(k in t for k in ("生成", "動画", "作成")) or any(k in low for k in ("generate", "video", "create"))
    confirm = ("確認" in t and any(k in t for k in ("必要", "してください", "お願い", "ください"))) \
              or any(k in t for k in ("よろしいですか", "よろしいでしょうか", "生成を開始しても", "進めてもよろしい", "進めます")) \
              or any(k in low for k in ("need.*confirm", "confirm", "shall i", "should i proceed"))
    needs_confirm = gen_ctx and confirm
    # Dola liệt kê phương án ("A. 15秒版 / B. 10秒版") = đang chờ mình chọn, dù không có chữ
    # "選んでください" nào. Bắt cả ca này, kẻo HTTP poll ngồi đợi tới hết giờ.
    lists_durations = bool(re.search(r"[A-G]\s*[.\)、]\s*[^\n]{0,12}\d+\s*秒", ht))
    return (hit and has_opts) or (spec_word and asks) or needs_confirm or (gen_ctx and lists_durations)


def _spec_menu_answer(text: str, ratio, duration) -> str:
    """Reply satisfying the menu. Dola thường đòi cả hai (ví dụ đáp "B、10秒" = chữ cái tỉ lệ + số giây)."""
    ht = _zen2han(text or "")
    want = _norm_ratio(ratio)
    # chữ cái cho tỉ lệ mong muốn: "A. 16:9", "B) 9:16", "1、16:9"
    ratio_letter = ""
    if want:
        for m in re.finditer(r"([A-G1-9])\s*[.\)、]\s*(\d{1,2}\s*:\s*\d{1,2})", ht):
            if _norm_ratio(m.group(2)) == want:
                ratio_letter = m.group(1)
                break
    # menu có hỏi thời lượng không?
    has_seconds = bool(re.search(r"秒数|長さ|duration|giây|second|\d+\s*秒", ht, re.IGNORECASE))
    # từ khoá dọc/ngang giúp Dola không nhầm khi phải ĐỔI tỉ lệ (nguyên nhân "dọc ra ngang")
    orient = {"9:16": "縦向き", "3:4": "縦", "16:9": "横向き", "4:3": "横", "1:1": "正方形"}.get(want, "")
    # chữ cái cho phương án THỜI LƯỢNG: "- A. 15秒版 / - B. 10秒版" → gọi đúng chữ cái,
    # nói vòng ("15秒に変更して…") thì Dola hay hỏi lại menu đó lần nữa.
    if duration and not ratio_letter:
        for m in re.finditer(r"([A-G])\s*[.\)、]\s*[^\n]{0,14}?(\d{1,2})\s*秒", ht):
            if int(m.group(2)) == int(duration):
                tail = f"、{want}{('（'+orient+'）') if orient else ''}" if want else ""
                return f"{m.group(1)}、{duration}秒{tail}でお願いします。"
    parts = []
    if ratio_letter:
        parts.append(ratio_letter)
    elif want:
        parts.append(f"{want}{('（'+orient+'）') if orient else ''}")
    if duration and has_seconds:
        parts.append(f"{duration}秒")
    if parts:
        # Khi phải sửa tỉ lệ (không có sẵn chữ cái option) → yêu cầu rõ ràng "đổi thành ..."
        if want and not ratio_letter:
            return f"{orient+'の' if orient else ''}{want}、{duration}秒に変更して生成してください。".replace("の、", "、")
        return "、".join(parts)
    return "はい"


def _is_status_text(text: str) -> bool:
    low = (text or "").lower()
    return any(m in text or m in low for m in _STATUS_MARKERS)


VIDEO_BTN_ALTS = ("動画を作成", "Tạo video", "Create video", "生成视频")   # action-bar entry (JA UI first)
_CONSENT_LABELS = ("OK", "Đồng ý", "同意する", "Accept", "Chấp nhận")        # cookie banner steals the first click
_NEW_CHAT_LABELS = ("新しいチャット", "New chat", "Cuộc trò chuyện mới", "新对话")
ENTRY_ATTEMPTS = 15
CAPTCHA_FRAME_KEY = "bdcaptcha.html"   # Captcha verifycenter iframe


async def _click_first_visible(page, labels, exact: bool = True, timeout: int = 3000) -> bool:
    for label in labels:
        try:
            loc = page.get_by_text(label, exact=exact).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


async def _open_video_composer(page) -> None:
    """Dismisses the cookie banner, starts a fresh chat and opens the video card.

    The action bar with 動画を作成 only renders on an empty new chat, and the consent
    banner swallows the first click, so both steps are required (Dola UI, 2026-09).
    """
    if await _click_first_visible(page, _CONSENT_LABELS):
        await page.wait_for_timeout(500)
    if await _click_first_visible(page, _NEW_CHAT_LABELS):
        await page.wait_for_timeout(1200)
    for _ in range(ENTRY_ATTEMPTS):
        if await _click_first_visible(page, VIDEO_BTN_ALTS, timeout=4000):
            await page.wait_for_timeout(1500)
            return
        await page.wait_for_timeout(1500)
    await page.screenshot(path="no_entry_btn.png")
    raise RuntimeError("Video entry button not found (動画を作成 / Tạo video)")


def _log_submit_payload(account: str):
    """Logs the ability_param the Dola UI really sends, so new model keys show up in logs."""
    def on_request(request):
        if request.method != "POST" or "/chat/completion" not in request.url:
            return
        try:
            ability = json.loads(request.post_data or "{}").get("chat_ability", {}).get("ability_param", "")
            print(f"[{account}] chat/completion ability_param={ability[:200]}", flush=True)
        except Exception:
            pass
    return on_request


# Read-only balance pre-check from recent conversations
BALANCE_JS = r"""
async ({msToken, fp}) => {
  const params = new URLSearchParams({
    version_code: "20800", language: "ja", device_platform: "web",
    doubao_device_platform: "web", aid: "495671", real_aid: "495671",
    pkg_type: "release_version", pc_version: "3.32.62", doubao_pc_version: "3.32.62",
    region: "JP", sys_region: "JP", samantha_web: "1", web_platform: "browser",
    "use-olympus-account": "1", web_tab_id: crypto.randomUUID(),
  });
  if (msToken) params.set("msToken", msToken);
  if (fp) params.set("fp", fp);
  const headers = {
    "Content-Type": "application/json; encoding=utf-8",
    "agw-js-conv": "str", "Accept": "*/*",
  };
  const recent = await fetch("/im/chain/recent_conv?" + params.toString(), {
    method: "POST", headers,
    body: JSON.stringify({
      cmd: 3200,
      uplink_body: {pull_recent_conv_chain_uplink_body: {
        limit: 20, message_count_per_conv: 10, api_version: 1, conv_version: 0,
        direction: 3,
        option: {not_need_message: false, need_complete_conversation: true,
          need_coco_conversation: true, need_coco_bot: true,
          need_pc_pin_chain: true, pc_pin_query_type: 0},
      }},
      sequence_id: crypto.randomUUID(), channel: 2, version: "1",
    }), credentials: "include",
  });
  if (!recent.ok) return {ok: false, texts: []};
  const recentData = await recent.json();
  const body = recentData.downlink_body || {};
  const down = body.pull_recent_conv_chain_downlink_body || {};
  const cells = down.cells || [];
  const ids = cells.map(c => (c.conversation || {}).conversation_id || c.id)
    .filter(Boolean).slice(0, 10);
  if (!ids.length) return {ok: true, texts: []};

  const batch = await fetch("/im/chain/batch_single?" + params.toString(), {
    method: "POST", headers,
    body: JSON.stringify({
      cmd: 3101,
      uplink_body: {batch_pull_singe_chain_uplink_body: {
        conversation_type: 3, direction: 3, limit: 1,
        params: ids.map(conversation_id => ({conversation_id})),
        evaluate_ab_params: "", evaluate_common_params: "", ext: {},
      }},
      sequence_id: crypto.randomUUID(), channel: 2, version: "1",
    }), credentials: "include",
  });
  if (!batch.ok) return {ok: true, texts: []};
  const data = await batch.json();
  // Messages carry create_time (seconds); keep it so callers can ignore yesterday's
  // "daily limit reached" reply, which stays in history after the quota resets.
  const items = [];
  const seen = new Set();
  const grab = (x, out) => {
    if (!x || typeof x !== "object") return;
    if (Array.isArray(x)) { for (const y of x) grab(y, out); return; }
    if (x.text_block && typeof x.text_block.text === "string") out.push(x.text_block.text);
    for (const y of Object.values(x)) grab(y, out);
  };
  const visit = (v) => {
    if (!v || typeof v !== "object") return;
    if (Array.isArray(v)) { for (const x of v) visit(x); return; }
    if (typeof v.content === "string" && v.create_time !== undefined) {
      let t = Number(v.create_time) || 0;
      if (t > 1e12) t = Math.floor(t / 1000);
      const texts = [];
      try { grab(JSON.parse(v.content), texts); } catch (e) {}
      if (!texts.length && /ポイント|积分|points|上限|limit/i.test(v.content)) texts.push(v.content.slice(0, 1200));
      for (const text of texts) {
        if (text.length < 1200 && !seen.has(text)) { seen.add(text); items.push({text, t}); }
      }
      return;
    }
    for (const x of Object.values(v)) visit(x);
  };
  visit(data);
  items.sort((a, b) => b.t - a.t);
  const top = items.slice(0, 80);
  return {ok: true, items: top, texts: top.map(i => i.text)};
}
""";


def find_captcha_frame(page):
    for f in page.frames:
        if CAPTCHA_FRAME_KEY in f.url:
            return f
    return None


async def _fetch_bytes(url: str) -> bytes:
    async with aiohttp.ClientSession() as s:
        async with s.get(url, proxy=config.PROXY or None) as r:
            return await r.read()



async def attach_reference_images(page, image_paths: list[str]) -> None:
    """Uploads reference images through the native file input and waits for the TOS upload.

    Retries the whole upload up to UPLOAD_ATTEMPTS times: Dola's uploader flakes on slow
    networks or when the same image is pushed repeatedly in a short window.
    """
    if not image_paths:
        return
    file_input = page.locator('input[type="file"]').first
    try:
        await file_input.wait_for(state="attached", timeout=8000)
    except Exception:
        # The composer may hide its file input until the "add image" control is clicked.
        for label in _IMG_TRIGGER_LABELS:
            try:
                trigger = page.get_by_text(label, exact=False).first
                if await trigger.count() and await trigger.is_visible():
                    await trigger.click(timeout=2000)
                    await page.wait_for_timeout(600)
            except Exception:
                continue
        try:
            await file_input.wait_for(state="attached", timeout=8000)
        except Exception as exc:
            raise RuntimeError("Không tìm thấy ô tải ảnh tham chiếu trong khung soạn video.") from exc
    events = []

    def on_response(response):
        url = response.url
        if "/alice/resource/prepare_upload" in url or "/upload/v1/" in url:
            events.append((response.status, url))

    page.on("response", on_response)
    expected = len(image_paths)
    tos_count = 0
    try:
        for attempt in range(1, UPLOAD_ATTEMPTS + 1):
            await file_input.set_input_files(image_paths)
            deadline = time.time() + max(UPLOAD_WINDOW_SEC, expected * 15)
            while time.time() < deadline:
                tos_count = sum("/upload/v1/" in url and 200 <= status < 300 for status, url in events)
                # Wait for thumbnails and TOS completion before sending
                thumb_count = await page.locator('img[alt]').count()
                if tos_count >= expected and thumb_count >= expected:
                    await page.wait_for_timeout(800)
                    print(f"[upload] Đã tải {expected} ảnh tham chiếu", flush=True)
                    return
                await page.wait_for_timeout(300)
            print(f"[upload] Lần {attempt} lỗi (tos={tos_count}/{expected}), thử lại...", flush=True)
            await page.wait_for_timeout(1500)
        raise RuntimeError(
            f"Lỗi tải ảnh tham chiếu lên Dola (tos={tos_count}/{expected}) sau {UPLOAD_ATTEMPTS} lần thử. "
            "Thường do mạng chập chờn hoặc tải cùng ảnh quá nhiều lần trong thời gian ngắn. "
            "Thử lại sau ít phút, hoặc đổi ảnh (JPG/PNG/WEBP, < 15MB)."
        )
    finally:
        page.remove_listener("response", on_response)


def _gen_track(distance: float):
    """Generates humanized cubic Bézier mouse drag trajectory with micro-tremor and realistic acceleration."""
    import math
    steps = random.randint(48, 68)
    overshoot = random.uniform(4.0, 9.0)
    target = distance + overshoot
    pts = []

    # Control points for slow start, smooth acceleration, and deceleration
    p0 = 0.0
    p1 = target * random.uniform(0.18, 0.32)
    p2 = target * random.uniform(0.72, 0.88)
    p3 = target

    for i in range(1, steps + 1):
        t = i / steps
        # Cubic Bézier: B(t) = (1-t)^3*p0 + 3(1-t)^2*t*p1 + 3(1-t)*t^2*p2 + t^3*p3
        x = (1 - t)**3 * p0 + 3 * (1 - t)**2 * t * p1 + 3 * (1 - t) * t**2 * p2 + t**3 * p3
        # Human hand micro-tremor
        y = random.uniform(-1.4, 1.4) * math.sin(math.pi * t) if 0.05 < t < 0.95 else 0.0
        # Realistic variable sleep duration
        dt = random.randint(10, 22) if (0.2 < t < 0.8) else random.randint(22, 42)
        pts.append((x, y, dt))

    # Pull back from overshoot
    pullback_steps = random.randint(4, 7)
    for i in range(1, pullback_steps + 1):
        ratio = i / pullback_steps
        curr_x = target - overshoot * ratio
        curr_y = random.uniform(-0.5, 0.5)
        dt = random.randint(18, 35)
        pts.append((curr_x, curr_y, dt))

    return pts



async def solve_slider(page, frame, attempt: int) -> bool:
    """Solves captcha slider notch within iframe and performs drag."""
    await frame.wait_for_selector("img", timeout=15000)
    await frame.evaluate("""async () => {
        const t0 = Date.now();
        while (Date.now() - t0 < 10000) {
            const imgs = [...document.images];
            if (imgs.length >= 2 && imgs.every(im => im.complete && im.naturalWidth > 0)) return;
            await new Promise(r => setTimeout(r, 200));
        }
        throw new Error("captcha images load timeout");
    }""")
    await page.wait_for_timeout(800)

    imgs = await frame.evaluate("""() => [...document.images].map(im => ({
        src: im.src, w: im.naturalWidth, h: im.naturalHeight,
        bw: im.getBoundingClientRect().width,
        left: im.getBoundingClientRect().left,
    }))""")
    bg = next((i for i in imgs if ".jpeg" in i["src"] or "-2." in i["src"]), None)
    piece = next((i for i in imgs if i is not bg and (".png" in i["src"] or "-1." in i["src"])), None)
    if not bg or not piece:
        print("  ✗ Captcha background or puzzle image not found", flush=True)
        return False

    bg_bytes = await _fetch_bytes(bg["src"])
    piece_bytes = await _fetch_bytes(piece["src"])
    Path("dbg_bg.jpg").write_bytes(bg_bytes)
    Path("dbg_piece.png").write_bytes(piece_bytes)

    gap_x, conf = find_gap_x(bg_bytes, piece_bytes)
    scale = bg["bw"] / bg["w"] if bg["w"] else 340 / 552
    distance = (gap_x - (piece["left"] - bg["left"]) / scale) * scale
    print(f"  [solve#{attempt}] gap_x={gap_x} conf={conf:.3f} scale={scale:.2f} distance={distance:.0f}px", flush=True)

    btn = frame.locator(".captcha-slider-btn")
    bb = await btn.bounding_box()
    if not bb:
        print("  ✗ Drag handle .captcha-slider-btn not found", flush=True)
        return False
    sx, sy = bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2
    await page.mouse.move(sx, sy)
    await page.wait_for_timeout(random.randint(150, 350))
    await page.mouse.down()
    await page.wait_for_timeout(random.randint(80, 180))
    for dx, dy, dt in _gen_track(distance):
        await page.mouse.move(sx + dx, sy + dy)
        await asyncio.sleep(dt / 1000)
    await page.wait_for_timeout(random.randint(120, 260))
    await page.mouse.up()

    for _ in range(10):
        await page.wait_for_timeout(700)
        if not find_captcha_frame(page):
            return True
    return False



# "Remaining N" — the actual balance. Anchored to a credit-ish unit / のみ so it never
# grabs "残り15秒" (remaining seconds). Checked BEFORE the cost patterns below.
_REMAINING_PATTERNS = (
    re.compile(r"残り\s*(\d+)\s*(?:のみ|ポイント|积分|クレジット|点|回|動画)", re.I),
    re.compile(r"本日は残り\s*(\d+)", re.I),
    re.compile(r"(?:remaining|left|còn lại)\s*[:：]?\s*(\d+)\s*(?:credit|point|lượt|积分)", re.I),
)

_BALANCE_PATTERNS = (
    re.compile(r"(?:本日は|今日(?:还剩|剩余)?|今天).*?(\d+)\s*(?:ポイント|积分|points?)", re.I),
    re.compile(r"(?:remaining|left)\s*[:：]?\s*(\d+)\s*points?", re.I),
    re.compile(r"(?:还剩|剩余|还有)\s*(\d+)\s*(?:积分|点)", re.I),
    re.compile(r"(\d+)\s*(?:動画クレジット|视频额度|video\s*credit)", re.I),
)


def _parse_balance_texts(texts: list[str]) -> tuple[int | None, bool, str]:
    for text in texts:
        if DAILY_LIMIT_PATTERN.search(text):
            return None, True, text
    # Prefer an explicit "remaining N" over "N credits will be used" (a COST, not the balance).
    for text in texts:
        for pattern in _REMAINING_PATTERNS:
            match = pattern.search(text)
            if match:
                return int(match.group(1)), False, text
    for text in texts:
        for pattern in _BALANCE_PATTERNS:
            match = pattern.search(text)
            if match:
                if "使用" in text or "will be used" in text.lower():
                    continue  # cost phrase (N credits will be used), not the remaining balance
                return int(match.group(1)), False, text
    return None, False, ""


def _last_limit_reset_ts() -> float:
    """Epoch seconds of the most recent daily-quota reset (midnight in config.LIMIT_RESET_TZ)."""
    try:
        tz = ZoneInfo(config.LIMIT_RESET_TZ)
    except Exception:  # tzdata missing: Dola resets on Japan time
        tz = timezone(timedelta(hours=9))
    now = datetime.now(tz)
    return datetime.combine(now.date(), dt_time.min, tzinfo=tz).timestamp()


def _fresh_texts(result: dict) -> list[str]:
    """Bot replies written since the last quota reset (untimed entries are kept, to be safe)."""
    items = result.get("items") or [{"text": t, "t": 0} for t in result.get("texts", [])]
    since = _last_limit_reset_ts()
    return [i["text"] for i in items if not i.get("t") or i["t"] >= since]


async def _preflight_balance(page, ms_token: str, fp: str, required: int) -> dict:
    """Reads known credit balance / daily-limit state from today's chat history."""
    try:
        result = await asyncio.wait_for(page.evaluate(
            BALANCE_JS, {"msToken": ms_token, "fp": fp}), timeout=30)
        balance, daily_limited, source = _parse_balance_texts(_fresh_texts(result))
        if daily_limited:
            raise AccountLimitedError(f"Account daily generation limit: {source[:120]}")
        if balance is not None and balance < required:
            raise CreditInsufficientError(
                f"Insufficient points: current {balance}, required {required} (source: {source[:120]})"
            )
        return {"balance": balance, "source": source}
    except (AccountLimitedError, CreditInsufficientError):
        raise
    except Exception as e:
        print(f"  Balance pre-check indeterminate (proceeding with submit): {str(e)[:120]}", flush=True)
        return {"balance": None, "source": ""}


class _NeedsBrowser(Exception):
    """HTTP poll gặp câu Dola hỏi lại — chỉ trang web trả lời được, phải mở lại nick.

    Giữ `full` (nguyên văn) để tính _answer_key giống hệt lúc poll trong trang; phần in ra
    cho người dùng thì cắt ngắn."""

    def __init__(self, text: str):
        self.full = text or ""
        super().__init__(re.sub(r"\s+", " ", self.full).strip()[:160])


def _question_needs_browser(text: str) -> bool:
    """Dola đang chờ mình trả lời (xác nhận thời lượng / thời lượng bị hạ / menu thông số)."""
    return _is_duration_confirm(text) or _is_duration_capped(text) or _is_spec_menu(text)


class _FetchSubmitFailed(Exception):
    """Fetch-mode submission never reached Dola (evaluate error / no device query); UI path can retry."""


class _FetchDelivered(Exception):
    """Fetch POST reached Dola (job may be charged) but the id could not be confirmed.

    Must propagate as a hard error — re-submitting via UI would double-charge credits."""


def _fetch_model_key(model_key: str) -> str:
    """ability_param model key for fetch mode, or "" when this model must go through the UI."""
    if model_key == "seedance_v2.0":
        return config.MODEL_KEY_SEEDANCE20
    if model_key == "seedance_v2.5":
        return config.MODEL_KEY_SEEDANCE25
    return ""


def _query_capture(page) -> dict:
    """Collects the API query string the page itself uses (device_id, tea_uuid, web_id, ...).

    Returns a dict filled in by a request listener; read it after the page has loaded.
    """
    captured: dict = {}

    def on_request(request):
        url = request.url
        if captured or "www.dola.com/" not in url or "device_id=" not in url or "web_id=" not in url:
            return
        parsed = parse_qs(urlsplit(url).query)
        captured.update({k: v[0] for k, v in parsed.items()})

    page.on("request", on_request)
    return captured


def _submit_query(captured: dict, fp: str) -> dict:
    """Base query for SUBMIT_JS: the page's own params minus what the bdms hook re-adds."""
    query = {k: v for k, v in captured.items() if k not in ("msToken", "a_bogus", "web_tab_id")}
    query.setdefault("fp", fp)
    query.setdefault("tz_name", "Asia/Tokyo")
    return query


# List recent conversation ids for THIS account (video convs are numeric). Used to recover a
# just-created conversation when the submit stream didn't yield the id, so we never re-submit.
RECENT_CONV_IDS_JS = r"""
async ({msToken, fp}) => {
  const params = new URLSearchParams({version_code: "20800", language: "ja", device_platform: "web",
    doubao_device_platform: "web", aid: "495671", real_aid: "495671", pkg_type: "release_version",
    pc_version: "3.32.62", doubao_pc_version: "3.32.62", region: "JP", sys_region: "JP",
    samantha_web: "1", web_platform: "browser", "use-olympus-account": "1", web_tab_id: crypto.randomUUID()});
  if (msToken) params.set("msToken", msToken);
  if (fp) params.set("fp", fp);
  const r = await fetch("/im/chain/recent_conv?" + params, {method: "POST",
    headers: {"Content-Type": "application/json; encoding=utf-8", "agw-js-conv": "str", "Accept": "*/*"},
    credentials: "include", body: JSON.stringify({cmd: 3200, uplink_body: {pull_recent_conv_chain_uplink_body:
      {limit: 20, message_count_per_conv: 1, api_version: 1, conv_version: 0, direction: 3,
       option: {not_need_message: true, need_complete_conversation: true}}},
      sequence_id: crypto.randomUUID(), channel: 2, version: "1"})});
  if (!r.ok) return [];
  const d = await r.json();
  const cells = (((d.downlink_body||{}).pull_recent_conv_chain_downlink_body)||{}).cells || [];
  return cells.map(c => String((c.conversation||{}).conversation_id || c.id || "")).filter(Boolean);
}
"""


async def _recent_conv_ids(page, ms_token: str, fp: str) -> set:
    try:
        ids = await asyncio.wait_for(
            page.evaluate(RECENT_CONV_IDS_JS, {"msToken": ms_token, "fp": fp}), timeout=20)
        return {i for i in ids if i.isdigit()}
    except Exception:
        return set()


async def _submit_via_fetch(page, context, account: str, prompt: str, ratio: str | None,
                            duration: int, model_key: str, captured: dict, ms_token: str, fp: str,
                            on_submitted=None) -> str:
    """Posts /chat/completion from inside the page (signed by bdms); returns conversation_id.

    If the POST reached Dola (HTTP 200) but the id wasn't in the stream, the job may have been
    accepted and CHARGED — so we recover the new conversation id by diffing recent conversations
    instead of re-submitting (a re-submit would double the credit spend).
    """
    query = _submit_query(captured, fp)
    if "device_id" not in query:
        raise _FetchSubmitFailed("page never exposed its API query (device_id missing)")
    before = await _recent_conv_ids(page, ms_token, fp)
    args = {"prompt": prompt, "ratio": ratio or "", "duration": duration, "model": model_key,
            "query": query, "ackIdleMs": FETCH_ACK_IDLE_MS}
    if on_submitted:
        on_submitted(account, True)   # từ đây trở đi lệnh có thể đã tới Dola → restart không được gửi lại
    try:
        result = await asyncio.wait_for(
            page.evaluate(SUBMIT_JS, args, isolated_context=False), timeout=FETCH_SUBMIT_TIMEOUT_SEC)
    except Exception as exc:
        # The evaluate itself failed → nothing was delivered → safe to fall back to UI.
        if on_submitted:
            on_submitted(account, False)
        raise _FetchSubmitFailed(f"submit call failed: {str(exc)[:160]}") from exc
    try:
        return _check_submit(result)
    except SubmitRejected as exc:
        # Dola/WAF/proxy trả HTTP lỗi, chưa nhận lệnh, chưa trừ lượt → thử lại như lỗi mạng,
        # KHÔNG cho nick nghỉ 30 phút (trước đây cả loạt nick bị "risk-control" oan vì 1 lỗi mạng).
        if on_submitted:
            on_submitted(account, False)
        raise _FetchSubmitFailed(f"Dola từ chối lệnh ({exc}) — kiểm tra mạng/proxy/cookie") from exc
    except SubmitDelivered:
        # Delivered but no id parsed: find the conversation Dola just created (do NOT re-submit).
        for _ in range(6):
            await page.wait_for_timeout(2000)
            new = await _recent_conv_ids(page, ms_token, fp) - before
            if new:
                conv_id = sorted(new)[-1]
                print(f"[{account}] fetch delivered; recovered conversation_id={conv_id}", flush=True)
                return conv_id
        # Couldn't confirm — raise WITHOUT allowing a UI re-submit (guards against double charge).
        raise _FetchDelivered(
            "Đã gửi lệnh tạo video tới Dola nhưng không xác nhận được — KHÔNG gửi lại để tránh trừ lượt 2 lần. "
            "Kiểm tra tài khoản trên dola.com; nếu chưa có video, thử lại.")


async def _generate_via_fetch(account: str, prompt: str, ratio: str | None, duration: int,
                              model_key: str, timeout: int, on_conversation_id, on_poll, on_balance,
                              on_submitted=None, on_browser_free=None, on_browser_hold=None) -> dict:
    """Request-style generation: logged-in page + signed in-page fetch, no clicking.

    Only the submission is retryable through the UI (raises _FetchSubmitFailed); once Dola
    has accepted a conversation every error propagates as-is so nothing is submitted twice.
    """
    async with async_playwright() as p:
        context = await launch_account_context(p, account, headless=None, use_extension=False)
        closed = False
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            captured = _query_capture(page)
            await _goto_dola(page, "https://www.dola.com/chat")
            # Chờ trang tự gọi API để bắt device_id/web_id (nền ký fetch). Trang/proxy chậm có thể
            # cần hơn 5s → chờ tới ~20s thay vì cứng 5s (giảm _FetchSubmitFailed "device_id missing").
            for _ in range(20):
                await page.wait_for_timeout(1000)
                if "device_id" in captured:
                    break
            if await _is_logged_out(page, context):
                raise LoggedOutError(_LOGOUT_MSG)
            cookies = await context.cookies("https://www.dola.com")
            ms_token, fp = cookie_value(cookies, "msToken"), cookie_value(cookies, "s_v_web_id")
            await _preflight_balance(page, ms_token, fp, config.VIDEO_REQUIRED_POINTS)
            conv_id = await _submit_via_fetch(page, context, account, prompt, ratio, duration,
                                              model_key, captured, ms_token, fp, on_submitted)
            print(f"[{account}] fetch submitted ({model_key} {duration}s {ratio or 'default'}) "
                  f"conversation_id={conv_id}, polling for video...", flush=True)
            deadline = time.time() + timeout
            if on_conversation_id:
                on_conversation_id(account, conv_id, deadline)
            if config.HTTP_POLL:
                # Ở trong trang đủ lâu để trả lời các câu Dola hỏi lại (xác nhận 30s, menu thông
                # số) — chỉ trang web ký được tin nhắn trả lời. Hết hỏi thì đóng trình duyệt và
                # theo dõi bằng HTTP thuần: mỗi nick giữ Chrome ~1 phút thay vì suốt 3–15 phút.
                answered: set = set()   # nhớ câu đã trả lời, dùng lại khi phải mở nick lần nữa
                early = await poll_conversation(account, page, context, conv_id, timeout, on_poll,
                                                on_balance, ratio, duration,
                                                handoff_after=config.HTTP_POLL_AFTER_SEC,
                                                answered=answered)
                if not early.get("handoff"):
                    return early                     # video xong ngay trong lúc còn trình duyệt
                remaining = max(30, int(deadline - time.time()))
                fresh = await context.cookies("https://www.dola.com")
                cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in fresh
                                          if c.get("name") and c.get("value"))
                await _persist_before_close(context, account)
                await context.close()
                closed = True
                if on_browser_free:
                    on_browser_free()
                try:
                    return await poll_conversation_http(account, cookie_header, ms_token, fp, conv_id,
                                                        remaining, on_poll, on_balance, answered=answered)
                except _NeedsBrowser as ask:
                    if _answer_key(ask.full) in answered:
                        # Đã trả lời câu này rồi mà Dola vẫn lặp lại → mở nick nữa cũng vô ích.
                        raise RuntimeError(
                            "Dola hỏi đi hỏi lại cùng một câu (thường vì prompt mô tả video dài hơn "
                            "mức Dola cho phép) — rút ngắn kịch bản cho khớp số giây."
                            f"\n↳ Dola: {ask}") from ask
                    print(f"[{account}] Dola hỏi lại muộn → mở lại nick để trả lời: {ask}", flush=True)
                    if on_browser_hold:
                        await on_browser_hold()      # xin lại slot Chrome trước khi mở
                    left = max(60, int(deadline - time.time()))
                    return await resume_video(account, conv_id, left, on_poll=on_poll,
                                              on_balance=on_balance, ratio=ratio, duration=duration,
                                              answered=answered)
            return await poll_conversation(account, page, context, conv_id, timeout, on_poll, on_balance, ratio, duration)
        finally:
            if not closed:
                await _persist_before_close(context, account)
                await context.close()


async def _strip_logo(result: dict, model_key: str, account: str) -> dict:
    """Seedance 2.0 / Dreamina burn a "Dola AI" logo into the corner; 2.5 does not."""
    if config.REMOVE_WATERMARK and model_key != "seedance_v2.5" and result.get("local_path"):
        from watermark import remove_watermark
        try:
            if await asyncio.to_thread(remove_watermark, result["local_path"]):
                print(f"[{account}] Đã xoá logo Dola AI", flush=True)
        except Exception as exc:  # cosmetic step: never fail a finished video over it
            print(f"  (bỏ qua xoá logo: {str(exc)[:80]})", flush=True)
    return result


_SINGLE_URL = "https://www.dola.com/im/chain/single"
_SINGLE_PARAMS = {
    "version_code": "20800", "language": "ja", "device_platform": "web", "doubao_device_platform": "web",
    "aid": "495671", "real_aid": "495671", "pkg_type": "release_version", "pc_version": "3.32.62",
    "doubao_pc_version": "3.32.62", "region": "JP", "sys_region": "JP", "samantha_web": "1",
    "web_platform": "browser", "use-olympus-account": "1",
}


def _parse_single(data: dict) -> dict:
    """Mirror of POLL_JS message parsing, in Python (texts / videos / videoModels / images)."""
    dl = (data.get("downlink_body") or {}).get("pull_singe_chain_downlink_body") or {}
    texts, videos, video_models, images = [], [], [], 0
    for msg in dl.get("messages") or []:
        content = msg.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                continue
        if not isinstance(content, list):
            continue
        for block in content:
            text = (((block.get("content") or {}).get("text_block")) or {}).get("text") or ""
            if text:
                texts.append(text[:600])
            if block.get("block_type") != 2074:
                continue
            for cre in (((block.get("content") or {}).get("creation_block")) or {}).get("creations") or []:
                if cre.get("type") == 1 and cre.get("image"):
                    images += 1
                    continue
                if cre.get("type") != 2:
                    continue
                url = (cre.get("video") or {}).get("download_url") or ""
                if url.startswith("http"):
                    videos.append(url)
                    video_models.append((cre.get("video") or {}).get("video_model") or "")
    return {"texts": texts, "videos": videos, "videoModels": video_models, "images": images}


async def poll_conversation_http(account: str, cookie: str, ms_token: str, fp: str,
                                 conversation_id: str, timeout: int, on_poll=None, on_balance=None,
                                 answered: set | None = None) -> dict:
    """Poll /im/chain/single over PLAIN HTTP (no browser) until a video appears, then download.

    The browser is only needed for the signed submission; polling + download work with cookies
    alone (verified). This frees the browser in ~5s instead of holding it open for the whole
    render (up to 30 min for 30s). Duration-confirm prompts can't be answered here (rare, since
    10/15/30 are all supported) — they surface as a clear error.
    """
    headers = {"Content-Type": "application/json; encoding=utf-8", "agw-js-conv": "str",
               "Accept": "*/*", "cookie": cookie, "Referer": f"https://www.dola.com/chat/{conversation_id}"}
    base = dict(_SINGLE_PARAMS)
    if ms_token:
        base["msToken"] = ms_token
    if fp:
        base["fp"] = fp
    start = time.time()
    last_cb = 0.0
    last_msg = ""
    stale_msg, stale_n = "", 0
    image_polls = 0
    answered = set() if answered is None else answered
    async with aiohttp.ClientSession() as session:
        while time.time() - start < timeout:
            await asyncio.sleep(5)
            body = {"cmd": 3100, "uplink_body": {"pull_singe_chain_uplink_body": {
                "conversation_id": conversation_id, "anchor_index": 9007199254740991,
                "conversation_type": 3, "direction": 1, "limit": 20, "ext": {},
                "filter": {"index_list": []}, "evaluate_ab_params": "", "evaluate_common_params": ""}},
                "sequence_id": str(uuid.uuid4()), "channel": 2, "version": "1"}
            params = {**base, "web_tab_id": str(uuid.uuid4())}
            try:
                async with session.post(_SINGLE_URL, params=params, data=json.dumps(body), headers=headers,
                                        proxy=config.PROXY or None, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    if r.status != 200:
                        continue
                    data = await r.json(content_type=None)
            except Exception as e:
                print(f"  Polling exception (http): {e}", flush=True)
                continue
            now = time.time()
            if on_poll and now - last_cb >= 30:
                on_poll(now)
                last_cb = now
            poll = _parse_single(data)
            for text in poll["texts"]:
                if _is_own_message(text):
                    continue
                balance, _, source = _parse_balance_texts([text])
                if balance is not None and on_balance:
                    on_balance(balance, source)
                if CONTENT_POLICY_PATTERN.search(text) and not _is_duration_capped(text):
                    raise ContentPolicyViolationError(
                        "Dola chặn nội dung (bạo lực / vi phạm chính sách) — đổi prompt nhẹ nhàng hơn.\n↳ Dola: " + text[:170])
                if PORTRAIT_PROTECTION_PATTERN.search(text):
                    raise PortraitProtectionError(
                        "Dola chặn (bảo vệ chân dung): model chỉ tạo video với ảnh MẶT CỦA CHÍNH BẠN.\n↳ Dola: " + text[:160])
                if PARAMETER_CHANGE_PATTERN.search(text):
                    raise ParameterChangeError(_CREDIT_SHORT_MSG + text[:150])
                if DAILY_LIMIT_PATTERN.search(text):
                    raise AccountLimitedError(f"Hết lượt tạo video hôm nay. Dola: {text[:140]}")
                if CREDIT_FAIL_PATTERN.search(text):
                    raise CreditError(f"Không đủ điểm/quota. Dola: {text[:120]}")
                if _is_transient_error(text):
                    raise TransientDolaError(
                        "Dola gặp lỗi tạm thời (hệ thống Dola báo lỗi, cần thử lại). Tự thử lại / xoay nick."
                        f"\n↳ Dola: {text[:140]}")
                if _question_needs_browser(text):
                    # Câu đã trả lời trong trang vẫn nằm trong 20 tin gần nhất suốt đời hội thoại;
                    # không bỏ qua thì ngay poll HTTP đầu tiên sau khi nhả nick đã "hỏi đi hỏi lại".
                    if _answer_key(text) in answered:
                        continue
                    raise _NeedsBrowser(text)
                tt = (text or "").strip()
                if len(tt) > 8 and not _is_status_text(tt) and not tt.startswith("生成された"):
                    last_msg = tt
            if poll["images"] and not poll["videos"]:
                image_polls += 1
                if image_polls >= IMAGE_ONLY_POLLS:
                    extra = f"\n↳ Dola: {last_msg[:140]}" if last_msg else ""
                    raise RuntimeError("Dola tạo ẢNH thay vì video (thường do ảnh tham chiếu). Thử lại." + extra)
            else:
                image_polls = 0
            if poll["videos"]:
                vm = poll["videoModels"]
                url = extract_unwatermarked_url(vm[0] if vm else "", poll["videos"][0])
                print(f"[{account}] Completed (http poll)! Downloading (unwatermarked priority)...", flush=True)
                local = await _download(url, account)
                print(f"[{account}] Downloaded {local} ({local.stat().st_size / 1e6:.1f} MB)", flush=True)
                return {"video_url": url, "local_path": str(local),
                        "conversation_id": conversation_id, "account": account}
            if last_msg and not _is_duration_confirm(last_msg) and not _is_spec_menu(last_msg):
                stale_msg, stale_n = (stale_msg, stale_n + 1) if last_msg == stale_msg else (last_msg, 1)
                if stale_n >= STALE_POLLS:
                    raise RuntimeError(f"Dola báo: {last_msg[:200]}")
            else:
                stale_msg, stale_n = "", 0
            print(f"  ...Generating http ({int(time.time() - start)}s)", flush=True)
    tail = f" Dola báo: {last_msg[:160]}" if last_msg else " (Dola không phản hồi gì thêm)"
    raise TimeoutError(f"Hết {timeout}s chưa ra video (conversation_id={conversation_id})." + tail)


HANDOFF_QUIET_SEC = 20        # chưa phải trả lời gì: im bấy nhiêu giây là nhả trình duyệt
HANDOFF_QUIET_AFTER_QA_SEC = 60   # đã phải trả lời: chờ lâu hơn, hội thoại kiểu này hay hỏi tiếp


async def poll_conversation(account: str, page, context, conversation_id: str,
                            timeout: int, on_poll=None, on_balance=None,
                            ratio: str | None = None, duration: int | None = None,
                            handoff_after: float | None = None, answered: set | None = None) -> dict:
    """Polls the accepted conversation until a video appears.

    Whatever Dola says, surface it: besides the specific handlers (daily limit, credits,
    content policy, portrait protection, duration confirm) the latest substantive reply is
    kept; if it sticks for STALE_POLLS polls with no video, fail fast quoting it, and the
    timeout message quotes it too, so a job never dies with a mystery error.
    """
    cookies = await context.cookies("https://www.dola.com")
    ms_token, fp = cookie_value(cookies, "msToken"), cookie_value(cookies, "s_v_web_id")
    start = time.time()
    last_callback = 0.0
    # Bộ nhớ "đã trả lời" dùng chung với các lần mở lại nick: không thì mỗi lần mở lại sẽ trả lời
    # lại đúng câu cũ còn nằm trong hội thoại → ping-pong vô tận (log 11:03–11:14: 20 lần mở lại).
    answered_specs = set() if answered is None else answered
    last_answer_at = start   # lần cuối phải trả lời Dola → mốc để biết đã hết hỏi
    want_duration = duration # Dola bảo chỉ tới 15s thì trả lời 15s, đừng đòi lại 30s
    image_polls = 0
    last_msg = ""            # latest substantive Dola reply (user messages are not text blocks, see POLL_JS)
    stale_msg, stale_n = "", 0
    while time.time() - start < timeout:
        await asyncio.sleep(5)
        try:
            poll = await asyncio.wait_for(page.evaluate(
                POLL_JS, {"conversationId": conversation_id, "msToken": ms_token, "fp": fp}), timeout=30)
        except Exception as e:
            if "has been closed" in str(e) or "Target closed" in str(e):
                raise RuntimeError(
                    "Trình duyệt của nick bị đóng giữa lúc theo dõi (Chrome tắt/crash) — chạy lại nick này."
                ) from e
            print(f"  Polling exception: {e}", flush=True)
            continue
        if not poll.get("ok") and await _is_logged_out(page, context):
            raise LoggedOutError(_LOGOUT_MSG)
        now = time.time()
        if on_poll and now - last_callback >= 30:
            on_poll(now)
            last_callback = now
        for text in poll.get("texts", []):
            if _is_own_message(text):
                continue
            balance, _, source = _parse_balance_texts([text])
            if balance is not None and on_balance:
                on_balance(balance, source)
            if CONTENT_POLICY_PATTERN.search(text) and not _is_duration_capped(text):
                raise ContentPolicyViolationError(
                    "Dola chặn nội dung (bạo lực / vi phạm chính sách) — đổi prompt nhẹ nhàng hơn."
                    f"\n↳ Dola: {text[:170]}")
            if PORTRAIT_PROTECTION_PATTERN.search(text):
                raise PortraitProtectionError(
                    "Dola chặn (bảo vệ chân dung): model này chỉ tạo video với ảnh MẶT CỦA CHÍNH BẠN. "
                    "Ảnh người khác đôi khi bị chặn (cả 2.0 lẫn 2.5). Không mất lượt — thử lại hoặc đổi ảnh/model."
                    f"\n↳ Dola: {text[:160]}")
            if PARAMETER_CHANGE_PATTERN.search(text):
                raise ParameterChangeError(_CREDIT_SHORT_MSG + text[:150])
            if DAILY_LIMIT_PATTERN.search(text):
                raise AccountLimitedError(f"Hết lượt tạo video hôm nay. Dola: {text[:140]}")
            if CREDIT_FAIL_PATTERN.search(text):
                raise CreditError(f"Không đủ điểm/quota. Dola: {text[:120]}")
            if _is_transient_error(text):
                raise TransientDolaError(
                    "Dola gặp lỗi tạm thời (hệ thống Dola báo lỗi, cần thử lại). Tự thử lại / xoay nick."
                    f"\n↳ Dola: {text[:140]}")
            key = _answer_key(text)
            # Dola nói "chỉ tới 15s" thì mọi câu trả lời sau phải dùng 15s, đừng đòi lại 30s
            if _is_duration_capped(text) or _is_duration_confirm(text):
                want_duration = _capped_seconds(text) or want_duration
            # Câu có menu A/B thì bỏ qua 2 nhánh 'はい', xuống nhánh menu đáp chữ cái
            yes_ok = not _lists_options(text)
            if yes_ok and _is_duration_capped(text) and key not in answered_specs:
                # Dola chặn thời lượng dài (30s) -> chấp nhận mức tối đa họ cho (15s) để có video
                if await _reply_yes(page):
                    answered_specs.add(key)
                    stale_msg, stale_n = "", 0
                    last_answer_at = time.time()
                    print(f"[{account}] Dola chặn thời lượng dài → chấp nhận mức tối đa (はい): {text[:80]}", flush=True)
                continue
            if yes_ok and _is_duration_confirm(text) and key not in answered_specs:
                answered_specs.add(key)
                last_answer_at = time.time()
                if await _reply_yes(page):
                    print(f"[{account}] Dola hỏi thời lượng → tự trả lời Có: {text[:80]}", flush=True)
                continue
            if _is_spec_menu(text) and key not in answered_specs and len(answered_specs) < 3:
                ans = _spec_menu_answer(text, ratio, want_duration)
                if await _reply_text(page, ans):
                    answered_specs.add(key)
                    stale_msg, stale_n = "", 0
                    last_answer_at = time.time()
                    print(f"[{account}] Dola hỏi thông số → tự chọn '{ans}': {text[:70]}", flush=True)
                continue
            tt = (text or "").strip()
            if len(tt) > 8 and not _is_status_text(tt) and not tt.startswith("生成された"):
                last_msg = tt
        # Dola rendered an IMAGE instead of a video (usually reference-image runs).
        if poll.get("images") and not poll.get("videos"):
            image_polls += 1
            if image_polls >= IMAGE_ONLY_POLLS:
                extra = f"\n↳ Dola: {last_msg[:140]}" if last_msg else ""
                raise RuntimeError(
                    "Dola tạo ẢNH thay vì video (thường do ảnh tham chiếu). "
                    "Thử lại, hoặc giảm bớt ảnh tham chiếu." + extra)
        else:
            image_polls = 0
        if poll.get("videos"):
            video_models = poll.get("videoModels", [])
            url = extract_unwatermarked_url(
                video_models[0] if video_models else "", poll["videos"][0])
            print(f"[{account}] Completed! Downloading (unwatermarked priority)...", flush=True)
            local = await _download(url, account)
            print(f"[{account}] Downloaded {local} ({local.stat().st_size / 1e6:.1f} MB)", flush=True)
            return {"video_url": url, "local_path": str(local),
                    "conversation_id": conversation_id, "account": account}
        # A substantive reply that sticks around without a video is Dola's way of saying no.
        if last_msg and not _is_duration_confirm(last_msg) and not _is_spec_menu(last_msg):
            stale_msg, stale_n = (stale_msg, stale_n + 1) if last_msg == stale_msg else (last_msg, 1)
            if stale_n >= STALE_POLLS:
                raise RuntimeError(f"Dola báo: {last_msg[:200]}")
        else:
            stale_msg, stale_n = "", 0
        now = time.time()
        quiet_needed = HANDOFF_QUIET_SEC if last_answer_at == start else HANDOFF_QUIET_AFTER_QA_SEC
        if (handoff_after is not None and now - start >= handoff_after
                and now - last_answer_at >= quiet_needed):
            print(f"[{account}] Dola hết hỏi lại → nhả trình duyệt, theo dõi tiếp bằng HTTP", flush=True)
            return {"handoff": True, "conversation_id": conversation_id, "account": account}
        print(f"  ...Generating ({int(time.time() - start)}s)", flush=True)
    tail = f" Dola báo: {last_msg[:160]}" if last_msg else " (Dola không phản hồi gì thêm)"
    raise TimeoutError(f"Hết {timeout}s chưa ra video (conversation_id={conversation_id})." + tail)


async def resume_video(account: str, conversation_id: str, timeout: int,
                       on_poll=None, on_balance=None, ratio: str | None = None,
                       duration: int | None = None, answered: set | None = None) -> dict:
    """Recovers accepted session after server restart without re-sending prompt."""
    async with async_playwright() as p:
        # 30s unlock now works headless via the fetch hijack (config.SKILLPACK_HIJACK); only the
        # legacy debugger extension needs a headed window.
        ui_headless = None if config.SKILLPACK_HIJACK else False
        context = await launch_account_context(p, account, headless=ui_headless, use_extension=True)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await _goto_dola(page, f"https://www.dola.com/chat/{conversation_id}")
            await page.wait_for_timeout(5000)
            return await poll_conversation(account, page, context, conversation_id, timeout, on_poll,
                                           on_balance, ratio, duration, answered=answered)
        finally:
            await _persist_before_close(context, account)
            await context.close()


async def generate_video(account: str, prompt: str, ratio: str = None,
                         duration: int = None, timeout: int = None,
                         model: str = "seedance_v2.0", use_extension: bool = True,
                         on_conversation_id=None, on_poll=None, on_balance=None,
                         on_submitted=None, on_browser_free=None, on_browser_hold=None,
                         reference_image_paths: list[str] | None = None) -> dict:
    """Full generation flow: signed in-page fetch first (config.SUBMIT_MODE), UI automation otherwise."""
    timeout = timeout or config.VIDEO_TIMEOUT
    model_key = model.lower().replace("-", "_")
    if model_key in ("seedance_2.5", "seedance_v2.5", "seedance_25", "seedance_v25"):
        model_key = "seedance_v2.5"
    elif model_key in ("seedance_2.0", "seedance_v2.0", "seedance_20", "seedance_v20"):
        model_key = "seedance_v2.0"
    else:
        raise ValueError(f"Unsupported model: {model} (supported: seedance-2.0 / seedance-2.5)")
    if duration is not None and duration not in (10, 15, 30):
        raise ValueError("Dola supports durations of 10s, 15s, and 30s via extension")
    # 30s videos cần chờ lâu hơn, nhưng 30 phút là quá dài (nick treo lâu) → chốt 15 phút.
    if duration == 30:
        timeout = max(timeout, 900)
    if reference_image_paths:
        timeout = max(timeout, config.REFERENCE_VIDEO_TIMEOUT)

    fetch_model = _fetch_model_key(model_key)
    if config.SUBMIT_MODE == "fetch" and fetch_model and not reference_image_paths:
        last_fetch_err = None
        # _FetchSubmitFailed = lệnh CHƯA tới Dola (evaluate lỗi trước khi gửi) → thử lại an toàn,
        # không lo trừ lượt 2 lần. Trang/bdms đôi khi chưa sẵn sàng ở lần đầu.
        for attempt in (1, 2):
            try:
                result = await _generate_via_fetch(account, prompt, ratio, duration or 10, fetch_model,
                                                   timeout, on_conversation_id, on_poll, on_balance,
                                                   on_submitted, on_browser_free, on_browser_hold)
                return await _strip_logo(result, model_key, account)
            except _FetchSubmitFailed as exc:
                last_fetch_err = exc
                if attempt == 1:
                    print(f"[{account}] fetch submit trượt ({exc}); thử lại lần 2...", flush=True)
                    await asyncio.sleep(2)
        # Cả 2 lần fetch đều trượt.
        if duration == 30:
            # UI KHÔNG chọn được chip 30s một cách tin cậy → đừng rớt xuống UI (sẽ ra lỗi chip khó hiểu).
            raise RuntimeError(
                f"Không tạo được video 30s: gửi nhanh (fetch) trượt 2 lần — {last_fetch_err}. "
                "30s chỉ chạy qua chế độ nhanh (giao diện không mở được chip 30s). "
                "Kiểm tra nick còn đăng nhập, mạng/proxy ổn định, rồi thử lại.")
        print(f"[{account}] fetch submit refused ({last_fetch_err}); falling back to UI automation", flush=True)
    if duration == 30 and not use_extension:
        raise ValueError("30s generation requires Dola30 extension enabled")
    result = await _generate_via_ui(account, prompt, ratio, duration, timeout, model_key, use_extension,
                                    on_conversation_id, on_poll, on_balance, reference_image_paths,
                                    on_submitted)
    return await _strip_logo(result, model_key, account)


async def _find_model_chip(page):
    """Returns the composer's model chip locator ("モデル 2.0高速" / "Model …"), or None.

    Text-anchored get_by_text is brittle (the label splits into icon + spans), so match a
    small composer button whose text starts with モデル/Model or names a Seedance model.
    """
    candidates = [
        page.get_by_text(re.compile(r"^\s*(モデル|Model)\b"), exact=False),
        page.locator("button, [role='button']").filter(has_text=re.compile(r"モデル|Model|Seedance")),
    ]
    for loc in candidates:
        try:
            first = loc.first
            if await first.count() and await first.is_visible():
                return first
        except Exception:
            continue
    return None


async def _pick_model_option(page, options) -> bool:
    for option_text in options:
        try:
            loc = page.get_by_text(option_text, exact=False).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


async def _list_model_options(page) -> str:
    """Best-effort readback of the model names shown in the open dropdown (for error messages)."""
    try:
        names = await page.evaluate(
            r"""() => [...document.querySelectorAll('[role=option], [role=menuitem], li, button')]
                    .map(e => (e.innerText||'').replace(/\s+/g,' ').trim())
                    .filter(t => /Seedance|Dreamina|モデル|Model/i.test(t) && t.length < 40)
                    .filter((t,i,a) => a.indexOf(t)===i).slice(0, 6)"""
        )
        return ", ".join(names)
    except Exception:
        return ""


async def _select_duration_chip(page, duration: int) -> bool:
    """Clicks the <duration>s chip and confirms it became the active selection. Returns success.

    The composer only offers 30s when the skill-pack hijack (or extension) added it; if it is
    missing, a click on text=30s silently misses and Dola keeps 10s — so we verify, not assume.
    """
    want = f"{duration}s"
    for attempt in range(3):
        try:
            chip = page.get_by_text(want, exact=True).first
            if await chip.count() and await chip.is_visible():
                await chip.click(timeout=3000)
                await page.wait_for_timeout(500)
            else:
                # open the duration dropdown, then pick
                await page.get_by_text(re.compile(r"^\d+s$")).first.click(timeout=3000)
                await page.wait_for_timeout(500)
                opt = page.get_by_text(want, exact=True).first
                if await opt.count() and await opt.is_visible():
                    await opt.click(timeout=3000)
                    await page.wait_for_timeout(500)
        except Exception:
            pass
        # verify: a visible chip whose text is exactly "<n>s" and that reads as active/current
        try:
            active = await page.evaluate(
                """(w) => [...document.querySelectorAll('button,[role=button],[class*=chip],[class*=selected],[aria-selected]')]
                    .some(e => (e.innerText||'').trim() === w &&
                        (e.getAttribute('aria-selected') === 'true' || /select|active|current/i.test(e.className||'')
                         || (e.innerText||'').trim() === w))""", want)
            # fall back to: the chip text is present anywhere visible (Dola shows the chosen chip in the bar)
            shown = await page.get_by_text(want, exact=True).first.is_visible()
            if active or shown:
                return True
        except Exception:
            pass
        await page.wait_for_timeout(800)
    return False


async def _generate_via_ui(account: str, prompt: str, ratio: str | None, duration: int | None,
                           timeout: int, model_key: str, use_extension: bool,
                           on_conversation_id, on_poll, on_balance,
                           reference_image_paths: list[str] | None, on_submitted=None) -> dict:
    """Drives the composer with clicks and keystrokes (reference images, extension durations)."""
    async with async_playwright() as p:
        # With the fetch hijack (config.SKILLPACK_HIJACK) the composer's 30s unlock works headless;
        # only the legacy debugger extension forces a headed window.
        ui_headless = None if (config.SKILLPACK_HIJACK or not use_extension) else False
        context = await launch_account_context(
            p, account, headless=ui_headless, use_extension=use_extension)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await _goto_dola(page, "https://www.dola.com/chat")
            await page.wait_for_timeout(5000)
            if await _is_logged_out(page, context):
                raise LoggedOutError(_LOGOUT_MSG)
            cookies = await context.cookies("https://www.dola.com")
            ms_token, fp = cookie_value(cookies, "msToken"), cookie_value(cookies, "s_v_web_id")
            await _preflight_balance(page, ms_token, fp, config.VIDEO_REQUIRED_POINTS)

            # ---- UI Submission ----
            page.on("request", _log_submit_payload(account))
            await _open_video_composer(page)
            if reference_image_paths:
                await attach_reference_images(page, reference_image_paths)
            # Select model. The composer shows a "モデル 2.0高速" (Model) chip; click it, then pick
            # the requested model from the dropdown. The chip already shows the CURRENT model, so
            # when the default (2.0) is what we want and the chip can't be opened, we just proceed.
            model_chip = await _find_model_chip(page)
            want_25 = model_key == "seedance_v2.5"
            if model_chip is None:
                if want_25:
                    raise RuntimeError("Không mở được ô chọn model trên Dola — thử lại, hoặc dùng Seedance 2.0.")
                print(f"[{account}] (không thấy ô model, dùng mặc định 2.0)", flush=True)
            else:
                try:
                    await model_chip.click(timeout=5000)
                    await page.wait_for_timeout(600)
                    options = (("Dreamina Seedance 2.5", "Seedance 2.5", "Seedance2.5")
                               if want_25
                               else ("Dreamina Seedance 2.0高速", "Dreamina Seedance 2.0",
                                     "Seedance 2.0 Fast", "Seedance2.0Fast", "Seedance 2.0"))
                    if not await _pick_model_option(page, options):
                        avail = await _list_model_options(page)
                        await page.keyboard.press("Escape")
                        if want_25:
                            raise RuntimeError(
                                "Tài khoản này không có Seedance 2.5 (thường chỉ tài khoản trả phí mới có). "
                                f"Model đang có: {avail or 'chỉ Seedance 2.0 / 1.0'}. Hãy chọn Seedance 2.0.")
                        # 2.0 default already selected: proceed instead of failing
                        print(f"[{account}] (không thấy option 2.0 trong menu, dùng mặc định)", flush=True)
                    await page.wait_for_timeout(500)
                except RuntimeError:
                    raise
                except Exception as e:
                    if want_25:
                        raise RuntimeError(f"Không chọn được Seedance 2.5: {str(e)[:120]}") from e
                    print(f"[{account}] (bỏ qua chọn model, dùng mặc định 2.0: {str(e)[:80]})", flush=True)
            if ratio:
                try:
                    await page.click("text=比率", timeout=3000)
                    await page.wait_for_timeout(500)
                    await page.click(f"text={ratio}", timeout=3000)
                except Exception as e:
                    print(f"  (Failed to set ratio, using default: {str(e)[:80]})", flush=True)
            if duration and duration != 10:
                # 10s is Dola's default; only 15/30 need an explicit chip. Verify the chip is
                # actually SELECTED — silently falling back to 10s produced wrong-length videos.
                selected = await _select_duration_chip(page, duration)
                if not selected:
                    await page.screenshot(path="no_duration.png")
                    raise RuntimeError(
                        f"Không chọn được thời lượng {duration}s trên giao diện (chip 30s chưa mở khoá / bấm trượt). "
                        f"Dùng chế độ nhanh (fetch) cho {duration}s, hoặc bật DOLA_SKILLPACK_HIJACK=1.")
            # A ratio/duration click can open a Radix dropdown without selecting; the open
            # menu then sits over the composer and the textarea click times out.
            if await page.locator('[data-slot="dropdown-menu-item"]').count():
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(300)

            # Session can die while we prepare the composer; surface it as a login problem
            # so the pool disables the nick instead of logging a generic error.
            if await _is_logged_out(page, context):
                raise LoggedOutError(_LOGOUT_MSG)

            box = await page.query_selector("textarea") or await page.query_selector('[contenteditable="true"]')
            if not box:
                await page.screenshot(path="no_composer.png")
                raise RuntimeError("Khung soạn video không mở được (kiểm tra tài khoản / thử lại)")

            try:
                try:
                    await box.click(timeout=2500)
                except Exception:
                    await box.focus()
                await page.keyboard.insert_text(prompt)   # dán nguyên khối (prompt dài không còn mất ~1 phút gõ)
                await page.wait_for_timeout(600)
                await page.keyboard.press("Enter")
            except Exception:
                await page.screenshot(path="ui_fail.png")
                raise
            print(f"[{account}] UI submitted prompt: {prompt[:40]}", flush=True)
            if on_submitted:
                on_submitted(account, True)
            await page.wait_for_timeout(1500)
            if await _is_logged_out(page, context):
                raise LoggedOutError(_LOGOUT_MSG)

            # ---- Captcha Solver (up to 3 attempts) ----
            solved_or_absent = False
            for attempt in range(1, 4):
                frame = None
                for _ in range(20):
                    await page.wait_for_timeout(1000)
                    frame = find_captcha_frame(page)
                    if frame:
                        break
                if not frame:
                    solved_or_absent = True
                    break
                print(f"[{account}] Captcha detected, attempt {attempt} solving...", flush=True)
                if await solve_slider(page, frame, attempt):
                    print(f"[{account}] Captcha passed ✓", flush=True)
                    await page.wait_for_timeout(3000)  # Wait for frontend auto-retry
                    solved_or_absent = True
                    break
                print(f"[{account}] Captcha not passed, retrying...", flush=True)
            if not solved_or_absent:
                await page.screenshot(path="solve_fail.png")
                raise RiskControlError("Captcha failed 3 times")

            # ---- Wait for real conversation_id ----
            conv_id = ""
            for _ in range(30):
                await page.wait_for_timeout(1000)
                if await _is_logged_out(page, context):
                    raise LoggedOutError(_LOGOUT_MSG)
                tail = page.url.rstrip("/").split("/")[-1]
                if tail.isdigit():
                    conv_id = tail
                    break
            if not conv_id:
                await page.screenshot(path="no_conv.png")
                raise TimeoutError("conversation_id not acquired within 30s")
            print(f"[{account}] conversation_id={conv_id}, polling for video...", flush=True)

            deadline = time.time() + timeout
            if on_conversation_id:
                on_conversation_id(account, conv_id, deadline)
            return await poll_conversation(account, page, context, conv_id, timeout, on_poll, on_balance, ratio, duration)
        finally:
            await _persist_before_close(context, account)
            await context.close()


async def _main():
    account = sys.argv[1] if len(sys.argv) > 1 else "acc1"
    prompt = sys.argv[2] if len(sys.argv) > 2 else "An orange cat napping on a sunny windowsill"
    ratio = sys.argv[3] if len(sys.argv) > 3 else None
    duration = int(sys.argv[4]) if len(sys.argv) > 4 else None
    model = sys.argv[5] if len(sys.argv) > 5 else "seedance_v2.0"
    result = await generate_video(account, prompt, ratio, duration, model=model)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except Exception:
        import traceback
        traceback.print_exc()