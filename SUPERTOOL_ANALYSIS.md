# Phân tích SuperTool Ultimate 9.0.5 (bản trong Downloads)

## 1. Bản này là gì
- Đóng gói **PyInstaller onedir** (thư mục `_internal/`), **Python 3.14**, GUI **PyQt6**, tự động **Playwright** (Chromium thật, `chrome_profile_dola/`), kèm **ffmpeg.exe** để xoá watermark.
- Code lõi **biên dịch native bằng mypyc** (`_internal/…__mypyc.cp314-win_amd64.pyd`) **cộng PyArmor** (`pyarmor_runtime_015125`) và có **integrity check** (tự băm exe lúc khởi động).
- Hệ quả: **không đọc được mã nguồn** — không có `.py`/`.pyc` rõ, không có chuỗi Dola/selector nào lộ ra trong blob native. Khác Seedance (`.cjs` đọc được) và DomixHub (bytecode đọc được). Với tool này chỉ quan sát được **hành vi và cấu hình**, không phải logic bên trong.

## 2. Các file kèm là bản BẺ KHOÁ (không hỗ trợ)
`SuperTool_DIFF.txt`, `VERIFICATION.txt`, `ROLLBACK.sh`, `SuperTool_MODIFIED.exe` là một **bản crack license**: vá `core.veo_license_system` / `veo_protection_system` / `security` / `veo_hash` ở mức nhị phân để bật license vĩnh viễn và tắt kiểm tra bảo vệ. `veo_security.log` cho thấy bản gốc chặn chạy khi phát hiện `FILE_MODIFIED`.

Tôi **không phân tích sâu hay mở rộng phần bẻ khoá này** — đó là vượt bản quyền phần mềm của người khác. Phần dưới chỉ rút **ý cải tiến hợp pháp** cho tool của anh từ tính năng quan sát được.

## 3. Tính năng quan sát được (từ `settings.json`) và ý cải tiến cho dola-render-gateway

| SuperTool có | Tool anh hiện tại | Nên thêm? |
|---|---|---|
| `concurrency` + `multi_instance_limit` | `DOLA_MAX_CONCURRENCY` (đã có) | Đã ngang |
| `duration` mặc định **30s**, `ratio` | 10/15/30 + ratio (đã có) | Đã ngang |
| `watermark_mode: "Blur (Delogo)"` | `watermark.py` delogo + fallback inpaint (vừa thêm) | **Đã ngang** — anh cũng dùng ffmpeg delogo |
| `show_browser` (hiện/ẩn cửa sổ) | worker chạy headless/headed tuỳ luồng | Nên thêm 1 cờ bật cửa sổ để user xem khi debug |
| `mobile_mode` | chưa có | Cân nhắc: giả lập thiết bị di động (UA + viewport) để đa dạng fingerprint |
| `auto_loop` | chưa có | Chạy lặp danh sách prompt tự động — hữu ích cho batch |
| `start_delay` / `next_delay` | chưa có | Thêm nghỉ giữa các lượt để giống người, giảm rủi ro risk-control |
| `max_cookies` / per-nick limit theo license tier | pool theo `DAILY_LIMIT` | Đã có cơ chế tương đương |
| `process_start_timeout` | timeout cứng | Nên tách timeout khởi động vs render |

## 4. Kết luận
- **Không rút được logic tạo video/login/cookie** từ SuperTool vì đã biên dịch native + PyArmor + integrity. Muốn học cách làm HTTP/login, **Seedance (`.cjs`) là nguồn đọc được tốt hơn nhiều** — xem `SEEDANCE_STUDIO_ANALYSIS.md`.
- Về tính năng, tool của anh đã ngang hoặc hơn SuperTool ở những phần cốt lõi (tạo video request trực tiếp, xoá watermark, pool xoay nick, chống phát hiện). Thứ đáng thêm là mấy tiện ích vận hành: `start_delay/next_delay`, `auto_loop`, `mobile_mode`, cờ `show_browser`.

## 5. Bản 9.0.7 (xem ngày 12/09/2026) — riêng câu hỏi "logic tạo video 30 giây"
- Vẫn **mypyc + PyArmor**: file native chỉ 216 KB / 788 chuỗi, không lộ URL, selector hay `duration` nào. Tên module đọc được từ PYZ: `dola_direct.py`, `core.dola_super_sessions_manager`, `core.chrome_manager`, `core.dola_topproxy`, `ui.dola_video_gen_page`, `ui.seedance_20_page`. `dola_direct` = gửi API trực tiếp, tương đương chế độ **fetch** của tool này.
- **Không có extension** trong `chrome_profile_dola` (không thư mục Extensions, Preferences trống phần extensions). Lịch sử chỉ ghé `www.dola.com/chat` 1–2 lần ngày 10/09, cache không có `skill/pack` → chưa từng tạo video trên máy này, không quan sát được hành vi.
- **exe build 04/09/2026** (PE timestamp), tức TRƯỚC ngày Dola tắt 30s phía máy chủ (11/09, xem `docs/NGHIEN_CUU_30S_2026-09-12.md`). Không có bằng chứng nào bản này còn ra 30s sau 11/09; log tool này 17:35 12/09 Dola vẫn trả 「30秒の動画生成は現在サポート外です。最長15秒」.
- Kết luận: **không có logic 30s nào để cóp**; cách duy nhất còn lại là ghép 2×15s (mục 5 tài liệu 30s).
