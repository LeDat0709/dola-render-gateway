"""Remove the "Dola AI" watermark (bottom-right) from Seedance 2.0 / Dreamina downloads.

Those models burn a small semi-transparent "Dola AI" logo into the bottom-right corner
(Seedance 2.5 does not). We erase just that logo box with FFmpeg's `delogo` filter: it
interpolates only inside the box, so the rest of the frame stays sharp. FFmpeg comes
from imageio-ffmpeg when installed, else the system `ffmpeg`; if neither exists we fall
back to an OpenCV inpaint of the same box (slower, softer).

Box is expressed as fractions of width/height, calibrated on a 1280x720 render
(logo ≈ x 0.86–0.995, y 0.905–0.99). Override via DOLA_WM_X/Y/W/H if Dola moves it.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_FFMPEG_TIMEOUT_SEC = 300
_TMP_SUFFIX = ".wm.mp4"


def _fracs() -> tuple[float, float, float, float]:
    def f(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, default))
        except (TypeError, ValueError):
            return float(default)
    return f("DOLA_WM_X", 0.86), f("DOLA_WM_Y", 0.905), f("DOLA_WM_W", 0.135), f("DOLA_WM_H", 0.086)


def _ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _dims(path: Path) -> tuple[int, int, float] | None:
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return None
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        cap.release()
        return (w, h, fps) if w > 0 and h > 0 else None
    except Exception:
        return None


def _box(w: int, h: int) -> tuple[int, int, int, int]:
    """Logo box (x, y, bw, bh) in pixels, clamped inside the frame (delogo rejects edges)."""
    fx, fy, fw, fh = _fracs()
    x, y = int(w * fx), int(h * fy)
    bw, bh = int(w * fw), int(h * fh)
    x = max(1, min(x, w - 3))
    y = max(1, min(y, h - 3))
    bw = max(1, min(bw, w - x - 1))
    bh = max(1, min(bh, h - y - 1))
    return x, y, bw, bh


def remove_watermark(path: str | Path) -> bool:
    """Overwrite `path` with a copy whose "Dola AI" logo is erased.

    Returns True on success; leaves the file untouched on any failure.
    """
    p = Path(path)
    if not p.exists():
        return False
    dims = _dims(p)
    if not dims:
        return False
    w, h, fps = dims
    x, y, bw, bh = _box(w, h)
    exe = _ffmpeg_exe()
    if exe:
        tmp = p.with_name(p.stem + _TMP_SUFFIX)
        cmd = [
            exe, "-y", "-hide_banner", "-loglevel", "error", "-i", str(p),
            "-vf", f"delogo=x={x}:y={y}:w={bw}:h={bh}",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-c:a", "copy", "-movflags", "+faststart", str(tmp),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=_FFMPEG_TIMEOUT_SEC, creationflags=_NO_WINDOW)
            if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                os.replace(str(tmp), str(p))
                return True
            print(f"  (delogo failed rc={r.returncode}: {r.stderr.decode(errors='replace')[:120]})", flush=True)
        except Exception as exc:
            print(f"  (delogo error: {str(exc)[:120]})", flush=True)
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return _inpaint_fallback(p, x, y, bw, bh, fps)


def _inpaint_fallback(p: Path, x: int, y: int, bw: int, bh: int, fps: float) -> bool:
    """OpenCV inpaint of the logo box, frame by frame (drops audio; last resort)."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return False
    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        return False
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    mask = np.zeros((h, w), np.uint8)
    mask[y:y + bh, x:x + bw] = 255
    tmp = p.with_name(p.stem + _TMP_SUFFIX)
    out = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            out.write(cv2.inpaint(frame, mask, 4, cv2.INPAINT_TELEA))
    finally:
        cap.release()
        out.release()
    if tmp.exists() and tmp.stat().st_size > 0:
        os.replace(str(tmp), str(p))
        return True
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    return False



# ========== Tự dò watermark TRÔI (Seedance 2.5) rồi delogo theo mốc thời gian ==========
# Watermark "Dola AI" luôn là CÙNG chữ, cùng cỡ, chỉ đổi vị trí theo thời gian. Dò bằng
# template-match TRÊN ẢNH CẠNH (Canny): cạnh chữ ổn định bất kể nền -> tách sạch nền sáng.
_TMPL = Path(__file__).resolve().parent / "assets" / "dola_watermark.png"
_AUTO = dict(sample_fps=4.0, thresh=0.38, merge_gap=1.2, loc_tol=90, pad=6)


def _load_template():
    import cv2
    t = cv2.imread(str(_TMPL), cv2.IMREAD_GRAYSCALE)
    if t is None:
        return None, None, 0, 0
    te = cv2.Canny(t, 50, 150)
    return t, te, t.shape[1], t.shape[0]   # gray, edges, w, h


def detect_watermark_segments(path):
    """Trả (segments, W, H, dur). segments = list (t0, t1, (x,y,w,h)) — mốc có watermark."""
    import cv2, numpy as np
    tg, te, tw, th = _load_template()
    if te is None:
        return [], 0, 0, 0.0
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return [], 0, 0, 0.0
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    nfr = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    step = max(1, int(round(fps / _AUTO["sample_fps"])))
    hits, i = [], 0            # hits = (t, x, y)
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % step == 0:
            ge = cv2.Canny(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), 50, 150)
            if ge.shape[0] >= th and ge.shape[1] >= tw:
                res = cv2.matchTemplate(ge, te, cv2.TM_CCOEFF_NORMED)
                _, mx, _, loc = cv2.minMaxLoc(res)
                if mx >= _AUTO["thresh"]:
                    hits.append((i / fps, loc[0], loc[1]))
        i += 1
    cap.release()
    dur = (nfr / fps) if nfr else (hits[-1][0] if hits else 0.0)
    # gom hit liền kề (gần thời gian + gần vị trí) thành mốc; hộp = bao chung + đệm
    segs = []
    for t, x, y in hits:
        if segs and t - segs[-1]["t1"] <= _AUTO["merge_gap"] \
           and abs(x - segs[-1]["cx"]) <= _AUTO["loc_tol"] and abs(y - segs[-1]["cy"]) <= _AUTO["loc_tol"]:
            g = segs[-1]
            g["t1"] = t; g["cx"], g["cy"] = x, y
            g["x0"] = min(g["x0"], x); g["y0"] = min(g["y0"], y)
            g["x1"] = max(g["x1"], x + tw); g["y1"] = max(g["y1"], y + th)
        else:
            segs.append(dict(t0=t, t1=t, cx=x, cy=y, x0=x, y0=y, x1=x + tw, y1=y + th))
    out = []
    pad = _AUTO["pad"]
    for g in segs:
        x = max(1, g["x0"] - pad); y = max(1, g["y0"] - pad)
        w = min(W - x - 1, g["x1"] - g["x0"] + 2 * pad); h = min(H - y - 1, g["y1"] - g["y0"] + 2 * pad)
        if w > 0 and h > 0:
            out.append((g["t0"], g["t1"], (x, y, w, h)))
    return out, W, H, dur


def auto_remove_watermark(path, out=None, replace=False):
    """Tự dò watermark trôi rồi xoá. Ghi ra <tên>_nologo.mp4 (giữ bản gốc).

    Trả dict: {ok, output, segments, error}. Không thấy watermark trôi -> thử hộp cố định.
    """
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "Không thấy file.", "output": None, "segments": 0}
    final = None
    if replace:
        final = str(p); out = str(p.with_name(p.stem + ".wmtmp" + p.suffix))
    elif out is None:
        out = str(p.with_name(p.stem + "_nologo" + p.suffix))
    segs, W, H, dur = detect_watermark_segments(p)
    exe = _ffmpeg_exe()
    if not exe:
        return {"ok": False, "error": "Thiếu ffmpeg.", "output": None, "segments": 0}
    if not segs:
        # không thấy watermark trôi -> thử hộp cố định góc phải-dưới (Seedance 2.0)
        import shutil
        shutil.copyfile(str(p), out)
        ok = remove_watermark(out)
        return {"ok": ok, "output": out if ok else None, "segments": 1 if ok else 0,
                "error": None if ok else "Không dò thấy watermark."}
    filt = ",".join(
        f"delogo=x={x}:y={y}:w={w}:h={h}:enable='between(t,"
        f"{(0.0 if k == 0 else max(0.0, t0 - 0.4)):.2f},"
        f"{(dur + 1 if k == len(segs) - 1 else t1 + 0.6):.2f})'"
        for k, (t0, t1, (x, y, w, h)) in enumerate(segs))
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", str(p),
           "-vf", filt, "-c:v", "libx264", "-crf", "18", "-preset", "medium",
           "-c:a", "copy", "-movflags", "+faststart", out]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=_FFMPEG_TIMEOUT_SEC, creationflags=_NO_WINDOW)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "output": None, "segments": len(segs)}
    if r.returncode != 0:
        return {"ok": False, "error": r.stderr.decode(errors="replace")[-300:],
                "output": None, "segments": len(segs)}
    if final:
        os.replace(out, final); out = final
    return {"ok": True, "output": out, "segments": len(segs),
            "boxes": [(round(a, 1), round(b, 1), c) for a, b, c in segs], "error": None}


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit("usage: python watermark.py [--auto] <video.mp4>")
    if args[0] == "--auto":
        res = auto_remove_watermark(args[1])
        if res.get("ok"):
            print(f"done ({res['segments']} moc) -> {res['output']}")
            for a, b, box in res.get("boxes", []):
                print(f"    {a}s-{b}s  {box}")
        else:
            sys.exit("failed: " + str(res.get("error")))
    else:
        print("done" if remove_watermark(args[0]) else "failed")
