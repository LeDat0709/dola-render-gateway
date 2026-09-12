# Repo GitHub làm tool tương tự — rà ngày 12/09/2026

Mục đích: tìm repo có thể cóp ý/cóp mã cho dola-render-gateway. Đã đọc README + mã chính (không chỉ mô tả).

## Đáng cóp (xếp theo giá trị)

### 1. Linkmail16/DolaAI-API — gửi lệnh Dola KHÔNG CẦN CHROME (2★, Python, 09/2026)
> **KIỂM CHỨNG 12/09**: `test_android_submit.py` gửi thử 1 nick (chat chữ) → **HTTP 200, SSE thật**: chữ ký X-Gorgon + cookie web được chấp nhận, không cần Chrome/a_bogus/X-Tt-Token. Lần thử rơi vào 710022002 (giới hạn tần suất IP), không phải lỗi ký. Còn: thử video (ability 17) + sinh device_id riêng mỗi nick.
> **CHỐT 12/09**: aid=495671 (đúng aid nick web) → 710022004 verify/slide (CAPTCHA shark_admin). X-Gorgon không thay được a_bogus mà shark tin; fingerprint lệch còn bị bắt slide. Đường Android KHÔNG bỏ được Chrome cho nick web → giữ đường fetch qua Chrome. Chi tiết: memory dola-request-signing.

- Bắt từ **app Android**: endpoint `https://api16-normal-i18n-myb.dola.com/chat/completion`, `aid=489823`,
  `device_platform=android`, cookie `sessionid` (+ `X-Tt-Token` từ app; README nói cookie web cũng dùng được).
- Ký bằng **X-Gorgon / X-Khronos / X-Ladon** tính cục bộ (`dolaSign.py`, ~100 dòng: MD5 query → RC4 biến thể →
  hoán vị bit), thay cho `msToken + a_bogus` của bản web (thứ đang bắt tool mở Chrome + bdms cho mỗi nick).
- Payload hiện chỉ chat (block_type 10000) và ảnh; **chưa có `chat_ability` (ability_type 17) để tạo video**.
  Cùng endpoint với web nên nhiều khả năng ghép được `chat_ability.ability_param = {ratio, model, duration}`.
- Việc thử (1 giờ, 1 nick): ghép payload video vào client này, gửi 1 lệnh 10s, xem Dola nhận không và có bị
  risk-control vì fingerprint app/web lệch không. Nếu được → bỏ Chrome ở bước gửi, mỗi nick chỉ còn HTTP.

### 2. LauZzL/doubao-downloader — đường tải video KHÔNG WATERMARK khác get_play_info (1.619★, TS, GPL-3.0)
- `src/api/video.ts`: `/samantha/aispace/homepage` → nút "我的创作" → `/samantha/aispace/node_info` (tìm theo vid)
  → `/samantha/aispace/get_download_info {requests:[{node_id}]}` → `download_infos[0].main_url`.
- Đây là đường "Không gian sáng tạo", khác `/samantha/media/get_play_info` mà repo hope0719 xác nhận đã bị
  Doubao đóng watermark vào mọi link từ 07/2026. Nếu Dola cũng đóng watermark vào `main_url` của play_info
  thì thử đường này trước khi sửa `watermark.py`.
- Ép 15s: `use-json.ts` sửa `ability_param.duration = 15` khi `ability_type === 17` — xác nhận mã ability
  của video là 17, khớp chế độ fetch của tool.

### 3. StevePan-912/doubao-video-maker — giao diện `VideoProvider` 2 backend (5★, Python, MIT)
- Cùng một interface, chọn `doubao` (RPA web miễn phí) hoặc `api` (Volcengine Ark Seedance trả phí).
- Khớp kế hoạch broker: khi có API key trả phí, `/v1/job` đổi backend mà client không đổi. MIT → cóp thẳng
  phần client Ark được.

### 4. MemeCalculate/moyin-creator — quy trình kịch bản → phân cảnh → nhiều clip Seedance (4.517★, TS, **AGPL-3.0**)
- Kịch bản → nhân vật (6 lớp neo nhận diện) → bối cảnh → phân cảnh → gộp nhiều cảnh thành video liền mạch;
  kiểm tra ràng buộc Seedance (≤9 ảnh, ≤3 video, ≤3 audio, prompt ≤5000 ký tự); hàng đợi song song, tự thử lại;
  nhiều nhà cung cấp, xoay API key.
- Là bản tham chiếu tốt nhất cho hướng "30s = nhiều clip 15s có liên tục" (xem KHAN_30S_ANALYSIS.md).
  **AGPL**: chỉ lấy ý, không dán mã vào tool đóng.

### 5. fran0220/jimeng-gateway — kiến trúc giống tool này (4★, Rust + React, 04/2026)
- Session pool xoay LRU + health check + tự tắt session hỏng; hàng đợi SQLite; **hiện vị trí hàng đợi và ETA**
  lấy từ phản hồi API; Playwright chỉ dùng ở bước gửi, còn lại HTTP thuần (đúng như tool đang làm).
- Cóp được: hiển thị vị trí hàng đợi/ETA cho job đang chờ.

## Đã xem, không cần cóp
- **zabid-coder/DolaFlow-Studio** (3★): Node + React, điều khiển profile Chrome thật, bấm giao diện
  `dola.com/chat/create-image`, hàng đợi có giới hạn credit/ngày theo profile, loại profile lỗi, ưu tiên tải
  sau 3 phút. Tool này đã có hết và đi đường fetch nhanh hơn. Ý nhỏ: thư mục `episodes/*.txt` → tự tách cảnh,
  thư viện ảnh tham chiếu theo tên file, phím tắt.
- **zhaihao118/Seedance2-Chrome-Extensions** (157★): cho jimeng (bản TQ); extension nhận job qua SSE từ mock
  server, chèn @mention ảnh qua ProseMirror. Không áp dụng cho Dola.
- **chuyoriko/dola-ai-api** (1★): chat bằng cookie, không ký → tự nhận là hay bị từ chối.
- **hope0719/doubao-video-watermark-analysis** (8★): 4 ngày thử, kết luận mọi link play_info của Doubao đều có
  watermark từ 07/2026; ảnh vẫn lấy được bản gốc. Củng cố cảnh báo watermark động trong UNLOCKER_30S_ANALYSIS.md.
- Prompt/skill: ZeroLu/awesome-seedance (2.4k★), YouMind-OpenLab/awesome-seedance-2-prompts (2k★),
  liyue-aigc/seedance-2-5-video-director (328★) — kho prompt Seedance 2.0/2.5, dùng cho mẫu prompt trong Studio.

## Đã biết từ trước (memory `dola-related-repos`)
shukeCyp/DoubaoManager, guo641/doustudio, microshark2024/dola-30s-unlocker, scj725/ai-media-extractor,
UltraFEmotes/dola-api, chuansd/doubao-international.
