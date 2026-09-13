# Phân tích Seedance AI Studio v1.0.48 — login, lưu cookie, tạo video HTTP

Nguồn: `resources/app/electron/*.cjs` (đọc được, bị làm rối tên biến — đã giải mã mảng chuỗi),
`resources/python/*.pyd` (Cython, chỉ đọc được TÊN hàm), `_dev/engine_sniffer.py`.
So sánh với repo `dola-render-gateway` của anh.

## 1. Tạo video qua HTTP — KHÁC hẳn tool của anh

Seedance **không gọi thẳng Dola**. Nó đi qua broker trả phí:

```
Renderer → backend nội bộ /api/jobs  (Cython server.pyd, header X-Seedance-Local-Secret)
        → https://api.seedancevip.top/v1/job   (ký Ed25519 job-grant + HMAC + attestation app.asar)
        → broker gọi Dola thật (veomedia.top) → render → /v1/kho/tai tải video về
```

Endpoint engine: `/v1/pubkey · /v1/job-grant · /v1/job · /v1/job/{id} · /v1/job/downloaded · /v1/kho/tai · /v1/login-url · /v1/check-account`.
Body thật của `/v1/job` **không đọc được** (backend Cython, key renderer bị obfuscate) — chỉ bắt được khi chạy `_dev/engine_sniffer.py` trên Windows.

**Kết luận:** không có "logic gọi Dola HTTP trực tiếp" để copy — phần gọi Dola nằm trong máy chủ của broker. **Tool của anh đang đi trước ở điểm này**: chế độ `fetch` của anh gọi thẳng `dola.com/chat/completion` (ký bằng bdms trong trang), không phụ thuộc broker trả phí nào.

## 2. Đăng nhập — CÙNG cách tiếp cận tôi đã dựng cho anh, có 2 thứ hơn

Seedance login (`nick-login.cjs`) chạy trong Electron:
- `session.fromPartition('persist:...')` mỗi nick một partition riêng — **giống tool anh**.
- Nạp cookie FB vào `*://*.facebook.com/*`, `*://facebook.com/*`, `*://*.facebook.net/*`, hâm phiên — **giống**.
- Bấm icon Facebook trong hộp đăng nhập Dola **bằng hình học**: tìm nút "Continue with Google" lớn, rồi hàng icon [điện thoại, Facebook, Apple] ngay dưới, chọn Facebook — **đúng cách tôi vừa làm cho anh**.
- Xử lý popup OAuth, tự xác nhận 18+ (`confirm your age / least 18 / đủ 18` → bấm Confirm/Đồng ý) — **giống**.

**Hai thứ Seedance có mà tool anh CHƯA có (đáng port nhất):**

1. **Tự điền form đăng nhập Facebook** (`jsDienLogin`): khi cookie chết và Facebook đòi login, nó tự điền email/mật khẩu từ dòng `uid|pass|2fa|...`, bấm nút login, và **phát hiện "sai mật khẩu"** (`incorrect password / không tìm thấy tài khoản`). Đây chính là chỗ anh đang phải làm tay mỗi lần cookie chết.
2. **Tự sinh mã 2FA (TOTP)** cho nick Facebook có khoá 2fa (`taoMa2fa`, chuẩn RFC 6238 y như `add_account.py` của anh dùng cho Google) — điền mã khi Facebook hỏi 2 lớp.

Nó cũng phân loại lỗi rõ: `checkpoint`, `region-restricted` (Dola chặn vùng IP/proxy → gợi ý đổi proxy), `2FA bị từ chối`, `c_user không khớp UID`, `cookie hết hạn`.

## 3. Lưu cookie — bắt đủ bộ passport, không chỉ sessionid

Seedance thu và lưu **cả bộ cookie phiên Dola**:
`sessionid`, `sessionid_ss`, `sid_guard`, `sid_ucp_v1`, `ssid_ucp_v1`, `sid_tt`, `uid_tt`.
Tool anh (hàm `harvestDolaSession`) thu **mọi cookie `dola.com`** nên đã bao trọn bộ này — tương đương. Điểm anh hơn: còn lấy thêm cookie ký request `msToken`, `s_v_web_id` (Seedance không cần vì broker ký hộ).

## 4. Nên port gì vào tool của anh

| Từ Seedance | Giá trị | Ghi chú |
|---|---|---|
| **Tự điền login Facebook** (email/pass, phát hiện sai mật khẩu) | Cao — hết cảnh gõ tay khi cookie chết | Thêm vào luồng cửa sổ Electron: khi popup/thẻ vào `login.php`, tự điền từ dòng `uid|pass` |
| **2FA TOTP cho Facebook** | Cao | `add_account.py` đã có hàm `totp()` — tái dùng, điền khi FB hỏi 2 lớp |
| Phân loại lỗi region/checkpoint/2FA rõ | Vừa | Thông báo tiếng Việt cụ thể hơn |
| Per-nick proxy | Vừa | Anh đã có `DOLA_PROXY` toàn cục; per-nick là bước sau |
| Gọi Dola HTTP trực tiếp | — | Anh đã hơn (chế độ fetch), Seedance phụ thuộc broker trả phí |

---

## 5. Cập nhật v1.0.59 (phân tích 2026-09-10)

**File `.rar` v1.0.59 KHÓA MẬT KHẨU** (RAR5 AES, mọi entry `Encrypted = +`) — không mở được
để đọc trực tiếp. Nhưng so header với bản v1.0.48 (đã giải nén đầy đủ trong `~/Downloads`),
thay đổi rất nhỏ và **kiến trúc y hệt** (vẫn qua broker trả phí `api.seedancevip.top`, không gọi Dola thẳng).

Khác biệt v1.0.48 → v1.0.59 (theo kích thước file trong header):
| File | Thay đổi | Ý nghĩa |
|---|---|---|
| `python/core/job_manager.pyd` | +48 KB | Worker render — thêm nhánh xử lý lỗi/xoay nick (biên dịch, không đọc được logic) |
| `python/core/account_store.pyd` | +16 KB | Kho nick — thêm trường/logic |
| `python/server.pyd`, `routes/api.pyd` | +11 KB / +5 KB | Local API |
| `python/core/api_client.pyd` | +5 KB | Client ký số |
| **MỚI** `core/chuoi_ui.pyd` + `dist/chuoi.bin` + `dist/chuoi-mo.json` | mới | **Lớp che chuỗi mới** (string table tách rời) — chống đọc UI/thông báo. Không thêm tính năng người dùng |
| `dist/assets/index-*.js/.css` | build lại | Frontend rebuild |

**Kết luận:** v1.0.59 không có tính năng gọi-Dola mới để copy. Điểm đáng port nhất vẫn là
**tự đăng nhập Facebook** (jsDienLogin/jsDien2fa) — nay đã port (xem mục 6).

## 6. ĐÃ PORT: tự đăng nhập lại Facebook khi cookie chết

Trước đây `facebook_login.py` **bó tay** khi cookie Facebook hết hạn: dù dòng tài khoản có
`uid|mật khẩu|2fa`, nó vẫn ném lỗi "cookie hết hạn, đăng nhập tay". Nay tự chữa:

- Hàm mới `_fb_autofill_login(page, cred, step)` — port từ `nick-login.cjs`:
  điền email/mật khẩu, bấm login, phát hiện **"sai mật khẩu"**, tự sinh **mã 2FA (TOTP)**
  bằng `add_account.totp` khi Facebook hỏi 2 lớp, bấm qua màn "lưu trình duyệt".
- Gọi ở 2 chỗ từng bó tay: nhánh warm-check `facebook.com/me` báo login, và popup OAuth vào `login.php`.
- Phân biệt **2FA (điền mã)** với **checkpoint xác minh danh tính** (không tự qua được → báo rõ).
- Self-check: `python test_fb_autofill.py` (chạy trong `.venv`).

## 7. ĐÃ PORT (bổ sung 2026-09-10): tự đăng nhập Facebook trong luồng ELECTRON

Mục 6 mới port vào `facebook_login.py` (đường Chrome/patchright). Nay port tiếp vào luồng
**Electron** — đường mặc định, nhanh, mà app đang dùng (`desktop/main.js`, handler
`account:importFacebookElectron`). Trước đây khi popup OAuth vào `login.php`/checkpoint nó chỉ
**cảnh báo, chờ gõ tay 5 phút**; nay tự chữa:

- `parseFacebookCred(line)` — tách `uid | mật khẩu | 2fa(base32)` từ dòng tài khoản
  (chấp nhận cả `uid|pass|2fa|cookie|ua` lẫn `uid|pass|cookie|token|email|app1=;`).
- `JS_FB_FILL_LOGIN` / `JS_FB_FILL_2FA` — mirror jsDienLogin/jsDien2fa: điền email/mật khẩu,
  phát hiện "sai mật khẩu", điền mã 2FA.
- `fbTotp()` — TOTP RFC 6238 bằng Node crypto, khớp `add_account.totp` (đã test trùng số).
- `fbAutofillStep()` gọi trong vòng lặp OAuth, cho cả popup lẫn cửa sổ chính khi FB điều hướng
  toàn trang. Sai mật khẩu → dừng và báo rõ; checkpoint danh tính / thiếu mã 2FA → báo xử lý tay.
- Self-check: `parseFacebookCred` + JS builders + `fbTotp` (chạy trong node, PASS).

Giờ cả hai đường (Chrome tự động và cửa sổ app) đều tự đăng nhập lại khi cookie chết.
