"""Nối các clip ≤15s (do split_prompt cắt ra) thành một video dài — đường HỢP LỆ để có 30 giây:
mỗi phần trả đúng giá của nó, không khai giá với máy chủ Dola.

Dola trả clip không phải lúc nào cũng cùng kích thước (đo 20/09: 720x1280 và 704x1248 trong cùng một buổi),
nên phải mã hoá lại khi lệch, chứ nối kiểu chép thẳng sẽ ra file hỏng.

Chạy: .venv/bin/python test_concat_clips.py
"""
import subprocess
import tempfile
from pathlib import Path

from concat_clips import concat_clips
from watermark import _ffmpeg_exe, _mp4_duration


def _tao_clip(ra: Path, giay: float, w: int, h: int) -> Path:
    exe = _ffmpeg_exe()
    subprocess.run([exe, "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=24:duration={giay}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(ra)],
                   check=True, capture_output=True, timeout=120)
    return ra


def test_noi_hai_clip_cung_kich_thuoc():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        a = _tao_clip(t / "a.mp4", 1.0, 64, 128)
        b = _tao_clip(t / "b.mp4", 1.0, 64, 128)
        ket = concat_clips([a, b], t / "ra.mp4")
        assert Path(ket["file"]).is_file(), ket
        assert ket["cach"] == "copy", f"cùng kích thước thì chép thẳng cho nhanh + không giảm chất: {ket}"
        assert 1.8 <= ket["giay"] <= 2.3, f"phải ~2s, thực tế {ket['giay']}"


def test_noi_clip_lech_kich_thuoc_thi_ma_hoa_lai():
    """20/09 đo thật: một job ra 720x1280, job khác ra 704x1248. Chép thẳng sẽ hỏng."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        a = _tao_clip(t / "a.mp4", 1.0, 64, 128)
        b = _tao_clip(t / "b.mp4", 1.0, 62, 124)
        ket = concat_clips([a, b], t / "ra.mp4")
        assert ket["cach"] == "ma_hoa_lai", f"lệch kích thước phải mã hoá lại: {ket}"
        assert 1.8 <= ket["giay"] <= 2.3, f"phải ~2s, thực tế {ket['giay']}"
        assert _mp4_duration(ket["file"]), "file ra phải đọc được thời lượng"


def test_mot_clip_thi_tra_nguyen():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        a = _tao_clip(t / "a.mp4", 1.0, 64, 128)
        ket = concat_clips([a], t / "ra.mp4")
        assert Path(ket["file"]).is_file() and 0.8 <= ket["giay"] <= 1.3, ket


def test_thieu_file_thi_bao_ro():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        a = _tao_clip(t / "a.mp4", 1.0, 64, 128)
        try:
            concat_clips([a, t / "khong-co.mp4"], t / "ra.mp4")
        except FileNotFoundError as e:
            assert "khong-co.mp4" in str(e), e
            return
        raise AssertionError("thiếu clip thì phải báo rõ, không nối ra file cụt")


if __name__ == "__main__":
    test_noi_hai_clip_cung_kich_thuoc()
    test_noi_clip_lech_kich_thuoc_thi_ma_hoa_lai()
    test_mot_clip_thi_tra_nguyen()
    test_thieu_file_thi_bao_ro()
    print("OK: concat_clips")
