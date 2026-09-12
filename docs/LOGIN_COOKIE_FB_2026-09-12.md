# Cơ chế đăng nhập bằng cookie Facebook — đối thủ vs tool này, và cách login nhanh hơn

Nguồn đối thủ: `Seedance AI Studio v1.0.48/resources/app_extracted/electron/nick-login.cjs` (đã giải mã
bảng chuỗi obfuscator — 292 chuỗi, giải sạch). Bản v1.0.72 khoá mật khẩu nên chỉ đọc được tên module.

## 1. Đối thủ làm gì (nick-login.cjs)

Luồng "đăng nhập bằng cookie FB" của họ:

1. Mỗi nick một `session.fromPartition('persist:...')` riêng (giống tool này).
2. `ganCookieFb`: nạp cookie Facebook vào `.facebook.com`, hạn dùng **120 ngày** (`now + 0x15180*0x78`).
3. Gọi backend Python `/api/login-url` để lấy URL đăng nhập Dola, mở trong một `BrowserWindow`.
4. **Thu cookie theo SỰ KIỆN, không chờ cứng**: gắn `thuHoachCookieWeb` vào cả `did-finish-load`,
   `did-navigate`, `did-navigate-in-page`, `did-stop-loading`, **cộng một `setInterval` mỗi `KIEM_MS`
   = 2000 ms**. Mỗi lần kích hoạt, đọc cookie `dola.com`, nếu có `sessionid` thì `laySessionid` = true
   → chờ thêm 800 ms rồi đọc lại để chắc, và kết thúc ngay.
5. `JS_XAC_NHAN_TUOI` (xác nhận 18+) chạy trên MỌI frame; `jsDienLogin` tự điền email/mật khẩu khi
   Facebook đòi đăng nhập lại, `taoMa2fa` sinh mã 2FA.
6. Trần an toàn: `HAN_MS` = 10 phút tổng, `MAX_NHAM_OAUTH` = 3, `MAX_PHIEN_LOGIN` = 8, `MAX_PHIEN_COOKIE` = 8.

Điểm cốt lõi khiến họ **nhanh**: kết thúc ngay khoảnh khắc `sessionid` xuất hiện (bám sự kiện điều hướng +
poll 2 giây), **không có `sleep` cố định dài**.

## 2. Tool này đang làm gì

Có HAI đường dùng cookie:

| Đường | Hàm | Tốc độ | Khi nào |
|---|---|---|---|
| **Nhập kho / dán cookie Dola** | `cookie_service.apply_cookies_to_account` | **~3 giây** | cookie đã có passport Dola (sessionid…) |
| **Thêm bằng Facebook (OAuth)** | `facebook_login.add_account_via_facebook` | **~30–40 giây** | chỉ có cookie Facebook, chưa có phiên Dola |

- Đường **Nhập kho đã NHANH HƠN đối thủ**: nạp cookie thẳng vào profile rồi kiểm tra phiên qua HTTP,
  không mở OAuth, không sleep. Đây là đường "Nhập kho 4 nick cùng lúc" vừa làm.
- Đường **OAuth thì CHẬM**: `facebook_login.py` chờ **cứng 32,4 giây** bằng `wait_for_timeout` rải khắp
  (5000 + 3500 + 3000 + 2×2500 + 2000 + … ms) bất kể trang tải nhanh hay chậm. Đây chính là chỗ đối thủ
  hơn — họ bám sự kiện, mình ngồi đếm giờ.
- Đường Electron (`main.js` `harvestDolaSession`) **đã đúng hướng**: poll `sessionid` mỗi `LOGIN_POLL_MS`
  và thoát ngay khi có. Chỉ đường Python OAuth là còn chờ cứng.

## 3. Đã cải tiến (đường OAuth Python)

Thay `wait_for_timeout(5000)` cố định sau khi mở Dola bằng vòng poll `sessionid` mỗi 500 ms, thoát ngay
khi thấy phiên — giống cơ chế bám sự kiện của đối thủ. Nick nào profile đã sẵn phiên Dola (phần lớn khi
dán lại cookie) rút ngắn ~5 giây mỗi nick; nick chưa có phiên không chậm đi. Chi tiết trong
`facebook_login.py` (`_wait_sessionid`).

## 4. Khuyến nghị dùng cho nhanh nhất

1. **Ưu tiên "Nhập kho"** cho file/cookie đã có passport Dola — 3 giây/nick, 4 nick song song. Đây là
   đường nhanh nhất, nhanh hơn cả đối thủ.
2. Chỉ dùng "Thêm bằng Facebook" (OAuth) khi chỉ có cookie Facebook trần. Sau cải tiến, mỗi nick đã sẵn
   phiên rút ~5 giây.
3. Có thể tăng `DOLA_LOGIN_CONCURRENCY` (mặc định 3) nếu RAM dư — mỗi cửa sổ Chrome ~0,4 GB.
