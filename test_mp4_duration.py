"""probe_duration đọc thời lượng THẬT từ atom mvhd của MP4 — không phụ thuộc cv2.

Dola có lúc trừ đủ credit 30s nhưng trả clip ngắn hơn. Cảnh báo "video ngắn" (server._short_video_note) chỉ
chạy khi probe_duration đo được; trước đây nó chỉ dùng cv2 nên cv2 hỏng (hay gặp trên Windows) là cảnh báo
biến mất im lặng và người dùng chỉ biết khi mở file. mvhd là stdlib, đọc vài trăm byte, không nạp mdat.
"""
import struct
import sys

import pytest

import watermark


def _atom(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def _mvhd_v0(timescale: int, duration: int) -> bytes:
    body = struct.pack(">B3xIIII", 0, 0, 0, timescale, duration) + b"\x00" * 80
    return _atom(b"mvhd", body)


def _mvhd_v1(timescale: int, duration: int) -> bytes:
    body = struct.pack(">B3xQQIQ", 1, 0, 0, timescale, duration) + b"\x00" * 80
    return _atom(b"mvhd", body)


def _write(tmp_path, *atoms: bytes):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"".join(atoms))
    return path


FTYP = _atom(b"ftyp", b"isom" + b"\x00" * 8)


def test_moov_truoc_mdat_doc_dung_thoi_luong(tmp_path):
    path = _write(tmp_path, FTYP, _atom(b"moov", _mvhd_v0(1000, 12042)), _atom(b"mdat", b"\x00" * 64))
    assert watermark.probe_duration(path) == pytest.approx(12.042)


def test_moov_sau_mdat_lon_van_doc_duoc(tmp_path):
    """Video không faststart: moov nằm CUỐI file sau mdat vài MB — phải nhảy qua mdat, không đọc nó."""
    path = _write(tmp_path, FTYP, _atom(b"mdat", b"\x00" * 3_000_000), _atom(b"moov", _mvhd_v0(600, 18000)))
    assert watermark.probe_duration(path) == pytest.approx(30.0)


def test_mvhd_phien_ban_1_64bit(tmp_path):
    path = _write(tmp_path, FTYP, _atom(b"moov", _mvhd_v1(90000, 90000 * 15)))
    assert watermark.probe_duration(path) == pytest.approx(15.0)


def test_mdat_dung_kich_thuoc_64bit_largesize(tmp_path):
    """size == 1 nghĩa là kích thước thật nằm ở 8 byte kế tiếp — không hiểu thì nhảy lệch và mất moov."""
    payload = b"\x00" * 1000
    big = struct.pack(">I4sQ", 1, b"mdat", 16 + len(payload)) + payload
    path = _write(tmp_path, FTYP, big, _atom(b"moov", _mvhd_v0(1000, 8000)))
    assert watermark.probe_duration(path) == pytest.approx(8.0)


def test_cv2_hong_van_do_duoc(tmp_path, monkeypatch):
    """Đúng lỗi thực tế: import cv2 nổ (thiếu VC++ runtime) mà thời lượng vẫn phải đo được."""
    monkeypatch.setitem(sys.modules, "cv2", None)   # import cv2 → ImportError
    path = _write(tmp_path, FTYP, _atom(b"moov", _mvhd_v0(1000, 12042)))
    assert watermark.probe_duration(path) == pytest.approx(12.042)


@pytest.mark.parametrize("content", [b"", b"khong phai mp4", FTYP, FTYP + _atom(b"mdat", b"\x00" * 200)])
def test_file_hong_hoac_khong_co_moov_thi_none(tmp_path, monkeypatch, content):
    monkeypatch.setitem(sys.modules, "cv2", None)   # loại cv2 để chỉ kiểm nhánh mvhd
    assert watermark.probe_duration(_write(tmp_path, content)) is None


def test_file_khong_ton_tai_thi_none(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)
    assert watermark.probe_duration(tmp_path / "nope.mp4") is None


def test_timescale_bang_0_khong_chia_cho_0(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)
    path = _write(tmp_path, FTYP, _atom(b"moov", _mvhd_v0(0, 5000)))
    assert watermark.probe_duration(path) is None
