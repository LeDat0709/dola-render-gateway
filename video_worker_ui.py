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
from browser import (PASSPORT_INFO_PATH, RegionBlockedError, cookie_value, passport_dead, launch_account_context, page_region_blocked,
                     pin_session_cookies, region_blocked_message)
from dola_client import CREDIT_FAIL_PATTERN, CreditError
from video_worker import (POLL_JS, SUBMIT_JS, DownloadError, RateLimitedError, RiskControlError, SubmitDelivered,
                          SubmitRejected, _check_submit, _download, extract_unwatermarked_url)

# Daily limit pattern matching response text (JA / ZH / EN / VI)
# Dola không hiểu prompt (vd prompt "con mefo"): "意味不明なため直接生成できません". Phải xét TRƯỚC
# CREDIT_FAIL_PATTERN — chữ "生成できません" trong câu này từng bị bắt thành "hết điểm" → nick hết lượt oan (log 11/9 16:51).
# Mốc thời gian trong prompt: "0–3.5秒", "25-30秒", "30s", "8 giây". Nhóm: 1=đầu, 2=dấu nối, 3=cuối, 4=đơn vị.
_TIME_MARK = re.compile(
    r"(\d+(?:[.,]\d+)?)(?:(\s*[–\-~〜至到]\s*)(\d+(?:[.,]\d+)?))?\s*(秒|giây|sec(?:ond)?s?\b|s\b)", re.IGNORECASE)


def _fmt_sec(v: float) -> str:
    return str(int(round(v))) if abs(v - round(v)) < 0.05 else f"{v:.1f}"


def fit_prompt_to_duration(prompt: str, duration, account: str = "") -> str:
    """Prompt mô tả dài hơn thời lượng chọn (mốc "25–30秒" mà chọn 10s) → co mọi mốc về thang 0..duration.

    Đây là nguồn của 369 vòng "Dola hỏi lại thời lượng" trong log 11/9: Dola đọc kịch bản 30s, tưởng
    xin 30s, hỏi "15s nhé?" rồi vòng vo 1–2 phút. Mốc khớp thời lượng thì Dola hết cớ hỏi.
    """
    if not prompt or not duration:
        return prompt
    marks = list(_TIME_MARK.finditer(prompt))
    if not marks:
        return prompt
    top = max(float((m.group(3) or m.group(1)).replace(",", ".")) for m in marks)
    if top <= float(duration) * 1.2:
        return prompt
    f = float(duration) / top

    def repl(m):
        a = _fmt_sec(float(m.group(1).replace(",", ".")) * f)
        if m.group(3):
            return f"{a}{m.group(2)}{_fmt_sec(float(m.group(3).replace(',', '.')) * f)}{m.group(4)}"
        return f"{a}{m.group(4)}"

    print(f"[{account}] prompt mô tả ~{_fmt_sec(top)}s → co mốc thời gian về {duration}s", flush=True)
    return _TIME_MARK.sub(repl, prompt)


# "現在のパラメーターで生成すると、4動画クレジットが使用されます。本日は残り2のみです" → (cần 4, còn 2).
_NEED_RE = re.compile(r"(\d+)\s*(?:動画クレジット|クレジット|credits?|积分|视频点数|điểm|lượt)", re.IGNORECASE)
_LEFT_RE = re.compile(r"(?:残り|remaining|left|剩余|còn(?: lại)?)\s*[:：]?\s*(\d+)|(\d+)\s*(?:credits?|クレジット)?\s*(?:remaining|left)",
                      re.IGNORECASE)


def _parse_credit_need(text: str):
    need = _NEED_RE.search(text or "")
    left = _LEFT_RE.search(text or "")
    return (int(need.group(1)) if need else None,
            int(left.group(1) or left.group(2)) if left else None)


def _param_change_error(text: str):
    err = ParameterChangeError(_CREDIT_SHORT_MSG + text[:150])
    err.need, err.left = _parse_credit_need(text)   # pool học chi phí (model, giây) + credit còn lại của nick
    return err


def _credits_used(text: str):
    """Dola báo giá lúc bắt đầu dựng ("…4動画クレジットを使用します") → 4. Câu số dư ("残り2") thì không."""
    t = text or ""
    low = t.lower()
    if not ("使用" in t or "will be used" in low or "will use" in low or "uses " in low):
        return None
    m = _NEED_RE.search(t)
    return int(m.group(1)) if m else None


async def _download_or_link(url: str, account: str, prompt: str = "") -> tuple[str | None, str | None]:
    """(đường dẫn file, lỗi). Tải hỏng sau mọi lần thử → (None, lỗi): job vẫn xong, giữ link CDN để tải tay."""
    try:
        local = await _download(url, account, prompt)
    except DownloadError as e:
        print(f"[{account}] {e}", flush=True)
        return None, str(e)
    print(f"[{account}] Downloaded {local} ({local.stat().st_size / 1e6:.1f} MB)", flush=True)
    return str(local), None


PROMPT_UNCLEAR_PATTERN = re.compile(
    r"意味不明|内容が不明|内容が不足|具体的に指定|指示内容として認識できません|有効な指示|プロンプトを補完|"
    r"无法理解|内容不明确|not a valid (?:prompt|instruction)|too vague|unclear prompt|"
    r"please (?:provide|specify) (?:a |the )?(?:video )?(?:content|description)",
    re.IGNORECASE,
)

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
    # Dola dựng xong rồi GIẤU video: "著作権を保護するため、生成された動画を表示できません" (không có chữ ポリシー → trước đây
    # không khớp mẫu nào, job treo tới hết 40 phút).
    r"著作権|動画を表示できません|版权保护|无法显示.{0,6}视频|copyright|"
    # Dola bảo SỬA PROMPT rồi thử lại = lượt này đã thua, chờ tiếp chỉ treo job.
    r"プロンプトを(?:編集|変更|修正)|修改提示词|(?:edit|modify|change) (?:the |your )?prompt|"
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
# Câu Dola trả cho nick KHÁCH (cookie chết) — phải bắt TRƯỚC CREDIT_FAIL_PATTERN: 「生成できません」 trong câu này
# từng bị hiểu là "hết điểm" → ghi nick 0 credit oan, còn cookie chết thì không ai biết.
GUEST_REFUSAL_PATTERN = re.compile(
    r"ゲスト.{0,20}(生成|作成|利用).{0,10}(できません|できない)|ログインしてください|请先登录|請先登入|"
    r"guests? (can ?not|can't|are not allowed)|log ?in to (start|create|generate)", re.IGNORECASE)
_GUEST_MSG = ("Cookie hết hạn — Dola coi nick là KHÁCH, không tạo được video (không trừ lượt). "
              "Đăng nhập lại nick này.")
_LOGOUT_MSG = ("Bị đăng xuất khỏi Dola giữa chừng (mất phiên đăng nhập) — cookie tài khoản đã chết. "
               "Hãy đăng nhập lại / thêm cookie mới cho tài khoản này.")

# Progress chatter Dola emits while rendering; never a reason to fail fast.
_STATUS_MARKERS = (
    "生成された動画", "動画が生成", "生成中", "作成中", "レンダリング", "処理中", "待って", "生成しています",
    "アップロード", "%", "generating", "rendering", "uploading", "đang tạo", "đang tải", "生成されます",
    "完成します", "完成予定", "お送りします", "完了したら", "クレジットを使用", "分後に", "只需", "分钟后",
    "will be generated", "will complete", "will send", "i'll send", "i'll start", "minutes", "残っています",
    # "安全チェックの対象外です。直接生成を開始します" = Dola BẮT ĐẦU tạo — từng bị coi là từ chối, job chết sau 20s
    "生成を開始", "開始します", "安全チェックの対象外", "start generating", "starting generation", "开始生成",
    # "動画の生成を受け付けました。" / "動画を生成しました。" = Dola NHẬN lệnh / báo đang ra; video tới sau vài phút
    # (log 17/9 08:28: 2 job chết oan "Dola báo: …" sau 20s dù lượt đã trừ).
    "受け付けました", "受け付けいたしました", "生成しました", "作成しました",
    # Dola BÁO là đang/sắp tạo bằng câu kể (không hỏi gì): "…15秒で生成します", "4秒のフック版をまず生成します",
    # "完了し次第、お知らせします". Thiếu mấy mẫu này thì sau 4 nhịp poll job bị kết luận "Dola báo: …" = LỖI,
    # trong khi Dola vẫn dựng và LƯỢT ĐÃ TRỪ (ảnh chụp 16/9: 3–5 nick lỗi oan kiểu này).
    "生成します", "生成いたします", "作成します", "作成いたします", "生成を進め", "生成に入り",
    "完了し次第", "お知らせします", "お知らせいたします", "will generate", "i'll generate", "让我生成", "为您生成",
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


class GuestRefusedError(LoggedOutError):
    """Dola đáp 「ゲストは動画と画像を生成できません」: cookie chết, Dola coi nick là KHÁCH. Khách không có credit nên
    CHẮC CHẮN không bị trừ lượt dù đã có conversation_id → pool được xoay sang nick khác (not_charged)."""
    not_charged = True


class ContentPolicyViolationError(Exception):
    """Prompt or reference image violated Dola content policy."""


class PortraitProtectionError(Exception):
    """Portrait / identity protection triggered."""


class PromptUnclearError(Exception):
    """Dola không hiểu prompt — lỗi của prompt, không phải của nick: không xoay, không trừ lượt."""


class ParameterChangeError(Exception):
    """Parameters rejected or need changes."""


class TransientDolaError(Exception):
    """Dola returned a transient/system error asking to retry (not an account fault)."""


PASSPORT_JS = """
async (path) => {
  try {
    const r = await fetch(path, {credentials: "include", headers: {Accept: "application/json"}});
    return await r.json();
  } catch (e) { return null; }
}
"""


async def _is_logged_out(page, context, deep: bool = False) -> bool:
    """True when Dola dropped the session (server-side logout, dead cookie, login wall).

    A stale sessionid can survive a server-side logout, so cookie presence alone is not
    proof; the ?from_logout redirect, a vanished sessionid, or login CTA text all count.
    deep=True (TRƯỚC khi gửi): hỏi thêm passport ngay trong trang — trang KHÁCH không có chữ "đăng nhập để…"
    nào khớp _LOGOUT_TEXTS (ảnh 15/09 chỉ có nút "Đăng nhập") nên từng lọt tới lúc gửi prompt.
    """
    if deep:
        try:
            data = await asyncio.wait_for(page.evaluate(PASSPORT_JS, PASSPORT_INFO_PATH), timeout=10)
            if passport_dead(data):
                return True
        except Exception:
            pass
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


# Câu tool TỰ TRẢ LỜI menu thông số (_spec_menu_answer): "A、15秒、9:16（縦向き）でお願いします。", "…秒に変更して生成してください。"
_OWN_ANSWER_TAILS = ("でお願いします。", "に変更して生成してください。")


def _is_own_message(text: str) -> bool:
    """Tin DO TOOL GỬI (prompt, câu trả lời menu) — không bao giờ là câu Dola nói.

    Lịch sử hội thoại trả CẢ tin người dùng. Log 17/9 08:27: prompt "生成された動画："15.8s…0–1.5秒…"" bị đọc như
    Dola hỏi thời lượng → mở Chrome trả lời câu hỏi không có thật, lấy "5秒" trong prompt làm mức giây → tự chọn
    "5秒に変更"; câu tool trả lời "A、15秒…でお願いします。" bị báo "Dola báo: …" → job chết oan (lượt đã trừ)."""
    t = (text or "").strip()
    return (any(k in t for k in _OWN_MESSAGE_MARKS) or t.startswith(_SENT_PREFIX)
            or (len(t) < 60 and t.endswith(_OWN_ANSWER_TAILS)))


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


async def _goto_dola(page, url: str, attempts: int = 3, account: str = ""):
    """page.goto có thử lại khi lỗi mạng chớp nhoáng (mất mạng/proxy blip) → job không chết vì 1 nhịp rớt.

    Mở xong thì soi ngay màn "Dola không khả dụng ở khu vực này" (proxy thoát sai nước / không proxy):
    trang đó không có khung chat nên nếu để chạy tiếp sẽ ra lỗi lạc đề "không thấy nút Tạo video".
    """
    for i in range(1, attempts + 1):
        try:
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            if await page_region_blocked(page):
                raise RegionBlockedError(region_blocked_message(account))
            return
        except RegionBlockedError:
            raise
        except Exception as e:
            msg = str(e)
            # goto quá 60s (proxy/mạng chậm, trang chưa tải xong) = lỗi TẠM THỜI, CHƯA gửi gì, CHƯA có
            # conversation_id. Trước đây raise nguyên TimeoutError → pool tưởng "job đã tạo hội thoại" nên
            # _claim đốt 1 lượt ngày và không xoay nick. Gộp vào nhóm mạng để thử lại rồi báo transient.
            is_timeout = "Timeout" in msg or "timeout" in msg
            if any(n in msg for n in _NET_ERRORS) or is_timeout:
                if i < attempts:
                    print(f"  Mở Dola chậm/lỗi mạng ({msg[:50]}), thử lại {i}/{attempts - 1}...", flush=True)
                    await asyncio.sleep(3)
                    continue
                raise TransientDolaError(
                    "Không tải được trang Dola (proxy/mạng quá chậm) — CHƯA gửi gì nên CHƯA tốn credit. "
                    f"Đã thử {attempts} lần; đổi proxy nhanh hơn cho nick rồi chạy lại.") from e
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


def _effective_duration(want: int | None, capped: int | None) -> int | None:
    """Dola nói "chỉ tới N giây": job xin NHIỀU hơn thì hạ xuống N; job xin ÍT hơn thì giữ nguyên.

    Trước đây job 10s bị nâng lên 15s → Dola làm 15s → tính credit 15s → "không đủ lượt"
    (log 11/9 15:49: 23 lần). Prompt mô tả 30s nên Dola tưởng xin 30s, nhưng job vẫn là 10s.
    """
    if capped and (not want or want > capped):
        return capped
    return want


def _capped_reply(text: str, ratio, want: int | None) -> str:
    """Câu trả lời khi Dola chặn thời lượng dài và mời mức tối đa (vd 15s).

    Xin >= mức đó → 'はい' (nhận 15s). Xin ít hơn → nói rõ số giây + tỉ lệ mình muốn.
    """
    capped = _capped_seconds(text)
    if capped and want and want < capped:
        return _spec_menu_answer(text, ratio, want)
    return "はい"


# Câu có chữ PHỦ ĐỊNH thì không bao giờ là "đang dựng", dù chứa marker: "…生成された動画を表示できません" (chặn bản quyền,
# ảnh 16/9) dính marker 生成された動画 → nhánh "câu lạ lặp lại → báo lỗi" bỏ qua, job treo tới hết 40 phút.
_REFUSAL_MARKERS = ("できません", "できない", "できませんでした", "无法", "不能", "cannot", "can't", "unable to", "không thể")


def _is_status_text(text: str) -> bool:
    low = (text or "").lower()
    if any(m in text or m in low for m in _REFUSAL_MARKERS):
        return False
    return any(m in text or m in low for m in _STATUS_MARKERS)


VIDEO_BTN_ALTS = ("動画を作成", "動画生成", "動画", "Tạo video", "Create video", "生成视频", "视频生成", "Video")  # action-bar entry
_CONSENT_LABELS = ("OK", "Đồng ý", "同意する", "Accept", "Chấp nhận", "同意")        # cookie banner steals the first click
_NEW_CHAT_LABELS = ("新しいチャット", "New chat", "Cuộc trò chuyện mới", "新对话", "新規チャット")
ENTRY_ATTEMPTS = 15
CAPTCHA_FRAME_KEY = "bdcaptcha.html"   # Captcha verifycenter iframe


async def _click_first_visible(page, labels, exact: bool = True, timeout: int = 3000) -> bool:
    """Bấm nhãn đầu tiên thấy được. Thử khớp CHÍNH XÁC trước rồi GẦN ĐÚNG (nút có icon/khoảng trắng
    hoặc chữ nằm trong span con thì exact=True trượt); nếu phần tử text không bấm được thì bấm nút
    tổ tiên gần nhất (button/[role=button]) — Dola hay bọc chữ trong span không nhận click."""
    modes = [True, False] if exact else [False]
    for want_exact in modes:
        for label in labels:
            try:
                loc = page.get_by_text(label, exact=want_exact).first
                if not (await loc.count()) or not (await loc.is_visible()):
                    continue
                try:
                    await loc.click(timeout=timeout)
                except Exception:
                    btn = loc.locator("xpath=ancestor-or-self::*[self::button or @role='button'][1]").first
                    if await btn.count():
                        await btn.click(timeout=timeout)
                    else:
                        await loc.evaluate("e => e.click()")
                return True
            except Exception:
                continue
    return False


async def _open_video_composer(page) -> None:
    """Dismisses the cookie banner, starts a fresh chat and opens the video card.

    The action bar with 動画を作成 only renders on an empty new chat, and the consent
    banner swallows the first click, so both steps are required (Dola UI, 2026-09).
    """
    if await _click_first_visible(page, _CONSENT_LABELS, exact=False):
        await page.wait_for_timeout(500)
    if await _click_first_visible(page, _NEW_CHAT_LABELS, exact=False):
        await page.wait_for_timeout(1200)
    for i in range(ENTRY_ATTEMPTS):
        if await _click_first_visible(page, VIDEO_BTN_ALTS, exact=False, timeout=4000):
            await page.wait_for_timeout(1500)
            return
        # Action bar chỉ hiện trên chat trống — thử mở cuộc trò chuyện mới lại giữa chừng nếu bị kẹt.
        if i in (4, 9):
            await _click_first_visible(page, _NEW_CHAT_LABELS, exact=False)
        await page.wait_for_timeout(1500)
    try:
        await page.screenshot(path="no_entry_btn.png")
    except Exception:
        pass
    raise RuntimeError("Không mở được khung tạo video trên Dola (nút '動画を作成/Tạo video' không thấy — "
                       "Dola có thể đang bắt xác minh/đổi giao diện, hoặc chat chưa về trạng thái trống).")


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
  if (!r.ok) return null;
  const d = await r.json();
  const cells = (((d.downlink_body||{}).pull_recent_conv_chain_downlink_body)||{}).cells || [];
  return cells.map(c => String((c.conversation||{}).conversation_id || c.id || "")).filter(Boolean);
}
"""


# Giãn nhịp CHUNG cho mọi proxy (config.SUBMIT_GAP_GLOBAL_SEC): mỗi lần gửi thật lấy một "khe" cách khe trước. Không có
# await giữa đọc và ghi khe nên nguyên tử trong asyncio, khỏi cần Lock (Lock dùng lại qua nhiều event loop hay vỡ).
_GLOBAL_SUBMIT = {"slot": 0.0}


async def _global_submit_gate(account: str) -> None:
    """Gọi NGAY TRƯỚC khi lệnh rời máy (mọi đường: HTTP, fetch, giao diện, kể cả gửi lại bên trong). Chờ tới khe của mình."""
    gap = config.SUBMIT_GAP_GLOBAL_SEC
    if gap <= 0:
        return
    now = time.monotonic()
    base = max(now, _GLOBAL_SUBMIT["slot"])
    _GLOBAL_SUBMIT["slot"] = base + gap + random.uniform(0, config.SUBMIT_JITTER_GLOBAL_SEC)
    wait = base - now
    if wait > 0.5:
        print(f"[{account}] giãn nhịp chung: chờ {wait:.1f}s rồi mới gửi (tránh nhiều nick gửi cùng lúc)", flush=True)
    if wait > 0:
        # ponytail: thời gian chờ ở đây tính vào đồng hồ chống treo trước khi gửi (PRESUBMIT_TIMEOUT_SEC=300s) — ổn với
        # ≤ vài chục job cùng lúc; hàng trăm job/lượt thì cần tạm dừng đồng hồ như _presubmit_guard._hold.
        await asyncio.sleep(wait)


async def _recent_conv_ids_http(account: str, proxy) -> set | None:
    """Tập conversation_id gần đây của nick, đọc bằng HTTP qua đúng proxy. None = không đọc được (KHÔNG phải tập rỗng)."""
    try:
        cookie, ms_token, fp = _account_cookies(account)
    except Exception:  # noqa: BLE001
        return None
    async with aiohttp.ClientSession() as session:
        convs = await _recent_conversations(session, cookie, ms_token, fp, 20, proxy)
    return None if convs is None else {c["conversation_id"] for c in convs}


async def _new_conv_after_http(account: str, proxy, before: set | None, tries: int) -> tuple[str, bool]:
    """Như _new_conv_after nhưng đọc bằng HTTP (engine không-Chrome). verified=True chỉ khi chụp được TRƯỚC khi gửi và
    MỌI lần dò đều đọc được — lúc đó "không thấy" mới chứng minh Dola chưa nhận lệnh."""
    if before is None:
        return "", False
    verified = True
    for _ in range(tries):
        await asyncio.sleep(2)
        cur = await _recent_conv_ids_http(account, proxy)
        if cur is None:
            verified = False
            continue
        new = cur - before
        if new:
            return max(new, key=int), True
    return "", verified


async def _recent_conv_ids(page, ms_token: str, fp: str) -> set | None:
    """Hội thoại gần đây của nick. None = KHÔNG dò được (HTTP/mạng lỗi) — khác set() rỗng của nick mới:
    trừ với set() rỗng giả sẽ ra cả 20 hội thoại CŨ → job nhận video cũ như thành công."""
    try:
        ids = await asyncio.wait_for(
            page.evaluate(RECENT_CONV_IDS_JS, {"msToken": ms_token, "fp": fp}), timeout=20)
    except Exception:
        return None
    return None if ids is None else {i for i in ids if i.isdigit()}


# Dò hội thoại mới sau lần gửi không rõ kết quả: 6 × 2s = cửa sổ Dola cần để hiện hội thoại vừa tạo.
_PROBE_TRIES = 6


async def _new_conv_after(page, ms_token: str, fp: str, before: set | None, tries: int) -> tuple[str, bool]:
    """(conversation_id Dola vừa tạo, verified). verified=True chỉ khi MỌI lần dò đều thành công — lúc đó
    "không thấy" mới chứng minh Dola CHƯA nhận lệnh; dò hỏng / thiếu ảnh chụp trước khi gửi → không kết luận."""
    if before is None:
        return "", False
    verified = True
    for _ in range(tries):
        try:
            await page.wait_for_timeout(2000)
        except Exception:
            return "", False              # trang đóng giữa lúc dò
        cur = await _recent_conv_ids(page, ms_token, fp)
        if cur is None:
            verified = False
            continue
        new = cur - before
        if new:
            return max(new, key=int), True   # id số: so theo số, không theo chuỗi
    return "", verified


def _delivered_err(why: str) -> _FetchDelivered:
    return _FetchDelivered(
        f"Gửi lệnh không rõ kết quả ({why[:120]}) — không xác nhận được Dola đã nhận hay chưa, KHÔNG gửi lại "
        "để tránh trừ lượt 2 lần. Kiểm tra dola.com; chưa có video thì chạy lại.")


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
    await _global_submit_gate(account)   # giãn nhịp chung, ngay trước lúc gửi thật (cả lần gửi lại "lần 2")
    before = await _recent_conv_ids(page, ms_token, fp)
    args = {"prompt": prompt, "ratio": ratio or "", "duration": duration, "model": model_key,
            "query": query, "ackIdleMs": FETCH_ACK_IDLE_MS}
    if on_submitted:
        on_submitted(account, True)   # từ đây trở đi lệnh có thể đã tới Dola → restart không được gửi lại
    try:
        result = await asyncio.wait_for(
            page.evaluate(SUBMIT_JS, args, isolated_context=False), timeout=FETCH_SUBMIT_TIMEOUT_SEC)
    except asyncio.TimeoutError as exc:
        # Hết giờ chờ evaluate: lệnh CÓ THỂ đã tới Dola và bị trừ lượt. Dò hội thoại mới trước; không
        # thấy thì KHÔNG cho gửi lại (log 11/9: gửi lần 2 = trừ lượt 2 lần).
        conv_id, _ = await _new_conv_after(page, ms_token, fp, before, 3)
        if conv_id:
            print(f"[{account}] submit quá giờ nhưng Dola đã nhận; conversation_id={conv_id}", flush=True)
            return conv_id
        raise _delivered_err(f"quá {FETCH_SUBMIT_TIMEOUT_SEC}s không có phản hồi") from exc
    except Exception as exc:
        # evaluate lỗi SAU on_submitted(True): fetch có thể đã gửi xong mà stream đứt ('TypeError: network
        # error'). Trước đây coi là "chưa gửi" → thử lại/UI = trừ lượt 2 lần. Chỉ gửi lại khi dò CHẮC CHẮN.
        return await _recover_or_raise(page, account, ms_token, fp, before, exc, on_submitted)
    try:
        return _check_submit(result)
    except SubmitRejected as exc:
        status = int(result.get("status") or 0)
        if 400 <= status < 500 and status != 408:
            # WAF/cookie/proxy chặn ở cửa (4xx): Dola chưa nhận, chưa trừ lượt → thử lại như lỗi mạng,
            # KHÔNG cho nick nghỉ 30 phút (trước đây cả loạt nick bị "risk-control" oan vì 1 lỗi mạng).
            if on_submitted:
                on_submitted(account, False)
            raise _FetchSubmitFailed(f"Dola từ chối lệnh ({exc}) — kiểm tra mạng/proxy/cookie") from exc
        # 5xx/408/0: gateway có thể lỗi SAU khi đã chuyển lệnh vào Dola → dò rồi mới được gửi lại.
        return await _recover_or_raise(page, account, ms_token, fp, before, exc, on_submitted)
    except RiskControlError as exc:
        # 710022002/710022004 (gồm RateLimitedError) không có convId: Dola từ chối rõ. Dò cho chắc — chỉ báo
        # False (pool được xoay nick/IP) khi dò đủ mà không thấy; dò hỏng thì giữ cờ đã gửi, không gửi lại.
        conv_id, verified = await _new_conv_after(page, ms_token, fp, before, _PROBE_TRIES)
        if conv_id:
            return conv_id
        if not verified:
            raise _delivered_err(str(exc)) from exc
        if on_submitted:
            on_submitted(account, False)
        raise
    except SubmitDelivered:
        # Delivered but no id parsed: find the conversation Dola just created (do NOT re-submit).
        conv_id, _ = await _new_conv_after(page, ms_token, fp, before, _PROBE_TRIES)
        if conv_id:
            print(f"[{account}] fetch delivered; recovered conversation_id={conv_id}", flush=True)
            return conv_id
        # Couldn't confirm — raise WITHOUT allowing a UI re-submit (guards against double charge).
        raise _FetchDelivered(
            "Đã gửi lệnh tạo video tới Dola nhưng không xác nhận được — KHÔNG gửi lại để tránh trừ lượt 2 lần. "
            "Kiểm tra tài khoản trên dola.com; nếu chưa có video, thử lại.")


async def _recover_or_raise(page, account: str, ms_token: str, fp: str, before, exc, on_submitted) -> str:
    """Lệnh CÓ THỂ đã tới Dola mà không có conversation_id. Thấy hội thoại mới → dùng nó; dò đủ mà không
    thấy → Dola chưa nhận → báo False + _FetchSubmitFailed (gửi lại an toàn); dò hỏng → _FetchDelivered."""
    conv_id, verified = await _new_conv_after(page, ms_token, fp, before, _PROBE_TRIES)
    if conv_id:
        print(f"[{account}] gửi lệnh lỗi ({str(exc)[:80]}) nhưng Dola đã nhận; conversation_id={conv_id}", flush=True)
        return conv_id
    if not verified:
        raise _delivered_err(str(exc) or type(exc).__name__) from exc
    if on_submitted:
        on_submitted(account, False)
    raise _FetchSubmitFailed(f"Dola chưa nhận lệnh ({str(exc)[:160]})") from exc


async def _generate_via_http(account: str, prompt: str, ratio: str | None, duration: int,
                             model_key: str, timeout: int, on_conversation_id, on_poll, on_balance,
                             on_submitted=None, on_browser_free=None, on_browser_hold=None) -> dict:
    """Gửi KHÔNG mở Chrome: ký a_bogus bằng Python (submit_http) rồi theo dõi + tải bằng HTTP thuần.

    Nhẹ RAM, mở nick nhanh (không launch Chrome mỗi nick) — như đối thủ. Chỉ dùng khi cookies.json còn sống;
    Dola từ chối chắc chắn (SubmitHttpRejected) → nổi lên để generate_video rơi về đường fetch (Chrome). Dola hỏi
    lại (30s/thông số) mà HTTP không trả lời được → mở Chrome bằng resume_video, giữ conversation_id (không gửi lại)."""
    from submit_http import SubmitHttpRateLimited, submit_via_http
    from browser import account_proxy_url
    # Lấy IP proxy lỗi (chưa whitelist, key đang chờ, nhà bán lỗi…) → ĐỂ LỖI NỔI LÊN, KHÔNG gửi thẳng. Trước đây gửi
    # với proxy=None: giao diện vẫn hiện IP proxy (bộ nhớ đệm) mà lệnh thật đi từ IP MÁY → nhiều nick dồn chung một IP
    # → 710022002 hàng loạt, lộ IP thật. Lúc này CHƯA gửi gì (chưa trừ lượt) nên pool chuyển nick an toàn — đúng giao
    # kèo của browser.account_proxy ("không lặng lẽ rơi về IP máy").
    proxy = account_proxy_url(account) or None   # đã gồm proxy chung; "" chỉ khi nick không khai proxy nào
    if on_browser_free:
        on_browser_free()   # không giữ slot Chrome nào cả → trả ngay cho nick khác
    await _global_submit_gate(account)   # giãn nhịp chung cho mọi proxy, ngay trước lúc gửi thật (đã nhả slot Chrome)
    import browser as _browser
    why = await _browser.probe_proxy_tunnel(proxy)   # proxy có mở được đường tới Dola không — CHƯA gửi gì
    if why:
        # Hay gặp nhất: IP máy vừa đổi (VPN/mạng động) nên nhà bán cắt kết nối → khai lại whitelist rồi thử một lần nữa.
        if await asyncio.to_thread(_browser.refresh_proxy_whitelist, account):
            proxy = account_proxy_url(account) or None
            why = await _browser.probe_proxy_tunnel(proxy)
        if why:
            import proxyxoay
            if _browser._effective_rotating(account) and await asyncio.to_thread(proxyxoay.machine_ip_unstable):
                raise RuntimeError(
                    f"Proxy của nick {account} từ chối kết nối ({why}) — CHƯA gửi lệnh, không mất lượt. IP máy đang ĐỔI THEO "
                    "TỪNG KẾT NỐI (thường do VPN) nên proxy xác thực theo whitelist IP máy (proxyxoay/proxy.vn/topproxy) không "
                    "dùng được. Tắt VPN, hoặc dùng proxy có user:pass / tmproxy.")
            raise RuntimeError(
                f"Proxy của nick {account} không mở được đường tới Dola ({why}) — CHƯA gửi lệnh, không mất lượt. Hay gặp "
                "khi IP máy vừa đổi (bật/tắt VPN, đổi mạng) mà nhà bán proxy chưa whitelist IP mới, hoặc proxy đã chết.")
    before = await _recent_conv_ids_http(account, proxy)   # để phân biệt 710022002 "chưa nhận" với "đã nhận"
    try:
        conv_id = await submit_via_http(account, prompt, ratio, duration, model_key, proxy, on_submitted=on_submitted)
    except SubmitHttpRateLimited as rl:
        if not rl.maybe_delivered:
            raise RateLimitedError(str(rl)) from rl   # 4xx: chắc chưa nhận, cờ đã hạ → pool chờ rồi thử lại
        conv_id, verified = await _new_conv_after_http(account, proxy, before, _PROBE_TRIES)
        if not conv_id:
            if not verified:
                raise RuntimeError(
                    "Dola báo 710022002 nhưng không dò được danh sách hội thoại — không chắc Dola đã nhận hay chưa, "
                    "KHÔNG gửi lại để tránh trừ lượt 2 lần. Xem dola.com của nick; chưa có video thì chạy lại.") from rl
            if on_submitted:
                on_submitted(account, False)   # dò đủ, không có hội thoại mới → CHẮC chưa nhận
            raise RateLimitedError(str(rl)) from rl
        print(f"[{account}] Dola báo 710022002 nhưng ĐÃ tạo hội thoại {conv_id} → theo dõi tiếp, không gửi lại", flush=True)
    print(f"[{account}] http submitted ({model_key} {duration}s {ratio or 'default'}) conversation_id={conv_id}", flush=True)
    deadline = time.time() + timeout
    if on_conversation_id:
        on_conversation_id(account, conv_id, deadline)
    cookie, ms_token, fp = _account_cookies(account)
    answered: set = set()
    try:
        return await poll_conversation_http(account, cookie, ms_token, fp, conv_id,
                                            timeout, on_poll, on_balance, answered=answered, prompt=prompt)
    except _NeedsBrowser as ask:
        # Dola hỏi lại (xác nhận 30s / menu thông số) — chỉ trang web ký được câu trả lời → mở Chrome, GIỮ conv_id.
        if _answer_key(ask.full) in answered:
            raise RuntimeError(
                "Dola hỏi đi hỏi lại cùng một câu — rút ngắn kịch bản cho khớp số giây."
                f"\n↳ Dola: {ask}") from ask
        print(f"[{account}] Dola hỏi lại → mở Chrome trả lời (không gửi lại): {ask}", flush=True)
        if on_browser_hold:
            await on_browser_hold()
        left = max(60, int(deadline - time.time()))
        return await resume_video(account, conv_id, left, on_poll=on_poll, on_balance=on_balance,
                                  ratio=ratio, duration=duration, answered=answered,
                                  on_browser_free=on_browser_free, on_browser_hold=on_browser_hold, prompt=prompt)


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
            await _goto_dola(page, "https://www.dola.com/chat", account=account)
            # Chờ trang tự gọi API để bắt device_id/web_id (nền ký fetch). Trang/proxy chậm có thể
            # cần hơn 5s → chờ tới ~20s thay vì cứng 5s (giảm _FetchSubmitFailed "device_id missing").
            for _ in range(20):
                await page.wait_for_timeout(1000)
                if "device_id" in captured:
                    break
            if await _is_logged_out(page, context, deep=True):
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
                                                answered=answered, prompt=prompt)
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
                                                        remaining, on_poll, on_balance, answered=answered, prompt=prompt)
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
                                              answered=answered, prompt=prompt)
            return await poll_conversation(account, page, context, conv_id, timeout, on_poll, on_balance, ratio, duration, prompt=prompt)
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


def _msg_index(msg: dict):
    """Số thứ tự tin nhắn Dola gắn (index), hoặc None nếu tin này không có."""
    v = msg.get("index")
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return float(v) if isinstance(v, str) and v.strip().lstrip("-").isdigit() else None


def _newest_first(messages: list) -> list:
    """Tin MỚI đứng trước. Cả việc lấy video (videos[0]) lẫn ghép prompt↔video (conversation_videos) đều dựa
    vào thứ tự này; hiện Dola trả sẵn đúng vậy nhưng đó là giả định NGẦM — Dola đổi thứ tự là mọi video đội
    nhầm prompt mà không ai thấy. Mọi tin có `index` thì tự sắp cho chắc; thiếu dù một tin thì giữ nguyên thứ
    tự Dola trả (không đoán = không làm xấu đi)."""
    idx = [_msg_index(m) for m in messages if isinstance(m, dict)]
    if len(idx) != len(messages) or any(i is None for i in idx):
        return messages
    return [m for _, m in sorted(zip(idx, messages), key=lambda p: p[0], reverse=True)]


# Tin KẾT QUẢ dựng video Dola đẩy về sau khi render xong (đọc thật 17/9 từ im/chain/single):
#   ra video      → send_scene "77", ext.ai_creation_res_code 0, có khối 2074
#   giấu bản quyền → send_scene "77", ext.ai_creation_res_code 710082022, KHÔNG có khối 2074
# Mã ≠ 0 ở tin 77 = lượt dựng đã THUA (credit đã trừ), chờ tiếp chỉ treo tới hết giờ. Không dựa vào câu chữ.
# KHÔNG dùng các cờ ở tin trả lời thường: 710082041 / is_creation_clarifying có cả ở câu hỏi thời lượng lẫn câu
# "bắt đầu dựng"; tin "tốn N credit" và tin "エラーが発生しました" mang cờ y hệt nhau.
CREATION_RESULT_SCENE = "77"
CREATION_POLICY_CODES = {710082022}


def _msg_ext(msg: dict) -> dict:
    ext = msg.get("ext")
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except json.JSONDecodeError:
            return {}
    return ext if isinstance(ext, dict) else {}


def _is_creation_result(msg: dict) -> bool:
    return str(msg.get("send_scene") or "") == CREATION_RESULT_SCENE and str(msg.get("user_type") or "") != "1"


def _creation_fail(msg: dict) -> dict | None:
    """{"code", "text"} nếu tin kết quả báo dựng THẤT BẠI (mã ≠ 0), không thì None."""
    raw = _msg_ext(msg).get("ai_creation_res_code")
    try:
        code = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if code == 0:
        return None
    return {"code": code, "text": str(msg.get("tts_content") or msg.get("brief") or "")[:200]}


def _creation_fail_error(fail: dict) -> Exception:
    """Lỗi cuối cùng cho lượt dựng đã thua. Lượt ĐÃ TRỪ → dùng loại lỗi không gửi lại."""
    tail = f"\n↳ Dola (mã {fail['code']}): {fail['text'][:170]}"
    if fail["code"] in CREATION_POLICY_CODES:
        return ContentPolicyViolationError(
            "Dola dựng xong nhưng GIẤU video (bản quyền / vi phạm chính sách) — đổi prompt." + tail)
    return RuntimeError("Dola dựng xong nhưng KHÔNG trả video." + tail)


def _parse_single(data: dict) -> dict:
    """Mirror of POLL_JS message parsing, in Python (texts / videos / videoModels / images)."""
    dl = (data.get("downlink_body") or {}).get("pull_singe_chain_downlink_body") or {}
    texts, videos, video_models, images = [], [], [], 0
    stream = []   # ("t", text) / ("v", url, model) theo đúng thứ tự Dola trả (tin MỚI đứng trước)
    creation_fail = None
    result_seen = False
    for msg in _newest_first(dl.get("messages") or []):
        if not result_seen and _is_creation_result(msg):
            result_seen = True   # chỉ tin kết quả MỚI NHẤT quyết định
            creation_fail = _creation_fail(msg)
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
                stream.append(("t", text[:600]))
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
                    model = (cre.get("video") or {}).get("video_model") or ""
                    video_models.append(model)
                    stream.append(("v", url, model))
    return {"texts": texts, "videos": videos, "videoModels": video_models, "images": images, "stream": stream,
            "creation_fail": creation_fail}


# Lỗi mạng LIÊN TIẾP khi theo dõi → coi như IP proxy chết giữa lúc Dola dựng (đã trừ lượt) → đổi đường đọc hội thoại.
POLL_NET_FAILS = 3


SCAN_MSG_LIMIT = 50   # quét lịch sử: hội thoại nhiều lượt thì 20 tin không đủ, video cũ rơi ra ngoài


def _single_request(cookie: str, ms_token: str, fp: str, conversation_id: str, limit: int = 20) -> tuple[dict, dict, dict]:
    """(headers, params, body) cho /im/chain/single — đọc hội thoại bằng cookie, không cần ký."""
    headers = {"Content-Type": "application/json; encoding=utf-8", "agw-js-conv": "str",
               "Accept": "*/*", "cookie": cookie, "Referer": f"https://www.dola.com/chat/{conversation_id}"}
    params = dict(_SINGLE_PARAMS)
    if ms_token:
        params["msToken"] = ms_token
    if fp:
        params["fp"] = fp
    params["web_tab_id"] = str(uuid.uuid4())
    body = {"cmd": 3100, "uplink_body": {"pull_singe_chain_uplink_body": {
        "conversation_id": conversation_id, "anchor_index": 9007199254740991,
        "conversation_type": 3, "direction": 1, "limit": limit, "ext": {},
        "filter": {"index_list": []}, "evaluate_ab_params": "", "evaluate_common_params": ""}},
        "sequence_id": str(uuid.uuid4()), "channel": 2, "version": "1"}
    return headers, params, body


async def _fetch_single(session, cookie: str, ms_token: str, fp: str, conversation_id: str, proxy,
                        limit: int = 20) -> dict | None:
    """Đọc hội thoại 1 lần qua HTTP (đi `proxy`) → dict như POLL_JS (texts/videos/images/videoModels); lỗi mạng/HTTP → None.
    proxy == PROXY_PENDING (chưa lấy được IP proxy) → None ngay, KHÔNG gửi request nào."""
    if proxy == PROXY_PENDING:
        _LAST_FETCH_FAIL[conversation_id] = "pending"
        return None
    headers, params, body = _single_request(cookie, ms_token, fp, conversation_id, limit)
    got_response = False
    try:
        async with session.post(_SINGLE_URL, params=params, data=json.dumps(body), headers=headers,
                                proxy=proxy, timeout=aiohttp.ClientTimeout(total=30)) as r:
            got_response = True   # có trả lời HTTP = proxy còn sống (lỗi sau đây là của Dola, không phải proxy chết)
            if r.status != 200:
                print(f"  Polling http: HTTP {r.status}", flush=True)
                _LAST_FETCH_FAIL[conversation_id] = "http"
                return None
            out = _parse_single(await r.json(content_type=None))
        _LAST_FETCH_FAIL.pop(conversation_id, None)
        return out
    except Exception as e:  # noqa: BLE001
        print(f"  Polling exception (http): {str(e)[:120]}", flush=True)
        _LAST_FETCH_FAIL[conversation_id] = "http" if got_response else "net"
        return None


# Lần đọc hội thoại hỏng gần nhất của từng conversation_id: "net" = không nối được/đứt qua proxy (proxy có thể đã chết),
# "http" = Dola có trả lời (proxy sống), "pending" = chưa có IP proxy. Khoá theo conversation_id nên job song song không lẫn.
_LAST_FETCH_FAIL: dict[str, str] = {}


# Chưa lấy được IP proxy của nick: CHỜ rồi lấy lại, không đọc hội thoại. KHÁC None (None = nick không khai proxy nào).
PROXY_PENDING = "__proxy_pending__"


def _next_poll_proxy(account: str, current, proxy_dead: bool = False):
    """Đường đọc hội thoại kế tiếp khi đường hiện tại lỗi liên tiếp: LẤY LẠI IP proxy của nick (nhà bán có thể đã cấp IP
    mới; proxy riêng, không thì proxy chung). Không bao giờ rơi về IP máy khi nick đã khai proxy — trước đây bước cuối là
    "đi thẳng", nên giao diện hiện IP proxy mà Dola thấy IP máy đọc hội thoại của cả loạt nick. Lấy IP lỗi → PROXY_PENDING
    (vòng theo dõi chờ rồi thử lại tới hết giờ; video vẫn nằm trên Dola, vòng cứu video nhặt sau)."""
    from browser import account_proxy_url, rotate_dead_proxy
    if proxy_dead:
        # Lỗi KẾT NỐI liên tiếp = IP proxy xoay đã chết → xin IP MỚI thật (bộ nhớ đệm vẫn giữ cổng chết tới khi hết hạn).
        rotate_dead_proxy(account, f"{POLL_NET_FAILS} lần lỗi kết nối liên tiếp khi theo dõi")
    try:
        fresh = account_proxy_url(account) or None
    except Exception as e:  # noqa: BLE001
        print(f"[{account}] theo dõi: chưa lấy lại được IP proxy ({str(e)[:90]}) — chờ rồi thử lại, KHÔNG đọc bằng IP máy", flush=True)
        return PROXY_PENDING
    if fresh != current:
        shown = re.sub(r"//[^@/]+@", "//***@", fresh) if fresh else "đi thẳng (nick không khai proxy)"
        print(f"[{account}] theo dõi lỗi mạng {POLL_NET_FAILS} lần liên tiếp → đổi đường đọc hội thoại: {shown}", flush=True)
    return fresh


SCAN_CONCURRENCY = 4   # đọc song song ngần này hội thoại khi quét video nick


def _account_cookies(account: str) -> tuple[str, str, str]:
    """(cookie header, msToken, s_v_web_id) từ accounts/<nick>/cookies.json — đọc Dola bằng HTTP không cần mở Chrome."""
    f = config.ACCOUNTS_DIR / account / "cookies.json"
    if not f.exists():
        raise RuntimeError(f"Nick {account} chưa có cookies.json — đăng nhập lại nick rồi quét.")
    items = json.loads(f.read_text(encoding="utf-8"))
    cookie = "; ".join(f"{c['name']}={c['value']}" for c in items if c.get("name") and c.get("value"))
    pick = lambda n: next((c["value"] for c in items if c.get("name") == n and c.get("value")), "")
    return cookie, pick("msToken"), pick("s_v_web_id")


async def _recent_conversations(session, cookie: str, ms_token: str, fp: str, limit: int, proxy) -> list[dict] | None:
    """Hội thoại gần đây của nick (HTTP thuần): [{conversation_id, name, created_at}]; lỗi mạng/HTTP → None."""
    params = {**_SINGLE_PARAMS, "web_tab_id": str(uuid.uuid4())}
    if ms_token:
        params["msToken"] = ms_token
    if fp:
        params["fp"] = fp
    body = {"cmd": 3200, "uplink_body": {"pull_recent_conv_chain_uplink_body": {
        "limit": limit, "message_count_per_conv": 1, "api_version": 1, "conv_version": 0, "direction": 3,
        "option": {"not_need_message": True, "need_complete_conversation": True}}},
        "sequence_id": str(uuid.uuid4()), "channel": 2, "version": "1"}
    headers = {"Content-Type": "application/json; encoding=utf-8", "agw-js-conv": "str", "Accept": "*/*", "cookie": cookie}
    try:
        async with session.post("https://www.dola.com/im/chain/recent_conv", params=params, data=json.dumps(body),
                                headers=headers, proxy=proxy, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                return None
            data = await r.json(content_type=None)
    except Exception:  # noqa: BLE001
        return None
    cells = ((data.get("downlink_body") or {}).get("pull_recent_conv_chain_downlink_body") or {}).get("cells") or []
    out = []
    for c in cells:
        conv = c.get("conversation") or {}
        cid = str(conv.get("conversation_id") or c.get("id") or "")
        if cid.isdigit():
            ts = conv.get("create_time") or 0
            out.append({"conversation_id": cid, "name": str(conv.get("name") or "")[:120],
                        "created_at": int(ts) // 1000 if int(ts or 0) > 10**11 else int(ts or 0)})
    return out


# Prompt của một hội thoại khi tool KHÔNG còn job cho nó (job đã dọn, video tạo ở máy khác): đọc lại chính
# tin nhắn mình đã gửi. Dola đặt tên hội thoại là "動画生成リクエスト" cho mọi job nên tên KHÔNG dùng để gộp được.
_SENT_PREFIX = "生成された動画："
_RATIO_TAIL = re.compile(r"[、,]\s*\d{1,2}\s*[:：]\s*\d{1,2}\s*$")


def conversation_videos(poll: dict) -> list[dict]:
    """[{video_url, video_model, prompt}] cho MỌI video trong hội thoại, không chỉ cái mới nhất.

    Dola trả tin MỚI trước, nên prompt sinh ra một video là tin "đã gửi" đầu tiên đứng SAU nó. Nhờ vậy hội
    thoại có nhiều lượt (mỗi lượt một prompt khác) thì mỗi video mang đúng prompt của nó, thay vì tất cả
    đội chung prompt mới nhất. Không có stream (server cũ) → rơi về cách cũ: 1 video + prompt đầu tiên.
    """
    stream = poll.get("stream")
    if not stream:
        vids, models = poll.get("videos") or [], poll.get("videoModels") or []
        if not vids:
            return []
        return [{"video_url": extract_unwatermarked_url(models[0] if models else "", vids[0]),
                 "video_model": models[0] if models else "", "prompt": prompt_from_texts(poll.get("texts"))}]
    out = []
    for i, item in enumerate(stream):
        if item[0] != "v":
            continue
        after = [x[1] for x in stream[i + 1:] if x[0] == "t"]   # tin cũ hơn video này
        out.append({"video_url": extract_unwatermarked_url(item[2], item[1]), "video_model": item[2],
                    "prompt": prompt_from_texts(after) or prompt_from_texts(poll.get("texts"))})
    return out


def prompt_from_texts(texts) -> str:
    """Prompt đọc từ tin nhắn tool đã gửi: khan ("生成された動画：<prompt>、<tỉ lệ>") hoặc bản có chỉ thị
    ("【この仕様で…】\n<prompt>"). Không thấy → "" để người gọi rơi về nguồn khác."""
    for t in texts or []:
        tt = (t or "").strip()
        if tt.startswith(_SENT_PREFIX):
            return _RATIO_TAIL.sub("", tt[len(_SENT_PREFIX):]).strip()
        if _is_own_message(tt) and "】" in tt:
            return tt.split("】", 1)[1].strip()
    return ""


async def scan_account_videos(account: str, limit: int = 30) -> list[dict]:
    """CHECK VIDEO NICK (đối thủ v1.0.88 kho_nick): quét hội thoại gần đây của nick bằng cookie — CHỈ ĐỌC, không gửi tin,
    không tốn lượt — lấy các video đã dựng xong trên Dola, kể cả của job lỗi / quá giờ / IP chết lúc tải (đã trừ lượt).
    Trả [{conversation_id, name, created_at, video_url}] mới nhất trước. Đi đúng proxy của nick; proxy lỗi → nổi lỗi."""
    cookie, ms_token, fp = _account_cookies(account)
    from browser import account_proxy_url
    # Proxy nick lỗi → NỔI LỖI (giao diện báo, vòng cứu video thử lại mốc sau). Trước đây đọc bằng IP máy.
    proxy = account_proxy_url(account) or None
    async with aiohttp.ClientSession() as session:
        convs = await _recent_conversations(session, cookie, ms_token, fp, limit, proxy)
        if convs is None:
            raise RuntimeError("Không đọc được danh sách hội thoại của nick (cookie chết hoặc mất mạng).")
        sem = asyncio.Semaphore(SCAN_CONCURRENCY)

        async def one(conv) -> list[dict]:
            async with sem:
                poll = await _fetch_single(session, cookie, ms_token, fp, conv["conversation_id"], proxy,
                                           limit=SCAN_MSG_LIMIT)
            if not poll:
                return []
            return [{**conv, "video_url": v["video_url"], "prompt_seen": v["prompt"]}
                    for v in conversation_videos(poll)]

        found = [v for group in await asyncio.gather(*(one(c) for c in convs)) for v in group]
    return sorted(found, key=lambda v: v["created_at"], reverse=True)


async def poll_conversation_http(account: str, cookie: str, ms_token: str, fp: str,
                                 conversation_id: str, timeout: int, on_poll=None, on_balance=None,
                                 answered: set | None = None, prompt: str = "") -> dict:
    """Poll /im/chain/single over PLAIN HTTP (no browser) until a video appears, then download.

    The browser is only needed for the signed submission; polling + download work with cookies
    alone (verified). This frees the browser in ~5s instead of holding it open for the whole
    render (up to 30 min for 30s). Duration-confirm prompts can't be answered here (rare, since
    10/15/30 are all supported) — they surface as a clear error.
    """
    start = time.time()
    last_cb = 0.0
    last_msg = ""
    stale_msg, stale_n = "", 0
    image_polls = 0
    credits_used = None      # giá Dola báo lúc bắt đầu dựng → pool học giá + trừ credit nick
    answered = set() if answered is None else answered
    from browser import account_proxy_url
    try:
        poll_proxy = account_proxy_url(account) or None   # đúng proxy nick (riêng, không thì chung), như lúc gửi
    except Exception as e:  # noqa: BLE001
        # SAU khi gửi (đã trừ lượt): KHÔNG làm hỏng job (người dùng bấm chạy lại = trừ lượt 2 lần), nhưng cũng KHÔNG đọc
        # bằng IP máy như trước. Chờ rồi lấy lại IP (_next_poll_proxy sau POLL_NET_FAILS nhịp); hết giờ → quá giờ, vòng
        # cứu video nhặt sau.
        print(f"[{account}] chưa lấy được IP proxy để theo dõi — chờ rồi lấy lại, KHÔNG đọc bằng IP máy: {e}", flush=True)
        poll_proxy = PROXY_PENDING
    net_fails = dead_fails = 0
    async with aiohttp.ClientSession() as session:
        while time.time() - start < timeout:
            await asyncio.sleep(5)
            poll = await _fetch_single(session, cookie, ms_token, fp, conversation_id, poll_proxy)
            if poll is None:
                net_fails += 1
                dead_fails = dead_fails + 1 if _LAST_FETCH_FAIL.get(conversation_id) == "net" else 0
                if net_fails >= POLL_NET_FAILS:
                    poll_proxy = _next_poll_proxy(account, poll_proxy, proxy_dead=dead_fails >= POLL_NET_FAILS)
                    net_fails = dead_fails = 0
                continue
            net_fails = dead_fails = 0
            now = time.time()
            if on_poll and now - last_cb >= 30:
                on_poll(now)
                last_cb = now
            for text in poll["texts"]:
                if _is_own_message(text):
                    continue
                balance, _, source = _parse_balance_texts([text])
                if balance is not None and on_balance:
                    on_balance(balance, source)
                credits_used = _credits_used(text) or credits_used
                # VIDEO ĐÃ RA rồi thì mọi lời than phiền của Dola trong cùng lượt đọc này không còn là lỗi:
                # trước đây các nhánh raise ở dưới chạy TRƯỚC khối lấy video nên job có video vẫn bị đánh
                # "Không đủ điểm/quota" / "Dola chặn nội dung" (ảnh 16/9: nick ra video mà thẻ vẫn đỏ).
                # Vẫn đọc số dư + giá ở trên để kế toán credit không hụt.
                if poll["videos"]:
                    continue
                if GUEST_REFUSAL_PATTERN.search(text):
                    raise GuestRefusedError(f"{_GUEST_MSG}\n↳ Dola: {text[:140]}")
                if CONTENT_POLICY_PATTERN.search(text) and not _is_duration_capped(text):
                    raise ContentPolicyViolationError(
                        "Dola chặn nội dung (bạo lực / bản quyền / vi phạm chính sách) — đổi prompt nhẹ nhàng hơn.\n↳ Dola: " + text[:170])
                if PORTRAIT_PROTECTION_PATTERN.search(text):
                    raise PortraitProtectionError(
                        "Dola chặn (bảo vệ chân dung): model chỉ tạo video với ảnh MẶT CỦA CHÍNH BẠN.\n↳ Dola: " + text[:160])
                if PARAMETER_CHANGE_PATTERN.search(text):
                    raise _param_change_error(text)
                if DAILY_LIMIT_PATTERN.search(text):
                    raise AccountLimitedError(f"Hết lượt tạo video hôm nay. Dola: {text[:140]}")
                if PROMPT_UNCLEAR_PATTERN.search(text):
                    raise PromptUnclearError(
                        "Dola không hiểu prompt — viết mô tả cảnh quay cụ thể (không mất lượt).\n↳ Dola: " + text[:140])
                # "直接生成できません。現在の動画生成は 4–15 秒まで対応しており…" là câu về THỜI LƯỢNG, nhưng chữ
                # 「生成できません」 nằm trong CREDIT_FAIL_PATTERN → job bị báo "Không đủ điểm/quota" và nick bị
                # ghi hết điểm oan (thấy trong tasks.db 16/9). Câu Dola đang hỏi/giới hạn thông số thì để nhánh
                # hỏi-đáp ở dưới xử lý (trả 'はい' → vẫn ra video), không phải lỗi điểm.
                if CREDIT_FAIL_PATTERN.search(text) and not _question_needs_browser(text):
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
            if poll.get("creation_fail") and not poll.get("videos"):
                raise _creation_fail_error(poll["creation_fail"])
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
                local, dl_err = await _download_or_link(url, account, prompt)
                return {"video_url": url, "local_path": local, "download_error": dl_err,
                        "conversation_id": conversation_id, "account": account, "credits_used": credits_used}
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
                            handoff_after: float | None = None, answered: set | None = None,
                            prompt: str = "") -> dict:
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
    credits_used = None      # giá Dola báo lúc bắt đầu dựng → pool học giá + trừ credit nick
    net_fails, alt_fails, alt_proxy, alt_session = 0, 0, None, None
    chrome_dead = alt_dead = 0   # số lần hỏng LIÊN TIẾP do không nối được (không tính Dola trả mã HTTP lỗi)   # đường dự phòng khi mạng trong Chrome chết (IP proxy nick chết)
    try:
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
                poll = {"ok": False, "net_error": True}   # fetch trong trang ném lỗi = không nối được (proxy của Chrome có thể chết)
            if not poll.get("ok"):
                try:   # trang treo thì evaluate trong _is_logged_out cũng treo → có trần
                    logged_out = await asyncio.wait_for(_is_logged_out(page, context), timeout=15)
                except asyncio.TimeoutError:
                    logged_out = False
                if logged_out:
                    raise LoggedOutError(_LOGOUT_MSG)
                net_fails += 1
                chrome_dead = chrome_dead + 1 if poll.get("net_error") else 0
                if net_fails < POLL_NET_FAILS:
                    continue
                # Chrome đi IP proxy của nick mà IP đó chết GIỮA lúc Dola dựng: trước đây quay vòng tới hết giờ rồi báo
                # quá giờ dù video vẫn ra trên Dola (đã trừ lượt). Đọc hội thoại bằng HTTP qua đường khác, không gửi lại gì.
                if alt_session is None:
                    alt_session = aiohttp.ClientSession()
                    # Chrome không đổi được proxy khi đang chạy → đọc bằng HTTP; lỗi KẾT NỐI liên tiếp thì xin IP mới luôn.
                    alt_proxy = _next_poll_proxy(account, "__chrome__", proxy_dead=chrome_dead >= POLL_NET_FAILS)
                fresh = await context.cookies("https://www.dola.com")
                cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in fresh if c.get("name") and c.get("value"))
                poll = await _fetch_single(alt_session, cookie_header, ms_token, fp, conversation_id, alt_proxy)
                if poll is None:
                    alt_fails += 1
                    alt_dead = alt_dead + 1 if _LAST_FETCH_FAIL.get(conversation_id) == "net" else 0
                    if alt_fails >= POLL_NET_FAILS:
                        alt_proxy = _next_poll_proxy(account, alt_proxy, proxy_dead=alt_dead >= POLL_NET_FAILS)
                        alt_fails = alt_dead = 0
                    continue
                alt_fails = 0
            else:
                net_fails = 0
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
                credits_used = _credits_used(text) or credits_used
                # VIDEO ĐÃ RA rồi thì mọi lời than phiền của Dola trong cùng lượt đọc này không còn là lỗi:
                # trước đây các nhánh raise ở dưới chạy TRƯỚC khối lấy video nên job có video vẫn bị đánh
                # "Không đủ điểm/quota" / "Dola chặn nội dung" (ảnh 16/9: nick ra video mà thẻ vẫn đỏ).
                # Vẫn đọc số dư + giá ở trên để kế toán credit không hụt.
                if poll["videos"]:
                    continue
                if GUEST_REFUSAL_PATTERN.search(text):
                    raise GuestRefusedError(f"{_GUEST_MSG}\n↳ Dola: {text[:140]}")
                if CONTENT_POLICY_PATTERN.search(text) and not _is_duration_capped(text):
                    raise ContentPolicyViolationError(
                        "Dola chặn nội dung (bạo lực / bản quyền / vi phạm chính sách) — đổi prompt nhẹ nhàng hơn."
                        f"\n↳ Dola: {text[:170]}")
                if PORTRAIT_PROTECTION_PATTERN.search(text):
                    raise PortraitProtectionError(
                        "Dola chặn (bảo vệ chân dung): model này chỉ tạo video với ảnh MẶT CỦA CHÍNH BẠN. "
                        "Ảnh người khác đôi khi bị chặn (cả 2.0 lẫn 2.5). Không mất lượt — thử lại hoặc đổi ảnh/model."
                        f"\n↳ Dola: {text[:160]}")
                if PARAMETER_CHANGE_PATTERN.search(text):
                    raise _param_change_error(text)
                if DAILY_LIMIT_PATTERN.search(text):
                    raise AccountLimitedError(f"Hết lượt tạo video hôm nay. Dola: {text[:140]}")
                if PROMPT_UNCLEAR_PATTERN.search(text):
                    raise PromptUnclearError(
                        "Dola không hiểu prompt — viết mô tả cảnh quay cụ thể (không mất lượt).\n↳ Dola: " + text[:140])
                # "直接生成できません。現在の動画生成は 4–15 秒まで対応しており…" là câu về THỜI LƯỢNG, nhưng chữ
                # 「生成できません」 nằm trong CREDIT_FAIL_PATTERN → job bị báo "Không đủ điểm/quota" và nick bị
                # ghi hết điểm oan (thấy trong tasks.db 16/9). Câu Dola đang hỏi/giới hạn thông số thì để nhánh
                # hỏi-đáp ở dưới xử lý (trả 'はい' → vẫn ra video), không phải lỗi điểm.
                if CREDIT_FAIL_PATTERN.search(text) and not _question_needs_browser(text):
                    raise CreditError(f"Không đủ điểm/quota. Dola: {text[:120]}")
                if _is_transient_error(text):
                    raise TransientDolaError(
                        "Dola gặp lỗi tạm thời (hệ thống Dola báo lỗi, cần thử lại). Tự thử lại / xoay nick."
                        f"\n↳ Dola: {text[:140]}")
                key = _answer_key(text)
                # Dola nói "chỉ tới 15s": job 30s hạ xuống 15s; job 10s GIỮ 10s (không nâng lên 15s)
                if _is_duration_capped(text) or _is_duration_confirm(text):
                    want_duration = _effective_duration(want_duration, _capped_seconds(text))
                # Câu có menu A/B thì bỏ qua 2 nhánh 'はい', xuống nhánh menu đáp chữ cái
                yes_ok = not _lists_options(text)
                if yes_ok and _is_duration_capped(text) and key not in answered_specs:
                    # Dola chặn thời lượng dài (30s): job >= mức tối đa thì nhận (はい); job ngắn hơn thì nói rõ số giây
                    ans = _capped_reply(text, ratio, want_duration)
                    sent = await _reply_yes(page) if ans == "はい" else await _reply_text(page, ans)
                    if sent:
                        answered_specs.add(key)
                        stale_msg, stale_n = "", 0
                        last_answer_at = time.time()
                        how = "chấp nhận mức tối đa (はい)" if ans == "はい" else f"giữ {want_duration}s, trả lời '{ans}'"
                        print(f"[{account}] Dola chặn thời lượng dài → {how}: {text[:80]}", flush=True)
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
            if poll.get("creation_fail") and not poll.get("videos"):
                raise _creation_fail_error(poll["creation_fail"])
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
                local, dl_err = await _download_or_link(url, account, prompt)
                return {"video_url": url, "local_path": local, "download_error": dl_err,
                        "conversation_id": conversation_id, "account": account, "credits_used": credits_used}
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
    finally:
        if alt_session is not None:
            await alt_session.close()
    tail = f" Dola báo: {last_msg[:160]}" if last_msg else " (Dola không phản hồi gì thêm)"
    raise TimeoutError(f"Hết {timeout}s chưa ra video (conversation_id={conversation_id})." + tail)


async def resume_video(account: str, conversation_id: str, timeout: int,
                       on_poll=None, on_balance=None, ratio: str | None = None,
                       duration: int | None = None, answered: set | None = None,
                       on_browser_free=None, on_browser_hold=None, prompt: str = "") -> dict:
    """Recovers accepted session after server restart without re-sending prompt.

    Giống đường tạo mới: ở trong trang tới khi Dola hết hỏi lại rồi nhả Chrome, theo dõi tiếp bằng
    HTTP. Trước đây giữ slot Chrome suốt tới 15 phút → sau restart mọi job mới xếp hàng sau nó.
    """
    answered = set() if answered is None else answered
    deadline = time.time() + timeout
    async with async_playwright() as p:
        # 30s unlock now works headless via the fetch hijack (config.SKILLPACK_HIJACK); only the
        # legacy debugger extension needs a headed window.
        ui_headless = None if config.SKILLPACK_HIJACK else False
        context = await launch_account_context(p, account, headless=ui_headless, use_extension=True)
        closed = False
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await _goto_dola(page, f"https://www.dola.com/chat/{conversation_id}", account=account)
            await page.wait_for_timeout(5000)
            handoff = config.HTTP_POLL_AFTER_SEC if config.HTTP_POLL else None
            early = await poll_conversation(account, page, context, conversation_id, timeout, on_poll,
                                            on_balance, ratio, duration, handoff_after=handoff, answered=answered, prompt=prompt)
            if not early.get("handoff"):
                return early
            cookies = await context.cookies("https://www.dola.com")
            ms_token, fp = cookie_value(cookies, "msToken"), cookie_value(cookies, "s_v_web_id")
            cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies
                                      if c.get("name") and c.get("value"))
            await _persist_before_close(context, account)
            await context.close()
            closed = True
            if on_browser_free:
                on_browser_free()
        finally:
            if not closed:
                await _persist_before_close(context, account)
                await context.close()
    try:
        return await poll_conversation_http(account, cookie_header, ms_token, fp, conversation_id,
                                            max(30, int(deadline - time.time())), on_poll, on_balance,
                                            answered=answered, prompt=prompt)
    except _NeedsBrowser as ask:
        if _answer_key(ask.full) in answered:
            raise RuntimeError(
                "Dola hỏi đi hỏi lại cùng một câu (thường vì prompt mô tả video dài hơn mức Dola cho phép)"
                f" — rút ngắn kịch bản cho khớp số giây.\n↳ Dola: {ask}") from ask
        print(f"[{account}] Dola hỏi lại muộn → mở lại nick để trả lời: {ask}", flush=True)
        if on_browser_hold:
            await on_browser_hold()
        return await resume_video(account, conversation_id, max(30, int(deadline - time.time())), on_poll,
                                  on_balance, ratio, duration, answered=answered,
                                  on_browser_free=on_browser_free, on_browser_hold=on_browser_hold, prompt=prompt)


async def generate_video(account: str, prompt: str, ratio: str = None,
                         duration: int = None, timeout: int = None,
                         model: str = "seedance_v2.0", use_extension: bool = True,
                         on_conversation_id=None, on_poll=None, on_balance=None,
                         on_submitted=None, on_browser_free=None, on_browser_hold=None,
                         reference_image_paths: list[str] | None = None) -> dict:
    """Full generation flow: signed in-page fetch first (config.SUBMIT_MODE), UI automation otherwise."""
    prompt = fit_prompt_to_duration(prompt, duration, account)
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
        timeout = max(timeout, config.VIDEO_TIMEOUT_30S)   # 30s dựng ~15 phút, chờ đủ kẻo cắt lúc sắp xong
    if reference_image_paths:
        timeout = max(timeout, config.REFERENCE_VIDEO_TIMEOUT)

    fetch_model = _fetch_model_key(model_key)
    # ENGINE KHÔNG-CHROME: ký a_bogus bằng Python, gửi HTTP thuần (nhẹ RAM, mở nick nhanh như đối thủ). Chỉ cho submit;
    # Dola từ chối chắc chắn (cookie/captcha/a_bogus lệch bản) → rơi về đường fetch (Chrome). Ảnh tham chiếu vẫn cần UI.
    if config.SUBMIT_MODE == "http" and fetch_model and not reference_image_paths:
        from submit_http import SubmitHttpRejected
        try:
            result = await _generate_via_http(account, prompt, ratio, duration or 10, fetch_model,
                                              timeout, on_conversation_id, on_poll, on_balance,
                                              on_submitted, on_browser_free, on_browser_hold)
            return await _strip_logo(result, model_key, account)
        except SubmitHttpRejected as exc:
            if on_submitted:
                on_submitted(account, False)   # chưa tới Dola → pool được xoay/thử đường khác
            print(f"[{account}] engine HTTP bị từ chối ({exc}); rơi về mở Chrome (fetch)", flush=True)
            if on_browser_hold:
                await on_browser_hold()        # đã nhả slot ở _generate_via_http → xin lại trước khi mở Chrome
    if config.SUBMIT_MODE in ("fetch", "http") and fetch_model and not reference_image_paths:
        last_fetch_err = None
        # _FetchSubmitFailed = lệnh CHẮC CHẮN chưa tới Dola (thiếu device_id / HTTP 4xx / dò đủ không thấy hội
        # thoại) → thử lại an toàn. Mọi lỗi "có thể đã gửi" đã thành _FetchDelivered → nổi lên, không gửi lại.
        for attempt in (1, 2):
            try:
                result = await _generate_via_fetch(account, prompt, ratio, duration or 10, fetch_model,
                                                   timeout, on_conversation_id, on_poll, on_balance,
                                                   on_submitted=on_submitted, on_browser_free=on_browser_free,
                                                   on_browser_hold=on_browser_hold)
                return await _strip_logo(result, model_key, account)
            except _FetchSubmitFailed as exc:
                last_fetch_err = exc
                if on_submitted:
                    on_submitted(account, False)   # chưa tới Dola → pool đặt lại đồng hồ chống treo cho pha kế (lượt 2/UI)
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
            await _goto_dola(page, "https://www.dola.com/chat", account=account)
            await page.wait_for_timeout(5000)
            if await _is_logged_out(page, context, deep=True):
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
                await _global_submit_gate(account)   # giãn nhịp chung, ngay trước Enter
                if on_submitted:
                    # TRƯỚC Enter (như đường fetch): Enter lỗi/bị hủy vẫn có thể đã phát đi → không xoay/gửi lại
                    on_submitted(account, True)
                await page.keyboard.press("Enter")
            except Exception:
                await page.screenshot(path="ui_fail.png")
                raise
            print(f"[{account}] UI submitted prompt: {prompt[:40]}", flush=True)
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
            return await poll_conversation(account, page, context, conv_id, timeout, on_poll, on_balance, ratio, duration, prompt=prompt)
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