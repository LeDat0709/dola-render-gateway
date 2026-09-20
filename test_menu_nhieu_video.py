"""Dola đề nghị CHIA prompt dài thành NHIỀU video → tool không được tự đồng ý: mỗi video là một lần trừ lượt.

Ảnh 20/09: prompt 30s → Dola đáp "4〜15秒" rồi đưa A/B/C, khuyến nghị "C với 12 cảnh + thẻ kết". Tool cũ chọn C
(vì trong C có chữ "15秒") = đặt 13 video trên một nick free chỉ có 4 credit/ngày.
Cách đúng: tự tách prompt ở máy (split_prompt.py) rồi chạy từng phần, mỗi phần là một lượt mình chủ động.

Chạy: .venv/bin/python test_menu_nhieu_video.py
"""
import video_worker_ui as vw

ANH_20_09 = """直接生成することはできません。この動画の合計時間は約 30 秒ですが、現在の動画生成は 1 回につき 4〜15 秒まで対応しています。

生成可能な方法は以下のいずれかです：

A. 最も強いフックとなる「钩子 C」のみを 5 秒で生成
B. 「钩子 C」と「镜头 2」の 2 本をそれぞれ 5 秒で生成
C. 全編をカットごとに最大 15 秒以内の複数動画に分割して生成

9:16
推奨：C で全 12 カット＋エンドカードを分割生成
ご希望の番号を選んでください。"""

MENU_LANH = """長さはどれにしますか？
A. 15秒に圧縮して1本で生成
B. 30秒のまま2本に分けて生成"""


def test_khong_chon_phuong_an_chia_nhieu_video():
    ans = vw._spec_menu_answer(ANH_20_09, "9:16", 30)
    assert not ans.startswith("C"), f"C = 13 video = 13 lần trừ lượt, không được tự chọn. Tool đáp: {ans!r}"
    assert not ans.startswith("B"), f"B = 2 video, cũng nhân đôi lượt. Tool đáp: {ans!r}"
    assert ans == "", f"cả 3 phương án đều tốn thêm lượt → phải TỪ CHỐI trả lời, để job dừng có lý do. Đáp: {ans!r}"


def test_van_chon_duoc_phuong_an_nen_mot_video():
    """Menu có phương án nén về 1 video thì vẫn tự chọn như cũ — đừng làm hỏng ca đang chạy tốt."""
    ans = vw._spec_menu_answer(MENU_LANH, "9:16", 15)
    assert ans.startswith("A"), f"A là nén 1 video, phải chọn: {ans!r}"


def test_bad_option_bat_du_cac_cach_noi_chia_nho():
    for body in ("全編をカットごとに最大15秒以内の複数動画に分割して生成",
                 "2本をそれぞれ5秒で生成",
                 "カットごとに分けて生成",
                 "split into multiple videos",
                 "generate each cut separately"):
        assert vw._BAD_OPTION.search(body), f"phải coi là phương án tốn thêm lượt: {body}"


def test_tin_co_menu_thi_bo_qua_nhanh_tra_loi_co():
    """Cờ yes_ok trong poll: tin CÓ menu A/B/C không được trả "はい" (Dola đang khuyến nghị C = 13 video,
    "はい" rất dễ bị hiểu là đồng ý với khuyến nghị đó) mà phải xuống nhánh chọn chữ cái."""
    assert vw._lists_options(ANH_20_09) is True, "phải nhận ra đây là menu → yes_ok=False"
    assert [L for L, _ in vw._menu_options(vw._zen2han(ANH_20_09))] == ["A", "B", "C"]


if __name__ == "__main__":
    test_khong_chon_phuong_an_chia_nhieu_video()
    test_van_chon_duoc_phuong_an_nen_mot_video()
    test_bad_option_bat_du_cac_cach_noi_chia_nho()
    test_tin_co_menu_thi_bo_qua_nhanh_tra_loi_co()
    print("OK: menu nhiều video")
