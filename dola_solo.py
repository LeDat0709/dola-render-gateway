#!/usr/bin/env python3
"""dola_solo.py — chạy MỘT tài khoản Dola của chính bạn, tuần tự, tôn trọng nhịp Dola.

Khác với gateway (nhiều nick + proxy + sổ credit), file này cố tình nhỏ: một profile Chrome
đã đăng nhập sẵn, một hàng đợi prompt, chạy lần lượt, nghỉ giữa các lần gửi.

Engine đã có sẵn ở standalone_video_generator.DolaVideoGenerator: mở Chrome persistent →
chọn model/tỉ lệ/thời lượng → gõ prompt → tự trả lời "はい" khi Dola hỏi xác nhận → poll →
tải mp4 bản không watermark. File này chỉ thêm 3 thứ engine còn thiếu: tag thông số cuối
prompt, hàng đợi + giãn nhịp, và hậu kỳ ffmpeg tuỳ chọn.

Dùng:
    .venv/bin/python dola_solo.py accounts/<nick> prompts.txt
    .venv/bin/python dola_solo.py accounts/<nick> prompts.txt --dur 30 --ratio 9:16 --gap 120 --ffmpeg

prompts.txt: cả file là MỘT prompt. Muốn xếp nhiều prompt thì ngăn nhau bằng một dòng
chỉ chứa `---`. (Cố tình KHÔNG tách theo dòng trống: kịch bản nhiều cảnh dùng dòng trống
để ngăn các mốc 镜头1/镜头2, tách kiểu đó sẽ xé một kịch bản thành hàng chục mảnh vụn.)
"""
from __future__ import annotations

import argparse
import asyncio
import random
import re
import subprocess
import sys
import time
from pathlib import Path

from standalone_video_generator import DolaVideoGenerator

# Nhịp mặc định giữa 2 lần gửi. Dola bung 710022002 ("リクエストが集中しています") khi gửi dày;
# một tài khoản đơn lẻ thì 90s là thoải mái, không cần bám sát ngưỡng.
GAP_MAC_DINH = 90.0
JITTER = 0.25                  # ±25% để nhịp không đều tăm tắp như máy
NGHI_KHI_RATE_LIMIT = 300.0    # Dola kêu gửi dày → nghỉ hẳn 5 phút rồi đi tiếp
DAU_HIEU_RATE_LIMIT = ("710022002", "リクエストが集中", "操作频繁", "rate limit")


def doc_prompts(path: Path) -> list[str]:
    """Tách file thành các khối prompt, ngăn bởi một dòng chỉ chứa '---'.

    KHÔNG tách theo dòng trống: kịch bản nhiều cảnh dùng dòng trống để ngăn các mốc
    (镜头1, 镜头2, …), tách kiểu đó sẽ xé một kịch bản thành hàng chục prompt vụn và
    đốt sạch lượt. Không có dòng '---' nào → cả file là MỘT prompt.
    """
    raw = path.read_text(encoding="utf-8")
    khoi = [k.strip() for k in re.split(r"(?m)^\s*-{3,}\s*$", raw)]
    return [k for k in khoi if k]


def gan_tag(prompt: str, model: str, ratio: str, duration: int) -> str:
    """Nối tag thông số vào cuối prompt cho chat agent của Dola đọc.

    Ở đường giao diện, Dola KHÔNG có tham số ratio/duration riêng — thông số đi vào bằng
    chính câu chữ này. Tag đặt ở cuối, cách một dòng trống, để không lẫn vào nội dung.
    """
    ten = "Seedance 2.5" if "2.5" in model else "Seedance 2.0"
    return f"{prompt}\n\n[Model {ten}, Tỷ lệ {ratio}, Độ dài video {duration}s]"


def la_rate_limit(loi: str) -> bool:
    return any(d.lower() in loi.lower() for d in DAU_HIEU_RATE_LIMIT)


def nhip_nghi(gap: float) -> float:
    """Giãn nhịp có nhiễu — tránh gửi đều tăm tắp."""
    return max(1.0, gap * (1.0 + random.uniform(-JITTER, JITTER)))


def hau_ky(mp4: Path, upscale: bool) -> dict[str, str]:
    """Cắt thumbnail, upscale tuỳ chọn. Lỗi ffmpeg KHÔNG làm hỏng job — video gốc vẫn còn."""
    ket: dict[str, str] = {}
    thumb = mp4.with_suffix(".jpg")
    try:
        subprocess.run(["ffmpeg", "-y", "-ss", "0.5", "-i", str(mp4), "-vframes", "1", str(thumb)],
                       check=True, capture_output=True, timeout=60)
        ket["thumbnail"] = str(thumb)
    except (subprocess.SubprocessError, OSError) as e:
        print(f"    (thumbnail bỏ qua: {type(e).__name__})")

    if not upscale:
        return ket
    scale_vf = "scale='if(gt(iw,ih),1920,-2)':'if(gt(iw,ih),-2,1920)':flags=lanczos"
    ra = mp4.with_name(f"{mp4.stem}_1920{mp4.suffix}")   # cạnh dài 1920, để cạnh video gốc
    try:
        subprocess.run(["ffmpeg", "-y", "-i", str(mp4), "-vf", scale_vf,
                        "-c:v", "libx264", "-crf", "18", "-preset", "medium", str(ra)],
                       check=True, capture_output=True, timeout=900)
        ket["upscale"] = str(ra)
    except (subprocess.SubprocessError, OSError) as e:
        print(f"    (upscale bỏ qua: {type(e).__name__})")
    return ket


async def chay_mot(gen: DolaVideoGenerator, prompt: str, model: str,
                   ratio: str, duration: int, timeout: int) -> dict:
    """Một lượt tạo video. Trả về dict kết quả, hoặc {'error': ...} nếu hỏng."""
    try:
        return await gen.generate_video(prompt=prompt, model=model, duration=duration,
                                        ratio=ratio, timeout=timeout)
    except Exception as e:                       # noqa: BLE001 — engine ném đủ loại lỗi Dola
        return {"error": f"{type(e).__name__}: {e}"}


async def chay_hang_doi(args: argparse.Namespace) -> int:
    prompts = doc_prompts(Path(args.prompts))
    if not prompts:
        print(f"Không có prompt nào trong {args.prompts}")
        return 1

    profile = Path(args.profile)
    if not profile.is_dir():
        print(f"Không thấy thư mục profile: {profile}")
        print("Mở profile và đăng nhập trước:  .venv/bin/python login_profile.py <nick>")
        return 1

    gen = DolaVideoGenerator(profile_dir=str(profile), headless=args.headless,
                             proxy=args.proxy or None, download_dir=args.out)
    print(f"profile : {profile}")
    print(f"prompt  : {len(prompts)} khối · model {args.model} · {args.dur}s · {args.ratio}")
    print(f"nhịp    : {args.gap:.0f}s giữa 2 lần gửi (±{int(JITTER * 100)}%)\n")

    xong, hong = 0, 0
    for i, p in enumerate(prompts, 1):
        print(f"[{i}/{len(prompts)}] {p.replace(chr(10), ' ')[:60]}…")
        timeout = 2400 if (args.dur == 30 and args.timeout <= 900) else args.timeout
        t0 = time.monotonic()
        ket = await chay_mot(gen, gan_tag(p, args.model, args.ratio, args.dur),
                             args.model, args.ratio, args.dur, timeout)
        giay = time.monotonic() - t0

        if "error" in ket:
            hong += 1
            print(f"    ✗ {ket['error']}  ({giay:.0f}s)")
            if la_rate_limit(ket["error"]):
                print(f"    Dola kêu gửi dày → nghỉ {NGHI_KHI_RATE_LIMIT:.0f}s")
                await asyncio.sleep(NGHI_KHI_RATE_LIMIT)
        else:
            xong += 1
            mp4 = Path(ket.get("local_path", ""))
            print(f"    ✓ {mp4.name}  ({giay:.0f}s)")
            if args.ffmpeg and mp4.is_file():
                for k, v in hau_ky(mp4, args.upscale).items():
                    print(f"    + {k}: {Path(v).name}")

        if i < len(prompts):
            nghi = nhip_nghi(args.gap)
            print(f"    nghỉ {nghi:.0f}s…\n")
            await asyncio.sleep(nghi)

    print(f"\nxong {xong} · hỏng {hong} · video ở {Path(args.out).resolve()}")
    return 0 if hong == 0 else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Chạy một tài khoản Dola, tuần tự, tôn trọng nhịp.")
    ap.add_argument("profile", help="thư mục profile Chrome đã đăng nhập, ví dụ accounts/<nick>")
    ap.add_argument("prompts", help="file prompt; cả file là 1 prompt, ngăn nhiều prompt bằng dòng '---'")
    ap.add_argument("--model", default="seedance-2.5", choices=["seedance-2.0", "seedance-2.5"])
    ap.add_argument("--dur", type=int, default=30, choices=[10, 15, 30])
    ap.add_argument("--ratio", default="9:16", choices=["16:9", "9:16", "1:1", "4:3", "3:4"])
    ap.add_argument("--gap", type=float, default=GAP_MAC_DINH, help="giây nghỉ giữa 2 lần gửi")
    ap.add_argument("--timeout", type=int, default=900, help="giây chờ tối đa mỗi video")
    ap.add_argument("--out", default="downloads", help="thư mục lưu video")
    ap.add_argument("--proxy", default="", help="proxy cho lần chạy này (để trống = nối thẳng)")
    ap.add_argument("--headless", action="store_true", help="chạy ẩn (mặc định hiện cửa sổ)")
    ap.add_argument("--ffmpeg", action="store_true", help="cắt thumbnail sau khi tải")
    ap.add_argument("--upscale", action="store_true", help="kèm --ffmpeg: phóng lên 1080p (Lanczos)")
    args = ap.parse_args()
    try:
        return asyncio.run(chay_hang_doi(args))
    except KeyboardInterrupt:
        print("\nđã dừng.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
