# So sánh quản lý profile giữa các tool + cải tiến

| Tool | Cô lập profile theo nick | Proxy | Điểm yếu |
|---|---|---|---|
| **SuperTool 9.0.5** | ❌ **1 profile chung** (`chrome_profile_dola/`, một `Default/`) | 1 proxy | Mọi nick chung cookie/fingerprint; không chạy song song; 1 nick lỗi ảnh hưởng cả |
| **DomixHub** | ✅ `accounts/Acc N/` (Chromium persistent theo nick) | 1 proxy | Giống tool anh; bản này chưa đăng nhập được nick nào |
| **Seedance Studio** | ✅ Electron partition `persist:dola-<name>` | ✅ **proxy riêng mỗi nick + xác thực** (`proxy-phien.cjs`: socks5/http, user:pass) | Đi qua broker trả phí |
| **Tool của anh (trước)** | ✅ `accounts/<name>/` (patchright) + `persist:dola-<name>` (Electron) | ❌ **chỉ proxy chung** `DOLA_PROXY` | Nhiều nick chung 1 IP → dễ bị Dola gắn cờ; IP chết là chết cả pool |

## Đã thêm cho tool anh (bằng với Seedance)
- **Proxy theo nick**: `accounts/<nick>/proxy.txt` (nếu có) → dùng riêng cho nick đó; không có thì về `DOLA_PROXY` chung.
- Hỗ trợ định dạng: `http://host:port`, `socks5://host:port`, `user:pass@host:port`, `host:port:user:pass`.
- `import_seedance_export.py` tự ghi proxy từ field `proxy` của export.
- API admin: `POST /api/admin/accounts/<nick>/proxy` với `{"proxy":"..."}` (rỗng = về proxy chung).
- Đọc/parse đã unit-test đủ định dạng.

## Vì sao quan trọng
Dola theo dõi theo IP. Mỗi nick một proxy JP/KR riêng thì: (1) trông như thiết bị/địa điểm khác nhau → ít bị captcha/gắn cờ; (2) một proxy chết chỉ ảnh hưởng một nick. Đây là cách chạy nhiều nick chuyên nghiệp mà Seedance làm và tool anh vừa có.
