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
