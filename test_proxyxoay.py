"""Self-check proxyxoay (không gọi mạng): nhận diện link key, parse JSON kiểu proxyxoay.shop và text
thuần, che key, cache IP cố định, rotate tôn trọng khoảng chờ. Chạy: .venv/bin/python test_proxyxoay.py"""
import proxyxoay
import browser

LINK = "https://proxyxoay.shop/api/get.php?key=ABC123&nhamang=random&tinhthanh=0"


def main():
    proxyxoay._public_ipv4 = lambda now: ""   # test không gọi mạng hỏi IP máy (tự khai whitelist test riêng bên dưới)
    assert proxyxoay.is_key_link(LINK)
    assert proxyxoay.is_key_link("http://x/get.php?key=1")
    assert not proxyxoay.is_key_link("http://user:pass@1.2.3.4:8080")
    assert not proxyxoay.is_key_link("tmproxy://KEY")

    proxyxoay._cache.clear()
    proxyxoay._get = lambda link: (
        '{"status":100,"message":"Chua den gio doi, cho 50 giay","proxyhttp":"1.2.3.4:8080:u:p",'
        '"Nha Mang":"Viettel","Vi Tri":"Ha Noi"}')
    d = browser.parse_proxy(LINK)
    assert d == {"server": "http://1.2.3.4:8080", "username": "u", "password": "p"}, d
    info = proxyxoay.lane_info(LINK)
    assert info["ip"] == "1.2.3.4:8080" and info["network"] == "Viettel" and info["location"] == "Ha Noi", info

    calls = {"n": 0}
    proxyxoay._get = lambda link: (calls.__setitem__("n", calls["n"] + 1) or '{"proxyhttp":"9.9.9.9:1:u:p"}')
    assert proxyxoay.current(LINK)["ip"] == "1.2.3.4:8080", "current phải trả IP đã cache"
    assert proxyxoay.rotate(LINK)["ip"] == "1.2.3.4:8080", "chưa tới giờ đổi → giữ IP cũ"
    assert calls["n"] == 0, "không được gọi lại link khi chưa tới giờ"

    proxyxoay._cache.clear()
    proxyxoay._get = lambda link: "5.6.7.8:3128"
    assert browser.parse_proxy(LINK) == {"server": "http://5.6.7.8:3128"}

    assert "ABC123" not in proxyxoay.mask(LINK) and "key=" in proxyxoay.mask(LINK)

    # TTL: còn hạn thì current() giữ IP cũ (ổn định trong 1 video); quá hạn thì lấy IP mới (không phục vụ IP chết mãi)
    proxyxoay._cache.clear()
    proxyxoay._get = lambda link: '{"proxyhttp":"1.1.1.1:80:u:p","message":"cho 5 giay"}'
    assert proxyxoay.current(LINK)["ip"] == "1.1.1.1:80"
    proxyxoay._get = lambda link: '{"proxyhttp":"2.2.2.2:80:u:p"}'
    assert proxyxoay.current(LINK)["ip"] == "1.1.1.1:80", "còn hạn → giữ IP cũ"
    ent = proxyxoay._cache[LINK]
    ent["fetched_at"] -= ent["ttl"] + 1                     # giả lập quá hạn
    assert proxyxoay.current(LINK)["ip"] == "2.2.2.2:80", "quá hạn → lấy IP mới"

    proxyxoay._cache.clear()
    proxyxoay._get = lambda link: '{"status":102,"message":"Key het han"}'
    assert browser.parse_proxy(LINK) is None

    # #4: proxy xoay RIÊNG của nick lấy IP hỏng → account_proxy RAISE (không lặng lẽ rơi về proxy chung/IP máy)
    import tempfile
    import config
    from pathlib import Path
    proxyxoay._cache.clear()
    proxyxoay._get = lambda link: '{"status":102,"message":"key het han"}'
    old_dir, old_proxy = config.ACCOUNTS_DIR, config.PROXY
    try:
        config.PROXY = ""
        d = Path(tempfile.mkdtemp())
        config.ACCOUNTS_DIR = d
        (d / "nickx").mkdir()
        (d / "nickx" / "proxy.txt").write_text(LINK, encoding="utf-8")
        try:
            browser.account_proxy("nickx")
            raise AssertionError("proxy xoay riêng hỏng phải RAISE, không rơi về proxy chung")
        except RuntimeError:
            pass
    finally:
        config.ACCOUNTS_DIR, config.PROXY = old_dir, old_proxy
    # "die sau Ns" = TUỔI THỌ IP, không phải thời gian chờ đổi (trước đây rotate() sau 710022002 thành no-op ~25 phút)
    proxyxoay._cache.clear()
    proxyxoay._get = lambda link: '{"status":100,"message":"proxy nay se die sau 1500s","proxyhttp":"3.3.3.3:80::"}'
    ent = proxyxoay.current(LINK)
    assert ent["next_ok"] == ent["fetched_at"] and ent["ttl"] == 1470, ent
    proxyxoay._get = lambda link: '{"status":100,"message":"Con 59s moi co the doi proxy","proxyhttp":"3.3.3.3:80::"}'
    ent = proxyxoay.rotate(LINK)
    assert ent["next_ok"] - ent["fetched_at"] == 59 and ent["ttl"] == 600, ent
    test_auto_whitelist_and_status()
    test_shoplike()
    print("OK")


def test_auto_whitelist_and_status():
    """Tự khai whitelist IP máy (&whitelist=IP) khi IP máy đổi; đếm lần đổi IP/ngày; status() cho Kho proxy."""
    urls, ips = [], {"now": "1.2.3.4"}
    replies = iter(['{"status":102,"message":"IP chua whitelist"}',
                    '{"status":100,"message":"proxy nay se die sau 1500s","proxyhttp":"42.117.243.215:10836::","Nha Mang":"fpt","Vi Tri":"HaNoi1"}',
                    '{"status":100,"proxyhttp":"42.117.243.215:10836::"}',
                    '{"status":100,"proxyhttp":"8.8.8.8:1::"}'])
    proxyxoay._public_ipv4 = lambda now: ips["now"]
    proxyxoay._get = lambda url: (urls.append(url), next(replies))[1]
    proxyxoay._cache.clear(); proxyxoay._wl_sent.clear()
    try:
        proxyxoay.rotate(LINK)
        raise AssertionError("status 102 phải ném lỗi")
    except proxyxoay.ProxyXoayError:
        pass
    assert urls[-1] == LINK + "&whitelist=1.2.3.4" and LINK not in proxyxoay._wl_sent, "khai lỗi → lần sau khai lại"
    ent = proxyxoay.rotate(LINK)
    assert urls[-1].endswith("&whitelist=1.2.3.4") and proxyxoay._wl_sent[LINK] == "1.2.3.4"
    assert LINK in proxyxoay._cache and ent["changes"] == 1, "khoá cache giữ NGUYÊN link (không dính &whitelist=)"
    st = proxyxoay.status(LINK)
    assert st["endpoint"] == "42.117.243.215:10836" and st["network"] == "fpt" and st["location"] == "HaNoi1", st
    assert 1490 <= st["expires_in"] <= 1500 and st["whitelist_ip"] == "1.2.3.4", st   # tuổi IP theo "die sau 1500s"
    proxyxoay._cache[LINK]["next_ok"] = 0
    assert proxyxoay.rotate(LINK)["changes"] == 1 and urls[-1] == LINK, "IP máy chưa đổi → không gắn lại; IP proxy trùng → không tính đổi"
    ips["now"] = "5.6.7.8"
    proxyxoay._cache[LINK]["next_ok"] = 0
    assert proxyxoay.rotate(LINK)["changes"] == 2 and urls[-1] == LINK + "&whitelist=5.6.7.8", "IP máy đổi → tự khai IP mới"
    info = browser.rotating_status(LINK)
    assert info["key_tail"] == "" and info["provider"] == "proxyxoay", info            # key ngắn (<8) → không hiện đuôi
    assert browser.rotating_status("proxyvn://ABCDEFGH1234")["key_tail"] == "1234"
    assert proxyxoay._whitelist_url("https://other.vn/get.php?key=K", 0) == ("https://other.vn/get.php?key=K", ""), "chỉ proxyxoay.shop"


def test_shoplike():
    """shoplike:TOKEN@location=hn → getNewProxy; current() dùng getCurrentProxy (KHÔNG đổi IP), rotate() mới getNewProxy;
    đọc data.proxy / nextChange / proxyTimeout; token rỗng bị từ chối; không lộ token."""
    link = browser.check_proxy_input("shoplike:TOK123456789@location=hn")
    assert link == "https://proxy.shoplike.vn/Api/getNewProxy?access_token=TOK123456789&location=hn", link
    assert browser.is_rotating_proxy(link) and browser.provider_label(link) == "shoplike"
    assert "TOK123456789" not in proxyxoay.mask(link) and "TOK123456789" not in browser.mask_proxy(link)
    try:
        browser.check_proxy_input("shoplike:")
        raise AssertionError("token rỗng phải bị từ chối")
    except ValueError:
        pass
    urls = []
    replies = {"getCurrentProxy": '{"status":"success","data":{"proxy":"1.2.3.4:8080","location":"hn","nextChange":45,"proxyTimeout":1800}}',
               "getNewProxy": '{"status":"success","data":{"proxy":"5.6.7.8:9090","location":"hn","nextChange":60,"proxyTimeout":1790}}'}
    proxyxoay._get = lambda url: (urls.append(url), replies["getCurrentProxy" if "getCurrentProxy" in url else "getNewProxy"])[1]
    proxyxoay._cache.clear()
    ent = proxyxoay.current(link)
    assert "getCurrentProxy" in urls[-1] and ent["ip"] == "1.2.3.4:8080" and ent["location"] == "hn", (urls, ent)
    assert ent["next_ok"] - ent["fetched_at"] == 45 and ent["ttl"] == 1770 and link in proxyxoay._cache, ent
    assert browser.rotating_status(link)["key_tail"] == "6789" and 1790 <= browser.rotating_status(link)["expires_in"] <= 1800
    proxyxoay._cache[link]["next_ok"] = 0
    assert proxyxoay.rotate(link)["ip"] == "5.6.7.8:9090" and "getNewProxy" in urls[-1], urls
    # chưa có IP hiện hành → getCurrentProxy báo lỗi → tự xin IP mới
    proxyxoay._cache.clear()
    replies["getCurrentProxy"] = '{"status":"error","mess":"Chua co proxy"}'
    assert proxyxoay.current(link)["ip"] == "5.6.7.8:9090" and "getNewProxy" in urls[-1], urls


if __name__ == "__main__":
    main()
