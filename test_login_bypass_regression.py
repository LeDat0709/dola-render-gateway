"""Regression: 4 đường login/verify phải đi qua _global_submit_gate.

Vì sao: gate chỉ chặn 3 đường GỬI VIDEO (`_generate_via_http`, `_submit_via_fetch`, submit_from_ui) —
nhưng các đường THÊM/KIỂM NICK hiện KHÔNG qua gate. Bulk import 20 cookie hoặc verify-all 50 nick
vẫn spam Dola như cũ → có thể ăn 710022002 trong lúc đang gửi video.

Các test dưới đây mô phỏng 2 job login chạy song song và xác nhận gate được gọi >=2 lần
(mỗi job 1 lần). Trước fix: FAIL. Sau fix: PASS.

Chạy: .venv/bin/python test_login_bypass_regression.py
"""
import asyncio
import json
import tempfile
import time
from pathlib import Path

import browser_pool
import config
import server
import video_worker_ui as vw
from browser_pool import BrowserPool

# Tắt mọi gap theo proxy để test chạy nhanh; chỉ quan tâm gate toàn cục.
config.SUBMIT_GAP_GLOBAL_SEC = 0.1    # 100ms — đủ để 2 job cách nhau nếu qua gate
config.SUBMIT_JITTER_GLOBAL_SEC = 0
vw._GLOBAL_SUBMIT["slot"] = 0.0


# ---------- helpers ----------
def _gate_recorder():
    """Trả về (gate_mock, list_seen). Mỗi lần gate được gọi, ghi ('login', acc)."""
    seen: list[tuple[str, str]] = []

    async def gate(account: str) -> None:
        seen.append(("login", account))

    return gate, seen


def _patch_gate(gate_mock):
    """Thay _global_submit_gate trong các module đang dùng."""
    saved_vw = vw._global_submit_gate
    vw._global_submit_gate = gate_mock
    return saved_vw


def _restore_gate(saved):
    vw._global_submit_gate = saved


def _fake_pool(tmp: Path) -> BrowserPool:
    root = Path(tmp) / "accounts"
    for n in ("n1", "n2"):
        (root / n).mkdir(parents=True, exist_ok=True)
        (root / n / "cookies.json").write_text(
            json.dumps([{"name": "sessionid", "value": "s"}, {"name": "msToken", "value": "m"}]),
            encoding="utf-8",
        )
    config.ACCOUNTS_DIR = root
    config.PROXY = ""
    return BrowserPool(accounts_dir=str(root), db_path=str(Path(tmp) / "p.db"), max_concurrency=2)


async def _expect_gate_called_for(seen, label: str):
    """Đếm số lần gate được gọi với label 'login'. Hiện tại = 0 (bypass); sau fix = >=1/job."""
    logins = [s for s in seen if s[0] == label]
    return len(logins)


# ---------- TEST 1: _run_add_job phải qua gate ----------
async def test_run_add_job_passes_through_gate():
    """Trước fix: gate không được gọi (bypass). Sau fix: gate được gọi với account name."""
    gate_mock, seen = _gate_recorder()
    saved = _patch_gate(gate_mock)
    try:
        # Mock add_account_flow để không mở Chrome thật
        async def fake_add(name, email, password, totp):
            return True
        saved_add = server.add_account_flow
        server.add_account_flow = fake_add
        try:
            with tempfile.TemporaryDirectory() as tmp:
                _fake_pool(Path(tmp))
                # Đặt JOBS = {} để _run_add_job không dính state cũ
                server.JOBS.clear()
                await asyncio.gather(
                    server._run_add_job("n1", "e1@x", "p1", ""),
                    server._run_add_job("n2", "e2@x", "p2", ""),
                )
        finally:
            server.add_account_flow = saved_add
    finally:
        _restore_gate(saved)
    n = await _expect_gate_called_for(seen, "login")
    assert n >= 2, (
        f"_run_add_job BYPASS gate (chỉ gọi {n} lần cho 2 job, kỳ vọng >=2). "
        f"Nếu mục tiêu là chống rate-limit, mọi đường tới Dola phải qua gate. seen={seen}"
    )
    assert ("login", "n1") in seen and ("login", "n2") in seen, seen


# ---------- TEST 2: _run_facebook_add_job phải qua gate ----------
async def test_run_facebook_add_job_passes_through_gate():
    gate_mock, seen = _gate_recorder()
    saved = _patch_gate(gate_mock)
    # server._run_facebook_add_job import add_account_via_facebook cục bộ từ facebook_login,
    # nên phải patch trên facebook_login chứ không phải server.
    import facebook_login
    async def fake_fb(name, cookie_line, on_step=None, visible=True):
        return name
    saved_fb = facebook_login.add_account_via_facebook
    facebook_login.add_account_via_facebook = fake_fb
    try:
        with tempfile.TemporaryDirectory() as tmp:
            _fake_pool(Path(tmp))
            server.JOBS.clear()
            await asyncio.gather(
                server._run_facebook_add_job("n1", "fake_cookie_1"),
                server._run_facebook_add_job("n2", "fake_cookie_2"),
            )
    finally:
        facebook_login.add_account_via_facebook = saved_fb
        _restore_gate(saved)
    n = await _expect_gate_called_for(seen, "login")
    assert n >= 2, f"_run_facebook_add_job BYPASS gate ({n}/2). seen={seen}"


# ---------- TEST 3: apply_cookies_to_account phải qua gate ----------
async def test_apply_cookies_to_account_passes_through_gate():
    """Gate phải được gọi ở ĐẦU apply_cookies_to_account, trước mọi thao tác tốn Chrome/HTTP.

    Để test không cần Chrome thật, ta ép parse_cookie_input trả [] → hàm raise sớm.
    Vì gate đặt ở dòng đầu tiên (theo thiết kế fix), nó vẫn phải được gọi.
    """
    from cookie_service import apply_cookies_to_account
    gate_mock, seen = _gate_recorder()
    saved = _patch_gate(gate_mock)
    import cookie_service
    saved_parse = cookie_service.parse_cookie_input

    async def fake_parse(_):
        return []
    cookie_service.parse_cookie_input = fake_parse
    try:
        with tempfile.TemporaryDirectory() as tmp:
            _fake_pool(Path(tmp))
            await asyncio.gather(
                _safe_call(apply_cookies_to_account, "n1", "sessionid=abc"),
                _safe_call(apply_cookies_to_account, "n2", "sessionid=def"),
            )
    finally:
        cookie_service.parse_cookie_input = saved_parse
        _restore_gate(saved)
    n = await _expect_gate_called_for(seen, "login")
    assert n >= 2, f"apply_cookies_to_account BYPASS gate ({n}/2). seen={seen}"


async def _safe_call(fn, *args):
    """Gọi async function, nuốt exception để gate vẫn được ghi nhận."""
    try:
        await fn(*args)
    except Exception:
        pass


# ---------- TEST 4: BrowserPool.verify_account phải qua gate ----------
async def test_verify_account_passes_through_gate():
    """verify_account dùng Chrome (browser.check_login_state). Mock để test gate."""
    gate_mock, seen = _gate_recorder()
    saved = _patch_gate(gate_mock)
    try:
        import browser
        async def fake_check(_):
            return True
        saved_check = browser.check_login_state
        browser.check_login_state = fake_check
        try:
            with tempfile.TemporaryDirectory() as tmp:
                pool = _fake_pool(Path(tmp))
                await asyncio.gather(
                    pool.verify_account("n1"),
                    pool.verify_account("n2"),
                )
        finally:
            browser.check_login_state = saved_check
    finally:
        _restore_gate(saved)
    n = await _expect_gate_called_for(seen, "login")
    assert n >= 2, f"BrowserPool.verify_account BYPASS gate ({n}/2). seen={seen}"


# ---------- TEST 5: gate trong login thực sự giãn nhịp ----------
async def test_login_gate_actually_spaces_two_logins():
    """Khi nhiều login job CHẠY SONG SONG, gate phải giãn nhịp giữa chúng.

    Gate thật của video_worker_ui được dùng (không mock). Mock chỉ ghi stamp. Cấu hình gap = 0.2s.
    Yêu cầu: stamp job 2 phải sau stamp job 1 ít nhất 0.18s.
    """
    config.SUBMIT_GAP_GLOBAL_SEC = 0.2
    config.SUBMIT_JITTER_GLOBAL_SEC = 0
    vw._GLOBAL_SUBMIT["slot"] = 0.0   # reset để gate "tươi" — job 1 sẽ đặt slot mới

    stamps: list[float] = []
    real_gate = vw._global_submit_gate

    async def recorder_gate(account: str) -> None:
        await real_gate(account)        # chạy gate thật (có sleep nếu cần)
        stamps.append(time.monotonic()) # ghi stamp SAU khi gate xong — đây mới là "thời điểm cho phép gửi"

    saved = _patch_gate(recorder_gate)

    async def fake_add(name, email, password, totp):
        await asyncio.sleep(0.05)   # mỗi job tốn 50ms để các job kịp overlap
        return True

    saved_add = server.add_account_flow
    server.add_account_flow = fake_add
    _s = __import__("server")
    _s.login_concurrency = 5
    from asyncio import Semaphore
    _s.login_slots = Semaphore(5)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            _fake_pool(Path(tmp))
            server.JOBS.clear()
            await asyncio.gather(
                server._run_add_job("n1", "e1@x", "p1", ""),
                server._run_add_job("n2", "e2@x", "p2", ""),
                server._run_add_job("n3", "e3@x", "p3", ""),
            )
    finally:
        server.add_account_flow = saved_add
        _restore_gate(saved)
    stamps.sort()
    assert len(stamps) >= 2, f"gate không được gọi: {stamps}"
    gap = stamps[1] - stamps[0]
    assert gap >= 0.18, (
        f"Gate không giãn nhịp giữa 2 login job: gap={gap:.3f}s "
        f"(kỳ vọng >=0.18s với SUBMIT_GAP_GLOBAL_SEC=0.2). stamps={stamps}"
    )


if __name__ == "__main__":
    async def _all():
        tests = [
            test_run_add_job_passes_through_gate(),
            test_run_facebook_add_job_passes_through_gate(),
            test_apply_cookies_to_account_passes_through_gate(),
            test_verify_account_passes_through_gate(),
            test_login_gate_actually_spaces_two_logins(),
        ]
        for i, t in enumerate(tests):
            try:
                await t
                print(f"PASS test {i + 1}")
            except AssertionError as e:
                print(f"FAIL test {i + 1}: {e}")

    asyncio.run(_all())
