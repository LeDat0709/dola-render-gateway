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
    # status() cho Kho proxy (không gọi mạng): endpoint, IP ra TMProxy báo sẵn, đếm lần ĐỔI IP (IP trùng không tính)
    tmproxy._cache.clear()
    seq = iter(["1.1.1.1:80", "1.1.1.1:80", "2.2.2.2:80"])
    tmproxy._post = lambda path, body: {"code": 0, "data": {"https": next(seq), "public_ip": "9.9.9.9", "timeout": 1800, "next_request": 0}}
    K = "b" * 32
    tmproxy.current(K); tmproxy.rotate(K)
    assert tmproxy.status(K)["changes"] == 1, tmproxy.status(K)
    tmproxy.rotate(K)
    st = tmproxy.status(K)
    assert st["changes"] == 2 and st["endpoint"] == "2.2.2.2:80" and st["exit_ip"] == "9.9.9.9" and 1790 <= st["expires_in"] <= 1800, st
    assert tmproxy.status("khong-co") == {}
    print("OK")


if __name__ == "__main__":
    main()


def test_goi_het_han_khong_dam_api_moi_lan():
    """Gói hết hạn: current() hỏng cả get-current lẫn get-new. Đừng gọi lại API trong thời gian nghỉ —
    đo 22/09: giao diện hỏi proxy/current mỗi giây, mỗi lần 2 lệnh API 'Gói Hết hạn' (log spam + dễ bị chặn)."""
    import pytest

    n = {"calls": 0}

    def fail_post(path, body):
        n["calls"] += 1
        raise tmproxy.TMProxyError("Gói Hết hạn")

    old_post = tmproxy._post
    tmproxy._post = fail_post
    tmproxy._cache.clear()
    tmproxy._fail_until.clear()
    key = "e2bfe0" + "0" * 26
    try:
        for _ in range(5):
            with pytest.raises(tmproxy.TMProxyError):
                tmproxy.current(key)
        # lần đầu gọi 2 API (get-current + get-new); 4 lần sau vào cache lỗi → KHÔNG gọi thêm
        assert n["calls"] == 2, n["calls"]
    finally:
        tmproxy._post = old_post
        tmproxy._fail_until.clear()
        tmproxy._cache.clear()


def test_het_nghi_thi_thu_lai():
    n = {"calls": 0}

    def fail_post(path, body):
        n["calls"] += 1
        raise tmproxy.TMProxyError("Gói Hết hạn")

    old_post = tmproxy._post
    tmproxy._post = fail_post
    tmproxy._cache.clear()
    tmproxy._fail_until.clear()
    key = "e2bfe0" + "0" * 26
    try:
        import pytest
        with pytest.raises(tmproxy.TMProxyError):
            tmproxy.current(key)
        tmproxy._fail_until[key] = time.time() - 1        # hết nghỉ
        with pytest.raises(tmproxy.TMProxyError):
            tmproxy.current(key)
        assert n["calls"] == 4                            # 2 lần × 2 API
    finally:
        tmproxy._post = old_post
        tmproxy._fail_until.clear()
        tmproxy._cache.clear()
