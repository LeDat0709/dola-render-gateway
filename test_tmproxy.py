"""Self-check adapter TMProxy (không gọi mạng): cache theo timeout, tôn trọng next_request, parse/mask,
key hỏng → parse trả None để Kho proxy từ chối. Chạy: .venv/bin/python test_tmproxy.py"""
import time

import browser
import tmproxy

calls: list[str] = []


def fake_post(path, body):
    calls.append(path)
    ip = {"get-current-proxy": "1.1.1.1:1000", "get-new-proxy": f"2.2.2.{len(calls)}:2000"}[path]
    return {"code": 0, "message": "ok", "data": {"https": ip, "username": "", "password": "",
                                                  "timeout": 600, "next_request": 30}}


def main():
    tmproxy._post = fake_post
    tmproxy._cache.clear()
    K = "tmproxy://abcdef0123456789"
    key = tmproxy.key_of(K)

    p = browser.parse_proxy(K)
    assert p == {"server": "http://1.1.1.1:1000"}, p
    assert calls == ["get-current-proxy"], calls
    browser.parse_proxy(K)
    assert calls == ["get-current-proxy"], "lần 2 phải dùng cache, không gọi mạng"

    tmproxy.rotate(key)
    assert calls == ["get-current-proxy"], "chưa tới next_request thì KHÔNG đổi IP"
    tmproxy._cache[key]["next_ok"] = time.time() - 1
    ent = tmproxy.rotate(key)
    assert calls[-1] == "get-new-proxy" and ent["https"].startswith("2.2.2."), (calls, ent)
    assert browser.parse_proxy(K)["server"] == "http://" + ent["https"], "sau khi đổi phải dùng IP mới"

    assert browser.mask_proxy(K) == "tmproxy://abcdef…", browser.mask_proxy(K)

    tmproxy._cache.clear()
    tmproxy._post = lambda path, body: {"code": 1, "message": "invalid api key"}
    assert browser.parse_proxy("tmproxy://xxxx") is None, "key hỏng → None (Kho proxy từ chối), không ném"
    print("OK")


if __name__ == "__main__":
    main()
