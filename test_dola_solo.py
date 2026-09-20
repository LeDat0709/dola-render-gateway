"""Test phần logic thuần của dola_solo.py — không mở Chrome, không chạm mạng, không tốn lượt.

Chạy: .venv/bin/python -m pytest test_dola_solo.py -q
"""
import dola_solo


def test_doc_prompts_khong_tach_theo_dong_trong(tmp_path):
    """REGRESSION 20/09: kịch bản 13 mốc cảnh cách nhau bằng dòng trống từng bị xé thành
    14 prompt vụn, mỗi mảnh gửi thành một job riêng → đốt sạch lượt. Dòng trống KHÔNG ngăn khối."""
    f = tmp_path / "p.txt"
    f.write_text("标准见证源输入：\n\n镜头1，0–2.0秒 — mở đầu\n\n镜头2，2.0–4.2秒 — phát hiện\n",
                 encoding="utf-8")

    assert len(dola_solo.doc_prompts(f)) == 1, "cả file phải là MỘT prompt"
    assert "镜头2" in dola_solo.doc_prompts(f)[0]


def test_doc_prompts_tach_theo_dau_gach_ngang(tmp_path):
    """Chỉ dòng chỉ chứa '---' mới ngăn hai prompt."""
    f = tmp_path / "p.txt"
    f.write_text("prompt một\ndòng hai\n---\nprompt hai\n-----\nprompt ba\n", encoding="utf-8")

    assert dola_solo.doc_prompts(f) == ["prompt một\ndòng hai", "prompt hai", "prompt ba"]


def test_doc_prompts_file_rong(tmp_path):
    f = tmp_path / "p.txt"
    f.write_text("\n\n   \n", encoding="utf-8")

    assert dola_solo.doc_prompts(f) == []


def test_gan_tag_dat_cuoi_va_giu_nguyen_prompt():
    """Tag phải nằm CUỐI, cách một dòng trống, không đụng vào nội dung prompt."""
    out = dola_solo.gan_tag("con mèo\ndòng hai", "seedance-2.5", "9:16", 30)

    assert out.startswith("con mèo\ndòng hai")
    assert out.endswith("[Model Seedance 2.5, Tỷ lệ 9:16, Độ dài video 30s]")
    assert "\n\n[Model" in out


def test_gan_tag_doi_ten_model_theo_phien_ban():
    assert "Seedance 2.0" in dola_solo.gan_tag("x", "seedance-2.0", "16:9", 10)
    assert "Seedance 2.5" in dola_solo.gan_tag("x", "seedance-2.5", "16:9", 10)


def test_la_rate_limit_bat_du_cac_dang_dola_tra():
    for loi in ("Dola tạm chặn (710022002)",
                "現在はリクエストが集中しています",
                "操作频繁，请稍后再试",
                "HTTP 429 rate limit exceeded"):
        assert dola_solo.la_rate_limit(loi), loi


def test_la_rate_limit_khong_bat_nham_loi_khac():
    """Chặn nội dung / cookie chết KHÔNG được coi là rate limit — nghỉ 5 phút là vô nghĩa."""
    for loi in ("Vi phạm chính sách nội dung: 著作権を保護するため",
                "Bị đăng xuất khỏi Dola giữa chừng",
                "TimeoutError: page never loaded"):
        assert not dola_solo.la_rate_limit(loi), loi


def test_nhip_nghi_nam_trong_bien_do_jitter():
    """Nhịp phải dao động quanh gap, không bao giờ âm hoặc bằng 0."""
    mau = [dola_solo.nhip_nghi(100.0) for _ in range(200)]

    assert all(m >= 1.0 for m in mau)
    assert all(75.0 <= m <= 125.0 for m in mau)
    assert len({round(m, 3) for m in mau}) > 1, "phải có nhiễu, không được cố định"


def test_nhip_nghi_gap_nho_khong_ra_so_am():
    assert dola_solo.nhip_nghi(0.5) >= 1.0


def test_hau_ky_upscale_khong_nem_name_error(tmp_path):
    """20/09: `ra` (đường dẫn file upscale) chưa từng được gán → NameError, mà NameError không nằm
    trong tuple except nên nó thoát ra và giết cả hàng đợi."""
    from pathlib import Path
    ket = dola_solo.hau_ky(Path(tmp_path) / "khong-ton-tai.mp4", upscale=True)
    assert isinstance(ket, dict), "ffmpeg hỏng thì bỏ qua, không được ném lỗi"


def test_khong_con_ten_bien_chua_dinh_nghia():
    """Chặn lại đúng lớp lỗi trên: mọi tên trong dola_solo.py phải đã được gán."""
    import subprocess, sys
    r = subprocess.run([sys.executable, "-m", "pyflakes", "dola_solo.py"], capture_output=True, text=True)
    undefined = [l for l in r.stdout.splitlines() if "undefined name" in l]
    assert not undefined, "còn tên chưa định nghĩa:\n" + "\n".join(undefined)
