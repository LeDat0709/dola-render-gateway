# Phân tích extension "Dola 30 Sec By KHAN" v1.1 (thư mục `Downloads/Dola 30s Khan`) — 12/09/2026

Câu hỏi: extension này tạo video 30 giây bằng cách nào, có gì để bắt sang tool?

## 1. Kết luận ngắn
**Không có logic 30 giây nào trong extension này.** Con số 30 chỉ nằm ở tên, README, và trong các thông
điệp `duration: 30` mà popup/background gửi đi — **không có dòng mã nào nhận và dùng** số đó. Bộ máy sửa
thời lượng thật (trong `extractor.js`) chỉ cho phép **5 / 10 / 15 giây** và mặc định 15. Thứ duy nhất
đáng lấy là **system prompt "storyboard"**: bảo trợ lý Dola tự chia một câu chuyện 30–180 giây thành
**nhiều clip nối tiếp** (mỗi clip trong giới hạn model), giữ liên tục nhân vật/bối cảnh/âm thanh giữa các
clip. Đó là cách hợp lệ duy nhất còn lại để có "30 giây" khi Dola đã tắt 30s phía máy chủ (11/09).

## 2. Từng file làm gì

| File | Vai trò thật |
|---|---|
| `inject.js` (177 KB, làm rối bằng javascript-obfuscator) | Giải mã ra chỉ còn **79 dòng**: hàm `findActiveDolaVideo()` tìm thẻ `<video>` đang hiện và một listener `PURZA_FETCH / ZDOLA_FETCH_ORIGINAL / CHANNA_FETCH_CLEAN` để gửi link đi tải. **Không hook fetch, không sửa duration.** Tên biến lộ nguồn gốc: "ChannaTheBrand Pro", "PURZA" — mã ghép từ extension khác, đổi tên. |
| `extractor.js` (3534 dòng, đọc được) | Bộ máy chính: hook `XMLHttpRequest.send` và `window.fetch`; với `/chat/completion` gọi `modifySeedanceRequestBody()` thay `"duration": N` trong `ability_param` bằng `seedanceTargetDuration` ∈ {5,10,15}, chỉ khi bật cờ `doubao-assistant-seedance-enabled` (mặc định **tắt**). Vá menu giao diện: nhân bản mục "10s" thành "15s", đổi nhãn nút. Bắt `/im/chain/single` để lấy link video gốc không watermark. Chứa `SEEDANCE_SYSTEM_PROMPT` (≈1050 dòng JSON, quy trình 30 bước) và nút "Send Prompt" điền nó vào ô chat. |
| `content.js` | Cầu nối: nhận `INJECT_DOLA_TASK` → điền prompt vào textarea, bấm gửi, và `postMessage PURZA_UPDATE_SETTINGS {duration: 30}` — **không ai lắng nghe thông điệp này**. Bắt toast hết lượt để báo background xoay nick. |
| `background.js` | Quản lý nhiều nick bằng cookie theo tab (DeclarativeNetRequest sửa header Cookie), tải file, hàng đợi prompt, xoay nick khi 429. Gửi `duration: 30` trong message — cũng không ai dùng. |
| `popup.js` | Giao diện: nút "Generate 30s Video" chỉ gửi `INJECT_DOLA_TASK`. |

Bằng chứng "30 chết": `grep PURZA_UPDATE_SETTINGS` chỉ thấy các lệnh `postMessage`, không có `addEventListener`
nào xử lý; `VALID_SEEDANCE_DURATIONS = new Set([5, 10, 15])`; hàm thay duration dùng đúng tập đó.

## 3. Cơ chế "30 giây" thật sự = storyboard nhiều clip (system prompt)
`docs/khan_storyboard_prompt.json` là bản trích nguyên (JSON hợp lệ). Ý chính:
- Trợ lý đóng vai "Universal AI Storyboard and Video Generation Pipeline": hỏi thể loại, nhân vật, bối
  cảnh, giọng đọc, rồi **hỏi tổng thời lượng** (30/60/90/120/180 hoặc tuỳ chọn).
- Bước "Create Adaptive Clip Breakdown": trợ lý **tự quyết số clip và độ dài từng clip** theo "độ dài
  model hỗ trợ", tổng phải đúng bằng thời lượng đã chọn.
- "Mandatory clip continuity policy": khung cuối clip N phải khớp khung đầu clip N+1 (nhân vật, vị trí,
  máy quay, âm thanh); dùng ảnh tham chiếu đã có trong hội thoại.
- `require_approval_before_generation: true`: mặc định trợ lý **dừng chờ duyệt** storyboard rồi mới tạo
  từng clip; "Skip approval only when the user explicitly enables automatic generation".

Tức là extension không "mở khoá" gì: nó để **Dola tự làm 2×15s (hoặc 3×10s) trong một hội thoại**, người
dùng tải từng clip về. Không có bước ghép video trong extension.

## 4. Áp dụng cho tool của mình
Đường này khớp với phương án 1 trong `docs/NGHIEN_CUU_30S_2026-09-12.md` (ghép 2×15s), nhưng để **Dola giữ
liên tục giữa hai đoạn** thay vì mình cắt khung cuối làm ảnh tham chiếu. Việc cần làm nếu muốn:
1. Một prompt hệ thống rút gọn (bản Khan quá dài: 30 bước, hỏi người dùng ở nhiều bước; cần bản "tự động, không hỏi lại": tổng
   30s, chia đúng 2 clip 15s, không chờ duyệt, tạo lần lượt).
2. Worker theo dõi **nhiều video trong một hội thoại** (hiện dừng ở video đầu tiên), đợi đủ 2 clip.
3. Ghép bằng ffmpeg (đã có trong `watermark.py`), tính 2 lượt.
Chi phí: 2 lượt + ~2×5 phút mỗi video 30s. Rủi ro: trợ lý vẫn có thể hỏi lại giữa chừng; mối nối phụ thuộc
Dola giữ liên tục tốt đến đâu — cần thử 1 video trước khi làm giao diện.

## 5. Không nên bắt chước
- Cookie theo tab bằng DNR `modifyHeaders`: tool đã cách ly bằng profile Chrome riêng + proxy riêng, tốt hơn.
- "Watermark-free" của họ = tải `main_url` từ `/im/chain/single` — tool đã làm y hệt.
