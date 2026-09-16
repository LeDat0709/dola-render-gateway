"""710022002: chờ 15s → 30s rồi thử lại CÙNG nick (chỉ khi CHẮC Dola chưa nhận lệnh) + giãn nhịp CHUNG ngay trước lúc gửi.

Vì sao: 16/09 máy bạn anh 8 job trên 8 proxy khác nhau cùng dính 710022002. _pace chỉ giãn theo TỪNG proxy và lúc BẮT
ĐẦU job; engine HTTP (mặc định) còn không nhận ra 710022002 (coi là "có thể đã trừ lượt" → job lỗi, không nghỉ/thử lại).
Chạy: .venv/bin/python test_rate_limit_retry.py
"""
import asyncio
import json
import tempfile
import time
from pathlib import Path

import browser
import browser_pool
import config
import submit_http
import video_worker_ui as vw
from browser_pool import BrowserPool
from video_worker import RateLimitedError

config.SUBMIT_GAP_SEC = config.SUBMIT_JITTER_SEC = 0
config.AUTO_RETRY = True
config.NO_COOLDOWN = True
RL = RateLimitedError("Dola tạm chặn vì gửi quá dày (710022002): 現在はリクエストが集中しています")


# ---------- giãn nhịp chung ----------
def test_global_gate_spaces_submits_across_all_proxies():
    saved = (config.SUBMIT_GAP_GLOBAL_SEC, config.SUBMIT_JITTER_GLOBAL_SEC)
    config.SUBMIT_GAP_GLOBAL_SEC, config.SUBMIT_JITTER_GLOBAL_SEC = 0.2, 0
    vw._GLOBAL_SUBMIT["slot"] = 0.0
    try:
        stamps = []

        async def one(acc):
            await vw._global_submit_gate(acc)
            stamps.append(time.monotonic())

        async def main():
            await asyncio.gather(*(one(f"n{i}") for i in range(3)))   # 3 nick, coi như 3 proxy khác nhau
        asyncio.run(main())
        stamps.sort()
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        assert all(g >= 0.18 for g in gaps), gaps
        config.SUBMIT_GAP_GLOBAL_SEC = 0
        t0 = time.monotonic(); asyncio.run(vw._global_submit_gate("x"))
        assert time.monotonic() - t0 < 0.05, "0 = tắt, không chờ"
    finally:
        config.SUBMIT_GAP_GLOBAL_SEC, config.SUBMIT_JITTER_GLOBAL_SEC = saved


def _cookies(root, nick):
    (root / nick).mkdir(parents=True, exist_ok=True)
    (root / nick / "cookies.json").write_text(json.dumps([{"name": "sessionid", "value": "s"}, {"name": "msToken", "value": "m"}]),
                                              encoding="utf-8")


def test_http_engine_gate_runs_right_before_submit():
    order, saved = [], (vw._global_submit_gate, submit_http.submit_via_http, browser.account_proxy_url,
                        vw._recent_conv_ids_http)

    async def gate(acc): order.append("gate")

    async def snap(acc, proxy): order.append("snapshot"); return set()

    async def submit(*a, **k):
        order.append("submit"); raise submit_http.SubmitHttpRejected("dừng test")
    vw._global_submit_gate, submit_http.submit_via_http, browser.account_proxy_url = gate, submit, lambda a: ""
    vw._recent_conv_ids_http = snap
    try:
        try:
            asyncio.run(vw._generate_via_http("n1", "p", "9:16", 10, "m", 60, None, None, None))
        except submit_http.SubmitHttpRejected:
            pass
    finally:
        vw._global_submit_gate, submit_http.submit_via_http, browser.account_proxy_url, vw._recent_conv_ids_http = saved
    assert order == ["gate", "snapshot", "submit"], order


def test_fetch_engine_gate_runs_before_marking_submitted():
    order, saved = [], (vw._global_submit_gate, vw._submit_query, vw._recent_conv_ids)

    async def gate(acc): order.append("gate")

    async def ids(page, ms, fp): return set()

    class Page:
        async def evaluate(self, js, args=None, **k):
            if js == vw.SUBMIT_JS:
                order.append("POST")
                return {"status": 200, "convId": "999", "events": [], "errors": []}
            return []
    vw._global_submit_gate, vw._submit_query, vw._recent_conv_ids = gate, (lambda c, fp: {"device_id": "1"}), ids
    try:
        cid = asyncio.run(vw._submit_via_fetch(Page(), None, "n1", "p", "9:16", 10, "m", {}, "m", "f",
                                               on_submitted=lambda a, s: order.append(f"submitted={s}")))
    finally:
        vw._global_submit_gate, vw._submit_query, vw._recent_conv_ids = saved
    assert cid == "999" and order == ["gate", "submitted=True", "POST"], order


# ---------- engine HTTP nhận ra 710022002 ----------
class _Resp:
    def __init__(self, status, text): self.status_code, self.text = status, text


def _run_submit(status, text):
    import curl_cffi.requests as cr
    root = Path(tempfile.mkdtemp()) / "accounts"; _cookies(root, "n1")
    saved = (config.ACCOUNTS_DIR, cr.post)
    config.ACCOUNTS_DIR, cr.post = root, (lambda url, **kw: _Resp(status, text))
    flags = []
    try:
        try:
            return asyncio.run(submit_http.submit_via_http("n1", "p", "9:16", 10, config.MODEL_KEY_SEEDANCE20, None,
                                                           on_submitted=lambda a, s: flags.append(s))), flags
        except Exception as e:  # noqa: BLE001
            return e, flags
    finally:
        config.ACCOUNTS_DIR, cr.post = saved


def test_http_submit_classifies_710022002():
    body4xx = '{"code":710022002,"message":"現在はリクエストが集中しています"}'
    e, flags = _run_submit(429, body4xx)
    assert isinstance(e, submit_http.SubmitHttpRateLimited) and e.maybe_delivered is False, e
    assert flags == [True, False], "4xx = chắc chắn chưa nhận → phải hạ cờ đã gửi"
    sse = 'event: STREAM_ERROR\ndata: {"error_code":710022002,"error_msg":"現在はリクエストが集中しています"}\n\n'
    e, flags = _run_submit(200, sse)
    assert isinstance(e, submit_http.SubmitHttpRateLimited) and e.maybe_delivered is True, e
    assert flags == [True], "200 không có mã hội thoại = CÓ THỂ đã nhận → chưa được hạ cờ, phải dò trước"
    ok = 'event: SSE_ACK\ndata: {"ack_client_meta":{"conversation_id":"38417931220334353"}}\n\n' + sse
    got, flags = _run_submit(200, ok)
    assert got == "38417931220334353", got


def _http_flow(snapshot_before, snapshots_after):
    """_generate_via_http khi submit trả 710022002 kiểu 'có thể đã nhận'."""
    saved = (submit_http.submit_via_http, browser.account_proxy_url, vw._recent_conv_ids_http, vw._global_submit_gate,
             vw.poll_conversation_http, vw.asyncio.sleep, config.ACCOUNTS_DIR)
    root = Path(tempfile.mkdtemp()) / "accounts"; _cookies(root, "n1")
    after = iter(snapshots_after); flags = []

    async def snap(acc, proxy):
        return snapshot_before if snap.first else next(after)
    snap.first = True

    async def submit(account, *a, on_submitted=None, **k):
        on_submitted(account, True); snap.first = False
        raise submit_http.SubmitHttpRateLimited("Dola tạm chặn vì gửi quá dày (710022002)", maybe_delivered=True)

    async def poll(account, cookie, ms, fp, conv_id, *a, **k):
        return {"conversation_id": conv_id, "account": account}
    real_sleep = asyncio.sleep

    async def fast(t): await real_sleep(0)

    async def gate(acc): pass
    submit_http.submit_via_http, browser.account_proxy_url, vw._recent_conv_ids_http = submit, (lambda a: ""), snap
    vw._global_submit_gate, vw.poll_conversation_http, vw.asyncio.sleep, config.ACCOUNTS_DIR = gate, poll, fast, root
    try:
        try:
            return asyncio.run(vw._generate_via_http("n1", "p", "9:16", 10, "m", 60, None, None, None,
                                                     on_submitted=lambda a, s: flags.append(s))), flags
        except Exception as e:  # noqa: BLE001
            return e, flags
    finally:
        (submit_http.submit_via_http, browser.account_proxy_url, vw._recent_conv_ids_http, vw._global_submit_gate,
         vw.poll_conversation_http, vw.asyncio.sleep, config.ACCOUNTS_DIR) = saved


def test_http_710022002_probe_decides_delivery():
    # dò đủ, không có hội thoại mới → CHẮC chưa nhận → RateLimitedError + hạ cờ (pool được thử lại)
    got, flags = _http_flow({"1"}, [{"1"}] * 10)
    assert isinstance(got, RateLimitedError) and flags == [True, False], (got, flags)
    # có hội thoại mới → Dola ĐÃ nhận → dùng luôn, không hạ cờ, không báo lỗi
    got, flags = _http_flow({"1"}, [{"1", "77"}])
    assert isinstance(got, dict) and got["conversation_id"] == "77" and flags == [True], (got, flags)
    # dò hỏng → KHÔNG kết luận → lỗi "có thể đã trừ lượt", KHÔNG phải RateLimitedError, KHÔNG hạ cờ
    got, flags = _http_flow({"1"}, [None] * 10)
    assert isinstance(got, RuntimeError) and not isinstance(got, RateLimitedError) and flags == [True], (got, flags)
    # không chụp được danh sách trước khi gửi → cũng không kết luận
    got, flags = _http_flow(None, [{"1"}] * 10)
    assert isinstance(got, RuntimeError) and not isinstance(got, RateLimitedError) and flags == [True], (got, flags)


# ---------- pool: thử lại CÙNG nick ----------
def _pool(tmp, nicks=("n1", "n2")):
    root = Path(tmp) / "accounts"
    for n in nicks:
        (root / n).mkdir(parents=True)
    config.ACCOUNTS_DIR, config.PROXY = root, ""
    return BrowserPool(accounts_dir=str(root), db_path=str(Path(tmp) / "p.db"), max_concurrency=2)


def _scripted(plan):
    calls, idx = [], {}

    async def gen(account, *a, on_submitted=None, **kw):
        calls.append(account)
        i = idx.get(account, 0); idx[account] = i + 1
        step = plan[account][min(i, len(plan[account]) - 1)]
        if step == "rl":                     # Dola CHẮC chưa nhận: cờ đã gửi True rồi hạ về False, rồi báo 710022002
            on_submitted(account, True); on_submitted(account, False); raise RL
        if step == "rl_maybe":               # có thể đã nhận: cờ vẫn True
            on_submitted(account, True); raise RL
        on_submitted(account, True)
        return {"video_url": "u", "account": account}
    gen.calls = calls
    return gen


def _with_waits(waits, fn):
    saved = (config.RATE_LIMIT_RETRY_WAITS, browser_pool.generate_video, config.SUBMIT_GAP_GLOBAL_SEC)
    config.RATE_LIMIT_RETRY_WAITS, config.SUBMIT_GAP_GLOBAL_SEC = waits, 0
    try:
        return fn()
    finally:
        config.RATE_LIMIT_RETRY_WAITS, browser_pool.generate_video, config.SUBMIT_GAP_GLOBAL_SEC = saved


def test_pool_waits_then_retries_same_nick():
    def body():
        with tempfile.TemporaryDirectory() as tmp:
            pool = _pool(tmp)
            gen = _scripted({"n1": ["rl", "rl", "ok"], "n2": ["ok"]})
            browser_pool.generate_video = gen
            t0 = time.monotonic()
            r = asyncio.run(pool.generate_video("p", "9:16", 10, account="n1"))
            assert r["account"] == "n1" and gen.calls == ["n1", "n1", "n1"], gen.calls
            assert time.monotonic() - t0 >= 0.15 - 0.02, "phải CHỜ trước mỗi lần thử lại"
    _with_waits((0.05, 0.1), body)


def test_pool_rotates_after_retries_exhausted():
    def body():
        with tempfile.TemporaryDirectory() as tmp:
            pool = _pool(tmp)
            gen = _scripted({"n1": ["rl"], "n2": ["ok"]})
            browser_pool.generate_video = gen
            r = asyncio.run(pool.generate_video("p", "9:16", 10, account="n1"))
            assert gen.calls == ["n1", "n1", "n1", "n2"] and r["account"] == "n2", gen.calls
    _with_waits((0.01, 0.02), body)


def test_pool_never_retries_when_maybe_delivered():
    def body():
        with tempfile.TemporaryDirectory() as tmp:
            pool = _pool(tmp)
            gen = _scripted({"n1": ["rl_maybe"], "n2": ["ok"]})
            browser_pool.generate_video = gen
            try:
                asyncio.run(pool.generate_video("p", "9:16", 10, account="n1"))
                raise AssertionError("có thể đã trừ lượt mà vẫn chạy tiếp")
            except RateLimitedError:
                pass
            assert gen.calls == ["n1"], f"gửi lại khi có thể đã nhận = trừ lượt 2 lần: {gen.calls}"
    _with_waits((0.01, 0.02), body)


def test_pool_retry_disabled_keeps_old_behaviour():
    def body():
        with tempfile.TemporaryDirectory() as tmp:
            pool = _pool(tmp)
            gen = _scripted({"n1": ["rl"], "n2": ["ok"]})
            browser_pool.generate_video = gen
            asyncio.run(pool.generate_video("p", "9:16", 10, account="n1"))
            assert gen.calls == ["n1", "n2"], gen.calls
    _with_waits((), body)


if __name__ == "__main__":
    for name in [n for n in dir() if n.startswith("test_")]:
        globals()[name]()
        print("PASS", name)
    print("OK")
