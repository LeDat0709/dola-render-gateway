# Phân tích dola-30s-unlocker (v2.0) và cải tiến cho tool

## 1. Repo này là gì
Extension Chrome v2.0 mở khoá 15s/30s cho Dola/Doubao bằng **chèn `hook.js` vào MAIN world, hijack `window.fetch` + XHR** (không dùng `chrome.debugger`). Nó:
- Chặn `samantha/skill/pack` → trả skill-pack đã sửa (có 30s + `model_capability.seedance_v2.0.supported_durations=["5","10","30"]`).
- Vá `action_bar_v3/get_item_conf` → chèn nút 30s.
- Chặn `/im/chain/single` → tách link video.

README quan trọng: **xoá watermark kiểu web đã CHẾT** — Dola giờ burn **watermark "Dola AI" ĐỘNG (trôi toàn khung, dịch mỗi vài giây)** vào luồng video ở server. Họ tách riêng công cụ `dola-watermark-remover` (bám vết theo thời gian) để xoá.

## 2. So với tool của anh

| | Extension anh (dola30 v1.0) | Unlocker v2.0 | Chế độ fetch của anh |
|---|---|---|---|
| Mở 30s | `chrome.debugger` + Fetch.enable | hook.js hijack fetch (MAIN world) | Đặt thẳng `duration:30` vào body |
| Quyền | debugger, tabs, downloads | chỉ downloads | Không cần extension |
| Cửa sổ | **Bắt buộc hiện hình** (ép `launch_headless=False`) | headless được | **headless** |

**Kết luận:** với 30s, **fetch mode của anh đã hơn cả unlocker** — không cần extension, chạy headless, đặt `duration:30` là backend Dola chấp nhận (unlocker xác nhận `seedance_v2.0` hỗ trợ 30s ở backend, chỉ UI giấu). Đường UI + extension debugger chỉ còn cần cho **ảnh tham chiếu** và **2.5**.

## 3. Cải tiến nên làm
1. **Ưu tiên fetch cho 30s** — đã đúng (fetch chạy trước). Không phải bật extension, không cửa sổ.
2. **Nâng extension lên kỹ thuật hook.js** (tuỳ chọn, giá trị vừa): thay `chrome.debugger` bằng chèn `hook.js` hijack fetch. Khi đó đường UI (ảnh tham chiếu) **cũng chạy headless được**, bỏ ép cửa sổ hiện hình ở `browser.py:108`, không còn thanh "đang debug". Cần viết lại `extensions/dola30/service-worker.js` + thêm `hook.js` + `content-panel.js` inject.
3. **Cảnh báo watermark ĐỘNG (quan trọng):** `watermark.py` của anh xoá bằng **hộp cố định** (delogo x/y/w/h). Nếu Dola đã chuyển sang watermark **trôi toàn khung**, hộp cố định **không xoá hết**. Hiện anh đang tải bản `main_url` (không watermark) nên có thể chưa dính; nhưng nếu Dola burn watermark động vào MỌI luồng thì cần đổi sang bám vết theo thời gian (như `dola-watermark-remover`) hoặc inpaint theo khung. Cần kiểm tra 1 video 2.0 mới tải xem còn logo trôi không.

## 4. Việc cụ thể tôi có thể làm tiếp
- Viết lại extension theo kỹ thuật hook.js để đường UI chạy headless (bỏ debugger).
- Kiểm tra 1 video 2.0 vừa tạo xem watermark là cố định hay động, rồi chỉnh `watermark.py` cho đúng.
