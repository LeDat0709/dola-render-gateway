"""dola_error_codes.py — bảng tra cứu + phân loại mã lỗi Dola/VeoMedia.

Lý do tồn tại
-------------
Trước đây mỗi chỗ (submit_http.py engine HTTP, video_worker.py engine Chrome) tự kiểm
"710022002" in text cứng → dễ sót mã mới, dễ quên phân loại đúng, không thống nhất
giữa 2 engine. Gom về đây:

    BẢNG_RATE_LIMIT     mã Dola trả khi "gửi quá dày" / "đợi chút"
    BẢNG_RISK_CONTROL   mã captcha / verify / WAF
    BẢNG_CONTENT_POLICY mã lỗi nội dung (bản quyền, vi phạm chính sách)
    BẢNG_QUOTA          mã hết credit / hết quota ngày

Mọi mã không có trong bảng → DolaErrorKind.UNKNOWN (an toàn nhất: KHÔNG retry).

Cập nhật khi gặp mã mới: thêm vào bảng tương ứng + viết test.

Mã nguồn bảng
-------------
Mã 710022002: ghi nhận từ log thật 12/9 "操作频繁 / リクエストが集中" (Dola rate limit IP)
              test_handoff.py:716, test_pool_meta.py:311, store.py, server.py đã dùng
Mã 710022004: captcha / risk control — test_handoff.py:716 dùng làm mẫu
Mã 710082022: "著作権を保護するため..." — Dola giấu video vì bản quyền
              test_creation_result_code.py (ContentPolicyViolationError)
Mã 710082041: "Only two videos..." — câu hỏi clarifying, KHÔNG phải kết quả dựng
              (test_creation_result_code.py chứng minh: KHÔNG coi là lỗi)
"""
from __future__ import annotations

import re

# ─────────────────────────────────────────────────────────────
# Bảng mã — chỉ chứa string, không có logic
# ─────────────────────────────────────────────────────────────

# Mã rate limit (Dola trả khi gửi quá dày): CẦN retry, có thể đã trừ lượt
BẢNG_RATE_LIMIT: frozenset[str] = frozenset({
    "710022002",   # "リクエストが集中しています" / "操作频繁" — rate limit theo IP+account
    "710022003",   # biến thể Dola thỉnh thoảng đổi số cuối (chưa gặp trực tiếp)
})

# Mã risk control (captcha / verify / WAF): cần đổi IP, không retry cùng IP
BẢNG_RISK_CONTROL: frozenset[str] = frozenset({
    "710022004",   # captcha / risk control — test_handoff.py:716 dùng làm mẫu
    "710022005",   # biến thể WAF (chưa gặp trực tiếp)
})

# Mã content policy (nội dung bị cấm): KHÔNG retry cùng prompt
BẢNG_CONTENT_POLICY: frozenset[str] = frozenset({
    "710082022",   # "著作権を保護するため..." — Dola giấu video vì bản quyền
    "710082023",   # biến thể vi phạm chính sách (chưa gặp)
    "710082024",   # biến thể nhạy cảm (chưa gặp)
})

# Mã quota (hết credit / hết quota ngày): KHÔNG retry, chờ nạp/ngày mới
BẢNG_QUOTA: frozenset[str] = frozenset({
    "750000001",   # hết credit (placeholder — cập nhật khi thấy thật)
})

# Lưu ý: 710082041 KHÔNG có trong bảng nào — đó là câu hỏi clarifying
# ("Only two videos can be generated at a time. I'll start…"), engine poll
# tự xử lý qua _check_not_started trong video_worker_ui.py


# ─────────────────────────────────────────────────────────────
# Phân loại + metadata
# ─────────────────────────────────────────────────────────────

class DolaErrorKind:
    """Các loại lỗi Dola. Dùng string để serialize được sang JSON khi cần log."""
    RATE_LIMIT = "rate_limit"          # gửi quá dày → chờ rồi retry
    RISK_CONTROL = "risk_control"      # captcha/verify → đổi IP
    CONTENT_POLICY = "content_policy"  # prompt vi phạm → đổi prompt
    QUOTA = "quota"                    # hết credit → chờ
    NOT_FOUND = "not_found"            # 404 / endpoint lỗi
    UNKNOWN = "unknown"                # mã lạ / không đọc được → KHÔNG retry


class DolaErrorInfo:
    """Metadata cho 1 mã lỗi Dola.

    Fields:
        code: mã (string số, vd "710022002")
        kind: 1 trong DolaErrorKind.*
        retryable: True nếu CÓ THỂ thử lại (cùng nick sau khi chờ / đổi IP)
        maybe_delivered: True nếu Dola CÓ THỂ đã trừ lượt (an toàn khi True;
                        nơi gọi sẽ dò conversation_id mới trước khi retry)
        action: gợi ý hành động (log + UI)
    """

    __slots__ = ("code", "kind", "retryable", "maybe_delivered", "action")

    def __init__(self, code: str, kind: str, retryable: bool,
                 maybe_delivered: bool, action: str) -> None:
        self.code = code
        self.kind = kind
        self.retryable = retryable
        self.maybe_delivered = maybe_delivered
        self.action = action

    def __repr__(self) -> str:
        return (f"DolaErrorInfo(code={self.code!r}, kind={self.kind!r}, "
                f"retryable={self.retryable}, maybe_delivered={self.maybe_delivered})")


# ─────────────────────────────────────────────────────────────
# Bảng tra cứu runtime (đăng ký từ các BẢNG_* ở trên)
# ─────────────────────────────────────────────────────────────

_BẢNG: dict[str, DolaErrorInfo] = {}


def _đăng_ký(code: str, kind: str, retryable: bool,
              maybe_delivered: bool, action: str) -> None:
    """Đăng ký 1 mã vào bảng. Gọi 1 lần lúc import module."""
    _BẢNG[code] = DolaErrorInfo(code, kind, retryable, maybe_delivered, action)


# Rate limit: retryable, maybe_delivered=True (Dola có thể đã nhận lệnh nhưng
# không trả conversation_id → mặc định an toàn: nơi gọi DÒ hội thoại mới trước)
for _code in BẢNG_RATE_LIMIT:
    _đăng_ký(_code, DolaErrorKind.RATE_LIMIT,
             retryable=True, maybe_delivered=True,
             action="chờ 15-30s rồi retry cùng nick; nếu vẫn fail → đổi IP")

# Risk control: cần đổi IP, có thể đã trừ lượt
for _code in BẢNG_RISK_CONTROL:
    _đăng_ký(_code, DolaErrorKind.RISK_CONTROL,
             retryable=True, maybe_delivered=False,
             action="đổi IP/proxy, dò conversation mới, không retry cùng IP")

# Content policy: KHÔNG retry cùng prompt, có thể đã trừ lượt
for _code in BẢNG_CONTENT_POLICY:
    _đăng_ký(_code, DolaErrorKind.CONTENT_POLICY,
             retryable=False, maybe_delivered=True,
             action="đổi prompt hoặc bỏ job; KHÔNG gửi lại")

# Quota: KHÔNG retry, chờ nạp/ngày mới
for _code in BẢNG_QUOTA:
    _đăng_ký(_code, DolaErrorKind.QUOTA,
             retryable=False, maybe_delivered=False,
             action="chờ nạp credit hoặc đợi quota ngày mới")


# ─────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────

# Pattern mã Dola: 7-9 chữ số (theo log: 710022002, 710082022, 750000001...)
_RE_MÃ_DOLA = re.compile(r"\b(\d{7,9})\b")


def tra_cứu(code: str | int | None) -> DolaErrorInfo | None:
    """Tra cứu mã trong bảng. Trả None nếu không có (gọi classify_dola_error để an toàn)."""
    if code is None:
        return None
    s = str(code).strip()
    if not s:
        return None
    return _BẢNG.get(s)


def classify_dola_error(text: str | None, status: int | None = None) -> DolaErrorInfo:
    """Phân loại 1 phản hồi lỗi Dola.

    Args:
        text: body phản hồi (SSE / JSON / HTML). None nếu lỗi mạng trước response.
        status: HTTP status code (nếu có, vd 200/429/500).

    Returns:
        DolaErrorInfo — KHÔNG BAO GIỜ trả None.

    Quy tắc an toàn (khi mã lạ):
        - retryable=False (không tự retry) — sửa lỗi thủ công đã có gì sai
        - maybe_delivered=True (coi như đã trừ lượt) — không retry cùng nick
    """
    text = text or ""

    # Bước 1: tìm mã trong body. Ưu tiên mã đầu tiên có trong bảng.
    candidates = _RE_MÃ_DOLA.findall(text)
    for cand in candidates:
        info = _BẢNG.get(cand)
        if info is not None:
            return info

    # Bước 2: không có mã nào khớp bảng
    if candidates:
        # Có mã số nhưng không biết → UNKNOWN với mã đầu tiên (để log)
        return DolaErrorInfo(
            code=candidates[0],
            kind=DolaErrorKind.UNKNOWN,
            retryable=False,
            maybe_delivered=True,
            action=f"mã lạ {candidates[0]} — KHÔNG retry, log để điền vào bảng",
        )

    # Bước 3: không đọc được mã → phân loại theo HTTP status
    if status is not None:
        if status == 404:
            return DolaErrorInfo(
                code="", kind=DolaErrorKind.NOT_FOUND,
                retryable=False, maybe_delivered=False,
                action="endpoint không tồn tại — kiểm tra URL/version",
            )
        if 500 <= status < 600:
            return DolaErrorInfo(
                code="", kind=DolaErrorKind.UNKNOWN,
                retryable=True, maybe_delivered=True,
                action=f"HTTP {status} server error — chờ rồi thử lại",
            )
        if 400 <= status < 500:
            return DolaErrorInfo(
                code="", kind=DolaErrorKind.UNKNOWN,
                retryable=False, maybe_delivered=False,
                action=f"HTTP {status} client error — KHÔNG retry",
            )

    # Bước 4: không có status, không có mã → lỗi mạng / response rỗng
    return DolaErrorInfo(
        code="", kind=DolaErrorKind.UNKNOWN,
        retryable=False, maybe_delivered=True,
        action="không đọc được lỗi — KHÔNG retry, log toàn bộ response",
    )


def known_codes() -> list[str]:
    """Liệt kê tất cả mã đã đăng ký (cho test + log debug)."""
    return sorted(_BẢNG.keys())


def is_rate_limit(info: DolaErrorInfo) -> bool:
    """Tiện ích: có phải rate limit không? (cho code cũ đọc nhanh)."""
    return info.kind == DolaErrorKind.RATE_LIMIT


def is_retryable(info: DolaErrorInfo) -> bool:
    """Tiện ích: có nên retry không?"""
    return info.retryable
