"""Lưu proxy KHÔNG gọi mạng + báo đúng lý do lấy IP hỏng mà không lộ key (cải tiến 4–5, 15/09).

Chạy: .venv/bin/python test_proxy_input.py
"""
import json
import tempfile
from pathlib import Path

import browser
import config
import proxyxoay
import tmproxy

KEY = "0123456789abcdef0123456789abcdef"
proxyxoay._public_ipv4 = lambda now: ""   # không gọi mạng hỏi IP máy (tự khai whitelist test ở test_proxyxoay.py)


def _boom(*a, **k):
    raise AssertionError("gọi mạng lúc lưu proxy!")


def test_check_proxy_input_normalizes_without_network():
    saved = tmproxy._call, tmproxy._post, proxyxoay._get
    tmproxy._call = tmproxy._post = proxyxoay._get = _boom
    try:
        assert browser.check_proxy_input("proxyvn://PV") == \
            "https://proxyxoay.shop/api/get.php?key=PV&nhamang=random&tinhthanh=0"
        assert browser.check_proxy_input(KEY + "\n") == "tmproxy://" + KEY
        # dạng tool Seedance AI Studio (1 dấu hai chấm) dán sang được, proxy tĩnh không bị ảnh hưởng
        assert browser.check_proxy_input("proxyvn:PV") == browser.check_proxy_input("proxyvn://PV")
        assert browser.check_proxy_input("tmproxy:" + KEY) == "tmproxy://" + KEY
        # đuôi khoá tỉnh/nhà mạng như tool Seedance: KHÔNG được nhét vào key
        base = "https://proxyxoay.shop/api/get.php?key=PV&"
        assert browser.check_proxy_input("proxyvn:PV@nhamang=viettel,tinhthanh=3") == base + "nhamang=viettel&tinhthanh=3"
        assert browser.check_proxy_input("proxyvn:PV@isp=fpt,location=16") == base + "nhamang=fpt&tinhthanh=16"
        v = browser.check_proxy_input("tmproxy:" + KEY + "@location=9,isp=1")
        assert v == "tmproxy://" + KEY + "@location=9,isp=1" and tmproxy.key_of(v) == KEY, v
        body = tmproxy._new_body(KEY)
        assert body["id_location"] == 9 and body["id_isp"] == 1 and body["api_key"] == KEY, body
        assert browser.normalize_proxy_input(v) == v and KEY not in browser.mask_proxy(v)
        assert browser.check_proxy_input("proxy.example.com:3128:u:p") == "proxy.example.com:3128:u:p"
        assert browser.check_proxy_input("1.2.3.4:8080") == "1.2.3.4:8080"
        assert browser.check_proxy_input(" 1.2.3.4:8080 ") == "1.2.3.4:8080"
        assert browser.check_proxy_input("") == ""
        assert browser.check_proxy_input("https://r.vn/get.php?api_key=ABC")   # kho proxy cũng nhận dạng này
        for bad in ("tmproxy://", "proxyvn://", "hello", "https://proxyxoay.shop/api/get.php?key="):
            try:
                browser.check_proxy_input(bad)
                raise AssertionError(f"phải từ chối {bad!r}")
            except ValueError:
                pass
        assert KEY not in browser.mask_proxy(KEY)
        assert "SECRETK" not in browser.mask_proxy("proxyvn://SECRETK")
    finally:
        tmproxy._call, tmproxy._post, proxyxoay._get = saved


def test_account_proxy_error_has_real_reason_and_hides_key():
    saved = (config.ACCOUNTS_DIR, config.PROXY, proxyxoay._get, tmproxy._post)
    try:
        d = Path(tempfile.mkdtemp())
        config.ACCOUNTS_DIR, config.PROXY = d, ""
        (d / "n").mkdir()
        proxyxoay._get = lambda link: json.dumps({"status": 102, "message": "IP chua whitelist"})
        tmproxy._post = lambda path, body: {"code": 5, "message": "key het han"}
        link = "https://proxyxoay.shop/api/get.php?key=K1&nhamang=random&tinhthanh=0"
        for content, want in [(link, "IP chua whitelist"), ("proxyvn://K1", "IP chua whitelist"),
                              ("topproxy://K1", "IP chua whitelist"), (link + "&s={SESSION}", "IP chua whitelist"),
                              ("tmproxy://" + KEY, "key het han"), (KEY + "\n", "key het han")]:
            proxyxoay._cache.clear(); proxyxoay._last_err.clear(); tmproxy._cache.clear(); tmproxy._last_err.clear()
            (d / "n" / "proxy.txt").write_text(content, encoding="utf-8")   # file cũ / desktop ghi thẳng
            try:
                browser.account_proxy("n")
                raise AssertionError("phải raise với " + content)
            except RuntimeError as e:
                msg = str(e)
                assert want in msg and KEY not in msg and "K1&" not in msg, (content, msg)
        browser.set_account_proxy("n", "proxyvn://K1")
        assert (d / "n" / "proxy.txt").read_text() == link, "lưu bản chuẩn"
        assert browser.account_proxy_raw("n") == link, "khoá nhịp gửi (_pace/710022002) khớp kho proxy"
        tmproxy._post = lambda path, body: {"code": 0, "data": {"https": "1.1.1.1:80", "timeout": "abc"}}
        assert tmproxy.resolve_dict("tmproxy://" + KEY) is None and "abc" in tmproxy.last_error(KEY)
        tmproxy._post = lambda path, body: {"code": 0, "data": {"https": "1.1.1.1:80", "timeout": 1800}}
        assert tmproxy.resolve_dict("tmproxy://" + KEY) and browser.rotating_last_error(KEY) == "", "lấy IP được → xoá lỗi cũ"
    finally:
        config.ACCOUNTS_DIR, config.PROXY, proxyxoay._get, tmproxy._post = saved
        proxyxoay._cache.clear(); tmproxy._cache.clear()


def test_garbage_is_rejected_not_stored_as_proxy():
    """Trước đây MỌI chuỗi có dấu ':' đều lọt: ':' thành {"server": "http://:"} rồi được lưu làm proxy của nick
    (dán nhầm 1 ký tự là nick đó hỏng, tới lúc chạy mới báo lỗi khó hiểu). Đòi host có thật + port là số hợp lệ."""
    import browser
    for rac in (":", "::", ":::", " : ", "abc:def", "1.2.3.4:abc", "1.2.3.4:0",
                "1.2.3.4:70000", ":8080", "1.2.3.4:"):
        assert browser.parse_proxy(rac) is None, f"{rac!r} phải bị từ chối"
        try:
            browser.check_proxy_input(rac)
            raise AssertionError(f"{rac!r} lọt vào kho proxy")
        except ValueError:
            pass
    # Proxy thật vẫn phải qua nguyên vẹn
    for tot, server in (("1.2.3.4:8080", "http://1.2.3.4:8080"),
                        ("user:pw@1.2.3.4:8080", "http://1.2.3.4:8080"),
                        ("http://user:pw@5.6.7.8:8080", "http://5.6.7.8:8080"),
                        ("socks5://1.2.3.4:1080", "socks5://1.2.3.4:1080"),
                        ("1.2.3.4:8080:user:pw", "http://1.2.3.4:8080"),
                        ("proxy.nhaban.vn:9999", "http://proxy.nhaban.vn:9999")):
        got = browser.parse_proxy(tot)
        assert got and got["server"] == server, f"{tot!r} -> {got}"


if __name__ == "__main__":
    test_check_proxy_input_normalizes_without_network()
    test_account_proxy_error_has_real_reason_and_hides_key()
    test_garbage_is_rejected_not_stored_as_proxy()
    print("OK")
