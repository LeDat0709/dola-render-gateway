# Phân tích DomixHub-Seedance.exe và những gì đã đưa vào dola-render-gateway

Ngày phân tích: 2026-09-09. File: `DomixHub-Seedance.exe` (150 MB, SHA-256 `4347df84…bae7f201`).

## 1. Bên trong file exe là gì

| Thành phần | Kết quả |
|---|---|
| Đóng gói | PyInstaller onefile, Python 3.12, chạy GUI qua **pywebview** (cửa sổ WebView2 nhờ pythonnet) |
| Trình duyệt | **patchright 1.62.3** (fork Playwright chống phát hiện), Chrome thật qua `channel=chrome` |
| Web server nội bộ | FastAPI + uvicorn, entry `studio_server.py`, UI ở `studio_web/index.html` (100 KB, tiếng Việt) |
| Xử lý ảnh/video | opencv 5.0, numpy, imageio-ffmpeg (ffmpeg đóng kèm) |
| Module riêng của app | `studio_server`, `video_worker_ui`, `video_worker`, `dola_client`, `browser`, `config`, `add_account`, `facebook_login`, `media`, `gap`, `video_probe`, `watermark` |
| Extension | `extensions/dola30` **giống hệt từng byte** với extension trong repo này |

Kết luận quan trọng: exe là **cùng một dòng code với repo dola-render-gateway** (tên module, hàm, chuỗi log, POLL_JS, BALANCE_JS trùng nhau). Khác biệt nằm ở lớp "Studio" phía trên và một số tinh chỉnh worker.

## 2. Sản phẩm "Dola Seedance Studio" làm gì

- Bảng tài khoản (`studio_accounts.json`): mỗi dòng có prompt riêng, tối đa 5 ảnh nhân vật (`refs/`), model / tỉ lệ / thời lượng riêng, trạng thái và % tiến độ.
- Chạy từng dòng hoặc chạy hàng loạt, giới hạn song song `DOLA_STUDIO_CONCURRENCY=3`.
- Thêm tài khoản 4 cách: dán cookie Dola, dán cookie Facebook (tự OAuth vào Dola), mở cửa sổ Chrome để tự đăng nhập, hoặc email + mật khẩu + 2FA Google.
- Kiểm tra cookie còn sống, kiểm tra lượt còn lại (đọc lịch sử chat qua BALANCE_JS, chỉ biết "hết lượt" hay "chưa thấy giới hạn").
- Xem / xoá video của từng tài khoản, đổi tên, xoá hàng loạt, mở profile Chrome.
- Tự xoá logo "Dola AI" góc dưới phải cho video Seedance 2.0 / Dreamina bằng ffmpeg `delogo`.
- **Paywall**: `DomixAuth` gọi `https://domixhub.com/api` (login + OTP), lưu phiên bằng Windows DPAPI vào `studio_session.dat`, kiểm tra gói trả phí trước khi cho chạy. Phần này không liên quan tới tool của mình.

Model exe hỗ trợ: Seedance 2.5, Seedance 2.0, Dreamina 1.0. Thời lượng mặc định 5 s hoặc 10 s (repo này dùng 10 / 15 / 30 s qua extension dola30).

## 3. So sánh worker tạo video (video_worker_ui)

| Tính năng trong exe | Trạng thái trong repo |
|---|---|
| Nhận diện hết lượt / hết điểm / chặn nội dung / bảo vệ chân dung | Có sẵn (đợt port trước) |
| Dola hỏi "tạo ở 15 giây được không?" → tự trả lời "はい" | **Vừa thêm** (`_is_duration_confirm`, `_reply_yes`) |
| Phát hiện bị đăng xuất giữa chừng (redirect `from_logout`, mất `sessionid`, chữ "ログインしてください") ở mọi bước | **Vừa thêm** (`_is_logged_out`, ném `LoggedOutError` để pool tắt nick) |
| Dola trả lời một câu không phải tiến độ và giữ nguyên 4 lần poll → fail sớm, trích lời Dola | **Vừa thêm** (`_STATUS_MARKERS`, `STALE_POLLS`) |
| Dola tạo ẢNH thay vì video (hay gặp khi có ảnh tham chiếu) | **Vừa thêm** (POLL_JS đếm `images`, `IMAGE_ONLY_POLLS`) |
| Timeout kèm câu cuối Dola nói | **Vừa thêm** |
| Upload ảnh tham chiếu thử lại 3 lần, tự bấm nút "thêm ảnh" nếu input ẩn | **Vừa thêm** |
| "Đổi tham số rồi thử lại" = không đủ lượt cho độ dài này | **Vừa sửa**: pool xoay sang nick khác thay vì báo lỗi chung |
| Ghim ngôn ngữ UI (i18next + user_language_code) cho mọi profile | **Vừa thêm** vào `launch_account_context`, mọi luồng đều đi qua |
| Lưu lại cookie với hạn xa (cookie phiên bị mất khi đóng Chrome) | **Vừa thêm** (`pin_session_cookies`) cho import cookie, đăng nhập tay, Facebook OAuth |
| Xoá logo Dola AI (ffmpeg delogo, fallback OpenCV inpaint) | **Vừa thêm** (`watermark.py`, tắt bằng `DOLA_REMOVE_WATERMARK=0`) |
| Bỏ "HeadlessChrome" khỏi UA, chặn mở 2 Chrome cùng profile | Repo **đã hơn exe** (exe không có) |
| Facebook OAuth vào Dola | Đã có (`facebook_login.py`, đợt port trước) |
| Callback `on_generating` để UI hiện % tiến độ | Bỏ qua, UI dashboard không dùng |
| Dreamina 1.0, thời lượng 5 s | Bỏ qua, repo đi hướng 10/15/30 s qua extension |
| Studio UI per-account, paywall DomixHub | Không port |

## 4. Thay đổi hôm nay (file)

- `video_worker_ui.py`: các mục "vừa thêm" ở trên; regex hết lượt thêm tiếng Việt / 今日; regex chính sách nhận cả "违反社区内容规范".
- `video_worker.py`: POLL_JS trả text dài 600 ký tự (đủ để đọc câu hỏi / từ chối) và đếm creation ảnh.
- `browser.py`: `force_ui_language` đặt cả `user_language_code`, gọi ngay khi launch; thêm `pin_session_cookies`.
- `browser_pool.py`: `ParameterChangeError` xử lý như hết điểm (đánh dấu quota, xoay nick).
- `cookie_service.py`, `login_profile.py`, `facebook_login.py`: ghim cookie phiên sau khi đăng nhập thành công.
- `config.py`: `DOLA_UI_LANG`, `DOLA_REMOVE_WATERMARK`; `watermark.py` nhận `DOLA_WM_X/Y/W/H`.
- `fb_login.py`: **xoá cookie Facebook thật bị hardcode** trong `DEFAULT_RAW_DATA`. Cookie đó đã nằm trên đĩa, nên coi như lộ và đăng xuất / đổi mật khẩu tài khoản Facebook đó.
- `.gitignore`: `*.exe`, `accounts_fb/`. `requirements.txt`: `imageio-ffmpeg`.
- `test_worker_patterns.py`: self-check cho các hàm thuần (chạy `python test_worker_patterns.py`).

Lưu ý: xoá logo là re-encode x264 CRF 18, file có thể lớn hơn bản gốc (clip mẫu 720p: 1.5 MB → 4.2 MB, 1.2 s). Video Seedance 2.5 không bị đụng tới.

## 5. Cách tôi đọc được exe

`pyinstxtractor-ng` bung archive → `.pyc` Python 3.12 → decompiler pycdc chưa hỗ trợ 3.12 nên đọc trực tiếp bytecode (`dis` + hằng số) cho từng hàm. Mã nguồn gốc không có, mọi hàm port lại đều được viết lại tay theo logic đọc được.

## 6. Tạo video bằng request (chế độ `fetch`) — bổ sung cùng ngày

Mục tiêu: gửi lệnh tạo video mà không bấm UI. Kết quả khảo sát trên Dola thật (nick `acc_test`):

- Mọi request ghi (`/chat/completion`) phải có **`msToken` + `a_bogus`**, do SDK `bdms` trong trang ký từng request. Python thuần (aiohttp) không ký được nên bị `710010202 common invalid param`; các endpoint đọc (`/im/chain/*`) thì chỉ cần cookie.
- `bdms` hook `window.fetch` ở **main world**. `patchright` mặc định chạy `page.evaluate` trong isolated world (không thấy hook, không thấy `window.bdms`), nên JS gửi lệnh phải chạy với `isolated_context=False`. Khi đó request tự được gắn chữ ký.
- Body thật của UI đã được bắt lại (abort trước khi gửi) và trở thành `SUBMIT_JS` trong `video_worker.py`: `chat_ability.ability_param = {"model","duration"(số),"ratio","input_box_content":{...}}`, text có tiền tố "生成された動画：", query lấy từ chính request của trang (`device_id`, `tea_uuid`, `web_id`…).
- Skill-pack thật của nick free JP chỉ có `seedance_v2.0` (Seedance 2.0 Fast) và `ic_mini` (1.0 Fast), 5s/10s. Key của Seedance 2.5 chưa biết: chạy một lần 2.5 qua UI bằng nick có quyền, đọc dòng log `chat/completion ability_param=…`, rồi đặt `DOLA_MODEL_KEY_SEEDANCE25`.

Cách hoạt động trong tool (`video_worker_ui.generate_video`):

1. `DOLA_SUBMIT_MODE=fetch` (mặc định): mở profile headless, kiểm tra đăng nhập và lượt, gọi `SUBMIT_JS` ở main world, lấy `conversation_id` từ SSE_ACK, rồi poll như cũ. Không cần extension, không cần cửa sổ.
2. Nếu Dola từ chối ngay lúc gửi (captcha, sai tham số) thì tự chuyển sang luồng UI. Khi đã có `conversation_id` thì không gửi lại để không tốn lượt đôi.
3. Ảnh tham chiếu và Seedance 2.5 (chưa có key) luôn đi luồng UI. `DOLA_SUBMIT_MODE=ui` để tắt hẳn fetch.

Sửa kèm cho luồng UI (nguyên nhân gãy hôm nay): phải bấm **OK** banner cookie và **新しいチャット** trước thì nút **動画を作成** mới xuất hiện; khung soạn giờ là DIV contenteditable. Preflight lượt giờ chỉ xét tin nhắn **từ mốc reset gần nhất** (00:00 JST), vì câu "hết lượt hôm nay" của hôm qua vẫn nằm trong lịch sử.

Lưu ý: bắt gói bằng cách abort `/chat/completion` **không miễn phí**, app gửi lại qua kênh khác và job vẫn chạy (acc_test hết lượt ngày vì vậy).
