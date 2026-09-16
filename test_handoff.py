"""Chỉ nhả trình duyệt khi Dola đã hết hỏi lại — bật HTTP poll không được làm chết job 30s."""
import asyncio
import tempfile
from pathlib import Path

import video_worker_ui as vw

CONFIRM = "30秒の動画を生成しますか？よろしいですか"       # Dola hỏi xác nhận thời lượng
VIDEO = {"ok": True, "texts": [], "videos": ["https://x/v.mp4"], "videoModels": [""], "images": []}


class FakePage:
    def __init__(self, polls):
        self.polls = list(polls)
    async def evaluate(self, *a, **kw):
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]


def _run(polls, quiet, answered, shared=None, handoff_after=0):
    async def main():
        page = FakePage(polls)
        return await vw.poll_conversation("acc1", page, FakeCtx(), "77", timeout=60,
                                          handoff_after=handoff_after, answered=shared)

    class FakeCtx:
        async def cookies(self, *a): return []

    vw.HANDOFF_QUIET_SEC = quiet
    vw._reply_yes = _fake_reply(answered)
    return asyncio.run(main())


def _fake_reply(answered):
    async def reply(page, *a, **kw):
        answered.append(True)
        return True
    return reply


def setup_module(_=None):
    real_sleep = asyncio.sleep
    async def fast(_s): await real_sleep(0)
    asyncio.sleep = fast                      # bỏ 5s chờ mỗi vòng poll
    tmp = Path(tempfile.mkdtemp()) / "v.mp4"; tmp.write_bytes(b"0" * 10)
    async def fake_download(url, account, prompt=""): return tmp   # prompt: khớp _download(url, account, prompt) mới
    vw._download = fake_download


def test_no_question_hands_off():
    out = _run([{"ok": True, "texts": [], "videos": [], "images": []}], quiet=0, answered=[])
    assert out.get("handoff") is True


def test_pending_question_is_answered_first():
    answered = []
    polls = [{"ok": True, "texts": [CONFIRM], "videos": [], "images": []}, VIDEO]
    out = _run(polls, quiet=30, answered=answered)     # còn trong cửa sổ im tiếng → không nhả
    assert answered, "phải trả lời câu hỏi của Dola trước khi nhả trình duyệt"
    assert not out.get("handoff") and out["conversation_id"] == "77"


# Đúng những câu Dola hỏi lại trong log 09:56 — phải nhận ra là "cần mở lại trình duyệt".
DOLA_ASKS = [
    "30秒の動画生成は現在サポートされていません。最短4秒、最長15秒まで可能です。",
    "30秒の動画生成は現在対応していません。最短4秒、最長15秒まで可能です。",
    "生成可能な案は以下のいずれです：\n\nA. 15秒版：前半の主要対立",
    "30秒の動画を生成しますか？よろしいですか",
]


def test_late_questions_reopen_browser():
    for text in DOLA_ASKS:
        assert vw._question_needs_browser(text), text[:40]
    assert not vw._question_needs_browser("生成中です")      # dòng trạng thái thì bỏ qua
    assert not vw._question_needs_browser("")


# Đúng các câu trong log 09:45–09:54 từng làm job chết oan / vòng vo.
CAP_MSGS = [
    "30秒の動画生成はできません。現在対応しているのは4～15秒です。",
    "30 秒の動画生成はできません。現在対応しているのは 4～15 秒です。",
    "30秒版は直接生成できません。動画生成は4～15秒まで対応しています。",
    "30秒の動画生成は現在サポートしていません。最短4秒、最長15秒まで対応可能です。",
]


def test_duration_cap_is_not_content_policy():
    """Dola hạ thời lượng ≠ chặn nội dung — trước đây job bị đánh chết oan."""
    blocks = lambda t: bool(vw.CONTENT_POLICY_PATTERN.search(t)) and not vw._is_duration_capped(t)
    for m in CAP_MSGS:
        assert vw._is_duration_capped(m), m[:30]
        assert not blocks(m), f"vẫn bị coi là chặn nội dung: {m[:40]}"
    real_block = "ご希望のコンテンツを生成できません。他の内容をお試しください。"
    assert blocks(real_block)                          # chặn nội dung thật thì vẫn phải bắt


def test_reply_uses_dola_cap_not_30s():
    assert vw._capped_seconds("最短4秒、最長15秒まで可能です") == 15
    assert vw._capped_seconds("最長の10秒で生成します") == 10
    assert vw._capped_seconds("生成中です") is None


def test_short_job_keeps_its_duration_when_dola_offers_cap():
    """Log 11/9 15:49: job 10s, Dola mời 15s, tool trả 'はい' → video 15s → credit 15s → 'không đủ lượt' ×23."""
    cap = "30秒の動画生成は現在サポートしていません。最短4秒、最長15秒まで対応可能です。"
    assert vw._effective_duration(30, 15) == 15            # xin 30 → hạ xuống 15 như trước
    assert vw._effective_duration(10, 15) == 10            # xin 10 → GIỮ 10, không nâng lên 15
    assert vw._effective_duration(None, 15) == 15
    assert vw._capped_reply(cap, "9:16", 30) == "はい"
    ans = vw._capped_reply(cap, "9:16", 10)
    assert ans != "はい" and "10秒" in ans and "9:16" in ans, ans


def test_unclear_prompt_is_not_a_credit_problem():
    """Log 11/9 16:51: prompt 'con mefo' → Dola '意味不明なため直接生成できません' → từng bị gắn 'Không đủ điểm/quota' → nick hết lượt oan."""
    unclear = [
        "「con mefo」が意味不明なため直接生成できません。正しいプロンプトを補完してください。",
        "プロンプトが「con mefo」だけでは内容が不明瞭なため、安全に生成できません。",
        "動画生成リクエストを受け付けました。ただし、「con mefo」は有効な指示内容として認識できません。",
        "実行する内容が不足しています。ビデオの内容を具体的に指定してください。",
    ]
    for m in unclear:
        assert vw.PROMPT_UNCLEAR_PATTERN.search(m), m[:30]
    credit = "現在のパラメーターで生成すると、4動画クレジットが使用されます。 本日は残り2のみです。"
    assert not vw.PROMPT_UNCLEAR_PATTERN.search(credit)      # thiếu credit thật thì vẫn là thiếu credit


def test_prompt_marks_are_scaled_to_duration():
    """Log 11/9: kịch bản 8 cảnh 30s mà chọn 10s → Dola hỏi lại 369 vòng. Co mốc về thang 10s trước khi gửi."""
    p = "镜头1，0–3.5秒 — 开场。镜头8，25-30秒 — 结尾。总时长30秒，9:16，30fps，1080p。"
    out = vw.fit_prompt_to_duration(p, 10)
    assert "0–1.2秒" in out and "8.3-10秒" in out and "总时长10秒" in out, out
    assert "9:16" in out and "30fps" in out and "1080p" in out            # không phải giây thì không đụng
    same = "một cô gái đi dưới mưa, cảnh 8 giây, 9:16"
    assert vw.fit_prompt_to_duration(same, 10) == same                      # trong thời lượng thì giữ nguyên
    assert vw.fit_prompt_to_duration(p, None) == p


def test_parse_credit_need_from_dola_message():
    ja = "現在のパラメーターで生成すると、4動画クレジットが使用されます。 本日は残り2のみです。パラメーターを変更してもう一度お試しください。"
    assert vw._parse_credit_need(ja) == (4, 2)
    assert vw._parse_credit_need("エラーが発生しました。") == (None, None)
    err = vw._param_change_error(ja)
    assert (err.need, err.left) == (4, 2) and isinstance(err, vw.ParameterChangeError)


def test_credits_used_is_read_from_start_message():
    """A2: Dola báo giá lúc bắt đầu dựng → học giá ngay video đầu, không đợi một nick thiếu credit."""
    assert vw._credits_used("この動画の生成には4動画クレジットを使用します。約5分後に完成します。") == 4
    assert vw._credits_used("本日は残り2のみです。") is None          # số dư, không phải giá
    assert vw._credits_used("生成を開始します。") is None
    r = _run([{"ok": True, "texts": ["4動画クレジットを使用します"], "videos": [], "images": []}, VIDEO],
             quiet=0, answered=[], handoff_after=None)                  # giữ trình duyệt tới khi có video
    assert r["credits_used"] == 4 and r["local_path"]                  # giá đi theo kết quả về pool


def test_download_failure_keeps_job_with_cdn_link():
    """A3: video đã dựng (đã trừ credit) mà tải rớt mạng → thử 3 lần; vẫn hỏng thì job xong với link Dola."""
    import video_worker as vwk
    calls = []
    async def flaky(url, fname, proxy=None):
        calls.append(url)
        if len(calls) < 3:
            raise RuntimeError("Connection reset")
        fname.write_bytes(b"0" * 200_000)
    saved = (vwk._fetch_to_file, vwk.config.DOWNLOAD_DIR, vwk.DOWNLOAD_RETRY_SEC, vw._download)
    vwk._fetch_to_file, vwk.config.DOWNLOAD_DIR, vwk.DOWNLOAD_RETRY_SEC = flaky, tempfile.mkdtemp(), 0
    try:
        p = asyncio.run(vwk._download("https://x/v.mp4", "acc1"))
        assert p.exists() and len(calls) == 3, calls                      # lần 3 mới được
        async def dead(url, fname, proxy=None): raise RuntimeError("Connection reset")
        vwk._fetch_to_file = dead
        try:
            asyncio.run(vwk._download("https://x/v.mp4", "acc1"))
            assert False, "phải ném DownloadError"
        except vwk.DownloadError as e:
            assert "https://x/v.mp4" in str(e)                            # link để tải tay
        vw._download = vwk._download                                      # poll dùng bản thật (đang hỏng)
        r = _run([VIDEO], quiet=0, answered=[])
        assert r["video_url"] == "https://x/v.mp4" and r["local_path"] is None and r["download_error"]
    finally:
        vwk._fetch_to_file, vwk.config.DOWNLOAD_DIR, vwk.DOWNLOAD_RETRY_SEC, vw._download = saved


def test_submit_timeout_never_resubmits():
    """Quá giờ chờ SUBMIT_JS: Dola đã tạo hội thoại → dùng nó; chưa → _FetchDelivered (không gửi lại = không trừ lượt 2 lần)."""
    saved = (vw.FETCH_SUBMIT_TIMEOUT_SEC, vw._recent_conv_ids)
    vw.FETCH_SUBMIT_TIMEOUT_SEC = 0.01

    class Page:
        async def evaluate(self, *a, **kw): await asyncio.Event().wait()   # treo mãi → wait_for hết giờ
        async def wait_for_timeout(self, ms): pass

    n = {"calls": 0}
    async def convs_then_new(page, t, f): n["calls"] += 1; return {"1"} if n["calls"] == 1 else {"1", "2"}
    vw._recent_conv_ids = convs_then_new
    submitted = []
    try:
        got = asyncio.run(vw._submit_via_fetch(Page(), None, "acc1", "p", "9:16", 10, "seedance_v2.5",
                                               {"device_id": "d"}, "tok", "fp", on_submitted=lambda a, s: submitted.append(s)))
        assert got == "2" and submitted == [True], (got, submitted)       # nhận hội thoại Dola vừa tạo, KHÔNG lật cờ về False
        async def convs_same(page, t, f): return {"1"}
        vw._recent_conv_ids = convs_same
        try:
            asyncio.run(vw._submit_via_fetch(Page(), None, "acc1", "p", "9:16", 10, "seedance_v2.5", {"device_id": "d"}, "tok", "fp"))
            assert False, "phải ném _FetchDelivered"
        except vw._FetchDelivered:
            pass
    finally:
        vw.FETCH_SUBMIT_TIMEOUT_SEC, vw._recent_conv_ids = saved


def test_uncertain_submit_only_resends_when_probe_is_certain():
    """Gửi lệnh lỗi không rõ kết quả (stream đứt / 5xx / 710022002): thấy hội thoại mới → dùng; dò ĐỦ mà không thấy →
    False + được gửi lại; dò hỏng hoặc thiếu ảnh chụp trước khi gửi → _FetchDelivered, KHÔNG gửi lại (trừ 2 lần)."""
    import video_worker as vwk
    saved = vw._recent_conv_ids

    def page(outcome):
        class Page:
            async def evaluate(self, *a, **kw):
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            async def wait_for_timeout(self, ms): pass
        return Page()

    def probes(*seq):   # lần 1 = ảnh chụp trước khi gửi, sau đó mỗi lần dò lấy phần tử kế (hết thì lặp phần tử cuối)
        it = {"i": 0}
        async def f(p, t, fp):
            v = seq[min(it["i"], len(seq) - 1)]; it["i"] += 1
            return v
        return f

    def submit(outcome, *seq):
        vw._recent_conv_ids = probes(*seq)
        flags = []
        try:
            got = asyncio.run(vw._submit_via_fetch(page(outcome), None, "acc1", "p", "9:16", 10, "seedance_v2.5",
                                                   {"device_id": "d"}, "tok", "fp", on_submitted=lambda a, s: flags.append(s)))
            return got, flags
        except Exception as e:  # noqa: BLE001
            return e, flags

    net = Exception("TypeError: network error")
    try:
        got, flags = submit(net, {"1"}, {"1"}, {"1", "12", "9"})
        assert got == "12" and flags == [True], (got, flags)                        # max theo số, không theo chuỗi
        got, flags = submit(net, {"1"}, {"1"})
        assert isinstance(got, vw._FetchSubmitFailed) and flags == [True, False], (got, flags)   # dò đủ → chắc chắn chưa nhận
        got, flags = submit(net, {"1"}, None)
        assert isinstance(got, vw._FetchDelivered) and flags == [True], (got, flags)   # dò hỏng → không gửi lại
        got, flags = submit(net, None, {"1", "2"})
        assert isinstance(got, vw._FetchDelivered) and flags == [True], (got, flags)   # thiếu ảnh chụp trước → không đoán hội thoại cũ
        got, flags = submit({"status": 502, "errors": []}, {"1"}, {"1"})
        assert isinstance(got, vw._FetchSubmitFailed) and flags == [True, False], (got, flags)
        got, flags = submit({"status": 502, "errors": []}, {"1"}, None)
        assert isinstance(got, vw._FetchDelivered) and flags == [True], (got, flags)
        got, flags = submit({"status": 403, "errors": ["<html>Forbidden</html>"]}, {"1"}, None)
        assert isinstance(got, vw._FetchSubmitFailed) and flags == [True, False], (got, flags)   # 4xx: chặn ở cửa
        rl = {"status": 200, "errors": ['{"error_code":710022002}']}
        got, flags = submit(rl, {"1"}, {"1"})
        assert isinstance(got, vwk.RateLimitedError) and flags == [True, False], (got, flags)
        got, flags = submit(rl, {"1"}, None)
        assert isinstance(got, vw._FetchDelivered) and flags == [True], (got, flags)
        got, flags = submit(rl, {"1"}, {"1", "5"})
        assert got == "5" and flags == [True], (got, flags)
    finally:
        vw._recent_conv_ids = saved


def _fake_net(dead_proxy):
    """aiohttp giả: đi qua `dead_proxy` thì lỗi mạng; đường khác đọc được hội thoại có video. Trả (Session, danh sách proxy đã đi)."""
    import json as _j
    used = []
    video_page = {"downlink_body": {"pull_singe_chain_downlink_body": {"messages": [{"content": _j.dumps([
        {"block_type": 2074, "content": {"creation_block": {"creations": [{"type": 2, "video": {"download_url": "https://x/v.mp4", "video_model": ""}}]}}}])}]}}}

    class Resp:
        status = 200
        async def json(self, **_): return video_page
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class Session:
        def post(self, *a, proxy=None, **k):
            used.append(proxy)
            if proxy == dead_proxy:
                raise OSError("Cannot connect to host (proxy chết)")
            return Resp()
        async def close(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    return Session, used


def test_dead_proxy_mid_render_switches_route_not_lose_video():
    """Đối thủ v1.0.88 "IP đứt giữa lúc chờ dựng → đổi IP rồi dò tiếp". IP proxy của nick chết SAU khi gửi (đã trừ lượt):
    trước đây quay vòng tới hết giờ rồi báo quá giờ, video mất. Giờ sau POLL_NET_FAILS lỗi liên tiếp đổi đường đọc."""
    import browser
    dead = "http://u:p@9.9.9.9:1"
    saved = (vw.aiohttp.ClientSession, browser.account_proxy_url, vw.config.PROXY)
    try:
        browser.account_proxy_url = lambda acc: dead
        vw.config.PROXY = ""
        # 1) theo dõi HTTP: đi proxy nick chết 3 lần → chuyển đi thẳng → ra video
        Session, used = _fake_net(dead)
        vw.aiohttp.ClientSession = Session
        out = asyncio.run(vw.poll_conversation_http("acc1", "c=1", "", "", "77", 60, answered=set()))
        assert out.get("local_path") and used[:vw.POLL_NET_FAILS] == [dead] * vw.POLL_NET_FAILS and used[-1] is None, used
        # 2) theo dõi TRONG Chrome: mạng trong trang chết → đọc qua HTTP (IP nick lấy lại vẫn chết → đi thẳng) → ra video
        Session, used = _fake_net(dead)
        vw.aiohttp.ClientSession = Session

        class Page:
            url = "https://www.dola.com/chat/77"
            async def evaluate(self, *a, **k): raise Exception("net::ERR_TUNNEL_CONNECTION_FAILED")

        class Ctx:
            async def cookies(self, *a): return [{"name": "sessionid", "value": "s"}]
        out = asyncio.run(vw.poll_conversation("acc1", Page(), Ctx(), "77", timeout=60, answered=set()))
        assert out.get("local_path") and out["conversation_id"] == "77", out
        assert used[:vw.POLL_NET_FAILS] == [dead] * vw.POLL_NET_FAILS and used[-1] is None, used
    finally:
        vw.aiohttp.ClientSession, browser.account_proxy_url, vw.config.PROXY = saved


def test_scan_account_videos_reads_history_only():
    """Check Video Nick: quét hội thoại gần đây bằng cookie (chỉ đọc) → chỉ trả hội thoại có video, mới nhất trước;
    proxy nick lỗi thì đọc đi thẳng."""
    import json as _j
    import browser
    tmp = Path(tempfile.mkdtemp())
    (tmp / "n1").mkdir()
    (tmp / "n1" / "cookies.json").write_text(_j.dumps([{"name": "sessionid", "value": "s"}, {"name": "msToken", "value": "m"}]), encoding="utf-8")
    cells = {"downlink_body": {"pull_recent_conv_chain_downlink_body": {"cells": [
        {"conversation": {"conversation_id": "111", "name": "cũ", "create_time": 1_700_000_000_000}},
        {"conversation": {"conversation_id": "222", "name": "chỉ chữ", "create_time": 1_700_000_500}},
        {"conversation": {"conversation_id": "333", "name": "mới", "create_time": 1_700_000_900}}]}}}
    def single(video):
        blocks = [{"content": {"text_block": {"text": "xin chào"}}}]
        if video:
            blocks.append({"block_type": 2074, "content": {"creation_block": {"creations": [{"type": 2, "video": {"download_url": video, "video_model": ""}}]}}})
        return {"downlink_body": {"pull_singe_chain_downlink_body": {"messages": [{"content": _j.dumps(blocks)}]}}}
    sent = []

    class Resp:
        status = 200
        def __init__(self, d): self.d = d
        async def json(self, **_): return self.d
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class Session:
        def post(self, url, data=None, **k):
            body = _j.loads(data)
            sent.append(body["cmd"])
            if "recent_conv" in url:
                return Resp(cells)
            cid = body["uplink_body"]["pull_singe_chain_uplink_body"]["conversation_id"]
            return Resp(single({"111": "https://x/a.mp4", "333": "https://x/c.mp4"}.get(cid)))
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    def boom(acc): raise RuntimeError("proxy nick lỗi")
    saved = (vw.aiohttp.ClientSession, vw.config.ACCOUNTS_DIR, browser.account_proxy_url)
    try:
        vw.aiohttp.ClientSession, vw.config.ACCOUNTS_DIR, browser.account_proxy_url = Session, tmp, boom
        vids = asyncio.run(vw.scan_account_videos("n1", 10))
        assert [v["conversation_id"] for v in vids] == ["333", "111"], vids        # mới nhất trước, bỏ hội thoại không có video
        assert vids[1]["created_at"] == 1_700_000_000 and vids[0]["video_url"] == "https://x/c.mp4", vids   # ms → giây
        assert set(sent) == {3200, 3100}, "chỉ lệnh ĐỌC (recent_conv + single), không gửi tin"
    finally:
        vw.aiohttp.ClientSession, vw.config.ACCOUNTS_DIR, browser.account_proxy_url = saved


def test_http_engine_submits_without_chrome_and_falls_back():
    """Engine không-Chrome: submit_via_http OK → nhả slot ngay, cờ 'đã gửi'=True, poll HTTP ra video, KHÔNG mở Chrome.
    Dola từ chối chắc chắn (SubmitHttpRejected) → generate_video rơi về đường fetch (Chrome), cờ về False để pool xoay."""
    import submit_http
    import browser
    saved = (vw.config.SUBMIT_MODE, submit_http.submit_via_http, vw.poll_conversation_http,
             vw._account_cookies, browser.account_proxy_url, vw._generate_via_fetch)
    freed, flags, opened = [], [], []

    async def anop():
        return None
    try:
        vw.config.SUBMIT_MODE = "http"
        browser.account_proxy_url = lambda a: None
        vw._account_cookies = lambda a: ("c=1", "m", "f")

        async def ok_submit(account, prompt, ratio, dur, model, proxy, on_submitted=None):
            if on_submitted:
                on_submitted(account, True)
            return "38400000000000123"

        async def ok_poll(account, cookie, ms, fp, cid, timeout, on_poll=None, on_balance=None, answered=None, prompt=""):
            return {"video_url": "u", "local_path": "/tmp/x.mp4", "conversation_id": cid, "account": account}
        submit_http.submit_via_http = ok_submit
        vw.poll_conversation_http = ok_poll
        r = asyncio.run(vw.generate_video("n1", "mèo", "9:16", 30, model="seedance-2.5",
                                          on_submitted=lambda a, s: flags.append(s),
                                          on_browser_free=lambda: freed.append(1)))
        assert r.get("local_path") and flags == [True] and freed == [1], (r, flags, freed)

        flags.clear()

        async def rejected(account, prompt, ratio, dur, model, proxy, on_submitted=None):
            if on_submitted:
                on_submitted(account, True)
                on_submitted(account, False)
            raise submit_http.SubmitHttpRejected("verify/captcha")

        async def fake_fetch(account, *a, on_submitted=None, **kw):
            opened.append(account)
            if on_submitted:
                on_submitted(account, True)
            return {"video_url": "u", "local_path": "/tmp/y.mp4", "conversation_id": "9", "account": account}
        submit_http.submit_via_http = rejected
        vw._generate_via_fetch = fake_fetch
        r = asyncio.run(vw.generate_video("n1", "mèo", "9:16", 10, model="seedance-2.5",
                                          on_submitted=lambda a, s: flags.append(s), on_browser_hold=anop))
        assert r.get("local_path") and opened == ["n1"], (r, opened)
        assert flags[-1] is True and False in flags, flags   # False (được xoay) rồi True lại khi fetch gửi
    finally:
        (vw.config.SUBMIT_MODE, submit_http.submit_via_http, vw.poll_conversation_http,
         vw._account_cookies, browser.account_proxy_url, vw._generate_via_fetch) = saved


def test_guest_session_is_detected_not_credit():
    """Ảnh 15/09: cookie chết → Dola coi là KHÁCH. Passport phân biệt được (recent_conv thì không); câu từ chối của
    Dola phải thành GuestRefusedError, KHÔNG phải CreditError (「生成できません」 từng bị hiểu là hết điểm)."""
    from browser import passport_dead
    assert passport_dead({"message": "success", "data": {"user_id": 1}}) is False
    assert passport_dead({"message": "error", "data": {"error_code": 13, "name": "account_info_error",
                                                       "description": "session expired, please sign in again"}}) is True
    assert passport_dead({"message": "error", "data": {"error_code": 7}}) is None      # lỗi lạ → chưa kết luận
    assert passport_dead(None) is None
    guest = "ゲストは動画と画像を生成できません。作成を開始するにはログインしてください。"
    assert vw.GUEST_REFUSAL_PATTERN.search(guest)
    try:
        _http_poll([guest], set())
        assert False, "phải ném GuestRefusedError"
    except vw.GuestRefusedError as e:
        assert isinstance(e, vw.LoggedOutError) and e.not_charged
    assert not vw.GUEST_REFUSAL_PATTERN.search("このリクエストは安全チェックの対象外です。直接生成を開始します。")


def test_generation_started_is_status_not_refusal():
    """Log 11/9 16:51: '直接生成を開始します' = Dola bắt đầu tạo — từng bị coi là từ chối, job chết sau 20s."""
    assert vw._is_status_text("このリクエストは安全チェックの対象外です。直接生成を開始します。")
    assert not vw._is_status_text("30秒の動画生成はできません。対応範囲は4～15秒です。")   # từ chối thật vẫn là từ chối


def test_own_directive_is_ignored():
    own = "【この仕様で直接生成してください（30秒・アスペクト比9:16（縦））。長さ・比率は変更せず、追加の確認は不要です】"
    assert vw._is_own_message(own)                     # tin của chính mình, không phải Dola hỏi
    assert not vw._is_own_message(CAP_MSGS[0])


def test_answered_memory_survives_reopen():
    """Mở lại nick không được trả lời lại câu cũ — đó là vòng lặp đã đốt 20 lần mở nick."""
    answered = set()
    polls = [{"ok": True, "texts": [CONFIRM], "videos": [], "images": []}, VIDEO]
    out = _run(polls, quiet=30, answered=[], shared=answered)
    assert vw._answer_key(CONFIRM) in answered, "phải nhớ là đã trả lời câu này"

    # lần mở lại nick (poll mới, cùng bộ nhớ): câu cũ vẫn còn trong hội thoại → KHÔNG trả lời nữa
    again = []
    out2 = _run([{"ok": True, "texts": [CONFIRM], "videos": [], "images": []}],
                quiet=0, answered=again, shared=answered)
    assert not again, "đã trả lời lại câu cũ → ping-pong"
    assert out2.get("handoff") is True          # hết việc phải làm thì nhả trình duyệt


# Nguyên văn Dola trong log 11:14–11:16 (fb61593690226090): cùng một câu bị trả lời 3 lần vì
# tin nhắn được đọc lúc còn đang stream nên chuỗi lưu lại NGẮN HƠN chuỗi đọc lần sau.
STREAM_SHORT = "リクエストされた秒数 30 秒は対応範囲外です。動画生成は 4～15 秒まで可能です。"
STREAM_GROWN = STREAM_SHORT + "ご希望の秒数を教えてください。"
STREAM_MENU = STREAM_SHORT + "以下からお選びください：\n- A. 15 秒版（フル構成）\n- B. 10 秒版\n- C. 4 秒版"


def _run_answers(polls, shared, ratio=None, duration=30):
    """Chạy 1 lượt poll, trả về (số lần 'はい', các câu trả lời dạng chữ)."""
    yes, texts = [], []
    async def reply_yes(page, *a, **kw):
        yes.append(True); return True
    async def reply_text(page, ans, *a, **kw):
        texts.append(ans); return True

    class FakeCtx:
        async def cookies(self, *a): return []

    vw._reply_yes = reply_yes
    vw._reply_text = reply_text
    vw.HANDOFF_QUIET_SEC = 30
    asyncio.run(vw.poll_conversation("acc1", FakePage(polls), FakeCtx(), "77", timeout=60,
                                     handoff_after=0, answered=shared, ratio=ratio,
                                     duration=duration))
    return yes, texts


def test_streaming_message_is_answered_once():
    """Tin nhắn dài thêm trong lúc stream không được tính là câu hỏi MỚI."""
    shared = set()
    yes, _ = _run_answers([{"ok": True, "texts": [STREAM_SHORT], "videos": [], "images": []}, VIDEO], shared)
    assert len(yes) == 1, "phải trả lời câu Dola hỏi"
    yes2, _ = _run_answers([{"ok": True, "texts": [STREAM_GROWN], "videos": [], "images": []}, VIDEO], shared)
    assert not yes2, "cùng một câu (chỉ dài thêm) mà trả lời lại → ping-pong như log 11:16"


def test_option_list_gets_a_letter_not_yes():
    """Dola liệt kê A/B/C thì 'はい' vô nghĩa — phải đáp chữ cái, và theo mức giây Dola cho."""
    shared = set()
    yes, texts = _run_answers([{"ok": True, "texts": [STREAM_MENU], "videos": [], "images": []}, VIDEO], shared)
    assert not yes, "trả 'はい' cho menu A/B/C là câu trả lời sai → Dola hỏi lại mãi"
    assert texts, "phải chọn một phương án trong menu"
    assert "30" not in texts[0], f"đòi lại 30s sau khi Dola nói tối đa 15s: {texts[0]}"
    assert texts[0].startswith("A"), f"phải gọi đúng chữ cái phương án 15 giây: {texts[0]}"
    # hỏi lại y nguyên → im lặng; và HTTP poll cũng phải nhận ra để không mở lại nick vô ích
    yes2, texts2 = _run_answers([{"ok": True, "texts": [STREAM_MENU], "videos": [], "images": []}, VIDEO], shared)
    assert not yes2 and not texts2, "trả lời lại menu cũ → ping-pong"
    assert vw._answer_key(vw._NeedsBrowser(STREAM_MENU).full) in shared


def _http_poll(texts, shared, video_now=False):
    """poll_conversation_http với aiohttp giả: lượt 1 chỉ có `texts` (video_now=True thì có luôn video),
    lượt 2 có thêm video."""
    import json as _j

    def msg(text=None, video=None):
        blocks = []
        if text:
            blocks.append({"content": {"text_block": {"text": text}}})
        if video:
            blocks.append({"block_type": 2074, "content": {"creation_block": {"creations": [
                {"type": 2, "video": {"download_url": video, "video_model": ""}}]}}})
        return {"content": _j.dumps(blocks)}

    def page(*msgs):
        return {"downlink_body": {"pull_singe_chain_downlink_body": {"messages": list(msgs)}}}

    first = [msg(t) for t in texts] + ([msg(video="https://x/v.mp4")] if video_now else [])
    pages = [page(*first),
             page(*[msg(t) for t in texts], msg(video="https://x/v.mp4"))]

    class Resp:
        status = 200
        def __init__(self, data): self.data = data
        async def json(self, **_): return self.data
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class Session:
        def post(self, *a, **k): return Resp(pages.pop(0) if len(pages) > 1 else pages[0])
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    real = vw.aiohttp.ClientSession
    vw.aiohttp.ClientSession = Session
    try:
        return asyncio.run(vw.poll_conversation_http("acc1", "c=1", "", "", "77", 60, answered=shared))
    finally:
        vw.aiohttp.ClientSession = real


def test_http_poll_skips_answered_question():
    """Sau khi nhả nick, câu ĐÃ trả lời vẫn nằm trong 20 tin gần nhất — HTTP poll phải bỏ qua và
    chờ video, không được ném _NeedsBrowser (→ "hỏi đi hỏi lại", job chết oan sau 60s)."""
    out = _http_poll([CONFIRM], {vw._answer_key(CONFIRM)})
    assert out["conversation_id"] == "77" and out.get("local_path"), "câu cũ đã trả lời mà vẫn đòi mở lại nick"
    try:                                   # câu MỚI (chưa trả lời) thì vẫn phải xin mở lại nick
        _http_poll([CONFIRM], set())
        assert False, "câu chưa trả lời phải ném _NeedsBrowser"
    except vw._NeedsBrowser:
        pass


# Nguyên văn Dola trong ảnh chụp 16/9 — cả ba đều là câu KỂ "tôi đang/sắp dựng", không hỏi gì.
# Trước đây bị coi là câu trả lời cuối cùng → sau 4 nhịp poll job chết với "Dola báo: …" dù lượt ĐÃ TRỪ.
GENERATING_NOTICES = (
    "ありがとうございます。動画の生成が完了し次第、お知らせします。",
    "動画生成には少なくとも4秒が必要です。4秒のフック版をまず生成します。",
    "18秒はサポートされていないため、最長の15秒で生成します。",
)


def test_generating_notice_is_not_a_refusal():
    for t in GENERATING_NOTICES:
        assert vw._is_status_text(t), f"câu Dola đang dựng mà bị tính là lỗi: {t}"
        assert not vw._question_needs_browser(t), f"câu kể, không hỏi → không được mở lại nick: {t}"
    # Câu từ chối / hỏi thật vẫn phải giữ nguyên nghĩa cũ
    assert not vw._is_status_text("この内容では動画を生成できません。")
    assert not vw._is_status_text(STREAM_SHORT) and vw._question_needs_browser(STREAM_SHORT)


# Ảnh 16/9: trên dola.com nick ĐÃ ra video («動画が生成されました»), nhưng thẻ trong tool vẫn đỏ. Lý do: các
# nhánh bắt lỗi chạy TRƯỚC khối lấy video, nên một câu than của Dola nằm cùng lượt đọc là job chết oan
# (lượt thì đã trừ, video thì đã có).
COMPLAINTS = (
    "本日はまだ0動画クレジットが残っています。",                    # số dư sau khi trừ — không phải từ chối
    "この内容では動画の生成はできません。",                          # chặn nội dung
    "エラーが発生しました。しばらくしてからもう一度お試しください。",   # lỗi tạm thời
    "パラメーターを変更してもう一度お試しください。",                 # đổi thông số
)


def test_video_wins_over_complaints():
    for t in COMPLAINTS:
        out = _http_poll([t], set(), video_now=True)
        assert out.get("local_path"), f"có video rồi mà vẫn coi là lỗi: {t}"
        assert out["conversation_id"] == "77"


def test_duration_message_is_not_out_of_credit():
    """"直接生成できません…4–15秒まで対応" là câu THỜI LƯỢNG; chữ 生成できません từng làm job báo "hết điểm"
    và nick bị ghi hết điểm oan (tasks.db 16/9)."""
    t = "直接生成できません。現在の動画生成は 4–15 秒まで対応しており、ご指定の30秒には対応していません。"
    assert vw.CREDIT_FAIL_PATTERN.search(t) and vw._question_needs_browser(t)
    try:
        _http_poll([t], set())
        assert False, "câu thông số phải xin mở lại nick để trả lời"
    except vw._NeedsBrowser:
        pass
    except vw.CreditError:
        assert False, "vẫn bị đọc thành hết điểm"


def test_prompt_read_back_from_conversation():
    """Quét video trên Dola: job không còn trong tool thì prompt phải đọc lại từ chính tin mình đã gửi —
    tên hội thoại KHÔNG dùng được vì Dola đặt tên "動画生成リクエスト" cho mọi job (kiểm với dữ liệu thật 16/9)."""
    khan = ['動画が生成されました。',
            '動画は**Dreamina Seedance 2.5モデル**を使用して生成されます。4クレジットを使用し…',
            '生成された動画：con mèo đi chơi ngoài đường、9:16']
    assert vw.prompt_from_texts(khan) == "con mèo đi chơi ngoài đường"      # cắt đuôi tỉ lệ, giữ nguyên prompt
    directive = ['【この仕様で直接生成してください（10秒・アスペクト比9:16（縦））。長さ・比率は変更せず、追加の確認は不要です】\ncon mèo lướt sóng']
    assert vw.prompt_from_texts(directive) == "con mèo lướt sóng"
    assert vw.prompt_from_texts(['生成された動画：a 9:16 shot of a cat、9:16']) == "a 9:16 shot of a cat"   # chỉ cắt ở CUỐI
    assert vw.prompt_from_texts(['動画が生成されました。']) == ""            # không thấy → để người gọi rơi về nguồn khác


def test_each_video_gets_its_own_prompt():
    """Một hội thoại nhiều lượt (prompt khác nhau) → mỗi video mang ĐÚNG prompt sinh ra nó, không đội chung
    prompt mới nhất. Dola trả tin mới trước, nên prompt của video là tin "đã gửi" đầu tiên đứng SAU nó."""
    poll = {"stream": [("v", "https://x/v2.mp4", "m2"),
                       ("t", "動画が生成されました。"),
                       ("t", "生成された動画：prompt hai、9:16"),
                       ("v", "https://x/v1.mp4", "m1"),
                       ("t", "生成された動画：prompt một、9:16")]}
    out = vw.conversation_videos(poll)
    assert [o["prompt"] for o in out] == ["prompt hai", "prompt một"], out
    assert [o["video_url"] for o in out] == ["https://x/v2.mp4", "https://x/v1.mp4"]
    # Server cũ (chưa có stream) vẫn chạy: 1 video + prompt đọc từ texts
    old = {"videos": ["https://x/v1.mp4"], "videoModels": [""], "texts": ["生成された動画：chỉ một prompt、9:16"]}
    assert [o["prompt"] for o in vw.conversation_videos(old)] == ["chỉ một prompt"]
    assert vw.conversation_videos({"texts": ["không có video"]}) == []


def test_http_error_is_not_risk_control():
    """Dola/WAF/proxy trả HTTP lỗi ≠ captcha: không được gắn cooldown 30 phút cho nick.

    Ảnh 14:10: cả loạt nick "đang nghỉ (risk-control)" với 0/4 lượt — vì mọi status ≠ 200
    đều bị coi là risk control."""
    import video_worker as vwk
    assert vwk._check_submit({"convId": "1", "status": 200, "errors": ["x"]}) == "1"
    for res in ({"status": 403, "errors": ["<html>Forbidden</html>"]},
                {"status": 502, "errors": []}, {"status": 0, "errors": []}):
        try:
            vwk._check_submit(res)
            assert False, f"phải ném lỗi cho {res}"
        except vwk.SubmitRejected as e:
            assert str(res["status"]) in str(e)
        except vwk.RiskControlError:
            assert False, f"HTTP {res['status']} bị coi là risk-control → nick nghỉ 30 phút oan"
    try:                                        # captcha thật thì vẫn là risk control
        vwk._check_submit({"status": 200, "errors": ['{"error_code":710022004}']})
        assert False
    except vwk.RiskControlError:
        pass
    try:                                        # 200 mà không có convId → đã gửi, không gửi lại
        vwk._check_submit({"status": 200, "errors": [], "events": []})
        assert False
    except vwk.SubmitDelivered:
        pass


def test_blocked_reason_says_one_thing():
    """Nick không chạy được thì phải nói ĐÚNG lý do — trước đây liệt kê cả 4 nên hướng dẫn sai."""
    import time as _t
    from browser_pool import BrowserPool
    r = BrowserPool.blocked_reason
    base = {"login_ok": 1, "scheduling": True, "cooling": False, "rate_limited": False,
            "quota_blocked": False, "credit_balance": 4, "used_today": 0, "cooldown_until": 0}
    assert "cookie chết" in r(None, {**base, "login_ok": 0})
    assert "tạm ngưng" in r(None, {**base, "scheduling": False})
    assert "nghỉ" in r(None, {**base, "cooling": True, "cooldown_until": _t.time() + 600})
    assert "hết lượt" in r(None, {**base, "rate_limited": True})
    assert "hết điểm" in r(None, {**base, "credit_balance": 0})
    # nick lành thì không được coi là bị chặn
    assert "không rõ" in r(None, base)


def test_thu_tu_tin_nhan_khong_lam_video_doi_nham_prompt():
    """Ghép prompt↔video dựa vào thứ tự tin nhắn — nếu Dola trả ngược (cũ trước) thì phải tự sắp lại.

    Không có guard này, hội thoại 2 lượt sẽ cho video A đội prompt của video B mà không báo lỗi gì.
    """
    import video_worker_ui as vw

    def tin(idx, blocks):
        return {"index": idx, "content": blocks}

    def text(t):
        return {"content": {"text_block": {"text": t}}}

    def video(u):
        return {"block_type": 2074,
                "content": {"creation_block": {"creations": [{"type": 2, "video": {"download_url": u}}]}}}

    # lượt 1: prompt "con mèo" → video v1 ; lượt 2: prompt "con chó" → video v2
    moi_truoc = [tin(4, [video("http://v2")]), tin(3, [text("生成された動画：con chó")]),
                 tin(2, [video("http://v1")]), tin(1, [text("生成された動画：con mèo")])]
    cu_truoc = list(reversed(moi_truoc))           # Dola trả ngược lại

    mong_doi = [("http://v2", "con chó"), ("http://v1", "con mèo")]
    for ten, msgs in (("mới trước", moi_truoc), ("cũ trước", cu_truoc)):
        poll = vw._parse_single({"downlink_body": {"pull_singe_chain_downlink_body": {"messages": msgs}}})
        got = [(v["video_url"], v["prompt"]) for v in vw.conversation_videos(poll)]
        assert got == mong_doi, f"{ten}: {got}"
        assert poll["videos"][0] == "http://v2", f"{ten}: phải lấy video MỚI nhất, được {poll['videos'][0]}"


def test_quet_lich_su_doc_sau_hon_poll():
    """Quét cứu video phải đọc sâu hơn 20 tin, không thì hội thoại nhiều lượt rụng mất video cũ."""
    import video_worker_ui as vw
    assert vw.SCAN_MSG_LIMIT > 20, vw.SCAN_MSG_LIMIT
    _, _, mac_dinh = vw._single_request("c", "m", "f", "1")
    _, _, khi_quet = vw._single_request("c", "m", "f", "1", vw.SCAN_MSG_LIMIT)
    lay = lambda b: b["uplink_body"]["pull_singe_chain_uplink_body"]["limit"]
    assert lay(mac_dinh) == 20 and lay(khi_quet) == vw.SCAN_MSG_LIMIT, (lay(mac_dinh), lay(khi_quet))


if __name__ == "__main__":
    setup_module(); test_no_question_hands_off(); test_pending_question_is_answered_first()
    test_late_questions_reopen_browser(); test_duration_cap_is_not_content_policy()
    test_reply_uses_dola_cap_not_30s(); test_own_directive_is_ignored()
    test_answered_memory_survives_reopen(); test_streaming_message_is_answered_once()
    test_option_list_gets_a_letter_not_yes(); test_http_poll_skips_answered_question()
    test_http_error_is_not_risk_control(); test_video_wins_over_complaints(); test_prompt_read_back_from_conversation(); test_each_video_gets_its_own_prompt(); test_duration_message_is_not_out_of_credit(); test_generating_notice_is_not_a_refusal(); test_blocked_reason_says_one_thing()
    test_prompt_marks_are_scaled_to_duration(); test_parse_credit_need_from_dola_message()
    test_credits_used_is_read_from_start_message(); test_download_failure_keeps_job_with_cdn_link()
    test_submit_timeout_never_resubmits(); test_uncertain_submit_only_resends_when_probe_is_certain(); test_guest_session_is_detected_not_credit(); test_dead_proxy_mid_render_switches_route_not_lose_video(); test_scan_account_videos_reads_history_only(); test_thu_tu_tin_nhan_khong_lam_video_doi_nham_prompt(); test_quet_lich_su_doc_sau_hon_poll(); print("OK")
