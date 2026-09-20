"""Chọn model trên giao diện Dola: trang vẽ nút/menu model CHẬM (log 20/09 12:29 + 12:35: cùng nick, lúc "không thấy ô
model", lúc "không có Seedance 2.5" dù menu thật sự có 2.5). Code phải CHỜ rồi mới kết luận.
Chạy: .venv/bin/python test_ui_model_choice.py
"""
import asyncio

import video_worker_ui as vw


class FakePage:
    """Chỉ đủ cho _wait_model_chip: đồng hồ giả tiến theo wait_for_timeout."""
    def __init__(self):
        self.now = 0.0

    async def wait_for_timeout(self, ms):
        self.now += ms / 1000


def _with_finder(finder, coro_fn):
    orig, vw._find_model_chip = vw._find_model_chip, finder
    try:
        return asyncio.run(coro_fn())
    finally:
        vw._find_model_chip = orig


def test_waits_until_chip_appears():
    page, chip, calls = FakePage(), object(), []

    async def finder(p):
        calls.append(p.now)
        return chip if p.now >= 3.0 else None      # nút hiện sau 3s

    got = _with_finder(finder, lambda: vw._wait_model_chip(page, seconds=15, clock=lambda: page.now))
    assert got is chip, "phải chờ tới khi nút hiện thay vì bỏ cuộc ở lần thử đầu"
    assert len(calls) > 1, "phải thử lại nhiều lần"


def test_gives_up_after_deadline():
    page = FakePage()

    async def never(p):
        return None

    got = _with_finder(never, lambda: vw._wait_model_chip(page, seconds=5, clock=lambda: page.now))
    assert got is None and page.now >= 5, "hết hạn thì trả None, không chờ vô hạn"


class _Loc:
    def __init__(self, visible):
        self.visible = visible
        self.first = self

    async def count(self):
        return 1 if self.visible else 0

    async def is_visible(self):
        return self.visible

    def filter(self, **_kw):
        return self


class VideoPage:
    """Composer video: chỉ các nút có thuộc tính dữ liệu data-input-engine-actionbar-control-key hiện; chip chế độ chat
    "⚡高速" (data-valid-btn) cũng hiện nhưng KHÔNG được coi là nút model."""
    def locator(self, sel):
        return _Loc(sel in (vw.VIDEO_MODEL_BTN, vw.VIDEO_DURATION_BTN))

    def get_by_text(self, *_a, **_k):
        return _Loc(False)


class ChatPage:
    """Trang chat THƯỜNG: chỉ mấy class Tailwind "duration-…" (chứa "ratio") hiện, không có chữ của thanh soạn video."""
    def locator(self, sel):
        return _Loc("[class*='ratio']" in sel)


def test_plain_chat_page_is_not_a_video_composer():
    """Log 20/09 12:56: [class*='ratio'] khớp 'duration-200' → composer bị coi là đã mở, không bao giờ bấm 動画を作成."""
    assert asyncio.run(vw._is_composer_open(ChatPage())) is False


def test_video_composer_detected_and_model_chip_found_by_data_attribute():
    page = VideoPage()
    assert asyncio.run(vw._is_composer_open(page)) is True, "nút video-duration hiện → composer đã mở"
    assert asyncio.run(vw._find_model_chip(page)) is not None, "tìm nút model qua thuộc tính video-model"


class ChipPage:
    """Composer với các nút hiện chữ cho trước; ratio_after = chữ nút tỉ lệ sau khi _set_ratio được gọi."""
    def __init__(self, ratio_text, duration_text, ratio_after=None, duration_after=None):
        self.texts = {vw.VIDEO_RATIO_BTN: ratio_text, vw.VIDEO_DURATION_BTN: duration_text}
        self.ratio_after = ratio_after
        self.duration_after = duration_after
        self.set_calls = 0
        self.dur_calls = 0

        class _Keyboard:
            async def press(self, _key):
                return None
        self.keyboard = _Keyboard()

    def locator(self, sel):
        page = self

        class _Chip:
            async def count(self):
                return 0 if page.texts.get(sel) is None else 1

            async def inner_text(self, timeout=0):
                return page.texts[sel]
        chip = _Chip()
        chip.first = chip
        return chip

    async def wait_for_timeout(self, _ms):
        return None


def _verify(page, ratio, duration):
    orig_set, orig_dur = vw._set_ratio, vw._select_duration_chip

    async def fake_set(p, _ratio):
        p.set_calls += 1
        if p.ratio_after is not None:
            p.texts[vw.VIDEO_RATIO_BTN] = p.ratio_after

    async def fake_dur(p, _duration):
        p.dur_calls += 1
        if p.duration_after is not None:
            p.texts[vw.VIDEO_DURATION_BTN] = p.duration_after
        return True
    vw._set_ratio, vw._select_duration_chip = fake_set, fake_dur
    try:
        asyncio.run(vw._verify_composer_settings(page, ratio, duration))
    finally:
        vw._set_ratio, vw._select_duration_chip = orig_set, orig_dur


def test_verify_composer_settings():
    _verify(ChipPage("比率 9:16", "30s"), "9:16", 30)                         # đúng → không lỗi
    p = ChipPage("比率", "30s", ratio_after="比率 9:16")                        # thiếu → chọn lại đúng 1 lần rồi đúng
    _verify(p, "9:16", 30)
    assert p.set_calls == 1, "tỉ lệ thiếu phải được chọn lại đúng 1 lần"
    for bad, args in ((ChipPage("比率", "30s"), ("9:16", 30)), (ChipPage("比率 16:9", "30s"), ("9:16", 30)),
                      (ChipPage("比率 9:16", "10s"), ("9:16", 30))):
        try:
            _verify(bad, *args)
        except RuntimeError:
            continue
        raise AssertionError(f"phải dừng khi nút hiện sai: {bad.texts}")
    _verify(ChipPage("比率 9:16", "10s"), "9:16", None)                       # không đòi thời lượng → bỏ qua
    assert not vw._shows("比率 16:9", "9:16") and vw._shows("比率 9:16", "9:16"), "9:16 không được khớp trong 16:9"
    assert not vw._shows("130s", "30s") and vw._shows("30s", "30s"), "30s không được khớp trong 130s"


class UnreadableChipPage(ChipPage):
    """Nút CÓ tồn tại nhưng đọc chữ luôn trượt (trang đang vẽ lại / locator detach)."""
    def locator(self, sel):
        chip = super().locator(sel)

        async def boom(timeout=0):
            raise RuntimeError("Timeout 2000ms exceeded")
        chip.inner_text = boom
        return chip


def test_chip_doc_khong_noi_thi_dung_lai_chu_khong_bo_qua_kiem():
    """20/09: _chip_text trả None cho CẢ 'không có nút' lẫn 'đọc lỗi', mà _verify_composer_settings
    hiểu None là 'giao diện khác → khỏi kiểm' → lá chắn tự tắt đúng lúc trang chập chờn."""
    page = UnreadableChipPage("比率 9:16", "30s")
    try:
        _verify(page, "9:16", 30)
    except RuntimeError:
        return
    raise AssertionError("nút có mà đọc không nổi thì phải dừng job, không được im lặng bỏ qua kiểm")


def test_khong_co_nut_thi_van_bo_qua_kiem():
    """Giao diện Dola khác (không có nút tỉ lệ): None là hợp lệ → bỏ qua, không dựng rào giả."""
    _verify(ChipPage(None, None), "9:16", 30)


def test_thoi_luong_sai_duoc_chon_lai_mot_lan_nhu_ti_le():
    """20/09: tỉ lệ sai thì thử lại rồi mới báo lỗi, còn thời lượng sai thì raise thẳng — job chết và tốn
    một vòng mở Chrome dù _select_duration_chip đã có sẵn và tự xác nhận."""
    p = ChipPage("比率 9:16", "10s", duration_after="30s")
    _verify(p, "9:16", 30)
    assert p.dur_calls == 1, f"phải chọn lại thời lượng đúng 1 lần, thực tế {p.dur_calls}"


def test_thoi_luong_chon_lai_van_sai_thi_dung_job():
    p = ChipPage("比率 9:16", "10s")   # chọn lại không ăn
    try:
        _verify(p, "9:16", 30)
    except RuntimeError:
        assert p.dur_calls == 1, "chỉ thử lại đúng một lần rồi dừng"
        return
    raise AssertionError("chọn lại vẫn sai thì phải dừng trước khi gửi")


if __name__ == "__main__":
    test_waits_until_chip_appears()
    test_gives_up_after_deadline()
    test_plain_chat_page_is_not_a_video_composer()
    test_video_composer_detected_and_model_chip_found_by_data_attribute()
    test_verify_composer_settings()
    test_chip_doc_khong_noi_thi_dung_lai_chu_khong_bo_qua_kiem()
    test_khong_co_nut_thi_van_bo_qua_kiem()
    test_thoi_luong_sai_duoc_chon_lai_mot_lan_nhu_ti_le()
    test_thoi_luong_chon_lai_van_sai_thi_dung_job()
    print("OK: ui model choice")
