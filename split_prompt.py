#!/usr/bin/env python3
"""split_prompt.py — cắt prompt nhiều shot thành các phần ≤ 15s để dựng riêng từng clip.

Vì sao: giao diện Dola chỉ nhận 4–15s. Đưa prompt 28–30s vào, Dola tự đề nghị "2 clip 15s" rồi hỏi xác nhận nhiều
lần, tool coi câu trả lời lặp là từ chối (log 20/09 13:19). Tự cắt trước thì mỗi clip vừa đúng 15s, Dola không hỏi.

Cắt ở RANH GIỚI SHOT (mốc "镜头N，0–2.0秒" / "SHOT N, 0–2.0s"), cân bằng độ dài các phần, dời mốc về 0 ở mỗi phần,
đánh số lại shot. Không đụng mốc phụ trong shot ("0.8秒") hay tuổi ("early 30s").

Dùng:
    .venv/bin/python split_prompt.py prompt.txt > parts.txt          # các phần cách nhau bằng dòng ---
    .venv/bin/python split_prompt.py prompt.txt --max 15
    .venv/bin/python dola_solo.py accounts/<nick> parts.txt --dur 15   # parts.txt dùng đúng định dạng của dola_solo
"""
from __future__ import annotations

import argparse
import math
import re
import sys

from video_worker_ui import _fmt_sec

_SHOT = re.compile(
    r"(?P<word>镜头|鏡頭|SHOT|Shot|shot|シーン|カット)(?P<sp>\s*)(?P<n>\d+)(?P<sep>\s*[，,:：]\s*)"
    r"(?P<a>\d+(?:[.,]\d+)?)(?P<dash>\s*[–\-~〜]\s*)(?P<b>\d+(?:[.,]\d+)?)(?P<unit>\s*(?:秒|s\b|sec))")
_LEAD_TOTAL = re.compile(r"^(\s*[\"“]?)\d+(?:[.,]\d+)?(\s*(?:s\b|秒))")
MAX_OVER = 1.2   # cho phần dài tới 1.2× rồi co nhẹ mốc — cùng ngưỡng fit_prompt_to_duration


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _balanced_bounds(times: list[float], n: int) -> tuple[float, list[int]]:
    """Chia m shot thành n phần liên tiếp sao cho phần DÀI NHẤT ngắn nhất. times = [t0, hết shot1, …, hết shot m].
    Trả (độ dài phần dài nhất, [0, i1, …, m]). DP nhỏ: m ≲ 20 nên không cần gì hơn."""
    m = len(times) - 1
    # Chỉ cắt được ở ranh giới shot → nhiều nhất m phần. n lớn hơn m là vô nghiệm: DP để nguyên inf rồi
    # vòng truy vết đọc chỉ số -1 (chỉ số âm của Python) và trả bounds rác thay vì báo lỗi (20/09: IndexError).
    n = max(1, min(n, m))
    inf = float("inf")
    dp = [[(inf, -1)] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = (0.0, -1)
    for k in range(1, n + 1):
        for j in range(k, m + 1):
            for i in range(k - 1, j):
                worst = max(dp[k - 1][i][0], times[j] - times[i])
                if worst < dp[k][j][0]:
                    dp[k][j] = (worst, i)
    bounds, j = [m], m
    for k in range(n, 0, -1):
        j = dp[k][j][1]
        bounds.append(j)
    return dp[n][m][0], bounds[::-1]


def split_prompt(prompt: str, max_sec: float = 15.0) -> list[str]:
    """Danh sách phần prompt, mỗi phần ≤ max_sec giây. Không có ≥2 mốc shot, hoặc đã ≤ max_sec → trả nguyên [prompt]."""
    marks = list(_SHOT.finditer(prompt))
    if len(marks) < 2:
        return [prompt]
    ends = [m.start() for m in marks[1:]] + [len(prompt)]
    shots = [(m, _num(m["a"]), _num(m["b"]), prompt[m.start():e]) for m, e in zip(marks, ends)]
    header = prompt[:marks[0].start()]
    times = [shots[0][1]] + [s[2] for s in shots]
    total = times[-1] - times[0]
    if total <= max_sec:
        return [prompt]

    n = 1 if total <= max_sec * MAX_OVER else math.ceil(total / max_sec)
    worst, bounds = _balanced_bounds(times, n)
    while worst > max_sec * MAX_OVER and n < len(shots):   # shot dài quá nên cắt n phần vẫn lố → thêm phần
        n += 1
        worst, bounds = _balanced_bounds(times, n)

    parts = []
    for lo, hi in zip(bounds, bounds[1:]):
        seg = shots[lo:hi]
        off, length = seg[0][1], seg[-1][2] - seg[0][1]
        k = min(1.0, max_sec / length)   # phần hơi lố → co nhẹ mốc cho vừa max_sec
        chunks = []
        for idx, (m, a, b, text) in enumerate(seg, 1):
            marker = (f"{m['word']}{m['sp']}{idx}{m['sep']}{_fmt_sec((a - off) * k)}"
                      f"{m['dash']}{_fmt_sec((b - off) * k)}{m['unit']}")
            chunks.append(marker + text[m.end() - m.start():])
        head = _LEAD_TOTAL.sub(lambda h: f"{h.group(1)}{_fmt_sec(length * k)}{h.group(2)}", header, count=1)
        parts.append(head + "".join(chunks).rstrip())
    return parts


def main() -> int:
    ap = argparse.ArgumentParser(description="Cắt prompt nhiều shot thành các phần ≤ --max giây.")
    ap.add_argument("file", help="file prompt (hoặc - để đọc stdin)")
    ap.add_argument("--max", type=float, default=15.0, help="độ dài tối đa mỗi phần, mặc định 15s")
    args = ap.parse_args()
    text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    parts = split_prompt(text.strip(), args.max)
    print("\n---\n".join(parts))
    print(f"→ {len(parts)} phần (≤ {args.max:g}s/phần)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
