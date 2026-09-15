"""Bất biến AN TOÀN TIỀN khi xoay nick (đề xuất sửa proxy/xoay job, 15/09).

Dola TRỪ CREDIT ngay khi nhận lệnh tạo video. Nên:
- Lỗi TRƯỚC khi gửi lệnh (hết điểm theo lời Dola, proxy riêng không lấy được IP, treo lúc mở nick) → XOAY nick là an toàn.
- Lỗi SAU khi đã gửi (on_submitted(account, True) đã chạy) → TUYỆT ĐỐI không xoay: gửi lại = trừ lượt 2 lần (log 11/9).
- Treo lúc mở nick không được giữ slot mãi (ảnh 15/09: "đang mở nick" 53 phút), nhưng render dài SAU khi gửi thì không được cắt.

Chạy: .venv/bin/python test_rotation_safety.py
"""
import asyncio
import tempfile
from pathlib import Path

import browser_pool
from browser_pool import BrowserPool
from video_worker import DownloadError
from video_worker_ui import ParameterChangeError, TransientDolaError, _FetchDelivered

browser_pool.config.SUBMIT_GAP_SEC = 0
browser_pool.config.SUBMIT_JITTER_SEC = 0
browser_pool.config.AUTO_RETRY = True

GUARD_SEC = 3.0   # test nào treo quá mức này = hỏng (không để runner treo theo)


def _pool(tmp: str, nicks=("n1", "n2")) -> BrowserPool:
    p = BrowserPool(accounts_dir=str(Path(tmp) / "accounts"), db_path=str(Path(tmp) / "pool.db"), max_concurrency=2)
    for n in nicks:
        (Path(tmp) / "accounts" / n).mkdir(parents=True)
    return p


def _run(coro, guard: float = GUARD_SEC):
    return asyncio.run(asyncio.wait_for(coro, guard))


def _scripted(plan):
    """plan: {nick: [bước, ...]} — mỗi lần gọi nick đó lấy bước kế. Bước: ("ok",) | ("raise", exc) |
    ("submit_raise", exc) (gửi lệnh rồi mới lỗi) | ("hang",) (treo TRƯỚC khi gửi) | ("submit_sleep", giây) (gửi rồi render lâu)."""
    calls, submits = [], []
    idx = {}

    async def gen(account, *a, **kw):
        calls.append(account)
        i = idx.get(account, 0)
        idx[account] = i + 1
        steps = plan[account]
        step = steps[min(i, len(steps) - 1)]
        cb = kw.get("on_submitted")
        kind = step[0]
        if kind == "ok":
            if cb:
                cb(account, True)
                submits.append(account)
            return {"video_url": f"u-{account}", "account": account}
        if kind == "raise":
            raise step[1]
        if kind == "submit_raise":
            if cb:
                cb(account, True)
                submits.append(account)
            raise step[1]
        if kind == "hang":
            await asyncio.sleep(3600)
        if kind == "submit_sleep":
            if cb:
                cb(account, True)
                submits.append(account)
            await asyncio.sleep(step[1])
            return {"video_url": f"u-{account}", "account": account}
        raise AssertionError(f"bước lạ {step}")

    gen.calls, gen.submits = calls, submits
    return gen


def _server_spy():
    got = []
    return got, (lambda account, submitted: got.append((account, submitted)))


# ---------- R1: hết điểm theo lời Dola ----------
# Soát code 15/09: ParameterChangeError chỉ được ném trong poll (video_worker_ui.py:1250 và :1360), tức LUÔN
# sau khi lệnh đã tới Dola. Nên quyết định xoay phải đi qua cổng "đã gửi": chưa gửi mới xoay, đã gửi thì báo lỗi.
def test_param_change_after_submit_never_rotates():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        err = ParameterChangeError("Không đủ lượt cho video này")
        err.need, err.left = 4, 2
        gen = _scripted({"n1": [("submit_raise", err)], "n2": [("ok",)]})
        browser_pool.generate_video = gen
        try:
            _run(pool.generate_video("p", "9:16", 10, model="seedance_v2.5", account="n1"))
            raise AssertionError("đã gửi lệnh rồi mới biết thiếu điểm → phải báo lỗi, không tạo lại trên nick khác")
        except ParameterChangeError:
            pass
        assert gen.calls == ["n1"], f"xoay sau khi đã gửi: {gen.calls} (= trừ lượt 2 lần)"
        assert pool._meta("n1")["credit_balance"] == 2, "vẫn phải học credit còn lại của n1"
        assert pool._cost_for("seedance_v2.5", 10) == 4, "vẫn phải học giá video"


def test_param_change_before_submit_rotates():
    """Nếu về sau Dola báo thiếu điểm TRƯỚC khi nhận lệnh (chưa trừ) thì được xoay."""
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        err = ParameterChangeError("Không đủ lượt cho video này")
        err.need, err.left = 4, 2
        gen = _scripted({"n1": [("raise", err)], "n2": [("ok",)]})
        browser_pool.generate_video = gen
        r = _run(pool.generate_video("p", "9:16", 10, model="seedance_v2.5", account="n1"))
        assert gen.calls == ["n1", "n2"], gen.calls
        assert r["account"] == "n2", r


# ---------- R2: proxy riêng không lấy được IP (trước khi gửi) → xoay ----------
def test_preflight_proxy_error_rotates():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        boom = RuntimeError("Proxy xoay riêng của nick n1 không lấy được IP (link) — kiểm tra key/link")
        gen = _scripted({"n1": [("raise", boom)], "n2": [("ok",)]})
        browser_pool.generate_video = gen
        r = _run(pool.generate_video("p", "9:16", 10, account="n1"))
        assert gen.calls == ["n1", "n2"], gen.calls
        assert r["account"] == "n2", r


# ---------- R3: lỗi SAU khi đã gửi → không bao giờ xoay ----------
def _assert_post_submit_never_rotates(exc):
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        gen = _scripted({"n1": [("submit_raise", exc)], "n2": [("ok",)]})
        browser_pool.generate_video = gen
        got, spy = _server_spy()
        try:
            _run(pool.generate_video("p", "9:16", 10, account="n1", on_submitted=spy))
            raise AssertionError(f"{type(exc).__name__} sau khi gửi phải nổi lên, KHÔNG được thành video trên nick khác")
        except type(exc):
            pass
        assert gen.calls == ["n1"], f"đã gửi lệnh trên n1 rồi mà vẫn xoay: {gen.calls} (= trừ lượt 2 lần)"
        assert ("n1", True) in got, f"callback on_submitted của server phải nhận được (n1, True): {got}"


def test_post_submit_generic_error_never_rotates():
    _assert_post_submit_never_rotates(RuntimeError("lỗi lạ sau khi gửi"))


def test_post_submit_fetch_delivered_never_rotates():
    _assert_post_submit_never_rotates(_FetchDelivered("Đã gửi lệnh tạo video tới Dola nhưng không xác nhận được"))


def test_post_submit_download_error_never_rotates():
    _assert_post_submit_never_rotates(DownloadError("https://x/v.mp4", "Connection reset"))


# ---------- R4: nhánh "lỗi tạm thời → thử lại 1 lần" cũng không được xoay sau khi đã gửi ----------
def test_retry_branch_post_submit_error_never_rotates():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        gen = _scripted({
            "n1": [("raise", TransientDolaError("エラーが発生しました")),
                   ("submit_raise", _FetchDelivered("Đã gửi lệnh tạo video tới Dola nhưng không xác nhận được"))],
            "n2": [("ok",)],
        })
        browser_pool.generate_video = gen
        try:
            _run(pool.generate_video("p", "9:16", 10, account="n1"), guard=15)   # nhánh thử lại có sleep(3) thật
            raise AssertionError("lần thử lại đã gửi lệnh rồi lỗi → phải nổi lỗi, không được sang n2")
        except _FetchDelivered:
            pass
        assert "n2" not in gen.calls, f"nhánh thử lại xoay sau khi đã gửi: {gen.calls} (= trừ lượt 2 lần)"


# ---------- R5: treo lúc mở nick (trước khi gửi) → watchdog hủy + xoay ----------
def test_presubmit_hang_is_cut_and_rotates():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        old = getattr(browser_pool, "PRESUBMIT_TIMEOUT_SEC", None)
        browser_pool.PRESUBMIT_TIMEOUT_SEC = 0.3
        try:
            gen = _scripted({"n1": [("hang",)], "n2": [("ok",)]})
            browser_pool.generate_video = gen
            try:
                r = _run(pool.generate_video("p", "9:16", 10, account="n1"))
            except asyncio.TimeoutError:
                raise AssertionError("treo lúc mở nick không bị cắt (không có watchdog trước khi gửi)")
            assert gen.calls == ["n1", "n2"], gen.calls
            assert r["account"] == "n2", r
            assert not pool._locks["n1"].locked(), "nick treo phải được nhả khoá"
        finally:
            if old is None:
                delattr(browser_pool, "PRESUBMIT_TIMEOUT_SEC")
            else:
                browser_pool.PRESUBMIT_TIMEOUT_SEC = old


# ---------- R6: render dài SAU khi gửi → không được cắt ----------
def test_long_render_after_submit_is_not_cut():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        old = getattr(browser_pool, "PRESUBMIT_TIMEOUT_SEC", None)
        browser_pool.PRESUBMIT_TIMEOUT_SEC = 0.3
        try:
            gen = _scripted({"n1": [("submit_sleep", 0.8)], "n2": [("ok",)]})
            browser_pool.generate_video = gen
            r = _run(pool.generate_video("p", "9:16", 10, account="n1"))
            assert r["account"] == "n1", r
            assert gen.calls == ["n1"], f"render sau khi gửi bị cắt rồi xoay: {gen.calls} (= trừ lượt 2 lần)"
            assert gen.submits == ["n1"], (f"worker không nhận được on_submitted ({gen.submits}): pool phải LUÔN truyền "
                                           "callback bọc xuống worker (kể cả khi server không đưa) để biết lúc đã gửi lệnh")
        finally:
            if old is None:
                delattr(browser_pool, "PRESUBMIT_TIMEOUT_SEC")
            else:
                browser_pool.PRESUBMIT_TIMEOUT_SEC = old


# ---------- R7: tắt tự xoay → lỗi trước khi gửi cũng dừng ngay ----------
def test_auto_retry_off_does_not_rotate_on_preflight_error():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        gen = _scripted({"n1": [("raise", RuntimeError("Proxy xoay riêng của nick n1 không lấy được IP"))], "n2": [("ok",)]})
        browser_pool.generate_video = gen
        browser_pool.config.AUTO_RETRY = False
        try:
            try:
                _run(pool.generate_video("p", "9:16", 10))
                raise AssertionError("tắt tự xoay mà vẫn chạy được trên nick khác")
            except RuntimeError as e:
                assert "Proxy xoay riêng" in str(e), str(e)
            assert gen.calls == ["n1"], gen.calls
        finally:
            browser_pool.config.AUTO_RETRY = True


# ---------- R8: lỗi trước khi gửi vẫn tôn trọng MAX_ROTATE ----------
def test_preflight_errors_respect_max_rotate():
    with tempfile.TemporaryDirectory() as tmp:
        nicks = tuple(f"n{i}" for i in range(6))
        pool = _pool(tmp, nicks)
        boom = RuntimeError("Proxy xoay riêng không lấy được IP")
        gen = _scripted({n: [("raise", boom)] for n in nicks})
        browser_pool.generate_video = gen
        try:
            _run(pool.generate_video("p", "9:16", 10))
            raise AssertionError("phải dừng sau MAX_ROTATE")
        except RuntimeError as e:
            assert "Đã thử" in str(e) or "Proxy xoay riêng" in str(e), str(e)
        assert len(gen.calls) == browser_pool.config.MAX_ROTATE, gen.calls


# ---------- R9: prompt bị chặn nội dung → xoay vô ích, không đổi hành vi ----------
def test_content_policy_still_not_rotated():
    from video_worker_ui import ContentPolicyViolationError
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        gen = _scripted({"n1": [("raise", ContentPolicyViolationError("chặn nội dung"))], "n2": [("ok",)]})
        browser_pool.generate_video = gen
        try:
            _run(pool.generate_video("p", "9:16", 10, account="n1"))
            raise AssertionError("prompt bị chặn phải nổi lên")
        except ContentPolicyViolationError:
            pass
        assert gen.calls == ["n1"], gen.calls


class _budget:
    """Tạm đặt đồng hồ chống treo trước khi gửi (pool đọc hằng lúc gọi)."""
    def __init__(self, sec): self.sec = sec
    def __enter__(self): self.old = browser_pool.PRESUBMIT_TIMEOUT_SEC; browser_pool.PRESUBMIT_TIMEOUT_SEC = self.sec
    def __exit__(self, *a): browser_pool.PRESUBMIT_TIMEOUT_SEC = self.old


# ---------- R10: TimeoutError THẬT của worker sau khi gửi không bị đồng hồ đổi thành "treo" ----------
def test_worker_timeout_after_submit_claims_not_rotates():
    with tempfile.TemporaryDirectory() as tmp, _budget(0.2):
        pool = _pool(tmp)
        calls = []

        async def gen(acc, *a, on_submitted=None, **kw):
            calls.append(acc)
            on_submitted(acc, True)
            await asyncio.sleep(0.4)
            raise TimeoutError("Hết 2400s chưa ra video")
        browser_pool.generate_video = gen
        try:
            _run(pool.generate_video("p", "9:16", 10))
            raise AssertionError("phải raise")
        except browser_pool.PreSubmitStallError:
            raise AssertionError("TimeoutError thật (sau khi gửi) bị đổi thành treo-trước-khi-gửi → xoay = trừ 2 lần")
        except TimeoutError as e:
            assert "2400" in str(e)
        assert calls == ["n1"], calls
        assert pool.used_today("n1") >= 1, "đã gửi mà hỏng → phải ghi lượt"


# ---------- R11: bị hủy lúc treo mà đóng Chrome lỗi → vẫn là treo, vẫn xoay, nhả khoá + slot ----------
def test_cancel_with_failing_finally_still_rotates():
    with tempfile.TemporaryDirectory() as tmp, _budget(0.2):
        pool = _pool(tmp)
        calls = []

        async def gen(acc, *a, on_submitted=None, **kw):
            calls.append(acc)
            try:
                if acc == "n1":
                    await asyncio.sleep(60)
                on_submitted(acc, True)
                return {"video_url": "u", "account": acc}
            finally:
                if acc == "n1":
                    raise RuntimeError("Target closed")   # context.close lỗi lúc bị hủy
        browser_pool.generate_video = gen
        r = _run(pool.generate_video("p", "9:16", 10))
        assert r["account"] == "n2" and calls == ["n1", "n2"], (r, calls)
        assert not pool._locks["n1"].locked() and pool.semaphore._value == pool.max_concurrency


# ---------- R12: fetch trượt (False) rồi UI: mỗi pha có hạn riêng, tổng dài hơn hạn vẫn không bị cắt ----------
def test_false_rearms_watchdog_per_phase():
    with tempfile.TemporaryDirectory() as tmp, _budget(0.25):
        pool = _pool(tmp)
        calls = []

        async def gen(acc, *a, on_submitted=None, **kw):
            calls.append(acc)
            await asyncio.sleep(0.15); on_submitted(acc, False)   # fetch lượt 1 trượt (chắc chắn chưa gửi)
            await asyncio.sleep(0.15); on_submitted(acc, False)   # fetch lượt 2 trượt
            await asyncio.sleep(0.15); on_submitted(acc, True)    # UI gửi
            await asyncio.sleep(0.5)                              # render
            return {"video_url": "u", "account": acc}
        browser_pool.generate_video = gen
        r = _run(pool.generate_video("p", "9:16", 10))
        assert r["account"] == "n1" and calls == ["n1"], (r, calls)


# ---------- R13: lỗi CODE → nổi ngay, không đốt lượt mở nick, không cho nick nghỉ oan ----------
def test_code_error_fails_fast_without_resting_nicks():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        gen = _scripted({"n1": [("raise", AttributeError("'NoneType' object has no attribute 'x'"))], "n2": [("ok",)]})
        browser_pool.generate_video = gen
        try:
            _run(pool.generate_video("p", "9:16", 10))
            raise AssertionError("lỗi code phải nổi lên")
        except AttributeError:
            pass
        assert gen.calls == ["n1"], gen.calls
        assert not pool._meta("n1")["cooldown_until"], "lỗi code không phải lỗi nick → không được cho nghỉ"


# ---------- R14: nick lỗi trước khi gửi → nghỉ ngắn để job sau không đâm vào nó trước ----------
def test_presubmit_fail_rests_nick():
    import time as _t
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        gen = _scripted({"n1": [("raise", RuntimeError("Proxy xoay riêng của nick n1 không lấy được IP"))], "n2": [("ok",)]})
        browser_pool.generate_video = gen
        r = _run(pool.generate_video("p", "9:16", 10, account="n1"))
        assert r["account"] == "n2"
        assert pool._meta("n1")["cooldown_until"] > _t.time(), "nick lỗi trước khi gửi phải nghỉ ngắn"
        assert not pool._meta("n2")["cooldown_until"]


# ---------- R15: 710022002 — đã gửi mà chưa xác nhận thì không xoay; worker xác nhận chưa nhận (False) thì xoay ----------
def test_rate_limited_respects_delivery_flag():
    from video_worker_ui import RateLimitedError
    old = browser_pool.config.NO_COOLDOWN
    browser_pool.config.NO_COOLDOWN = True   # khỏi dừng gửi 90s trong test
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pool = _pool(tmp)
            gen = _scripted({"n1": [("submit_raise", RateLimitedError("710022002"))], "n2": [("ok",)]})
            browser_pool.generate_video = gen
            try:
                _run(pool.generate_video("p", "9:16", 10))
                raise AssertionError("710022002 lúc cờ còn 'đã gửi' → không được xoay")
            except RateLimitedError:
                pass
            assert gen.calls == ["n1"], gen.calls
        with tempfile.TemporaryDirectory() as tmp:
            pool = _pool(tmp)
            calls = []

            async def gen2(acc, *a, on_submitted=None, **kw):
                calls.append(acc)
                on_submitted(acc, True)
                if acc == "n1":
                    on_submitted(acc, False)       # worker dò đủ, chắc chắn Dola chưa nhận
                    raise RateLimitedError("710022002")
                return {"video_url": "u", "account": acc}
            browser_pool.generate_video = gen2
            got, spy = _server_spy()
            r = _run(pool.generate_video("p", "9:16", 10, on_submitted=spy))
            assert r["account"] == "n2" and calls == ["n1", "n2"], (r, calls)
            assert got == [("n1", True), ("n1", False), ("n2", True)], got   # server nhận đủ chuỗi cờ
    finally:
        browser_pool.config.NO_COOLDOWN = old


# ---------- R16: callback server lỗi đúng lúc báo "đã gửi" → cờ vẫn giữ, không xoay ----------
def test_server_callback_error_on_submit_never_rotates():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        gen = _scripted({"n1": [("ok",)], "n2": [("ok",)]})
        browser_pool.generate_video = gen

        def boom(acc, submitted):
            raise RuntimeError("database is locked")
        try:
            _run(pool.generate_video("p", "9:16", 10, on_submitted=boom))
            raise AssertionError("phải nổi lỗi")
        except RuntimeError as e:
            assert "locked" in str(e), str(e)
        assert gen.calls == ["n1"], gen.calls


# ---------- R17: server biết lúc job BẮT ĐẦU mở nick (tách khỏi "chờ slot Chrome") ----------
def test_on_opening_reports_nick_before_worker():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        order = []

        async def gen(acc, *a, on_submitted=None, **kw):
            order.append(("worker", acc))
            on_submitted(acc, True)
            return {"video_url": "u", "account": acc}
        browser_pool.generate_video = gen
        _run(pool.generate_video("p", "9:16", 10, on_opening=lambda acc: order.append(("opening", acc))))
        assert order == [("opening", "n1"), ("worker", "n1")], order


# ---------- R18: video xong mà ghi sổ lỗi → vẫn trả video (không để người dùng chạy lại = trừ 2 lần) ----------
def test_settle_error_keeps_video():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        browser_pool.generate_video = _scripted({"n1": [("ok",)], "n2": [("ok",)]})

        def bad_settle(*a, **kw):
            raise RuntimeError("disk I/O error")
        pool._settle = bad_settle
        r = _run(pool.generate_video("p", "9:16", 10))
        assert r["account"] == "n1", r


# ---------- R19: chỉ VIDEO XONG mới chứng minh cookie sống (nick KHÁCH vẫn có conversation_id rồi mới bị từ chối) ----------
def test_only_finished_video_marks_cookie_alive():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        pool.set_login_status("n1", None)
        got = []

        async def gen(acc, *a, on_submitted=None, on_conversation_id=None, **kw):
            on_submitted(acc, True)
            on_conversation_id(acc, "123", 0)
            assert pool._meta(acc)["login_ok"] is None, "mới có conversation_id đã ghi 'cookie sống' → nick khách bị báo sống oan"
            return {"video_url": "u", "account": acc}
        browser_pool.generate_video = gen
        _run(pool.generate_video("p", "9:16", 10, account="n1", on_conversation_id=lambda *x: got.append(x)))
        m = pool._meta("n1")
        assert m["login_ok"] == 1 and m["login_checked_at"] > 0, dict(m)
        assert got == [("n1", "123", 0)], "callback server vẫn phải nhận conversation_id"
        assert next(x for x in pool.account_status() if x["account"] == "n1")["login_checked_at"] > 0, "/health phải có mốc xác nhận"


# ---------- R20: Dola đáp "khách không tạo được video" → cookie chết + xoay nick (khách không bị trừ lượt) ----------
def test_guest_refusal_marks_dead_and_rotates():
    from video_worker_ui import GuestRefusedError
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        pool.set_login_status("n1", True)
        gen = _scripted({"n1": [("submit_raise", GuestRefusedError("Cookie hết hạn — Dola coi nick là KHÁCH"))], "n2": [("ok",)]})
        browser_pool.generate_video = gen
        got, spy = _server_spy()
        r = _run(pool.generate_video("p", "9:16", 10, account="n1", on_submitted=spy))
        assert r["account"] == "n2" and gen.calls == ["n1", "n2"], (r, gen.calls)
        assert pool._meta("n1")["login_ok"] == 0, "nick khách phải bị đánh dấu cookie chết"
        assert got == [("n1", True), ("n1", False), ("n2", True)], got


# ---------- R21–R23: sổ giữ chỗ proxy xoay (đối thủ v1.0.88: không đổi IP khi làn còn nick chạy) ----------
_LINK = "https://proxyxoay.shop/api/get.php?key=KEYLEASE1234&nhamang=random&tinhthanh=0"


class _proxy_env:
    """n1, n2 cùng 1 link proxyxoay; IP cache sống `life` giây; đếm số lần gọi đổi IP thật."""
    def __init__(self, tmp, life):
        self.tmp, self.life, self.rotations = tmp, life, []

    def __enter__(self):
        import time as _t
        import browser
        import proxyxoay
        self.saved = (browser_pool.config.ACCOUNTS_DIR, browser_pool.config.PROXY, proxyxoay.rotate)
        browser_pool.config.ACCOUNTS_DIR, browser_pool.config.PROXY = Path(self.tmp) / "accounts", ""
        for n in ("n1", "n2"):
            (Path(self.tmp) / "accounts" / n / "proxy.txt").write_text(_LINK, encoding="utf-8")
        proxyxoay._cache[_LINK] = {"server": "http://1.1.1.1:80", "ip": "1.1.1.1:80", "message": f"proxy nay se die sau {self.life}s",
                                   "fetched_at": _t.time(), "ttl": max(self.life - 30, 0), "next_ok": 0, "network": "", "location": ""}
        proxyxoay.rotate = lambda link: (self.rotations.append(link), proxyxoay._cache[link])[1]
        browser._proxy_leases.clear()
        return self

    def __exit__(self, *a):
        import browser
        import proxyxoay
        browser_pool.config.ACCOUNTS_DIR, browser_pool.config.PROXY, proxyxoay.rotate = self.saved
        proxyxoay._cache.pop(_LINK, None)
        browser._proxy_leases.clear()


def test_job_holds_proxy_lease_while_running():
    import browser
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        with _proxy_env(tmp, life=1500) as env:
            seen = []

            async def gen(acc, *a, on_submitted=None, **kw):
                seen.append(dict(browser._proxy_leases))
                on_submitted(acc, True)
                return {"video_url": "u", "account": acc}
            browser_pool.generate_video = gen
            _run(pool.generate_video("p", "9:16", 10, account="n1"))
            assert seen == [{_LINK: 1}], seen
            assert browser._proxy_leases == {}, "hết job phải trả chỗ"
            assert env.rotations == [], "IP còn sống lâu → không đổi trước job"


def test_auto_rotate_skipped_while_other_job_on_same_proxy():
    """710022002 / tới lượt đổi sau N video trên n1 mà n2 ĐANG chạy cùng key → không đổi (đổi = cắt IP của n2)."""
    import browser
    with tempfile.TemporaryDirectory() as tmp:
        _pool(tmp)
        with _proxy_env(tmp, life=1500) as env:
            with browser.proxy_lease("n2"):
                assert browser.rotate_tmproxy_now("n1") is None and env.rotations == [], env.rotations
            browser.rotate_tmproxy_now("n1")
            assert env.rotations == [_LINK], "không còn job nào trên proxy → được đổi"


def test_expiring_ip_rotated_before_opening_nick():
    import browser
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        browser_pool.generate_video = _scripted({"n1": [("ok",)], "n2": [("ok",)]})
        with _proxy_env(tmp, life=100) as env:           # IP chỉ còn ~100s < PROXY_MIN_LIFE_SEC
            with browser.proxy_lease("n2"):                # n2 đang chạy cùng key → KHÔNG đổi
                _run(pool.generate_video("p", "9:16", 10, account="n1"))
            assert env.rotations == [], env.rotations
            _run(pool.generate_video("p", "9:16", 10, account="n1"))
            assert env.rotations == [_LINK], "IP sắp hết tuổi, không ai dùng → đổi TRƯỚC khi mở nick"


# ---------- R24–R26: nick được chọn ĐANG BẬN → không báo lỗi "Nick đang bận" ngay (đối thủ: job chờ tài nguyên) ----------
class _busy_cfg:
    def __init__(self, auto_retry, wait=5.0):
        self.auto_retry, self.wait = auto_retry, wait
    def __enter__(self):
        self.saved = (browser_pool.config.AUTO_RETRY, browser_pool.PINNED_BUSY_WAIT_SEC, browser_pool.PINNED_BUSY_POLL_SEC)
        browser_pool.config.AUTO_RETRY, browser_pool.PINNED_BUSY_WAIT_SEC, browser_pool.PINNED_BUSY_POLL_SEC = self.auto_retry, self.wait, 0.05
    def __exit__(self, *a):
        browser_pool.config.AUTO_RETRY, browser_pool.PINNED_BUSY_WAIT_SEC, browser_pool.PINNED_BUSY_POLL_SEC = self.saved


def _run_while_busy(pool, nick, release_after, **kw):
    async def main():
        lock = pool._locks.setdefault(nick, asyncio.Lock())
        await lock.acquire()                         # job khác đang dựng trên nick
        async def release():
            await asyncio.sleep(release_after)
            lock.release()
        rel = asyncio.create_task(release())
        try:
            return await pool.generate_video("p", "9:16", 10, account=nick, **kw)
        finally:
            await rel
    return _run(main())


def test_busy_pinned_nick_waits_instead_of_failing():
    with tempfile.TemporaryDirectory() as tmp, _busy_cfg(auto_retry=False):
        pool = _pool(tmp)
        gen = _scripted({"n1": [("ok",)], "n2": [("ok",)]})
        browser_pool.generate_video = gen
        r = _run_while_busy(pool, "n1", 0.3)
        assert r["account"] == "n1" and gen.calls == ["n1"], (r, gen.calls)


def test_busy_pinned_nick_rotates_to_free_nick_when_auto_retry():
    with tempfile.TemporaryDirectory() as tmp, _busy_cfg(auto_retry=True):
        pool = _pool(tmp)
        gen = _scripted({"n1": [("ok",)], "n2": [("ok",)]})
        browser_pool.generate_video = gen
        r = _run_while_busy(pool, "n1", 0.3)
        assert r["account"] == "n2" and gen.calls == ["n2"], "có nick rảnh → chạy luôn, không chờ nick bận"


def test_busy_pinned_nick_gives_up_after_wait_cap():
    with tempfile.TemporaryDirectory() as tmp, _busy_cfg(auto_retry=False, wait=0.2):
        pool = _pool(tmp)
        browser_pool.generate_video = _scripted({"n1": [("ok",)], "n2": [("ok",)]})
        try:
            _run_while_busy(pool, "n1", 0.6)
            raise AssertionError("chờ quá trần phải báo lỗi rõ")
        except RuntimeError as e:
            assert "đang bận" in str(e), str(e)
        assert pool.semaphore._value == pool.max_concurrency, "chờ nick không được giữ slot Chrome"


# ---------- R27: nick lỗi trước khi gửi trên ≥3 IP khác nhau → cách ly dài (đối thủ: ACC SPAM CHỜ XỬ LÝ) ----------
def test_nick_failing_on_many_ips_is_quarantined():
    import time as _t
    import proxyxoay
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp)
        with _proxy_env(tmp, life=1500):
            for i, ip in enumerate(["1.1.1.1:80", "2.2.2.2:80", "2.2.2.2:80", "3.3.3.3:80"]):
                proxyxoay._cache[_LINK]["ip"] = ip
                pool._rest_after_presubmit_fail("n1", RuntimeError("Khung soạn video không mở được"))
                m = pool._meta("n1")
                left = m["cooldown_until"] - _t.time()
                if i < 3:   # 1.1 → 2.2 → 2.2 (trùng IP không tính) = mới 2 IP khác nhau → nghỉ ngắn
                    assert left <= browser_pool.PRESUBMIT_FAIL_COOLDOWN_SEC + 5, (i, left)
            assert left > browser_pool.SPAM_COOLDOWN_SEC - 60, "3 IP khác nhau → cách ly dài"
            a = next(x for x in pool.list_accounts() if x["name"] == "n1")
            assert "cách ly" in pool.blocked_reason(a) and "3 IP" in pool.blocked_reason(a), pool.blocked_reason(a)
            pool.clear_cooldown("n1")
            assert not pool._quarantine and "n1" not in pool._fail_ips, "Bỏ nghỉ = xoá cách ly"


# ---------- R28: bằng điểm thì nick lâu chưa dùng chạy trước (rải đều, không dồn vài nick) ----------
def test_equal_credit_prefers_least_recently_used_nick():
    import time as _t
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp, ("n1", "n2", "n3"))
        for n in ("n1", "n2", "n3"):
            pool._ensure_meta(n)
        for n, ago in (("n1", 10), ("n2", 3600), ("n3", 600)):
            pool._conn.execute("UPDATE accounts_meta SET last_used_at=? WHERE name=?", (_t.time() - ago, n))
        pool._conn.commit()
        gen = _scripted({n: [("ok",)] for n in ("n1", "n2", "n3")})
        browser_pool.generate_video = gen
        r = _run(pool.generate_video("p", "9:16", 10))
        assert r["account"] == "n2", (r, "n2 lâu chưa dùng nhất phải chạy trước")
        pool._set_credit_balance("n1", 4, "test"); pool._set_credit_balance("n3", 2, "test"); pool._set_credit_balance("n2", 2, "test")
        r = _run(pool.generate_video("p", "9:16", 10))
        assert r["account"] == "n1", (r, "nhiều điểm hơn vẫn ưu tiên trước")


# ---------- R29: IP vừa bị Dola chặn (710022002) = IP bẩn → nick kế mở nick trên key đó phải đổi IP trước ----------
def test_dirty_ip_rotated_before_next_nick():
    import browser
    from video_worker_ui import RateLimitedError
    old_nc = browser_pool.config.NO_COOLDOWN
    browser_pool.config.NO_COOLDOWN = True
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pool = _pool(tmp)
            with _proxy_env(tmp, life=1500) as env:      # IP còn sống lâu → bình thường KHÔNG đổi trước job
                browser._dirty_ips.clear()
                gen = _scripted({"n1": [("raise", RateLimitedError("710022002"))], "n2": [("ok",)]})
                browser_pool.generate_video = gen
                r = _run(pool.generate_video("p", "9:16", 10, account="n1"))
                assert r["account"] == "n2", r
                assert "1.1.1.1:80" in browser._dirty_ips, browser._dirty_ips
                # 1 lần đổi ngay sau 710022002 (n1) + 1 lần trước khi mở n2 vì IP (giả lập nhà bán cấp lại y IP) vẫn bẩn
                assert env.rotations == [_LINK, _LINK], env.rotations
                assert browser.rotating_status(_LINK)["dirty"] is True
                browser._dirty_ips.clear()
                assert browser.rotating_status(_LINK)["dirty"] is False
    finally:
        browser_pool.config.NO_COOLDOWN = old_nc
        import browser as _b
        _b._dirty_ips.clear()


if __name__ == "__main__":
    import sys
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001 — in hết để thấy toàn cảnh RED/GREEN
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {str(e)[:160]}")
    print(f"\n{len(tests) - failed}/{len(tests)} pass")
    sys.exit(1 if failed else 0)
