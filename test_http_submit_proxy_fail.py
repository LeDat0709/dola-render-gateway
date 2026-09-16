"""Engine gửi KHÔNG-Chrome: lấy IP proxy của nick lỗi → KHÔNG được gửi thẳng bằng IP máy.

Trước đây _generate_via_http bắt lỗi proxy rồi gửi với proxy=None ("gửi đi thẳng còn hơn chặn cả job"). Giao diện
vẫn hiện IP proxy (bộ nhớ đệm) nhưng lệnh thật đi từ IP máy → nhiều nick cùng dồn vào một IP → 710022002 hàng loạt,
và lộ IP thật của máy. Đúng phải: nổi lỗi TRƯỚC khi gửi (chưa trừ lượt) để pool chuyển nick.
Chạy: .venv/bin/python test_http_submit_proxy_fail.py
"""
import asyncio

import browser
import submit_http
import video_worker_ui as vw


def test_proxy_error_does_not_submit_direct():
    saved = (browser.account_proxy_url, submit_http.submit_via_http)
    sent, submitted_flags = [], []

    def broken_proxy(account):
        raise RuntimeError("Proxy xoay riêng của nick n1 không lấy được IP: IP chưa whitelist")

    async def fake_submit(account, prompt, ratio, duration, model, proxy, on_submitted=None):
        sent.append(proxy)
        return "123456789"

    browser.account_proxy_url = broken_proxy
    submit_http.submit_via_http = fake_submit
    try:
        try:
            asyncio.run(vw._generate_via_http("n1", "p", "9:16", 10, "seedance_v2.0", 60, None, None, None,
                                              on_submitted=lambda a, s: submitted_flags.append(s)))
            raise AssertionError("proxy lỗi mà job vẫn chạy tiếp")
        except RuntimeError as e:
            assert "không lấy được IP" in str(e), e
        assert sent == [], f"đã gửi lệnh lên Dola với proxy={sent} — lộ IP máy, dồn chung IP"
        assert True not in submitted_flags, "chưa gửi gì thì không được báo 'đã gửi' (pool sẽ không dám chuyển nick)"
    finally:
        browser.account_proxy_url, submit_http.submit_via_http = saved


if __name__ == "__main__":
    test_proxy_error_does_not_submit_direct()
    print("OK")
