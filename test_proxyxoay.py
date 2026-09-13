"""Self-check proxyxoay (không gọi mạng): nhận diện link key, parse JSON kiểu proxyxoay.shop và text
thuần, che key, cache IP cố định, rotate tôn trọng khoảng chờ. Chạy: .venv/bin/python test_proxyxoay.py"""
import proxyxoay
import browser

LINK = "https://proxyxoay.shop/api/get.php?key=ABC123&nhamang=random&tinhthanh=0"


def main():
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

    proxyxoay._cache.clear()
    proxyxoay._get = lambda link: '{"status":102,"message":"Key het han"}'
    assert browser.parse_proxy(LINK) is None
    print("OK")


if __name__ == "__main__":
    main()
