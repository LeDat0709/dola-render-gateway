"""Regression: MỌI đường verify phải đi qua CỔNG RIÊNG của verify, không bao giờ để N nick cùng gõ cửa Dola.

Thay cho bản cũ (gác `_global_submit_gate`): khe đó dành cho LỆNH GỬI VIDEO — 35 nick kiểm cùng lúc từng
giữ 35 khe (~9 phút) làm video thật phải chờ 242s (log 19/09). Nhưng bỏ hẳn gác thì "Kiểm tra tất cả" 50 nick
bắn 50 request cùng lúc từ một IP → 710022002, đúng nhóm lỗi lớn nhất trong log 20/09. Cổng riêng giữ cả hai.

Chạy: .venv/bin/python test_login_bypass_regression.py
"""
import asyncio
import json
import tempfile
from pathlib import Path

import browser
import browser_pool
from browser_pool import BrowserPool


def _pool_voi_nick(tmp: str, so_nick: int):
    pool = BrowserPool(accounts_dir=str(Path(tmp) / "accounts"), db_path=str(Path(tmp) / "pool.db"))
    ten = [f"n{i}" for i in range(so_nick)]
    for n in ten:
        d = Path(tmp) / "accounts" / n
        d.mkdir(parents=True)
        (d / "cookies.json").write_text(json.dumps([{"name": "sessionid", "value": "x"}]), encoding="utf-8")
    return pool, ten


def _do_dong_thoi(goi_ham):
    """Chạy song song và trả về SỐ REQUEST CÙNG LÚC cao nhất chạm tới Dola."""
    dang, dinh = {"n": 0}, {"n": 0}

    async def fake_verify(cookie_str, timeout=20, proxy=None):
        dang["n"] += 1
        dinh["n"] = max(dinh["n"], dang["n"])
        await asyncio.sleep(0.05)
        dang["n"] -= 1
        return True, "ok"

    goc = browser.verify_cookie_http
    browser.verify_cookie_http = fake_verify
    try:
        ket = asyncio.run(goi_ham())
    finally:
        browser.verify_cookie_http = goc
    # Chống "qua giả": nếu cổng hỏng và lỗi bị nuốt thì không request nào tới Dola, đỉnh đồng thời = 0
    # mà assert trần vẫn qua. Bắt buộc mọi nick phải thật sự được kiểm.
    assert dinh["n"] >= 1, "không request nào tới được Dola — cổng hỏng và lỗi bị nuốt?"
    return dinh["n"], ket


def test_verify_http_khong_vuot_tran_dong_thoi():
    with tempfile.TemporaryDirectory() as tmp:
        pool, ten = _pool_voi_nick(tmp, 12)

        async def chay():
            return await asyncio.gather(*(pool.verify_account_http(n) for n in ten))

        dinh, _ = _do_dong_thoi(chay)
        assert dinh <= browser_pool.VERIFY_CONCURRENCY, \
            f"{dinh} request cùng lúc tới Dola, trần là {browser_pool.VERIFY_CONCURRENCY} — cổng verify bị gỡ?"
        assert dinh > 1, "cổng không được biến verify thành tuần tự hoàn toàn (kiểm 50 nick sẽ quá chậm)"


def test_verify_all_cung_di_qua_cong():
    """Đường 'Kiểm tra tất cả' của Studio — nơi từng bắn 50 request một lúc."""
    with tempfile.TemporaryDirectory() as tmp:
        pool, _ = _pool_voi_nick(tmp, 12)
        dinh, ket = _do_dong_thoi(lambda: pool.verify_all())
        assert dinh <= browser_pool.VERIFY_CONCURRENCY, \
            f"verify_all bắn {dinh} request cùng lúc, trần là {browser_pool.VERIFY_CONCURRENCY}"
        assert all(r["checked"] and r["ok"] for r in ket), f"nick bị nuốt lỗi thay vì kiểm xong: {ket}"


if __name__ == "__main__":
    test_verify_http_khong_vuot_tran_dong_thoi()
    test_verify_all_cung_di_qua_cong()
    print("OK: cổng verify còn nguyên")
