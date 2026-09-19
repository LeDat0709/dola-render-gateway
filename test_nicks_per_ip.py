"""N LƯỢT/IP khi MỖI NICK MỘT PROXY RIÊNG — chạy .venv/bin/python test_nicks_per_ip.py (không cần mạng).

Lỗ được khoá ở đây: cả khối "mỗi IP dùng N nick rồi xin IP mới" từng nằm trong `if one_nick_on:`, mà
    one_nick_on = config.ONE_NICK and is_rotating_proxy(config.PROXY)
Thực tế DOLA_PROXY để RỖNG (mỗi nick một proxy.txt riêng) → one_nick_on luôn False → DOLA_NICKS_PER_IP=2
KHÔNG chạy một dòng nào, nhiều nick dồn chung một IP cho tới khi Dola bung 710022002.

Bốn bất biến:
  1) Đếm RIÊNG từng khoá proxy — job của khoá A không làm khoá B xoay IP.
  2) Không bao giờ xoay IP khi còn job đang chạy trên khoá đó (cắt IP = mất video đã trừ lượt).
  3) KHÔNG serialize — người dùng đang cần max luồng, không được chờ khoá rỗng mới cho chạy.
  4) Nhà bán còn cooldown (trả nguyên IP cũ dù báo thành công) → KHÔNG được reset số đếm, không báo "IP mới".
"""
import asyncio
import tempfile
from pathlib import Path

import browser
import browser_pool
import config
from browser_pool import BrowserPool

KEY_A = browser.normalize_proxy_input("tmproxy://" + "a" * 32)
KEY_B = browser.normalize_proxy_input("tmproxy://" + "b" * 32)


async def _nopace(*a, **k):      # bỏ giãn nhịp gửi: đo song song thuần, khỏi lệ thuộc DOLA_SUBMIT_GAP
    return None


def _pool(tmp, nicks: dict):
    """nicks: {tên nick: chuỗi proxy riêng ("" = nối thẳng)}."""
    accounts = Path(tmp) / "accounts"
    for n, raw in nicks.items():
        (accounts / n).mkdir(parents=True)
        if raw:
            (accounts / n / "proxy.txt").write_text(raw, encoding="utf-8")
    config.ACCOUNTS_DIR = accounts   # account_proxy_raw đọc CÁI NÀY, không đọc pool.accounts_dir
    p = BrowserPool(accounts_dir=str(accounts), db_path=str(Path(tmp) / "pool.db"), max_concurrency=len(nicks))
    for n in nicks:
        p.set_login_status(n, True)
    return p


def _fakes(ghi, peak, doi_ip_that=True):
    """Thay lời gọi mạng bằng sổ ghi. doi_ip_that=False mô phỏng nhà bán còn cooldown: báo OK, IP y nguyên."""
    live = {"n": 0}
    endpoint = {KEY_A: 0, KEY_B: 0}

    async def fake_generate(account, prompt, *a, on_browser_free=None, **kw):
        live["n"] += 1
        peak.append(live["n"])
        try:
            await asyncio.sleep(0.05)
            if on_browser_free:
                on_browser_free()
            await asyncio.sleep(0.15)     # render
            return {"local_path": f"/tmp/{account}.mp4", "account": account}
        finally:
            live["n"] -= 1

    def fake_rotate(account):
        key = browser._effective_rotating(account)
        # Ghi sổ giữ chỗ ĐÚNG lúc xoay: >0 nghĩa là vừa cắt IP dưới chân một job đang chạy.
        ghi["xoay"].append((key, browser._proxy_leases.get(key, 0)))
        if doi_ip_that:
            endpoint[key] = endpoint.get(key, 0) + 1
        return True

    def fake_status(raw):
        key = browser.normalize_proxy_input(raw)
        if key not in endpoint:
            return {}
        # expires_in lớn để rotate_if_expiring (chạy trước đó trong _run_worker) không tự xoay thêm
        return {"endpoint": f"10.0.0.{endpoint[key]}:1000", "expires_in": 999999}

    browser_pool.generate_video = fake_generate
    browser_pool._pace = _nopace
    browser.rotate_effective_proxy = fake_rotate
    browser.rotating_status = fake_status


async def _chay(tmp, nicks: dict, doi_ip_that=True):
    # ONE_NICK=True + PROXY rỗng: chứng minh N lượt/IP KHÔNG còn phụ thuộc hai biến này.
    # XOAY_THEO_LUOT là công tắc riêng (mặc định TẮT) vì xoay chủ động đổi giao kèo cũ "chỉ xoay khi cần".
    # Lưu MỌI global sẽ đụng rồi khôi phục ở finally: _fakes vá browser.rotating_status/rotate_effective_proxy/_pace/
    # generate_video, và ta ghim config — không hoàn thì rò sang file test sau (proxyxoay/dead_rotation đỏ oan khi
    # chạy chung). Ghim SUBMIT_MODE="chrome" + MAX_JOBS_PER_IP=0 để không phụ thuộc .env.local của máy.
    saved_cfg = (config.ONE_NICK, config.PROXY, config.NICKS_PER_IP, config.XOAY_THEO_LUOT,
                 config.SUBMIT_MODE, config.MAX_JOBS_PER_IP)
    saved_glob = (browser_pool.generate_video, browser_pool._pace, browser.rotate_effective_proxy,
                  browser.rotating_status)
    config.ONE_NICK, config.PROXY, config.NICKS_PER_IP = True, "", 2
    config.XOAY_THEO_LUOT = True
    config.SUBMIT_MODE, config.MAX_JOBS_PER_IP = "chrome", 0
    # Dọn trạng thái proxy DÙNG CHUNG (module-global) để độc lập với file test chạy trước: sổ giữ chỗ, IP bẩn, WAF-key.
    browser._proxy_leases.clear()
    browser._dirty_ips.clear()
    try:
        import proxy_status
        proxy_status._status.clear()
    except Exception:  # noqa: BLE001
        pass
    try:
        pool = _pool(tmp, nicks)
        ghi, peak = {"xoay": []}, []
        _fakes(ghi, peak, doi_ip_that)
        ket = await asyncio.wait_for(
            asyncio.gather(*(pool.generate_video(f"p{i}", account=n) for i, n in enumerate(nicks)),
                           return_exceptions=True),
            timeout=30)   # serialize hoặc deadlock → treo → TimeoutError làm test đỏ thay vì treo mãi
        return pool, ghi, max(peak), ket
    finally:
        (config.ONE_NICK, config.PROXY, config.NICKS_PER_IP, config.XOAY_THEO_LUOT,
         config.SUBMIT_MODE, config.MAX_JOBS_PER_IP) = saved_cfg
        (browser_pool.generate_video, browser_pool._pace, browser.rotate_effective_proxy,
         browser.rotating_status) = saved_glob


def test_dem_rieng_tung_khoa_proxy():
    """4 nick khoá A + 2 nick khoá B, N=2: mỗi khoá tự đếm lượt của mình."""
    with tempfile.TemporaryDirectory() as tmp:
        nicks = {f"a{i}": "tmproxy://" + "a" * 32 for i in range(1, 5)}
        nicks.update({f"b{i}": "tmproxy://" + "b" * 32 for i in range(1, 3)})
        nicks["solo"] = ""                       # nối thẳng: không đếm, không xoay
        pool, ghi, peak, ket = asyncio.run(_chay(tmp, nicks))

        loi = [r for r in ket if isinstance(r, Exception)]
        assert not loi, f"job lỗi: {loi}"
        khoa_xoay = [k for k, _ in ghi["xoay"]]
        # sổ rỗng lúc bật server = coi như đủ lô → job đầu mỗi khoá xoay thật; rồi cứ 2 lượt một lần.
        assert khoa_xoay.count(KEY_A) == 2, f"khoá A: 4 job / N=2 phải xoay 2 lần, thực tế {khoa_xoay.count(KEY_A)}"
        assert khoa_xoay.count(KEY_B) == 1, f"khoá B: 2 job / N=2 phải xoay 1 lần, thực tế {khoa_xoay.count(KEY_B)}"
        assert "" not in pool._ip_used_by_key, "nick nối thẳng không được vào sổ đếm"
        assert all(k for k, _ in ghi["xoay"]), "không được xoay cho nick nối thẳng"


def test_khong_bao_gio_cat_ip_cua_job_dang_chay():
    """Chốt sinh tử: xoay IP lúc còn job trên khoá đó = cắt cổng của video ĐÃ TRỪ LƯỢT."""
    with tempfile.TemporaryDirectory() as tmp:
        nicks = {f"a{i}": "tmproxy://" + "a" * 32 for i in range(1, 5)}
        _, ghi, _, _ = asyncio.run(_chay(tmp, nicks))
        giu = [n for _, n in ghi["xoay"]]
        assert giu == [0] * len(giu), f"xoay IP khi còn job đang chạy trên proxy đó: sổ giữ chỗ {giu}"


def test_khong_serialize_khi_mo_max_luong():
    """Người dùng vừa yêu cầu mở max luồng — nhánh mới không được chờ khoá rỗng mới cho chạy."""
    with tempfile.TemporaryDirectory() as tmp:
        nicks = {f"a{i}": "tmproxy://" + "a" * 32 for i in range(1, 5)}
        _, _, peak, _ = asyncio.run(_chay(tmp, nicks))
        assert peak > 1, f"bị serialize: cao nhất chỉ {peak} job chạy cùng lúc"


def test_nha_ban_cooldown_thi_khong_reset_so_dem():
    """rotate() còn cooldown → trả NGUYÊN IP CŨ dù báo thành công. Reset số đếm lúc đó = tưởng đã sang IP mới."""
    with tempfile.TemporaryDirectory() as tmp:
        nicks = {f"a{i}": "tmproxy://" + "a" * 32 for i in range(1, 4)}
        pool, ghi, _, _ = asyncio.run(_chay(tmp, nicks, doi_ip_that=False))
        # 3 job, lần nào cũng thấy used >= n (vì không reset) nên lần nào cũng thử xoay
        assert len(ghi["xoay"]) == 3, f"IP không đổi thì lượt nào cũng phải thử xoay lại, thực tế {len(ghi['xoay'])}"
        assert pool._ip_used_by_key[KEY_A] > config.NICKS_PER_IP, \
            f"IP không hề đổi mà số đếm bị reset: {pool._ip_used_by_key[KEY_A]}"


def main():
    goc = (browser_pool.generate_video, browser_pool._pace, browser.rotate_effective_proxy,
           browser.rotating_status, config.ONE_NICK, config.PROXY, config.NICKS_PER_IP, config.ACCOUNTS_DIR, config.XOAY_THEO_LUOT)
    try:
        for t in (test_dem_rieng_tung_khoa_proxy, test_khong_bao_gio_cat_ip_cua_job_dang_chay,
                  test_khong_serialize_khi_mo_max_luong, test_nha_ban_cooldown_thi_khong_reset_so_dem):
            browser._proxy_leases.clear()
            t()
            print(f"PASS {t.__name__}")
    finally:
        (browser_pool.generate_video, browser_pool._pace, browser.rotate_effective_proxy,
         browser.rotating_status, config.ONE_NICK, config.PROXY, config.NICKS_PER_IP,
         config.ACCOUNTS_DIR, config.XOAY_THEO_LUOT) = goc
    print("OK")


if __name__ == "__main__":
    main()
