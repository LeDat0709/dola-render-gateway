"""Self-check for the pure text helpers ported from DomixHub-Seedance (no browser needed).

Run: python test_worker_patterns.py
"""
from video_worker_ui import (
    DAILY_LIMIT_PATTERN, PARAMETER_CHANGE_PATTERN, CONTENT_POLICY_PATTERN,
    _is_duration_confirm, _is_status_text, _parse_balance_texts,
)
from watermark import _box


def main():
    # Duration confirm: Dola asks to render at a different supported length
    assert _is_duration_confirm("動画の最大長は 15 秒です。15 秒で生成してもよろしいですか？")
    assert _is_duration_confirm("Video generation currently supports durations from 4 to 15 seconds. "
                                "I can generate it at the nearest supported duration of 15 seconds.")
    assert not _is_duration_confirm("生成中です。少々お待ちください")
    assert not _is_duration_confirm("A cat on a windowsill")

    # Status chatter must never be mistaken for a refusal
    assert _is_status_text("動画を生成しています。完了したらお送りします")
    assert _is_status_text("Generating your video, this takes about 3 minutes")
    assert not _is_status_text("肖像保護のため生成できません")
    # Câu từ chối LẠ (chưa có trong mẫu) chứa marker "生成された動画" vẫn phải là từ chối → báo lỗi sau STALE_POLLS
    assert not _is_status_text("理由は不明ですが、生成された動画を送信できません。")

    # Log 17/9 08:27: prompt tool gửi + câu tool tự trả lời bị đọc như lời Dola (mở Chrome oan, tự chọn "5秒", job chết oan)
    from video_worker_ui import _is_own_message, _SENT_PREFIX
    assert _is_own_message(_SENT_PREFIX + '"15.8s 标准见证源输入：  镜头1，0–1.5秒 — 最强触发"、9:16')
    assert _is_own_message("A、15秒、9:16（縦向き）でお願いします。")
    assert _is_own_message("縦向きの9:16、5秒に変更して生成してください。")
    assert not _is_own_message("動画の長さが 15.8 秒と指定されていますが、最も近い 15 秒で生成してよろしいですか？")
    assert _is_status_text("動画の生成を受け付けました。") and _is_status_text("動画を生成しました。")

    # Balance / limit parsing
    assert _parse_balance_texts(["本日は残り 3 ポイントです"])[0] == 3
    assert _parse_balance_texts(["残り 2 動画クレジット"])[0] == 2
    assert _parse_balance_texts(["Bạn đã đạt giới hạn tạo video hằng ngày"])[1] is True
    assert DAILY_LIMIT_PATTERN.search("今日生成额度已达到上限")
    assert PARAMETER_CHANGE_PATTERN.search("パラメーターを変更してもう一度お試しください")
    assert CONTENT_POLICY_PATTERN.search("违反社区内容规范，无法生成视频")
    assert CONTENT_POLICY_PATTERN.search("この内容は生成できませんでした。プロンプトを編集して、もう一度お試しください。")
    assert not CONTENT_POLICY_PATTERN.search("動画を生成しています。完了したらお送りします")
    # Ảnh 16/9: câu chặn bản quyền KHÔNG có chữ ポリシー → job treo tới hết giờ
    assert CONTENT_POLICY_PATTERN.search("著作権を保護するため、生成された動画を表示できません。別の参照物を使用するか、プロンプトを編集して、もう一度お試しください。")

    # Watermark box: 1280x720 calibration and edge clamping
    assert _box(1280, 720) == (1100, 651, 172, 61)
    x, y, bw, bh = _box(10, 10)
    assert x + bw < 10 and y + bh < 10 and bw >= 1 and bh >= 1
    print("OK")


if __name__ == "__main__":
    main()
