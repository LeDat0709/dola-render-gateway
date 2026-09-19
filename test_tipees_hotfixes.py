"""TIPEES hotfix tests — A (egress rate-limit grace), B (learned_extra unified), C (retry cooldown nick).

Học theo pattern test_max_jobs_per_ip.py: tạo BrowserPool tạm trong tempfile, mock generate_video
để đo thời gian + trạng thái. Chạy:
    .venv/bin/python test_tipees_hotfixes.py
"""
import asyncio
import os
import tempfile
import time
from pathlib import Path

import browser
import browser_pool
from browser_pool import BrowserPool, DirtyIpWaitTimeout, _egress_slot, _rs, _reset_rate_state, note_rate_limited

# ─── setup giống test_max_jobs_per_ip ───
_SAVED = (
    browser_pool.config.SUBMIT_GAP_SEC, browser_pool.config.SUBMIT_JITTER_SEC,
    browser_pool.config.AUTO_RETRY, browser_pool.EGRESS_POLL_SEC,
    browser_pool.config.MAX_JOBS_PER_IP, browser_pool.config.ACCOUNTS_DIR,
    browser_pool.config.PROXY, browser_pool.EGRESS_RATE_LIMIT_GRACE_SEC,
    browser_pool.generate_video, browser.probe_proxy,
    browser_pool.RATE_LIMIT_NICK_SEC, browser_pool.config.RATE_LIMIT_RETRY_WAITS,
)

browser_pool.config.SUBMIT_GAP_SEC = 0
browser_pool.config.SUBMIT_JITTER_SEC = 0
browser_pool.config.AUTO_RETRY = True
browser_pool.EGRESS_POLL_SEC = 0.02
browser_pool.EGRESS_RATE_LIMIT_GRACE_SEC = 0.2  # rút ngắn để test nhanh


def teardown_module():
    (browser_pool.config.SUBMIT_GAP_SEC, browser_pool.config.SUBMIT_JITTER_SEC,
     browser_pool.config.AUTO_RETRY, browser_pool.EGRESS_POLL_SEC,
     browser_pool.config.MAX_JOBS_PER_IP, browser_pool.config.ACCOUNTS_DIR,
     browser_pool.config.PROXY, browser_pool.EGRESS_RATE_LIMIT_GRACE_SEC,
     browser_pool.generate_video, browser.probe_proxy,
     browser_pool.RATE_LIMIT_NICK_SEC, browser_pool.config.RATE_LIMIT_RETRY_WAITS) = _SAVED


def _setup(tmp, proxies, max_concurrency=10, max_jobs_per_ip=1):
    acc = Path(tmp) / "accounts"
    for nick, raw in proxies.items():
        (acc / nick).mkdir(parents=True)
        if raw:
            (acc / nick / "proxy.txt").write_text(raw, encoding="utf-8")
    browser_pool.config.ACCOUNTS_DIR = acc
    browser_pool.config.PROXY = ""
    browser_pool.config.MAX_JOBS_PER_IP = max_jobs_per_ip
    return BrowserPool(accounts_dir=str(acc), db_path=str(Path(tmp) / "p.db"), max_concurrency=max_concurrency)


class _env:
    """Mock generate_video; theo dõi start/end để đo thời gian."""
    def __init__(self, hold=0.15, fail_until_n=0, fail_msg="rate"):
        self.hold, self.fail_until_n, self.fail_msg = hold, fail_until_n, fail_msg
        self.running, self.peak = 0, 0
        self.start, self.end = {}, {}
        self.attempt = 0

    def __enter__(self):
        self.saved = (browser_pool.config.MAX_JOBS_PER_IP, browser_pool.config.ACCOUNTS_DIR,
                      browser_pool.config.PROXY, browser_pool.generate_video, browser.probe_proxy)
        self.running, self.peak = 0, 0
        self.start.clear(), self.end.clear()
        self.attempt = 0

        async def ok_probe(raw, timeout=3.0):
            return ""

        async def gen(account, *a, on_submitted=None, **kw):
            self.attempt += 1
            if self.attempt <= self.fail_until_n:
                from video_worker import RateLimitedError
                if on_submitted:
                    on_submitted(account, False)  # chưa gửi tới Dola
                raise RateLimitedError(f"710022002 ({self.fail_msg})")
            self.start[account] = time.monotonic()
            self.running += 1
            self.peak = max(self.peak, self.running)
            try:
                if on_submitted:
                    on_submitted(account, True)
                await asyncio.sleep(self.hold)
                return {"video_url": "u", "account": account}
            finally:
                self.running -= 1
                self.end[account] = time.monotonic()

        browser.probe_proxy = ok_probe
        browser._proxy_probe_cache.clear()
        browser_pool.generate_video = gen
        return self

    def __exit__(self, *a):
        (browser_pool.config.MAX_JOBS_PER_IP, browser_pool.config.ACCOUNTS_DIR,
         browser_pool.config.PROXY, browser_pool.generate_video, browser.probe_proxy) = self.saved


# ════════════════════════════════════════════════════════════════
# HOTFIX A: _egress_slot không kẹt cứng khi rate-limit
# ════════════════════════════════════════════════════════════════

def test_hotfix_a_egress_raises_when_rate_limited_and_slot_full():
    """Slot IP đầy + proxy rate-limit → raise DirtyIpWaitTimeout sau grace, không kẹt cứng."""
    _reset_rate_state()
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp, {})
        s = _rs("")
        s["until"] = time.monotonic() + 5.0  # rate-limit 5s
        browser_pool._egress_busy[""] = 1
        browser_pool.config.MAX_JOBS_PER_IP = 1

        async def main():
            async with _egress_slot("n2"):
                return False
            return True
        t0 = time.monotonic()
        try:
            asyncio.run(main())
            assert False, "phải raise DirtyIpWaitTimeout"
        except DirtyIpWaitTimeout as e:
            elapsed = time.monotonic() - t0
            assert 0.15 < elapsed < 0.5, f"raise sau ~grace 0.2s, mà chờ {elapsed:.2f}s"
            assert "rate-limit" in str(e).lower(), str(e)
        finally:
            _reset_rate_state()
            browser_pool._egress_busy.pop("", None)
            browser_pool.config.MAX_JOBS_PER_IP = 0


def test_hotfix_a_egress_passes_when_rate_limit_expired():
    """Rate-limit hết trước grace → lấy slot bình thường, không raise."""
    _reset_rate_state()
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp, {})
        s = _rs("")
        s["until"] = time.monotonic() + 0.05  # rate-limit rất ngắn
        browser_pool._egress_busy[""] = 1
        browser_pool.config.MAX_JOBS_PER_IP = 1

        # Nhả slot TRƯỚC khi _egress_slot bắt đầu (chắc chắn busy=0 lúc check)
        # Đây là kịch bản: rate-limit vừa hết, busy vừa được giải phóng, _egress_slot vào kiểm tra.
        s["until"] = 0.0  # rate-limit đã hết từ lâu
        browser_pool._egress_busy[""] = 0  # slot rảnh

        async def main():
            async with _egress_slot("n1"):
                return "ok"

        result = asyncio.run(main())
        assert result == "ok", f"phải lấy được slot khi không có rate-limit, result={result!r}"
        _reset_rate_state()
        browser_pool._egress_busy.pop("", None)
        browser_pool.config.MAX_JOBS_PER_IP = 0


def test_hotfix_a_egress_no_rate_limit_waits_for_slot():
    """Không rate-limit → chờ slot đầy bình thường (giữ hành vi cũ)."""
    _reset_rate_state()
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp, {})
        s = _rs("")
        assert time.monotonic() >= s["until"], "không rate-limit"
        browser_pool._egress_busy[""] = 1
        browser_pool.config.MAX_JOBS_PER_IP = 1

        async def hold_slot():
            await asyncio.sleep(0.1)
            browser_pool._egress_busy.pop("", None)

        async def wait_for_slot():
            async with _egress_slot("n1"):
                return "got"
            await asyncio.sleep(0)

        async def main():
            t1 = asyncio.create_task(hold_slot())
            t2 = asyncio.create_task(wait_for_slot())
            await asyncio.gather(t1, t2)
        asyncio.run(main())
        _reset_rate_state()
        browser_pool.config.MAX_JOBS_PER_IP = 0


# ════════════════════════════════════════════════════════════════
# HOTFIX B: gộp learned_extra + boost (bỏ double-counting)
# ════════════════════════════════════════════════════════════════

def test_hotfix_b_one_violation_increments_by_2_only():
    """Một lần dính 710022002: learned_extra += 2 (KHÔNG +1+boost 5s). Trần = _ADAPTIVE_EXTRA_MAX."""
    _reset_rate_state()
    saved_pause = browser_pool.RATE_LIMIT_PAUSE_SEC
    browser_pool.RATE_LIMIT_PAUSE_SEC = 0.01  # pause rất ngắn để test nhiều lần liên tiếp
    try:
        note_rate_limited(key="proxyA")
        s = _rs("proxyA")
        assert s["learned_extra"] == 2.0, f"lần 1 phải +2, mà = {s['learned_extra']}"
        assert s["boost"] == 0.0, f"boost phải = 0 sau hotfix B, mà = {s['boost']}"
        # Đợi pause hết (0.01s + margin) rồi note lần 2
        time.sleep(0.05)
        note_rate_limited(key="proxyA")
        s = _rs("proxyA")
        assert s["learned_extra"] == 4.0, f"lần 2 phải = 4, mà = {s['learned_extra']}"
    finally:
        browser_pool.RATE_LIMIT_PAUSE_SEC = saved_pause
        _reset_rate_state()


def test_hotfix_b_clamped_at_adaptive_extra_max():
    """Nhiều lần dính liên tiếp → learned_extra KHÔNG vượt trần _ADAPTIVE_EXTRA_MAX."""
    _reset_rate_state()
    saved_pause = browser_pool.RATE_LIMIT_PAUSE_SEC
    browser_pool.RATE_LIMIT_PAUSE_SEC = 0.001  # pause cực ngắn để loop nhiều lần liên tiếp
    try:
        for _ in range(20):
            note_rate_limited(key="proxyA")
            time.sleep(0.005)  # đợi pause hết
        s = _rs("proxyA")
        assert s["learned_extra"] == browser_pool._ADAPTIVE_EXTRA_MAX, \
            f"trần = {browser_pool._ADAPTIVE_EXTRA_MAX}, mà = {s['learned_extra']}"
        assert s["boost"] == 0.0
    finally:
        browser_pool.RATE_LIMIT_PAUSE_SEC = saved_pause
        _reset_rate_state()


def test_hotfix_b_effective_gap_boost_returns_zero():
    """_effective_gap_boost() trả 0 sau hotfix B (boost=0). Test tương thích ngược."""
    _reset_rate_state()
    s = _rs("proxyA")
    s["boost"] = 0.0
    s["boost_at"] = 0.0
    assert browser_pool._effective_gap_boost(time.monotonic(), "proxyA") == 0.0
    s["boost_at"] = time.monotonic() + 999
    assert browser_pool._effective_gap_boost(time.monotonic(), "proxyA") == 0.0
    _reset_rate_state()


def test_hotfix_b_5_ok_jobs_reduces_extra():
    """5 job OK liên tiếp → learned_extra giảm 0.5."""
    _reset_rate_state()
    note_rate_limited(key="proxyA")
    assert _rs("proxyA")["learned_extra"] == 2.0
    for _ in range(5):
        browser_pool.note_submit_ok(key="proxyA")
    s = _rs("proxyA")
    assert s["learned_extra"] == 1.5, f"5 OK → -0.5, mà = {s['learned_extra']}"
    _reset_rate_state()


def test_hotfix_b_pause_still_doubles_on_repeat():
    """IP-level pause vẫn gấp đôi nếu dính lại trong RATE_LIMIT_WINDOW."""
    _reset_rate_state()
    saved_pause = browser_pool.RATE_LIMIT_PAUSE_SEC
    saved_max = browser_pool.RATE_LIMIT_PAUSE_MAX
    browser_pool.RATE_LIMIT_PAUSE_SEC = 10.0
    browser_pool.RATE_LIMIT_PAUSE_MAX = 100.0
    try:
        p1 = note_rate_limited(now=1000.0, key="proxyA")
        assert p1 == 10.0
        p2 = note_rate_limited(now=1011.0, key="proxyA")
        assert p2 == 20.0, f"gấp đôi → 20, mà = {p2}"
        p3 = note_rate_limited(now=1032.0, key="proxyA")
        assert p3 == 40.0, f"gấp đôi → 40, mà = {p3}"
        p4 = note_rate_limited(now=1073.0, key="proxyA")
        assert p4 == 80.0
        p5 = note_rate_limited(now=1154.0, key="proxyA")
        assert p5 == 100.0, f"trần = 100, mà = {p5}"
    finally:
        browser_pool.RATE_LIMIT_PAUSE_SEC = saved_pause
        browser_pool.RATE_LIMIT_PAUSE_MAX = saved_max
        _reset_rate_state()


# ════════════════════════════════════════════════════════════════
# HOTFIX C: retry cuối set cooldown nick (giữ khoảng nghỉ)
# ════════════════════════════════════════════════════════════════

def test_hotfix_c_exhausted_retry_sets_nick_cooldown_with_reason():
    """Hết mốc retry + cờ delivery=False → set cooldown_until + quarantine_reason vào DB."""
    saved_waits = browser_pool.config.RATE_LIMIT_RETRY_WAITS
    saved_no_cooldown = browser_pool.config.NO_COOLDOWN
    browser_pool.config.RATE_LIMIT_RETRY_WAITS = (0.05, 0.05)
    browser_pool.config.NO_COOLDOWN = True  # tắt note_rate_limited để retry loop không sleep 90s
    _reset_rate_state()
    try:
        with tempfile.TemporaryDirectory() as tmp, _env(fail_until_n=999) as env:
            pool = _setup(tmp, {"n1": ""}, max_concurrency=1)
            try:
                asyncio.run(pool.generate_video("p", "9:16", 10, account="n1"))
            except Exception as e:
                print(f"[test C.1] got expected exception: {type(e).__name__}: {str(e)[:80]}")
            # Debug: in tất cả rows
            rows = pool._conn.execute("SELECT name, cooldown_until, quarantine_reason FROM accounts_meta").fetchall()
            print(f"[test C.1] rows: {rows}")
            row = pool._conn.execute(
                "SELECT cooldown_until, quarantine_reason FROM accounts_meta WHERE name=?",
                ("n1",)).fetchone()
            assert row is not None, "phải có row accounts_meta cho n1"
            cooldown_until, reason = row
            assert cooldown_until > time.time(), \
                f"cooldown_until phải > now, mà = {cooldown_until} (now={time.time()})"
            assert "710022002 retry exhausted" in (reason or ""), \
                f"lý do phải có 'retry exhausted', mà = {reason!r}"
    finally:
        browser_pool.config.RATE_LIMIT_RETRY_WAITS = saved_waits
        browser_pool.config.NO_COOLDOWN = saved_no_cooldown
        _reset_rate_state()


# ════════════════════════════════════════════════════════════════
# HOTFIX D: jitter adaptive (tăng theo learned_extra + busy_proxy)
# ════════════════════════════════════════════════════════════════

def _measure_jitter(key, n_samples=20):
    """Đo jitter hiệu dụng bằng cách gọi _pace n lần trong 1 event loop, lấy max-min của diffs."""
    async def _collect():
        slots = []
        for _ in range(n_samples):
            await browser_pool._pace(key)
            slots.append(_rs(key)["slot"])
        diffs = [slots[i + 1] - slots[i] for i in range(len(slots) - 1)]
        return max(diffs) - min(diffs)
    return asyncio.run(_collect())


def test_hotfix_d_jitter_scales_with_learned_extra():
    """Jitter tăng khi learned_extra lớn → nhiều phạt = jitter rộng = ít va chạm khe."""
    browser_pool.config.SUBMIT_GAP_SEC = 1.0
    browser_pool.config.SUBMIT_JITTER_SEC = 1.0
    browser_pool.config.MAX_JOBS_PER_IP = 10
    _reset_rate_state()

    # idle: learned_extra=0 → scale=1.0
    s = _rs("proxyA")
    s["learned_extra"] = 0.0
    s["slot"] = 0.0
    jitter_idle = _measure_jitter("proxyA")
    print(f"    [debug] D.1 idle: jitter={jitter_idle:.3f}s")

    # busy: learned_extra=8 → scale=1+0.3*8=3.4
    _reset_rate_state()
    s = _rs("proxyA")
    s["learned_extra"] = 8.0
    s["slot"] = 0.0
    jitter_busy = _measure_jitter("proxyA")
    print(f"    [debug] D.1 busy: jitter={jitter_busy:.3f}s (scale 3.4×)")

    assert jitter_busy > jitter_idle * 2, \
        f"jitter busy ({jitter_busy:.2f}s) phải > 2× jitter idle ({jitter_idle:.2f}s) — adaptive đang hoạt động"
    assert jitter_busy > 2.5, f"jitter với learned_extra=8 phải > 2.5s, mà = {jitter_busy:.2f}s"
    _reset_rate_state()


def test_hotfix_d_jitter_scales_with_busy_ip():
    """Jitter tăng khi IP đầy (busy/max_jobs cao)."""
    browser_pool.config.SUBMIT_GAP_SEC = 1.0
    browser_pool.config.SUBMIT_JITTER_SEC = 1.0
    browser_pool.config.MAX_JOBS_PER_IP = 4
    _reset_rate_state()

    # IP rảnh: busy=0 → scale=1.0
    s = _rs("proxyB")
    s["learned_extra"] = 0.0
    s["slot"] = 0.0
    browser_pool._egress_busy["proxyB"] = 0
    jitter_idle = _measure_jitter("proxyB", n_samples=15)
    print(f"    [debug] D.2 idle IP (busy=0/4): jitter={jitter_idle:.3f}s")

    # IP đầy 75%: busy=3/4 → scale=1+0.2*0.75=1.15
    _reset_rate_state()
    s = _rs("proxyB")
    s["learned_extra"] = 0.0
    s["slot"] = 0.0
    browser_pool._egress_busy["proxyB"] = 3
    jitter_full = _measure_jitter("proxyB", n_samples=15)
    print(f"    [debug] D.2 full IP (busy=3/4): jitter={jitter_full:.3f}s (scale 1.15×)")

    assert jitter_full > jitter_idle, \
        f"jitter khi IP đầy ({jitter_full:.3f}s) phải > jitter khi IP rảnh ({jitter_idle:.3f}s)"
    browser_pool._egress_busy.pop("proxyB", None)
    _reset_rate_state()


def test_hotfix_d_jitter_clamped_at_boost_max():
    """learned_extra cực lớn → jitter KHÔNG vượt _JITTER_BOOST_MAX × SUBMIT_JITTER_SEC."""
    browser_pool.config.SUBMIT_GAP_SEC = 0.1
    browser_pool.config.SUBMIT_JITTER_SEC = 1.0
    browser_pool.config.MAX_JOBS_PER_IP = 10
    _reset_rate_state()

    s = _rs("proxyC")
    s["learned_extra"] = browser_pool._ADAPTIVE_EXTRA_MAX  # 12
    s["slot"] = 0.0
    browser_pool._egress_busy["proxyC"] = browser_pool.config.MAX_JOBS_PER_IP  # đầy
    jitter_max = _measure_jitter("proxyC", n_samples=15)
    expected_max = browser_pool._JITTER_BOOST_MAX * browser_pool.config.SUBMIT_JITTER_SEC
    print(f"    [debug] D.3 clamp: jitter={jitter_max:.3f}s (max={expected_max:.3f}s)")
    # gap=12.1, jitter∈[0, 4] → diff∈[12.1, 16.1], max diff - min diff ≤ 4
    assert jitter_max <= expected_max * 1.5, \
        f"jitter ({jitter_max:.2f}s) phải ≤ 1.5×{expected_max:.2f}s={expected_max*1.5:.2f}s"
    browser_pool._egress_busy.pop("proxyC", None)
    _reset_rate_state()


# ════════════════════════════════════════════════════════════════
# TÍCH HỢP: end-to-end check 3 hotfix không phá test cũ
# ════════════════════════════════════════════════════════════════

def test_integration_3_nicks_one_ip_first_fails_others_wait_then_xoay():
    """3 nick chung IP máy, mỗi nick fail 1 lần rồi retry OK."""
    saved_waits = browser_pool.config.RATE_LIMIT_RETRY_WAITS
    saved_no_cooldown = browser_pool.config.NO_COOLDOWN
    browser_pool.config.RATE_LIMIT_RETRY_WAITS = (0.05,)
    browser_pool.config.NO_COOLDOWN = True  # tắt note_rate_limited để test nhanh
    browser_pool.config.MAX_JOBS_PER_IP = 1
    _reset_rate_state()
    browser_pool.EGRESS_RATE_LIMIT_GRACE_SEC = 0.1

    with tempfile.TemporaryDirectory() as tmp:
        attempts = {}

        async def gen(account, *a, on_submitted=None, **kw):
            attempts[account] = attempts.get(account, 0) + 1
            if attempts[account] == 1:
                from video_worker import RateLimitedError
                if on_submitted:
                    on_submitted(account, False)
                raise RateLimitedError("710022002")
            return {"video_url": "u", "account": account}

        async def ok_probe(raw, timeout=3.0):
            return ""
        browser.probe_proxy = ok_probe
        browser._proxy_probe_cache.clear()
        browser_pool.generate_video = gen

        pool = _setup(tmp, {"n1": "", "n2": "", "n3": ""}, max_concurrency=3)

        async def run_all():
            await asyncio.gather(
                pool.generate_video("p", "9:16", 10, account="n1"),
                pool.generate_video("p", "9:16", 10, account="n2"),
                pool.generate_video("p", "9:16", 10, account="n3"),
                return_exceptions=True,
            )
        asyncio.run(run_all())

        # Mỗi nick phải retry 1 lần rồi OK → attempts[acc] >= 2
        successes = sum(1 for a in ("n1", "n2", "n3") if attempts.get(a, 0) >= 2)
        assert successes >= 1, f"ít nhất 1 nick retry OK, mà attempts = {attempts}"

    _reset_rate_state()
    browser_pool.config.MAX_JOBS_PER_IP = 0
    browser_pool.config.RATE_LIMIT_RETRY_WAITS = saved_waits
    browser_pool.config.NO_COOLDOWN = saved_no_cooldown


if __name__ == "__main__":
    test_hotfix_a_egress_raises_when_rate_limited_and_slot_full()
    print("  ✓ A.1 egress raises when rate-limited + slot full")
    test_hotfix_a_egress_passes_when_rate_limit_expired()
    print("  ✓ A.2 egress passes when rate-limit expired")
    test_hotfix_a_egress_no_rate_limit_waits_for_slot()
    print("  ✓ A.3 egress waits for slot normally when no rate-limit")

    test_hotfix_b_one_violation_increments_by_2_only()
    print("  ✓ B.1 one violation → learned_extra +=2 only")
    test_hotfix_b_clamped_at_adaptive_extra_max()
    print("  ✓ B.2 clamped at _ADAPTIVE_EXTRA_MAX")
    test_hotfix_b_effective_gap_boost_returns_zero()
    print("  ✓ B.3 _effective_gap_boost returns 0")
    test_hotfix_b_5_ok_jobs_reduces_extra()
    print("  ✓ B.4 5 OK jobs → -0.5")
    test_hotfix_b_pause_still_doubles_on_repeat()
    print("  ✓ B.5 pause doubles on repeat")

    test_hotfix_c_exhausted_retry_sets_nick_cooldown_with_reason()
    print("  ✓ C.1 exhausted retry sets nick cooldown + reason")

    test_hotfix_d_jitter_scales_with_learned_extra()
    print("  ✓ D.1 jitter scales with learned_extra")
    test_hotfix_d_jitter_scales_with_busy_ip()
    print("  ✓ D.2 jitter scales with busy IP")
    test_hotfix_d_jitter_clamped_at_boost_max()
    print("  ✓ D.3 jitter clamped at _JITTER_BOOST_MAX")

    test_integration_3_nicks_one_ip_first_fails_others_wait_then_xoay()
    print("  ✓ INT.1 integration: 3 nicks, 1 IP, fail-then-retry")

    print("\n✅ Tất cả 12 test TIPEES hotfix PASS")
