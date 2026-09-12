# Báo cáo test toàn bộ Dola Studio — 12/09/2026

Vai trò: tester. Phạm vi: backend (gateway), desktop (Electron), giao diện 5 tab (bản mới, commit `29d8a34`),
bản đóng gói macOS 1.0.9 và Windows v1.0.9. Máy test: MacBook Apple Silicon, 51 nick thật, gateway cổng 8000.

## 1. Test tự động — 12/12 bộ xanh

| Bộ test | Kết quả | Ghi chú |
|---|---|---|
| `test_api_smoke.py` | OK (36 ca) | API admin: nick, proxy, xuất/nhập kho, cấu hình, 422/404 |
| `test_handoff.py` | OK | bàn giao Chrome → HTTP, co prompt, học giá credit, tải lại video |
| `test_pool_meta.py` | OK | credit, reset ngày, lỗi mạng không giết nick, 710022002 dừng toàn bộ + leo thang |
| `test_restart_recovery.py` | OK | job dở khi server khởi động lại |
| `test_config_env.py` | OK | đọc `.env.local` |
| `test_cookie_parse.py` | OK (5 ca) | JSON, Netscape, pipe, `{tên: giá_trị}` của tool khác |
| `desktop/test-gateway.cjs` | 29/29 | vòng đời uvicorn: tạm dừng, bật lại, kill cây tiến trình |
| `desktop/test-remote.cjs` | 22/22 | chế độ máy chủ từ xa |
| `desktop/test-proxy.cjs` | 20/20 | phân tích chuỗi proxy |
| `desktop/test-bundle.cjs` | OK | file seedance-accounts → gói nhập kho, bỏ trùng, đổi tên |
| `desktop/test-datadir.cjs` | OK | thư mục dữ liệu riêng + dọn dữ liệu cũ |
| `deploy/push_nicks.py --selftest` | OK | đẩy nick lên VPS |

## 2. Gateway đang chạy (cổng 8000, 51 nick)

| Kiểm tra | Kết quả |
|---|---|
| `/health` | 200, có 51 nick, `available: true` |
| `/api/admin/accounts` · `/tasks` · `/config` · `/report` · `/accounts/export` | 200, mỗi cái dưới 0,2 s |
| Sai admin key | 401 (đúng) |
| Nhập cookie với tên nick `../x` | 400 (đúng, chặn đường dẫn lạ) |
| Kiểm tra phiên 1 nick thật (`POST …/verify`) | `ok: true` sau ~9 s (đo sáng nay với bản 1.0.8) |
| Cấu hình đọc được | 10 nick song song · giãn nhịp 3s + 0–3s · 4 video/nick/ngày · chờ 15 phút · xoay tối đa 3 |

## 3. Giao diện — thao tác thật trên từng tab (dev server nối gateway thật)

| Tab | Thao tác | Kết quả |
|---|---|---|
| Studio | bấm "Tất cả sẵn sàng" | chọn 2 nick, ô ước tính "Đã chọn 2 nick · cần 2 credit", nút chính "Chạy đã chọn (2)" bật |
| Studio | gõ 2 dòng prompt có từ "súng" | đếm "2 dòng · 42 ký tự", chip cảnh báo "Có từ dễ bị chặn" hiện |
| Studio | thẻ nick | 51 thẻ, timeline 5 bước, chip trạng thái, ô prompt riêng |
| Kho tài khoản | tìm "fb615936" | 60 → 13 dòng; xoá tìm kiếm về 60 |
| Kho tài khoản | tích 1 dòng | thanh "Đã chọn 1 nick" với 6 hành động hàng loạt |
| Proxy | mở tab | cảnh báo đỏ "Proxy chung đang gánh 51 nick (tối đa 5)", chip "Quá tải" |
| Cài đặt | mở tab | 4 khối Máy chủ / Mạng / Vận hành / Thư mục; Vận hành hiện đúng số từ server |
| Tổng quan | bấm "Hết lượt hôm nay" | chip "Lọc: Hết lượt hôm nay ✕", bảng lọc theo lỗi; bấm lần nữa bỏ lọc |
| Tổng quan | bấm video | hộp xem video mở, Esc đóng |

**Lỗi tìm thấy và đã sửa (commit `627e0bd`)**: khối Vận hành trong Cài đặt chỉ đọc cấu hình một lần lúc
app mở; server tự bật sau 3–4 giây nên khối này báo "server tắt — chưa đọc được" cho tới khi mở lại app.
Nay nạp lại mỗi lần mở tab. Đã kiểm chứng: sau sửa hiện "đọc từ server đang chạy".

Không thấy lỗi JavaScript nào trong console ngoài các 401 do trình duyệt poll trước khi có admin key
(chỉ xảy ra khi chạy ngoài Electron; trong app, khoá được nạp qua IPC trước khi poll).

## 4. Bản đóng gói

| Bản | Kết quả |
|---|---|
| macOS `dist-app/DolaStudio-1.0.9-mac-arm64.dmg` (582 MB) và `.zip` | build xong, python trong .app import được `browser.py`, ký ad-hoc hợp lệ |
| Windows Release v1.0.9 | `Dola.Studio.Setup.1.0.9.exe` và `DolaStudio-portable-1.0.9.exe` (~432 MB), CI xanh |
| Bản 1.0.8 chạy thật sáng nay | gateway tự bật sau 4 s, 46/46 nick còn phiên, đã render video |

### Lỗi "Dola Studio is damaged and can't be opened" (ảnh bạn gửi)

Nguyên nhân: app ký ad-hoc (không có chứng chỉ Apple Developer, `TeamIdentifier=not set`). File `.dmg`
tải bằng Chrome bị macOS gắn cờ quarantine; Gatekeeper gặp app có quarantine mà không notarize thì báo
"damaged" thay vì hỏi. Đây không phải file hỏng. Cách mở:

```bash
xattr -d com.apple.quarantine ~/Downloads/DolaStudio-1.0.9-mac-arm64.dmg
```

rồi mở `.dmg`, kéo app vào Applications. Nếu đã kéo app vào Applications rồi mới bị chặn:

```bash
xattr -dr com.apple.quarantine "/Applications/Dola Studio.app"
```

Lâu dài: mua Apple Developer ID (99 USD/năm) và thêm bước ký + notarize vào `scripts/build-mac.sh`
thì mọi máy mở được ngay. Bản `.dmg` copy trực tiếp giữa các máy (AirDrop, USB) không bị gắn quarantine.

## 5. Chưa test (nói rõ để bạn cân nhắc)

- Tạo video thật từ giao diện mới trong lần test này (tốn credit; đã chạy thật sáng nay qua bản 1.0.8 với
  cùng backend, video ra bình thường).
- Đăng nhập Facebook trong app, nạp cookie qua cửa sổ Electron (cần thao tác tay).
- Nhập kho file 130 nick của tool khác trên máy thật (đã test đơn vị và giao diện; mỗi nick 5–30 giây).
- Chế độ máy chủ từ xa với VPS (VPS chưa có proxy Nhật).
- Chạy bản Windows trên máy Windows thật (chỉ có CI build xanh).

## 6. Khuyến nghị

1. Chia 51 nick ra proxy riêng trước khi chạy đợt lớn — cảnh báo đỏ ở tab Proxy chính là nguyên nhân lỗi "gửi quá dày" hôm nay.
2. Gỡ quarantine như mục 4 khi cài từ file tải về; hoặc đầu tư chứng chỉ Apple để khỏi làm bước này.
3. Sau khi cập nhật Windows lên v1.0.9, dữ liệu cũ tự chuyển sang thư mục `Dola Studio`; kiểm tra lại số nick lần mở đầu.
