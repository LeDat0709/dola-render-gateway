"""submit_via_http: khi nào được phép rơi về đường Chrome (gửi lại) và khi nào TUYỆT ĐỐI không.

Dola trừ lượt NGAY LÚC NHẬN lệnh. Nếu lệnh đã tới Dola mà tool tưởng "chưa gửi" rồi gửi lại bằng Chrome thì
nick mất 2 lượt cho 1 video. Hai cái bẫy được khoá ở đây:

1. Lỗi mạng sau khi gói tin đã rời máy (hết giờ chờ trả lời, đứt lúc đang đọc) KHÔNG phải là "chưa gửi".
2. Phần trả lời có conversation_id = Dola ĐÃ nhận việc, kể cả khi trong đó lẫn chữ "verify"/"captcha".
"""
import asyncio

from curl_cffi.requests import errors

import submit_http as S


def test_only_pre_send_network_errors_count_as_not_sent():
    # Lỗi xảy ra TRƯỚC khi byte đầu rời máy → chắc chắn chưa gửi
    for code, name in [(7, "COULDNT_CONNECT"), (6, "RESOLVE_HOST"), (5, "RESOLVE_PROXY"),
                       (35, "SSL_CONNECT"), (97, "PROXY")]:
        assert S._never_left_machine(errors.RequestsError(name, code=code)), name
    # Lỗi có thể xảy ra SAU khi Dola đã nhận → phải coi như đã gửi
    for code, name in [(28, "TIMEOUT"), (56, "RECV"), (55, "SEND"), (18, "PARTIAL"), (52, "GOT_NOTHING")]:
        assert not S._never_left_machine(errors.RequestsError(name, code=code)), name
    # Không rõ mã lỗi → chọn phía an toàn cho lượt
    assert not S._never_left_machine(RuntimeError("lỗi lạ"))


def _run(post):
    """Chạy submit_via_http với _post giả. Trả (loại kết quả, nội dung, các lần gọi on_submitted)."""
    calls = []
    S.load_account_cookies = lambda a: {"sessionid": "x"}
    S.build_video_body = lambda *a, **k: {}
    S.build_signed = lambda c, b: ("https://x", "{}")
    import curl_cffi.requests as creq
    creq.post = post
    try:
        cid = asyncio.run(S.submit_via_http("nick", "p", "9:16", 30,
                                            on_submitted=lambda a, ok: calls.append(ok)))
        return "ok", cid, calls
    except S.SubmitHttpRejected as exc:
        return "reject", str(exc), calls
    except RuntimeError as exc:
        return "runtime", str(exc), calls


def _boom(code):
    def post(*a, **k):
        raise errors.RequestsError("net", code=code)
    return post


def _reply(text, status=200):
    def post(*a, **k):
        return type("R", (), {"status_code": status, "text": text})()
    return post


def test_connect_failure_may_fall_back_to_chrome():
    kind, _, calls = _run(_boom(7))
    # reject = báo "chưa gửi" → generate_video được phép mở Chrome gửi lại; on_submitted(False) mở lại cổng xoay nick
    assert (kind, calls) == ("reject", [True, False])


def test_post_send_drop_must_not_resend():
    for code in (28, 56):
        kind, msg, calls = _run(_boom(code))
        assert kind == "runtime", f"mã {code} phải cấm gửi lại, got {kind}"
        assert "KHÔNG gửi lại" in msg
        assert calls == [True], "không được mở lại cổng xoay: lệnh có thể đã tới Dola"


def test_conversation_id_wins_over_stray_verify_text():
    kind, cid, _ = _run(_reply('{"conversation_id":"123456","tip":"please verify later"}'))
    assert (kind, cid) == ("ok", "123456"), "có conv_id = Dola đã nhận việc, không được coi là bị chặn"


def test_real_captcha_without_conversation_id_still_falls_back():
    kind, _, calls = _run(_reply('{"msg":"slide captcha required"}'))
    assert (kind, calls) == ("reject", [True, False])


if __name__ == "__main__":
    test_only_pre_send_network_errors_count_as_not_sent()
    test_connect_failure_may_fall_back_to_chrome()
    test_post_send_drop_must_not_resend()
    test_conversation_id_wins_over_stray_verify_text()
    test_real_captcha_without_conversation_id_still_falls_back()
    print("OK")
