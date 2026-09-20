"""Nạp extension Khan vào profile TẠO VIDEO (use_extension=True) — kiểm phần thuần + nạp qua CDP giả.

Chrome 137+ (bản chính hãng, kênh 'chrome') bỏ lặng lẽ --load-extension nên phải nạp bằng CDP
Extensions.loadUnpacked (cần cờ --enable-unsafe-extension-debugging). Đổi sang Chromium kèm patchright thì
KHÔNG được: nó là bản 151 còn profile nick đã dùng bởi Chrome 153 (hạ phiên bản → Chrome reset profile)."""
import asyncio

import browser
import config
import video_worker_ui


class _FakeCdp:
    def __init__(self, fail_on: str | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.fail_on = fail_on

    async def send(self, method, params):
        self.calls.append((method, params))
        if self.fail_on and params.get("path") == self.fail_on:
            raise RuntimeError("boom")
        return {"id": "x"}


class _FakeBrowser:
    def __init__(self, cdp):
        self._cdp = cdp

    async def new_browser_cdp_session(self):
        return self._cdp


class _FakeContext:
    def __init__(self, browser_obj):
        self.browser = browser_obj


def test_no_extension_when_profile_is_not_for_video(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EXTRA_EXTENSION_DIR", str(tmp_path), raising=False)
    assert browser._extension_dirs(False, want_extra=True) == []


def test_extra_extension_loaded_for_video_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EXTRA_EXTENSION_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(config, "SKILLPACK_HIJACK", True)
    assert browser._extension_dirs(True, want_extra=True) == [str(tmp_path.resolve())]


def test_khan_not_loaded_unless_caller_asks(monkeypatch, tmp_path):
    """Khan ghi đè "duration":N của MỌI lệnh gửi từ trang (regex không có điều kiện) → nạp vào job 10s/15s/2.0
    là ra video sai độ dài + sai giá. Chỉ nạp khi job thật sự muốn 30s."""
    monkeypatch.setattr(config, "EXTRA_EXTENSION_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(config, "SKILLPACK_HIJACK", True)
    assert browser._extension_dirs(True, want_extra=False) == []
    assert browser._extension_dirs(True) == []          # mặc định KHÔNG nạp (đăng nhập, job thường)


def test_missing_extension_dir_is_skipped_not_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EXTRA_EXTENSION_DIR", str(tmp_path / "khong-co"), raising=False)
    monkeypatch.setattr(config, "SKILLPACK_HIJACK", True)
    assert browser._extension_dirs(True, want_extra=True) == []


def test_unset_extra_extension_loads_nothing(monkeypatch):
    monkeypatch.setattr(config, "EXTRA_EXTENSION_DIR", "", raising=False)
    monkeypatch.setattr(config, "SKILLPACK_HIJACK", True)
    assert browser._extension_dirs(True, want_extra=True) == []


def test_load_unpacked_sends_each_dir_over_cdp():
    cdp = _FakeCdp()
    asyncio.run(browser._load_unpacked_extensions(_FakeContext(_FakeBrowser(cdp)), ["/a", "/b"]))
    assert cdp.calls == [("Extensions.loadUnpacked", {"path": "/a"}),
                         ("Extensions.loadUnpacked", {"path": "/b"})]


def test_load_unpacked_error_does_not_block_next_or_raise():
    cdp = _FakeCdp(fail_on="/a")
    asyncio.run(browser._load_unpacked_extensions(_FakeContext(_FakeBrowser(cdp)), ["/a", "/b"]))
    assert [c[1]["path"] for c in cdp.calls] == ["/a", "/b"]


def test_load_unpacked_without_browser_handle_does_not_raise():
    asyncio.run(browser._load_unpacked_extensions(_FakeContext(None), ["/a"]))


def test_extension_loaded_flag_follows_real_load():
    ok, none_ok, no_browser = _FakeContext(_FakeBrowser(_FakeCdp())), _FakeContext(_FakeBrowser(_FakeCdp(fail_on="/a"))), _FakeContext(None)
    for ctx, dirs in ((ok, ["/a"]), (none_ok, ["/a"]), (no_browser, ["/a"])):
        asyncio.run(browser._load_unpacked_extensions(ctx, dirs))
    assert browser.extension_loaded(ok) is True
    assert browser.extension_loaded(none_ok) is False       # nạp lỗi hết → KHÔNG coi là đã có extension
    assert browser.extension_loaded(no_browser) is False
    assert browser.extension_loaded(_FakeContext(None)) is False   # chưa nạp gì


def test_skip_30s_chip_only_when_khan_really_loaded(monkeypatch):
    monkeypatch.setattr(config, "EXTRA_EXTENSION_DIR", "/khan", raising=False)
    loaded, bare = _FakeContext(_FakeBrowser(_FakeCdp())), _FakeContext(None)
    asyncio.run(browser._load_unpacked_extensions(loaded, ["/khan"]))
    msg_30s_25 = video_worker_ui._khan_settings_msg("9:16", 30, "seedance_v2.5")
    assert video_worker_ui._khan_replaces_duration_chip(loaded, msg_30s_25) is True
    assert video_worker_ui._khan_replaces_duration_chip(bare, msg_30s_25) is False   # nạp lỗi → chọn chip như cũ
    for ratio, dur, model in [("9:16", 15, "seedance_v2.5"), ("9:16", 30, "seedance_v2.0")]:
        msg = video_worker_ui._khan_settings_msg(ratio, dur, model)
        assert video_worker_ui._khan_replaces_duration_chip(loaded, msg) is False


def test_extension_only_adds_a_flag_and_never_touches_headless():
    """Đo 20/09 trên Chrome 153: extension MV3 nạp và chạy BÌNH THƯỜNG ở chế độ ẩn (service worker lên,
    vân tay không khác bản hiện cửa sổ). Nên hàm này chỉ thêm cờ, KHÔNG đụng tới chế độ ẩn — công tắc
    "chạy ẩn" của người dùng phải có hiệu lực cả với job 30s."""
    args = browser._extension_launch(["/a"], ["--x"])
    assert "--enable-unsafe-extension-debugging" in args and "--x" in args
    args2 = browser._extension_launch([], ["--x"])
    assert args2 == ["--x"]                                   # không có extension → không thêm cờ nào
    assert not any(a.startswith("--load-extension") for a in args + args2)   # cờ này Chrome 153 bỏ qua


def test_khan_message_only_for_30s_seedance25(monkeypatch):
    monkeypatch.setattr(config, "EXTRA_EXTENSION_DIR", "/khan", raising=False)
    assert video_worker_ui._khan_settings_msg("9:16", 30, "seedance_v2.5") == {
        "type": "PURZA_UPDATE_SETTINGS", "ratio": "9:16", "duration": 30}
    assert video_worker_ui._khan_settings_msg(None, 30, "seedance_v2.5")["ratio"] == "9:16"   # mặc định của Khan
    assert video_worker_ui._khan_settings_msg("9:16", 15, "seedance_v2.5") is None    # 15s: đừng ép thành 30s
    assert video_worker_ui._khan_settings_msg("9:16", 30, "seedance_v2.0") is None    # 2.0 giữ nguyên
    monkeypatch.setattr(config, "EXTRA_EXTENSION_DIR", "", raising=False)
    assert video_worker_ui._khan_settings_msg("9:16", 30, "seedance_v2.5") is None    # chưa bật extension
