"""test_dola_error_codes.py — test cho dola_error_codes.py.

Chạy:
    pytest test_dola_error_codes.py -v
hoặc:
    .venv/bin/python test_dola_error_codes.py

Mục đích:
    - Đảm bảo phân loại đúng cho từng mã đã biết
    - Đảm bảo an toàn cho mã lạ / response rỗng / HTTP status đặc biệt
    - Tài liệu sống: dev mới đọc test để hiểu API

Mỗi test ghi 1 câu: "nếu response có X, thì classify phải trả Y" — không phụ thuộc
implementation nội bộ.
"""
from __future__ import annotations

import dola_error_codes as d
from dola_error_codes import DolaErrorKind


# ─────────────────────────────────────────────────────────────
# Nhóm 1: Mã đã biết — phân loại đúng
# ─────────────────────────────────────────────────────────────

def test_rate_limit_710022002_in_json_body():
    """Log 12/9: {"error_code":710022002,"error_msg":"操作频繁"} → rate_limit."""
    info = d.classify_dola_error('{"error_code":710022002,"error_msg":"操作频繁"}')
    assert info.kind == DolaErrorKind.RATE_LIMIT
    assert info.code == "710022002"
    assert info.retryable is True
    assert info.maybe_delivered is True


def test_rate_limit_710022002_in_sse_stream():
    """Cùng mã 710022002 trong SSE stream (engine Chrome đọc) → rate_limit."""
    sse = 'event: STREAM_ERROR\ndata: {"error_code":710022002,"error_msg":"集中しています"}\n\n'
    info = d.classify_dola_error(sse)
    assert info.kind == DolaErrorKind.RATE_LIMIT


def test_rate_limit_710022003_in_ext_field():
    """Mã 710022003 cũng nằm trong bảng RATE_LIMIT (biến thể)."""
    info = d.classify_dola_error('{"ai_creation_res_code":"710022003"}')
    assert info.kind == DolaErrorKind.RATE_LIMIT


def test_content_policy_710082022_jp_text():
    """Log 17/9: '著作権を保護するため...' + mã 710082022 → content_policy."""
    body = '"ai_creation_res_code": "710082022", "block_meta": {"items": ["txt"]}'
    info = d.classify_dola_error(body)
    assert info.kind == DolaErrorKind.CONTENT_POLICY
    assert info.code == "710082022"
    assert info.retryable is False       # KHÔNG retry cùng prompt
    assert info.maybe_delivered is True   # Dola có thể đã trừ lượt


def test_risk_control_710022004_is_captcha():
    """Mã 710022004 (captcha / risk control) → risk_control, cần đổi IP."""
    info = d.classify_dola_error('{"error_code":710022004}')
    assert info.kind == DolaErrorKind.RISK_CONTROL
    assert info.code == "710022004"
    assert info.retryable is True        # có retry, nhưng phải đổi IP
    assert info.maybe_delivered is False  # captcha chặn trước khi nhận


# ─────────────────────────────────────────────────────────────
# Nhóm 2: Mã lạ / response rỗng → UNKNOWN an toàn
# ─────────────────────────────────────────────────────────────

def test_unknown_code_defaults_to_safe():
    """Mã lạ (chưa có trong bảng) → UNKNOWN, KHÔNG retry, maybe_delivered=True.

    Đây là QUY TẮC AN TOÀN quan trọng: nếu gặp mã mới, ta KHÔNG tự retry
    (sửa lỗi thủ công đã), và coi như đã trừ lượt (tránh trừ 2 lần)."""
    info = d.classify_dola_error('{"error_code":999999999}')
    assert info.kind == DolaErrorKind.UNKNOWN
    assert info.retryable is False
    assert info.maybe_delivered is True
    assert info.code == "999999999"      # giữ lại để log
    assert "999999999" in info.action   # action phải nhắc tới mã để log dễ truy


def test_empty_body_returns_unknown():
    """Response rỗng → UNKNOWN. Không được raise."""
    info = d.classify_dola_error("")
    assert info.kind == DolaErrorKind.UNKNOWN


def test_none_body_returns_unknown():
    """text=None (lỗi mạng trước response) → UNKNOWN, không raise."""
    info = d.classify_dola_error(None)
    assert info.kind == DolaErrorKind.UNKNOWN


def test_no_code_in_text_only_message():
    """Text không chứa mã số 7-9 chữ số (vd HTML error page) → UNKNOWN."""
    info = d.classify_dola_error("<html><body>502 Bad Gateway</body></html>")
    assert info.kind == DolaErrorKind.UNKNOWN


# ─────────────────────────────────────────────────────────────
# Nhóm 3: HTTP status đặc biệt
# ─────────────────────────────────────────────────────────────

def test_http_404_is_not_found():
    """404 → NOT_FOUND, không retry."""
    info = d.classify_dola_error("", status=404)
    assert info.kind == DolaErrorKind.NOT_FOUND
    assert info.retryable is False
    assert info.maybe_delivered is False


def test_http_500_retryable():
    """HTTP 5xx → UNKNOWN nhưng retryable, vì server có thể phục hồi."""
    info = d.classify_dola_error("", status=502)
    assert info.kind == DolaErrorKind.UNKNOWN
    assert info.retryable is True
    assert info.maybe_delivered is True    # server có thể đã nhận


def test_http_429_with_no_body():
    """HTTP 429 mà body rỗng → UNKNOWN, KHÔNG tự retry (để engine HTTP xử lý 429)."""
    info = d.classify_dola_error("", status=429)
    assert info.kind == DolaErrorKind.UNKNOWN
    assert info.retryable is False


def test_http_400_not_retryable():
    """HTTP 4xx (không 429, không 404) → UNKNOWN, không retry."""
    info = d.classify_dola_error("", status=403)
    assert info.kind == DolaErrorKind.UNKNOWN
    assert info.retryable is False


# ─────────────────────────────────────────────────────────────
# Nhóm 4: Tiện ích (API ergonomic)
# ─────────────────────────────────────────────────────────────

def test_tra_cứu_known_code():
    """tra_cứu() trả về DolaErrorInfo nếu mã có trong bảng."""
    info = d.tra_cứu("710022002")
    assert info is not None
    assert info.kind == DolaErrorKind.RATE_LIMIT


def test_tra_cứu_accepts_int():
    """tra_cứu() chấp nhận int (parse từ JSON)."""
    info = d.tra_cứu(710022002)
    assert info is not None
    assert info.kind == DolaErrorKind.RATE_LIMIT


def test_tra_cứu_unknown_returns_none():
    """tra_cứu() trả None cho mã không có (khác với classify_dola_error trả UNKNOWN)."""
    assert d.tra_cứu("999999999") is None
    assert d.tra_cứu(None) is None
    assert d.tra_cứu("") is None
    assert d.tra_cứu("  ") is None     # whitespace-only cũng None


def test_is_rate_limit_helper():
    """is_rate_limit(info) tiện hơn info.kind == DolaErrorKind.RATE_LIMIT."""
    info = d.classify_dola_error('{"error_code":710022002}')
    assert d.is_rate_limit(info) is True

    info_cp = d.classify_dola_error('{"error_code":710082022}')
    assert d.is_rate_limit(info_cp) is False


def test_is_retryable_helper():
    """is_retryable(info) phải khớp với info.retryable."""
    for text, expected in [
        ('{"error_code":710022002}', True),    # rate_limit
        ('{"error_code":710082022}', False),   # content_policy
        ('{"error_code":710022004}', True),    # risk_control (retry được sau đổi IP)
        ('{"error_code":999999999}', False),   # unknown
    ]:
        info = d.classify_dola_error(text)
        assert d.is_retryable(info) is expected, (text, info)


def test_known_codes_includes_all_registered():
    """known_codes() phải liệt kê đủ 4 bảng, không sót."""
    codes = d.known_codes()
    # Ít nhất mỗi bảng có 1 mã
    assert "710022002" in codes      # RATE_LIMIT
    assert "710022004" in codes      # RISK_CONTROL
    assert "710082022" in codes      # CONTENT_POLICY
    assert "750000001" in codes      # QUOTA
    # Mã giả định KHÔNG có trong known_codes
    assert "999999999" not in codes
    # Đã sort
    assert codes == sorted(codes)


# ─────────────────────────────────────────────────────────────
# Nhóm 5: Edge cases
# ─────────────────────────────────────────────────────────────

def test_multiple_codes_picks_first_known():
    """Nếu body có nhiều mã, ưu tiên mã ĐẦU TIÊN có trong bảng (an toàn nhất)."""
    # Body có 999999999 (unknown) + 710022002 (rate limit)
    body = '{"error_code":999999999,"nested":{"code":710022002}}'
    info = d.classify_dola_error(body)
    assert info.kind == DolaErrorKind.RATE_LIMIT
    assert info.code == "710022002"


def test_no_7_to_9_digit_numbers_in_text():
    """Chỉ có số ngắn (vd timestamp, status code 1-4 chữ số) → không khớp pattern."""
    info = d.classify_dola_error("status=200, took=1250ms")
    assert info.kind == DolaErrorKind.UNKNOWN


def test_dola_error_info_is_immutable_slots():
    """DolaErrorInfo dùng __slots__ → không thể set thuộc tính mới (an toàn hơn)."""
    info = d.DolaErrorInfo("1", "rate_limit", True, True, "x")
    try:
        info.code_mới = "hack"           # type: ignore[attr-defined]
        raise AssertionError("__slots__ phải chặn việc gán thuộc tính mới")
    except AttributeError:
        pass


def test_classify_dola_error_never_returns_none():
    """Quy ước cứng: classify_dola_error KHÔNG BAO GIỜ trả None."""
    for text in [None, "", "abc", '{"error_code":999999999}', "{}"]:
        for status in [None, 200, 404, 429, 500, 502]:
            info = d.classify_dola_error(text, status)
            assert isinstance(info, d.DolaErrorInfo), (text, status)


# ─────────────────────────────────────────────────────────────
# Runner (cho người không dùng pytest)
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    failed = 0
    for name in sorted(n for n in dir() if n.startswith("test_")):
        try:
            globals()[name]()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}", file=sys.stderr)
    if failed:
        print(f"\n{failed} test FAIL", file=sys.stderr)
        sys.exit(1)
    print("\nALL PASS")
