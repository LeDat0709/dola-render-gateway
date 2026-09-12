# Tra cứu cách tạo video Dola trên mạng — để cải tiến tool — 12/09/2026

Tổng hợp sau khi tìm web + đọc mã repo. Xếp theo giá trị cải tiến.

## 1. BỎ CHROME KHỎI BƯỚC GỬI — a_bogus thuần Python (giá trị cao nhất)
Vấn đề hiện tại: mỗi nick mở một Chrome (~0.4GB) chỉ để `window.bdms.frontierSign` tính a_bogus cho
`/chat/completion`. Đường Android (X-Gorgon) đã thử và CHẾT (710022004, CAPTCHA slide — xem memory
dola-request-signing). Nhưng có bản a_bogus **thuần Python cho đúng cổng web**:

- **5201213/doubao-free-api** (`pure_signer.py`, 392 dòng, đã tải về scratchpad `pure_signer_ref.py`):
  hàm `long_a_bogus(query_string, body, opts)` tự tính a_bogus bằng SM3 + RC4 + custom base64, KHÔNG cần
  trình duyệt. `aid` là tham số (`opts.aid`, mặc định 497858 = doubao.com CN) → truyền `495671` cho Dola.
  File `signer.py` cùng repo là bản browser (`window.bdms.frontierSign`) — xác nhận cùng thuật toán bdms.
- Cùng họ thuật toán web ByteDance mà Dola dùng. Đây là ĐÚNG cổng (`www.dola.com/chat/completion`, aid
  495671) mà nick web của mình đang dùng, khác hẳn cổng app Android đã bị chặn.

**Việc thử (rủi ro trung bình, ~1 buổi):** port `long_a_bogus` vào repo, ghép a_bogus + msToken vào
`build_query_params`, gửi 1 job 10s bằng aiohttp (không Chrome), xem Dola nhận không. Nếu nhận → bỏ Chrome
khỏi submit, cả pipeline thành HTTP (poll+download vốn đã HTTP). Rủi ro: thuật toán a_bogus ByteDance
**xoay ~mỗi quý**; bản repo push 09/06/2026 có thể lệch bản bdms hiện tại của Dola → phải so lại phiên bản.
Cách kiểm nhanh: lấy 1 a_bogus thật từ trang (đang có sẵn khi mở Chrome) và a_bogus tính từ `long_a_bogus`
với cùng input, so khớp trước khi gửi thật.

Repo a_bogus khác (tham khảo, không dùng trực tiếp): itsharex/doubao-api-free (signer Java),
lza6/doubao-2api (Playwright — như mình), wangchuxiaoji-oss/doubao2api (Playwright fetch-hook).

## 2. 30 GIÂY THẬT — chỉ Seedance 2.5 qua API trả phí
- Seedance **2.0** (bản Dola web/free đang dùng): duration **4–15s**, mặc định 5s. 30s KHÔNG có ở 2.0.
  Khớp với kết luận đã có: Dola tắt 30s server-side vì nó là tính năng 2.5.
- Seedance **2.5**: ra 31/07/2026, **30s một lần quay**, tới 50 tham chiếu (30 ảnh/10 video/10 audio),
  sửa theo mốc thời gian. Có trên Dreamina/Doubao Pro (trả phí), API còn pre-release.
- **Đường sạch cho 30s = Volcano Ark / BytePlus ModelArk** (mô hình `dreamina-seedance-2-5-260628`), kiểu
  async: submit job → poll → tải. Giá theo giây/độ phân giải: ~$0.10 (480p) → ~$0.30 (720p) → ~$0.59 (1080p)/giây.
  Cắm vào `broker.py` `/v1/job` (đã chừa chỗ `ctx.create_video`) khi có API key. Đây là backend trả phí
  mà `StevePan-912/doubao-video-maker` (MIT) đã làm interface — cóp được.
- Đường miễn phí cho "30s": ghép nhiều clip 15s bằng storyboard (xem KHAN_30S_ANALYSIS.md).

## 3. Payload video tham chiếu (đối chiếu chế độ fetch của mình)
- **wangchuxiaoji-oss/doubao2api** (225★, Apache-2.0): reverse đầy đủ video — `POST /samantha/chat/completion`
  `content_type=2020` → nhận `content_type=2021` (SamanthaVideoGenerationOutput) → URL video; có `ref_image_key`
  cho img2video, `duration`. Xác nhận Seedance 2.0 ~10 lượt free/ngày. Chữ ký vẫn qua Playwright fetch-hook.
- Mẹo phụ trong repo đó: `/v1/files` upload file ≤1GB lấy TOS URI vĩnh viễn → kênh chuyển file giữa máy
  miễn phí (không cần OSS). Có thể dùng cho việc đẩy nick/cookie giữa máy.

## 4. Watermark
- `LauZzL/doubao-downloader` (1.6k★): tải không watermark qua đường aispace (homepage → node_info →
  get_download_info.main_url), khác get_play_info đã bị đóng watermark.
- `ihmily/doubao-nomark`, `chenwenhe12/Dola-Image-Downloader`: ảnh không watermark.
- Nhắc lại: Dola burn watermark ĐỘNG vào video 2.x server-side (hope0719 xác nhận mọi link play_info có
  watermark từ 07/2026) → hộp delogo cố định trong `watermark.py` có thể không xoá hết.

## 5. Việc nên làm, theo thứ tự
1. **Port `long_a_bogus` + so a_bogus tính-cục-bộ với a_bogus-thật** (mục 1). Nếu khớp và Dola nhận → bỏ
   Chrome khỏi submit. Đây là cải tiến lớn nhất.
2. Khi có ngân sách: cắm **Volcano Ark Seedance 2.5** vào broker cho 30s thật (mục 2).
3. Kiểm 1 video 2.0 mới xem còn watermark động không, sửa `watermark.py` nếu cần (mục 4).

Nguồn: dolaseed.ai, digitalapplied.com (Seedance 2.5 30s), BytePlus/Volcano Ark docs, wan27.org &
atlascloud.ai (giá 2.5), GitHub: 5201213/doubao-free-api, wangchuxiaoji-oss/doubao2api, itsharex/doubao-api-free,
lza6/doubao-2api, LauZzL/doubao-downloader.
