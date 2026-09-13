# Hướng Dẫn Quản Lý & Đăng Nhập Facebook Vào Dola Gateway

Hệ thống hỗ trợ 2 cơ chế tự động hoá Facebook:
1. **`facebook_login.py`**: Tự động đăng nhập Dola thông qua tài khoản / cookie Facebook (tự động OAuth, bắt popup, duyệt quyền, vượt age-gate và cấp phiên Dola để render video).
2. **`fb_login.py`**: Khởi chạy phiên trình duyệt Facebook độc lập để kiểm tra hoặc giữ phiên.

---

### 1. Tự Động Nạp Tài Khoản Facebook Vào Dola Gateway (`facebook_login.py`)

#### A. Qua Giao Diện Web Dashboard
- Truy cập Dashboard quản trị (`http://localhost:8000/` hoặc `http://127.0.0.1:8000/web`).
- Chọn tab **Accounts** -> Bấm nút **🔵 Thêm bằng Facebook**.
- Nhập tên tài khoản (ví dụ: `acc_fb1`) và dán dòng tài khoản định dạng:
  ```text
  UID|PASS|2FA|COOKIE|UA
  ```
  hoặc chỉ chuỗi cookie:
  ```text
  datr=...; c_user=...; xs=...; sb=...;
  ```
- Bấm **🚀 Bắt đầu OAuth Dola**: Hệ thống sẽ tự động khởi chạy tiến trình ngầm, kiểm tra checkpoint, kết nối Dola và kích hoạt tài khoản.

#### B. Qua Dòng Lệnh (CLI)
```bash
# Đăng nhập tự động bằng dòng tài khoản
.venv/bin/python facebook_login.py acc_fb1 "UID|PASS|2FA|datr=...;c_user=...;xs=...|Chrome"

# Mở cửa sổ trình duyệt để theo dõi trực quan (non-headless)
.venv/bin/python facebook_login.py acc_fb1 "c_user=...; xs=...; datr=...; sb=..." --visible

# Đọc dữ liệu từ file cookie
.venv/bin/python facebook_login.py acc_fb1 cookies_fb.txt

# Chạy thông qua Proxy
.venv/bin/python facebook_login.py acc_fb1 "UID|PASS|..." --proxy "http://user:pass@ip:port"
```

#### C. Qua App Desktop (Electron)
- Mở Dola Studio (`npm start --prefix desktop`).
- Ở phần **Thêm / đăng nhập nick**, bấm **🔵 Thêm bằng Facebook**.
- Dán cookie hoặc chuỗi tài khoản và bấm **🚀 Đăng nhập OAuth Dola**.

---

### 2. Các Lưu Ý Về Cookie Facebook
- **Bắt buộc**: Phải có `c_user` và `xs`.
- **Khuyên dùng**: Nên có đầy đủ `datr` và `sb` để tránh bị Facebook nghi ngờ thiết bị mới lạ và bắt đăng nhập lại mật khẩu.
- Nếu tài khoản Facebook đang bị checkpoint xác minh danh tính (*confirm you're human* / *confirm your identity*), tool sẽ phát hiện và thông báo ngay lập tức.
