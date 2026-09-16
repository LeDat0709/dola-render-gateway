"""Self-check: ghi trạng thái cho nick CHƯA có dòng meta không được rơi vào khoảng không.

Máy VN đẩy cookie nick mới lên VPS qua /api/admin/accounts/import-cookie → server gọi
set_login_status ngay sau khi tạo thư mục profile, trước khi ai đọc pool.accounts.
Chạy: .venv/bin/python test_pool_meta.py
"""
import asyncio
import tempfile
from pathlib import Path

import browser_pool
from browser_pool import BrowserPool
from video_worker_ui import AccountLimitedError, ParameterChangeError, TransientDolaError


# Test không cần giãn nhịp thật (mặc định 3–6s mỗi lần gửi); test riêng bên dưới bật lại.
browser_pool.config.SUBMIT_GAP_SEC = 0

# Các test ở đây kiểm nhánh xử lý 710022002 SAU khi hết lượt thử lại (IP bẩn, tạm dừng proxy, xoay nick) → tắt thử lại
# cùng nick và giãn nhịp chung; hai hành vi đó có test riêng ở test_rate_limit_retry.py.
browser_pool.config.RATE_LIMIT_RETRY_WAITS = ()
browser_pool.config.SUBMIT_GAP_GLOBAL_SEC = 0
browser_pool.config.SUBMIT_JITTER_SEC = 0
browser_pool.config.AUTO_RETRY = True   # mặc định code (bật) — không kế thừa DOLA_AUTO_RETRY=0 của .env.local máy


def _pool(tmp: str, conc: int = 1) -> BrowserPool:
    return BrowserPool(accounts_dir=str(Path(tmp) / "accounts"), db_path=str(Path(tmp) / "pool.db"),
                       max_concurrency=conc)


def test_usage_day_follows_dola_reset_timezone():
    """Dola reset lượt lúc 0h JST (= 22h VN). Video 22:30 VN ngày 12 phải tính vào ngày 13 (JST); ghi theo
    ngày máy thì sáng hôm sau nick 'còn 4' oan (13/09: 2 nick bị Dola báo hết lượt dù pool ghi 0/4)."""
    from datetime import datetime, timezone, timedelta
    vn = timezone(timedelta(hours=7))
    assert BrowserPool._usage_day(datetime(2026, 9, 12, 22, 30, tzinfo=vn).timestamp()) == "2026-09-13", "22:30 VN = 00:30 JST hôm sau"
    assert BrowserPool._usage_day(datetime(2026, 9, 12, 21, 30, tzinfo=vn).timestamp()) == "2026-09-12", "21:30 VN = 23:30 JST cùng ngày"


def test_30s_costs_two_credits_and_is_blocked_before_chrome():
    """13/09: 30s Seedance 2.5 = 2 credit. Xong 1 video 30s → used=2 (không phải 1); nick đã tiêu 2/4 credit
    không được nhận thêm 30s (2+2>4) NGAY TRƯỚC khi mở Chrome, nhưng 10s (1 credit) vẫn được."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        (Path(tmp) / "accounts" / "n1").mkdir(parents=True)
        pool._settle("n1", {"video_url": "u"}, "seedance-2.5", 30, False)   # Dola không báo giá → mặc định 2
        assert pool.used_today("n1") == 2, f"30s phải tính 2 credit, đang {pool.used_today('n1')}"
        a = next(x for x in pool.list_accounts() if x["name"] == "n1")
        assert a["remaining"] == 2
        # 2/4 đã dùng: 30s thứ 2 (2+2=4) còn được; 10s Seedance 2.5 (4 credit) thì KHÔNG; 10s 2.0 (1) thì được
        assert pool._credit_short(a, pool._default_cost("seedance-2.5", 30), 30, "seedance-2.5") is None, "30s thứ 2 vẫn vừa 4 credit"
        assert pool._credit_short(a, pool._default_cost("seedance-2.5", 10), 10, "seedance-2.5"), "10s 2.5 = 4 credit, 2+4>4 phải chặn"
        assert pool._credit_short(a, pool._default_cost("seedance-2.0", 10), 10, "seedance-2.0") is None, "10s 2.0 = 1 credit vẫn chạy được"
        # 30s thứ 2 xong (Dola báo 2クレジット) → 4/4: đây là tình huống 13/09 pool đếm 2 video nên vẫn mở Chrome
        pool._settle("n1", {"video_url": "u", "credits_used": 2}, "seedance-2.5", 30, False)
        assert pool.used_today("n1") == 4, f"phải là 4 credit, đang {pool.used_today('n1')}"
        a = next(x for x in pool.list_accounts() if x["name"] == "n1")
        assert a["remaining"] == 0
        assert pool._credit_short(a, pool._default_cost("seedance-2.5", 30), 30, "seedance-2.5"), "30s thứ 3 phải bị chặn TRƯỚC khi mở Chrome"
        assert pool._credit_short(a, pool._default_cost("seedance-2.0", 10), 10, "seedance-2.0"), "hết credit thì 10s cũng chặn"
        assert not pool._schedulable(a), "4/4 credit → không schedulable"
        assert pool._cost_for("seedance-2.5", 30) == 2, "phải học được giá 30s = 2 từ câu của Dola"


def test_status_for_unknown_nick_is_kept():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        pool.set_login_status("moi", True)
        pool.set_email("moi", "moi@example.com")
        meta = pool._meta("moi")
        assert meta is not None and meta["login_ok"] == 1, "login_ok của nick mới bị mất"
        assert meta["email"] == "moi@example.com", "email của nick mới bị mất"


def test_new_profile_dir_shows_up_without_restart():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        assert pool.accounts == []
        (Path(tmp) / "accounts" / "nick2").mkdir(parents=True)
        assert pool.accounts == ["nick2"], "nick tạo sau khi pool khởi động phải hiện ngay"


def _fake_gen(*outcomes):
    calls = []
    async def gen(account, *a, **kw):
        calls.append(account)
        r = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(r, Exception):
            raise r
        return r
    gen.calls = calls
    return gen


def test_timeout_in_retry_does_not_rotate():
    """Log 11/9 11:09: lỗi tạm thời → thử lại → quá giờ → 'xoay nick' = gửi lần 2 trong khi Dola vẫn dựng video cũ."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        for n in ("n1", "n2"):
            (Path(tmp) / "accounts" / n).mkdir(parents=True)
        gen = _fake_gen(TransientDolaError("エラーが発生しました"), TimeoutError("Hết 237s chưa ra video"))
        browser_pool.generate_video = gen
        try:
            asyncio.run(pool.generate_video("p", "9:16", 10, account="n1"))
            assert False, "phải ném TimeoutError"
        except TimeoutError:
            pass
        assert gen.calls == ["n1", "n1"], gen.calls     # thử lại 1 lần trên chính nick, KHÔNG sang n2
        assert pool.used_today("n1") == 1                # video vẫn đang dựng trên Dola → tính lượt


def test_pinned_nick_reports_real_reason():
    """Ảnh 11/9 15:57: nick 'sẵn sàng' mà job báo 'Hết nick chạy được' — lý do thật bị bọc mất."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        (Path(tmp) / "accounts" / "n1").mkdir(parents=True)
        browser_pool.generate_video = _fake_gen(AccountLimitedError("Hết lượt tạo video hôm nay. Dola: 上限"))
        try:
            asyncio.run(pool.generate_video("p", "9:16", 10, account="n1"))
            assert False, "phải ném lỗi"
        except AccountLimitedError as e:
            assert "Hết lượt" in str(e)
        except RuntimeError as e:
            assert False, f"lý do thật bị bọc thành: {e}"


def test_auto_retry_off_fails_fast():
    """Tắt 'tự thử lại / xoay nick': lỗi tạm thời → dừng ngay, 1 lần gửi, không sang nick khác."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        for n in ("n1", "n2"):
            (Path(tmp) / "accounts" / n).mkdir(parents=True)
        gen = _fake_gen(TransientDolaError("エラーが発生しました"))
        browser_pool.generate_video = gen
        browser_pool.config.AUTO_RETRY = False
        try:
            try:
                asyncio.run(pool.generate_video("p", "9:16", 10))      # không ghim nick → bình thường sẽ xoay
                assert False, "phải ném lỗi"
            except TransientDolaError:
                pass
            assert gen.calls == ["n1"], gen.calls                     # đúng 1 lần gửi, không thử lại, không sang n2
        finally:
            browser_pool.config.AUTO_RETRY = True


def test_submits_are_paced():
    """DomixHub: nghỉ + jitter giữa các job. 3 job song song không được gửi cùng một giây."""
    import time
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp, conc=3)
        for n in ("n1", "n2", "n3"):
            (Path(tmp) / "accounts" / n).mkdir(parents=True)
        stamps = []
        async def gen(account, *a, **kw):
            stamps.append(time.monotonic())
            return {"ok": True}
        browser_pool.generate_video = gen
        browser_pool._reset_rate_state()   # reset adaptive pacing learned_gap
        browser_pool.config.SUBMIT_GAP_SEC = 0.3
        try:
            async def main():
                await asyncio.gather(*(pool.generate_video("p", "9:16", 10, account=n) for n in ("n1", "n2", "n3")))
            asyncio.run(main())
        finally:
            browser_pool.config.SUBMIT_GAP_SEC = 0
        stamps.sort()
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        assert len(stamps) == 3 and all(g >= 0.25 for g in gaps), gaps


def test_unpinned_job_stops_after_max_rotate():
    """DomixHub xoay tối đa 3 nick. Trước đây duyệt hết danh sách → 1 lỗi = mở Chrome trên cả kho."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        for i in range(6):
            (Path(tmp) / "accounts" / f"n{i}").mkdir(parents=True)
        gen = _fake_gen(AccountLimitedError("Hết lượt tạo video hôm nay"))
        browser_pool.generate_video = gen
        try:
            asyncio.run(pool.generate_video("p", "9:16", 10))       # không ghim nick
            assert False, "phải ném lỗi"
        except RuntimeError as e:
            assert "Đã thử 3 nick" in str(e), str(e)
        assert len(gen.calls) == 3, gen.calls                       # dừng ở 3, không sang n3..n5


def test_credit_cost_learned_and_enforced():
    """Log 11/9: Dola "4動画クレジット… 残り2" — trước đây nick "còn 2" vẫn bị gửi lại rồi lỗi (23 lần/ngày)."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        (Path(tmp) / "accounts" / "n1").mkdir(parents=True)
        err = ParameterChangeError("Không đủ lượt cho video này")
        err.need, err.left = 4, 2
        gen = _fake_gen(err)
        browser_pool.generate_video = gen
        try:
            asyncio.run(pool.generate_video("p", "9:16", 10, model="seedance_v2.5", account="n1"))
            assert False, "phải ném lỗi"
        except ParameterChangeError:
            pass
        assert pool._cost_for("seedance_v2.5", 10) == 4                 # học được giá video
        assert pool._meta("n1")["credit_balance"] == 2                  # và credit còn lại của nick
        try:
            asyncio.run(pool.generate_video("p", "9:16", 10, model="seedance_v2.5", account="n1"))
            assert False, "phải chặn trước khi mở Chrome"
        except RuntimeError as e:
            assert "còn 2 credit" in str(e) and "cần 4" in str(e), str(e)
        assert len(gen.calls) == 1                                       # lần 2 không gửi gì lên Dola


def test_credit_is_learned_and_deducted_after_success():
    """A1+A2: video xong → học giá từ "4動画クレジットを使用", trừ credit nick; còn 2 < 4 thì chặn trước khi mở Chrome."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        (Path(tmp) / "accounts" / "n1").mkdir(parents=True)
        pool._ensure_meta("n1")
        pool._set_credit_balance("n1", 10, "test")
        gen = _fake_gen({"video_url": "u", "local_path": "p", "credits_used": 4})
        browser_pool.generate_video = gen
        run = lambda: asyncio.run(pool.generate_video("p", "9:16", 10, model="seedance_v2.5", account="n1"))
        run(); run()
        assert pool._cost_for("seedance_v2.5", 10) == 4                 # học giá ngay video đầu
        assert pool._meta("n1")["credit_balance"] == 2                  # 10 − 4 − 4
        try:
            run(); assert False, "phải chặn trước khi mở Chrome"
        except RuntimeError as e:
            assert "còn 2 credit" in str(e) and "cần 4" in str(e), str(e)
        assert len(gen.calls) == 2


def test_no_double_deduction_when_dola_reports_balance():
    """Dola báo "残り3" ngay trong job → tin số của Dola, không trừ thêm lần nữa."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        (Path(tmp) / "accounts" / "n1").mkdir(parents=True)
        pool._ensure_meta("n1")
        pool._set_credit_balance("n1", 10, "test")
        async def gen(account, *a, **kw):
            kw["on_balance"](3, "本日は残り3")
            return {"video_url": "u", "local_path": "p", "credits_used": 4}
        browser_pool.generate_video = gen
        asyncio.run(pool.generate_video("p", "9:16", 10, model="seedance_v2.5", account="n1"))
        assert pool._meta("n1")["credit_balance"] == 3


def test_yesterdays_credit_reading_is_forgotten():
    """Credit Dola reset theo ngày: số 0 đọc trước mốc reset không được kẹt nick hôm nay."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        (Path(tmp) / "accounts" / "n1").mkdir(parents=True)
        pool._ensure_meta("n1")
        pool._conn.execute("UPDATE accounts_meta SET credit_balance=0, credit_checked_at=? WHERE name='n1'",
                           (pool._next_limit_reset() - 86400 - 60,))    # đọc 1 phút trước mốc reset
        pool._conn.commit()
        a = pool.list_accounts()[0]
        assert a["credit_balance"] is None and pool._schedulable(a), a
        pool._set_credit_balance("n1", 0, "test")                        # đọc hôm nay → chặn thật
        a = pool.list_accounts()[0]
        assert a["credit_balance"] == 0 and not pool._schedulable(a)


def test_network_error_does_not_kill_nick():
    """Máy mới chưa có proxy (ảnh 12/9: 8/9 nick 'cookie chết' ngay sau khi nhập): kiểm tra phiên lỗi
    mạng → login_ok để trống, nick vẫn xếp lịch được; chỉ Dola nói 'chưa đăng nhập' mới là chết."""
    import browser
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        (Path(tmp) / "accounts" / "n1").mkdir(parents=True)
        (Path(tmp) / "accounts" / "n1" / "cookies.json").write_text('[{"name":"sessionid","value":"x"}]')
        saved = browser.config.PROXY
        browser.config.PROXY = "http://127.0.0.1:9"      # cổng đóng → lỗi mạng ngay, không ra Internet
        try:
            assert asyncio.run(pool.verify_account_http("n1")) is None
        finally:
            browser.config.PROXY = saved
        assert pool._meta("n1")["login_ok"] is None, "lỗi mạng không được ghi 0"
        assert pool._schedulable(pool.list_accounts()[0])
        pool.set_login_status("n1", False)
        assert pool._meta("n1")["login_ok"] == 0 and not pool._schedulable(pool.list_accounts()[0])


def _reset_rate_limit():
    browser_pool._reset_rate_state()
    browser_pool.config.NO_COOLDOWN = False   # kiểm hành vi nghỉ mặc định, không phụ thuộc .env.local


def test_rate_limit_widens_submit_gap_then_decays():
    """Mỗi lần dính 710022002 thì giãn nhịp gửi thêm RATE_LIMIT_GAP_STEP (không dồn vào IP y như cũ),
    có trần, và tự giảm dần khi êm — để vòng dừng-chạy-lại không leo thang mãi."""
    b = browser_pool
    _reset_rate_limit()
    try:
        b.note_rate_limited(now=1000.0)
        assert b._effective_gap_boost(1000.0) == b.RATE_LIMIT_GAP_STEP
        # dính lại (qua đợt, pause=90s > 60s decay → boost cũ đã decay hết)
        t2 = 1000.0 + b.RATE_LIMIT_PAUSE_SEC + 1
        b.note_rate_limited(now=t2)
        after = b._effective_gap_boost(t2)
        # boost cũ decay hết (90s > 60s), boost mới = STEP
        assert after == b.RATE_LIMIT_GAP_STEP, f"expected {b.RATE_LIMIT_GAP_STEP}, got {after}"
        assert after <= b.RATE_LIMIT_GAP_MAX
        # để yên vài phút → giãn nhịp giảm về 0
        assert b._effective_gap_boost(t2 + 600) == 0.0
    finally:
        _reset_rate_limit()


def test_rate_limited_pauses_everyone_then_rotates():
    """Log 12/9 'Rate limited: {"error_code":710022002…}': Dola báo gửi quá dày (tính theo IP, 46 nick chung
    một IP) → dừng gửi TOÀN BỘ một lúc rồi mới xoay; nick dính chỉ nghỉ ngắn, không phải 30 phút."""
    import time
    from video_worker import RateLimitedError, _check_submit
    try:
        _check_submit({"errors": ['{"error_code":710022002,"error_msg":"操作频繁"}'], "status": 200})
        assert False, "phải ném RateLimitedError"
    except RateLimitedError as e:
        assert "gửi quá dày" in str(e) and "操作频繁" in str(e), str(e)
    _reset_rate_limit()
    saved = browser_pool.RATE_LIMIT_PAUSE_SEC
    saved_ar = browser_pool.config.AUTO_RETRY
    browser_pool.config.AUTO_RETRY = True   # kiểm xoay nick → phải BẬT (không phụ thuộc DOLA_AUTO_RETRY của .env.local)
    browser_pool.RATE_LIMIT_PAUSE_SEC = 0.3
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pool = _pool(tmp)
            for n in ("n1", "n2"):
                (Path(tmp) / "accounts" / n).mkdir(parents=True)
            gen = _fake_gen(RateLimitedError("Dola tạm chặn vì gửi quá dày (710022002): x"), {"ok": True})
            browser_pool.generate_video = gen
            t0 = time.monotonic()
            asyncio.run(pool.generate_video("p", "9:16", 10))
            assert sorted(gen.calls) == ["n1", "n2"] and len(gen.calls) == 2, gen.calls
            assert time.monotonic() - t0 >= 0.3, "nick sau phải chờ hết lệnh tạm dừng toàn cục"
            rest = pool._meta(gen.calls[0])["cooldown_until"] - time.time()
            assert 0 < rest <= browser_pool.RATE_LIMIT_NICK_SEC, rest      # nghỉ 5 phút, không phải 30
    finally:
        browser_pool.RATE_LIMIT_PAUSE_SEC = saved
        browser_pool.config.AUTO_RETRY = saved_ar
        _reset_rate_limit()


def test_rate_limit_backoff_escalates_only_across_bursts():
    """10 job song song cùng dính một đợt = một lần dừng; dính lại sau khi hết dừng (trong 10 phút) mới gấp đôi."""
    b = browser_pool
    _reset_rate_limit()
    try:
        assert b.note_rate_limited(now=1000.0) == b.RATE_LIMIT_PAUSE_SEC
        assert b.note_rate_limited(now=1001.0) < b.RATE_LIMIT_PAUSE_SEC              # cùng đợt: chỉ báo phần còn lại
        assert b.note_rate_limited(now=1000.0 + b.RATE_LIMIT_PAUSE_SEC + 1) == 2 * b.RATE_LIMIT_PAUSE_SEC
        assert b.note_rate_limited(now=9000.0) == b.RATE_LIMIT_PAUSE_SEC             # yên quá 10 phút: về mức đầu
        p = 0.0
        for _ in range(6):
            p = b.note_rate_limited(now=b._rs("")["until"] + 1)
        assert p == b.RATE_LIMIT_PAUSE_MAX, p
    finally:
        _reset_rate_limit()


def test_rate_limit_is_per_proxy():
    """710022002 tính theo IP thoát: proxy A dính quá tải KHÔNG được làm dừng/giãn nhịp nick trên proxy B."""
    b = browser_pool
    _reset_rate_limit()
    try:
        assert b.note_rate_limited(now=1000.0, key="proxyA") == b.RATE_LIMIT_PAUSE_SEC
        assert b._rs("proxyA")["until"] == 1000.0 + b.RATE_LIMIT_PAUSE_SEC
        assert b._rs("proxyB")["until"] == 0.0                       # proxy B không dính theo proxy A
        assert b._effective_gap_boost(1000.0, "proxyB") == 0.0       # proxy B không bị giãn nhịp
        assert b._effective_gap_boost(1000.0, "proxyA") == b.RATE_LIMIT_GAP_STEP
    finally:
        _reset_rate_limit()


def test_submit_gap_change_applies_to_proxy_already_running():
    """Đổi nhịp gửi lúc ĐANG CHẠY (/api/admin/submit-gap) phải ăn ngay với proxy ĐÃ gửi rồi.

    Trước đây adaptive pacing lưu gap TUYỆT ĐỐI, chụp config.SUBMIT_GAP_SEC một lần lúc khoá proxy được tạo →
    người dùng đang bị 710022002, kéo nhịp 3s lên 8s, UI báo OK mà proxy đang chạy vẫn giữ 3s tới khi restart.
    Đúng cái núm người ta với tay tới khi đang bị chặn thì lại không có tác dụng.
    """
    b = browser_pool
    _reset_rate_limit()
    saved = b.config.SUBMIT_GAP_SEC
    try:
        b.config.SUBMIT_GAP_SEC = 3.0
        b._rs("proxyA")                      # proxy A đã gửi job → khoá đã tồn tại
        b.note_rate_limited(now=1000.0, key="proxyA")
        b.note_rate_limited(now=1000.0 + b.RATE_LIMIT_PAUSE_SEC + 1, key="proxyA")
        assert b._rs("proxyA")["learned_extra"] == 2.0, b._rs("proxyA")

        b.config.SUBMIT_GAP_SEC = 8.0        # người dùng kéo nhịp lên trong Cài đặt
        s = b._rs("proxyA")
        assert b.config.SUBMIT_GAP_SEC + s["learned_extra"] == 10.0, s
        # proxy chưa từng gửi cũng phải ra CÙNG mức nền, không lệch nhau
        assert b.config.SUBMIT_GAP_SEC + b._rs("proxyB")["learned_extra"] == 8.0

        for _ in range(b._ADAPTIVE_STREAK_THRESHOLD):
            b.note_submit_ok("proxyA")
        assert b._rs("proxyA")["learned_extra"] == 1.5, "5 job OK liên tiếp → bớt phạt 0.5s"
        # phạt không bao giờ kéo nhịp xuống dưới mức người dùng đặt
        for _ in range(b._ADAPTIVE_STREAK_THRESHOLD * 10):
            b.note_submit_ok("proxyA")
        assert b._rs("proxyA")["learned_extra"] == 0.0, "sàn phạt là 0"
    finally:
        b.config.SUBMIT_GAP_SEC = saved
        _reset_rate_limit()


if __name__ == "__main__":
    test_rate_limit_is_per_proxy()
    test_rate_limited_pauses_everyone_then_rotates()
    test_rate_limit_backoff_escalates_only_across_bursts()
    test_network_error_does_not_kill_nick()
    test_credit_is_learned_and_deducted_after_success()
    test_no_double_deduction_when_dola_reports_balance()
    test_yesterdays_credit_reading_is_forgotten()
    test_credit_cost_learned_and_enforced()
    test_usage_day_follows_dola_reset_timezone()
    test_30s_costs_two_credits_and_is_blocked_before_chrome()
    test_status_for_unknown_nick_is_kept()
    test_new_profile_dir_shows_up_without_restart()
    test_timeout_in_retry_does_not_rotate()
    test_pinned_nick_reports_real_reason()
    test_auto_retry_off_fails_fast()
    test_submits_are_paced()
    test_submit_gap_change_applies_to_proxy_already_running()
    test_rate_limit_widens_submit_gap_then_decays()   # trước đây định nghĩa mà KHÔNG ai gọi
    test_unpinned_job_stops_after_max_rotate()
    print("OK")
