"""Proxy whitelist (proxyxoay/proxy.vn/topproxy) + IP máy đổi theo từng kết nối (VPN) = không bao giờ dùng được.
Tool phải NÓI THẲNG điều đó ở MỌI đường, không chỉ đường gửi http (20/09: người dùng ở chế độ 'ui' chỉ thấy
"Connection reset by peer"; ExpressVPN đổi IP 5 lần trong 6 request nên whitelist không thể khai kịp).

Chạy: .venv/bin/python test_whitelist_hint.py
"""
import asyncio

import browser
import browser_pool
import proxyxoay


def _gia_lap(rotating: str, ip_doi: bool):
    """Trả (khôi phục,) sau khi thay _effective_rotating + machine_ip_unstable."""
    g1, g2 = browser._effective_rotating, proxyxoay.machine_ip_unstable
    browser._effective_rotating = lambda a: rotating
    proxyxoay.machine_ip_unstable = lambda: ip_doi
    browser._ip_unstable_cache.clear()

    def khoi_phuc():
        browser._effective_rotating, proxyxoay.machine_ip_unstable = g1, g2
        browser._ip_unstable_cache.clear()
    return khoi_phuc


def test_noi_ro_khi_proxy_whitelist_gap_ip_xoay():
    khoi_phuc = _gia_lap("https://proxyxoay.shop/api/get.php?key=x", True)
    try:
        hint = browser.rotating_whitelist_hint("n1")
        assert hint, "phải có câu giải thích"
        assert "VPN" in hint and "tmproxy" in hint, f"phải nêu nguyên nhân + lối ra: {hint}"
    finally:
        khoi_phuc()


def test_im_lang_khi_ip_on_dinh():
    khoi_phuc = _gia_lap("https://proxyxoay.shop/api/get.php?key=x", False)
    try:
        assert browser.rotating_whitelist_hint("n1") == "", "IP ổn định thì đừng đổ lỗi cho VPN"
    finally:
        khoi_phuc()


def test_im_lang_khi_khong_dung_proxy_xoay():
    khoi_phuc = _gia_lap("", True)
    try:
        assert browser.rotating_whitelist_hint("n1") == "", "proxy tĩnh/đi thẳng không liên quan whitelist"
    finally:
        khoi_phuc()


def test_chi_do_ip_mot_lan_trong_han_cache():
    """machine_ip_unstable gọi ipify 2 lần; không cache thì mỗi job tốn 2 request."""
    dem = {"n": 0}
    g1, g2 = browser._effective_rotating, proxyxoay.machine_ip_unstable
    browser._effective_rotating = lambda a: "https://proxyxoay.shop/api/get.php?key=x"

    def dem_roi_tra():
        dem["n"] += 1
        return True
    proxyxoay.machine_ip_unstable = dem_roi_tra
    browser._ip_unstable_cache.clear()
    try:
        for _ in range(5):
            browser.rotating_whitelist_hint("n1")
        assert dem["n"] == 1, f"phải cache, thực tế đo IP {dem['n']} lần"
    finally:
        browser._effective_rotating, proxyxoay.machine_ip_unstable = g1, g2
        browser._ip_unstable_cache.clear()


def test_tmproxy_khong_bi_do_oan():
    """tmproxy://KEY xác thực bằng CHÍNH KEY → IP máy xoay vẫn chạy tốt. Đổ oan sẽ chặn một cấu hình đang chạy được."""
    khoi_phuc = _gia_lap("tmproxy://" + "a" * 32, True)
    try:
        assert browser.rotating_whitelist_hint("n1") == "", "tmproxy không dùng whitelist IP"
    finally:
        khoi_phuc()


def test_loi_chrome_kieu_proxy_duoc_kem_ly_do():
    """Chrome chỉ ném ERR_PROXY_CONNECTION_FAILED; pool phải kèm nguyên nhân thật."""
    khoi_phuc = _gia_lap("https://proxyxoay.shop/api/get.php?key=x", True)
    try:
        ra = asyncio.run(browser_pool._kem_ly_do_proxy("n1", RuntimeError("net::ERR_PROXY_CONNECTION_FAILED")))
        assert "VPN" in str(ra), f"phải kèm lý do: {ra}"
        giu_nguyen = asyncio.run(browser_pool._kem_ly_do_proxy("n1", RuntimeError("Dola báo hết lượt")))
        assert "VPN" not in str(giu_nguyen), "lỗi không liên quan proxy thì đừng chèn câu proxy"
    finally:
        khoi_phuc()


if __name__ == "__main__":
    test_noi_ro_khi_proxy_whitelist_gap_ip_xoay()
    test_im_lang_khi_ip_on_dinh()
    test_im_lang_khi_khong_dung_proxy_xoay()
    test_chi_do_ip_mot_lan_trong_han_cache()
    test_tmproxy_khong_bi_do_oan()
    test_loi_chrome_kieu_proxy_duoc_kem_ly_do()
    print("OK: whitelist hint")


# --- Kho proxy cũng phải nói được lý do, không chỉ đường theo-nick (thêm 21/09) ---
# Đo thật 21/09: 10 proxy proxyxoay trong kho báo "[Errno 54] Connection reset by peer", mỗi cái khai
# whitelist một IP khác (194.5.83.8/.51/.31/…) vì IP máy đổi mỗi kết nối (6 lần gọi ra 5 IP). Lỗi thô đó
# không nói lên điều gì; kho proxy cần CÙNG câu giải thích mà đường theo-nick đã có.

def test_hint_cho_chuoi_proxy_khong_can_nick(monkeypatch):
    import browser
    monkeypatch.setattr(browser, "_ip_doi_theo_ket_noi", lambda: True)
    hint = browser.whitelist_hint_for_raw("https://proxyxoay.shop/api/get.php?key=abc")
    assert "whitelist" in hint.lower() and "tmproxy" in hint.lower()


def test_hint_im_lang_voi_tmproxy(monkeypatch):
    """tmproxy xác thực bằng key → IP máy xoay vẫn chạy, đừng đổ oan."""
    import browser
    monkeypatch.setattr(browser, "_ip_doi_theo_ket_noi", lambda: True)
    assert browser.whitelist_hint_for_raw("tmproxy://" + "a" * 32) == ""


def test_hint_im_lang_khi_ip_on_dinh(monkeypatch):
    import browser
    monkeypatch.setattr(browser, "_ip_doi_theo_ket_noi", lambda: False)
    assert browser.whitelist_hint_for_raw("https://proxyxoay.shop/api/get.php?key=abc") == ""


def test_ham_theo_nick_van_chay_nhu_cu(monkeypatch):
    """rotating_whitelist_hint(account) phải dùng chung thân với bản theo-chuỗi, không nhân đôi câu chữ."""
    import browser
    monkeypatch.setattr(browser, "_ip_doi_theo_ket_noi", lambda: True)
    monkeypatch.setattr(browser, "_effective_rotating",
                        lambda a: "https://proxyxoay.shop/api/get.php?key=abc")
    assert browser.rotating_whitelist_hint("nick1") == browser.whitelist_hint_for_raw(
        "https://proxyxoay.shop/api/get.php?key=abc")
