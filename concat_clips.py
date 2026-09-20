#!/usr/bin/env python3
"""concat_clips.py — nối các clip ≤15s thành MỘT video dài (cặp với split_prompt.py).

Vì sao: giao diện Dola chỉ nhận 4–15s mỗi lần. Muốn 30s thì tách prompt (split_prompt.py), dựng từng phần
rồi nối lại ở máy — mỗi phần trả đúng giá của nó.

Dola KHÔNG trả clip cùng kích thước mọi lúc (đo 20/09: 720x1280 và 704x1248 trong cùng một buổi), nên:
  - cùng kích thước → nối kiểu chép thẳng (-c copy): nhanh, không giảm chất lượng
  - lệch kích thước → mã hoá lại, co/đệm về khung của clip ĐẦU TIÊN (giữ tỉ lệ, viền đen)

Dùng:
    .venv/bin/python concat_clips.py ra.mp4 phan1.mp4 phan2.mp4
    .venv/bin/python concat_clips.py ra.mp4 downloads/*.mp4
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from watermark import _dims_ffmpeg, _ffmpeg_exe, _mp4_duration

TIMEOUT_SEC = 900


def _kich_thuoc(f: Path) -> tuple[int, int]:
    d = _dims_ffmpeg(f)
    if not d:
        raise RuntimeError(f"Không đọc được kích thước video: {f}")
    return d[0], d[1]


def _chay(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_SEC)
    if r.returncode != 0:
        loi = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("ffmpeg lỗi: " + (loi[-1] if loi else f"mã {r.returncode}"))


def concat_clips(files, ra) -> dict:
    """Nối `files` (theo ĐÚNG thứ tự truyền vào) thành `ra`. Trả {'file', 'giay', 'cach', 'so_clip'}."""
    files = [Path(f) for f in files]
    ra = Path(ra)
    if not files:
        raise ValueError("chưa truyền clip nào")
    for f in files:
        if not f.is_file():
            raise FileNotFoundError(f"không thấy clip: {f}")
    exe = _ffmpeg_exe()
    if not exe:
        raise RuntimeError("không tìm thấy ffmpeg (cài ffmpeg hoặc gói imageio-ffmpeg)")

    ra.parent.mkdir(parents=True, exist_ok=True)
    kich_thuoc = [_kich_thuoc(f) for f in files]
    cung_khung = len(set(kich_thuoc)) == 1

    if cung_khung:
        # concat demuxer: đọc danh sách file, chép thẳng luồng. Đường dẫn phải escape dấu nháy đơn.
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            for f in files:
                fh.write("file '%s'\n" % str(f.resolve()).replace("'", r"'\''"))
            danh_sach = fh.name
        try:
            _chay([exe, "-y", "-f", "concat", "-safe", "0", "-i", danh_sach, "-c", "copy", str(ra)])
        finally:
            Path(danh_sach).unlink(missing_ok=True)
        cach = "copy"
    else:
        # Lệch khung → co vừa khung clip đầu rồi đệm viền đen, KHÔNG kéo méo hình.
        w, h = kich_thuoc[0]
        vao = []
        for f in files:
            vao += ["-i", str(f)]
        loc = "".join(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}];"
            for i in range(len(files))
        ) + "".join(f"[v{i}]" for i in range(len(files))) + f"concat=n={len(files)}:v=1:a=0[out]"
        _chay([exe, "-y", *vao, "-filter_complex", loc, "-map", "[out]",
               "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", str(ra)])
        cach = "ma_hoa_lai"

    giay = _mp4_duration(ra)
    if not giay:
        raise RuntimeError(f"nối xong nhưng không đọc được thời lượng: {ra}")
    return {"file": str(ra), "giay": round(giay, 2), "cach": cach, "so_clip": len(files)}


def main() -> int:
    if len(sys.argv) < 3:
        sys.exit("usage: python concat_clips.py <ra.mp4> <clip1.mp4> [clip2.mp4 ...]")
    ket = concat_clips(sys.argv[2:], sys.argv[1])
    print(f"✓ {ket['file']} — {ket['giay']}s từ {ket['so_clip']} clip ({ket['cach']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
