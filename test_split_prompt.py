"""split_prompt: cắt prompt nhiều shot (mốc "0–2.0s") thành các phần ≤ 15s, mỗi phần bắt đầu từ 0, shot đánh số lại.
Lý do (log 20/09 13:19): prompt 28–30s → Dola tự đề nghị 2 clip 15s rồi hỏi xác nhận nhiều lần, tool coi là từ chối.
Chạy: .venv/bin/python test_split_prompt.py
"""
import re

from split_prompt import split_prompt

CN = "标准见证源输入：\n\n" + "\n\n".join(
    f"镜头{i}，{a}–{b}秒 — 段{i}： 内容{i}，early 30s 的男人 | 音效：0.8秒轻响"
    for i, (a, b) in enumerate([(0, 2.0), (2.0, 4.2), (4.2, 6.5), (6.5, 8.8), (8.8, 11.2), (11.2, 13.6), (13.6, 16.0),
                                (16.0, 18.5), (18.5, 21.0), (21.0, 23.5), (23.5, 26.0), (26.0, 28.0), (28.0, 30.0)], 1))

EN = ("22s STANDARD WITNESS SOURCE INPUT: A mechanic refuses a car.  "
      "SHOT 1, 0–8.0s — HOOK: he slams the gate, late 40s.  SHOT 2, 8.0–15.0s — TURN: she finds the truck.  "
      "SHOT 3, 15.0–22.0s — END: she films it.")


def _ranges(part):
    return [(float(a), float(b)) for a, b in re.findall(r"(\d+(?:\.\d+)?)[–-](\d+(?:\.\d+)?)\s*(?:秒|s)\b", part)]


def test_cn_30s_splits_in_two_and_each_starts_at_zero():
    parts = split_prompt(CN, max_sec=15)
    assert len(parts) == 2, f"30s / 15s = 2 phần, thực tế {len(parts)}"
    for p in parts:
        r = _ranges(p)
        assert r[0][0] == 0, "mỗi phần bắt đầu từ mốc 0"
        assert r[-1][1] <= 15.01, f"mỗi phần ≤ 15s, thực tế {r[-1][1]}"
        assert p.startswith("标准见证源输入："), "giữ đoạn mở đầu"
        assert "镜头1，" in p, "shot đánh số lại từ 1 ở mỗi phần"


def test_no_shot_lost_or_duplicated_and_ages_untouched():
    parts = split_prompt(CN, max_sec=15)
    body = "\n".join(parts)
    for i in range(1, 14):
        assert body.count(f"内容{i}，") == 1, f"shot {i} phải xuất hiện đúng 1 lần"
    assert body.count("early 30s") == 13, "tuổi 'early 30s' không phải mốc thời gian, không được đụng"
    assert body.count("0.8秒轻响") == 13, "mốc phụ trong shot không bị đổi"


def test_english_inline_shots_and_leading_total_is_rewritten():
    parts = split_prompt(EN, max_sec=15)
    assert len(parts) == 2, parts
    lead = re.match(r"(\d+(?:\.\d+)?)s STANDARD", parts[1])
    assert lead and float(lead.group(1)) <= 15.01, f"tổng ở đầu phần phải khớp độ dài phần: {parts[1][:30]}"
    assert "late 40s" in parts[0], "tuổi giữ nguyên"
    assert "SHOT 1, 0–" in parts[1], "phần 2 đánh số lại từ SHOT 1"


def test_short_prompt_and_no_shots_are_returned_whole():
    assert split_prompt("chú mèo ngồi bên cửa sổ", max_sec=15) == ["chú mèo ngồi bên cửa sổ"]
    short = "SHOT 1, 0–5.0s — A.  SHOT 2, 5.0–10.0s — B."
    assert split_prompt(short, max_sec=15) == [short], "≤ 15s thì không cắt"


def test_fit_prompt_to_duration_leaves_ages_alone():
    """20/09: 'early 30s' (tuổi) bị co thành 'early 22.5s' khi prompt 40s ép về 30s."""
    import video_worker_ui as vw
    p = "镜头1，0–20秒 — 开场： an officer, early 30s, olive vest. 镜头2，20–40秒 — 结尾： a man, late 30s, in his 40s."
    out = vw.fit_prompt_to_duration(p, 30, "t")
    assert "early 30s" in out and "late 30s" in out and "in his 40s" in out, out
    assert "镜头2，15–30秒" in out, "mốc thật vẫn được co 40s → 30s"


def test_shot_dai_hon_max_sec_khong_lam_crash():
    """20/09: n = ceil(total/max_sec) > số shot → DP vô nghiệm, truy vết dùng chỉ số -1 → IndexError."""
    p = "SHOT 1, 0–50.0s — A.  SHOT 2, 50.0–100.0s — B."
    parts = split_prompt(p, max_sec=15)
    assert len(parts) == 2, f"cắt ở ranh giới shot → 2 phần, thực tế {len(parts)}"
    for part in parts:
        r = _ranges(part)
        assert r[0][0] == 0 and r[-1][1] <= 15.01, f"mốc phải được co về ≤15s, thực tế {r}"


def test_mot_shot_duy_nhat_rat_dai():
    """Một shot 40s: không có ranh giới nào để cắt → trả nguyên, không được nổ."""
    p = "SHOT 1, 0–40.0s — A.  SHOT 2, 40.0–41.0s — B."
    parts = split_prompt(p, max_sec=15)
    assert parts and all(_ranges(x)[-1][1] <= 15.01 for x in parts), parts


def test_tu_ket_thuc_bang_my_mid_khong_bi_coi_la_tuoi():
    """20/09: _AGE_CONTEXT thiếu \b nên 'enemy 30s', 'army 30s', 'amid 30s' bị coi là TUỔI,
    mốc thời gian thật sót lại trong prompt gửi đi → Dola hỏi lại thời lượng."""
    import video_worker_ui as vw
    p = "镜头1，0–20秒 — A. the enemy 30s later. 镜头2，20–40秒 — B."
    out = vw.fit_prompt_to_duration(p, 30, "t")
    assert "enemy 30s" not in out, f"'enemy' không phải tiền tố tuổi, 30s phải được co: {out}"
    assert "early 30s" in vw.fit_prompt_to_duration("镜头1，0–40秒 — a man, early 30s.", 30, "t"), "tuổi thật vẫn giữ"


if __name__ == "__main__":
    test_fit_prompt_to_duration_leaves_ages_alone()
    test_cn_30s_splits_in_two_and_each_starts_at_zero()
    test_no_shot_lost_or_duplicated_and_ages_untouched()
    test_english_inline_shots_and_leading_total_is_rewritten()
    test_short_prompt_and_no_shots_are_returned_whole()
    test_shot_dai_hon_max_sec_khong_lam_crash()
    test_mot_shot_duy_nhat_rat_dai()
    test_tu_ket_thuc_bang_my_mid_khong_bi_coi_la_tuoi()
    print("OK: split_prompt")
