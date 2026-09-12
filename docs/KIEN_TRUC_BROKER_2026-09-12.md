# Kiến trúc broker kiểu Seedance cho tool của bạn — đã dựng — 12/09/2026

Ánh xạ đúng mô tả bạn đưa. **Toàn bộ phần khách hàng + kho proxy đã dựng và test xong**; chỉ phần
backend render trả phí là chờ bạn có API key (không phải code).

## 1. Khách không nối thẳng nhà cung cấp AI — POST /v1/job

| Mô tả của bạn | Đã dựng |
|---|---|
| Khách không nối thẳng ByteDance/Seedance/Kling | Client gọi broker, không chạm Dola trực tiếp |
| Mọi lệnh render (prompt, ratio) gửi qua `POST /v1/job` | `POST /v1/job` (broker.py) nhận `{model, duration, ratio, prompt}` |
| Server giữ API Key / cookie gốc, request giùm, trả kết quả | `/v1/job` dùng pool nick + cookie sẵn trên server; khách không thấy nick nào |
| Poll + báo tải | `GET /v1/job/{id}`, `POST /v1/job/downloaded` |

Khách chỉ cần **grant** (vé job), không thấy nick/cookie/backend. Đã kiểm chứng đầu-cuối.

## 2. Ký số chống can thiệp (Signing Server)

| Mô tả | Đã dựng |
|---|---|
| Ký số để client không giả mạo | Grant ký **HMAC-SHA256**: chỉ broker ký được (`sign_grant`), client bịa grant → chữ ký sai → 401 |
| Khoá công khai để client kiểm | `GET /v1/pubkey` trả vân tay khoá |

Đã thử: không grant → 401, grant giả → 401, grant sửa ruột → 401, grant hết hạn → 401. (Đối thủ dùng
Ed25519 bất đối xứng; nội bộ dùng HMAC là đủ và không phải cài thêm thư viện. Muốn mở API ra ngoài công
khai thì nâng lên Ed25519 sau — một hàm.)

## 3. Kho proxy tập trung (Proxy Pool)

| Mô tả của bạn | Đã dựng |
|---|---|
| Định dạng host:port:user:pass, host:port, socks5://… | `POST /api/admin/proxies` nhận cả ba |
| Admin kiểm tra tự động 8 luồng, 15s/proxy, lọc chết | `POST /api/admin/proxies/check` (8 luồng, 15s), `POST /prune` xoá chết |
| Mật khẩu không bao giờ lộ ra API | `mask_proxy` — API chỉ trả `user:***@host`, kho gốc giữ riêng |
| Tự cấp proxy sạch cho máy khách chưa mua proxy | `POST /api/admin/proxies/assign` chia proxy sống cho nick chưa có (vòng tròn, tối đa N/IP) |

Có giao diện đầy đủ trong tab **Proxy → Kho proxy tập trung**: dán proxy, Kiểm tra kho, Xoá proxy chết,
Chia cho nick — chạy cả khi nối VPS từ xa.

## 4. Proxy chung + đặt từ app

`POST /api/admin/global-proxy` đặt proxy chung của server ngay lúc chạy (app ở chế độ VPS đặt được từ
giao diện, không cần SSH sửa .env.local).

## 5. Còn thiếu gì để "mượt như họ"

Một thứ duy nhất, và **không phải code**: **backend render trả phí**. Hiện `/v1/job` render qua nick free
(15 giây, Dola free). Muốn 30 giây mượt và không dính rate-limit thì trỏ `/v1/job` sang **API Seedance 2.5
trả phí** (BytePlus ModelArk / Volcengine / fal.ai) — đã chừa sẵn chỗ cắm (`ctx.create_video` trong
broker.py), đổi một hàm là xong khi bạn có API key.

Điểm khác nhỏ so với đối thủ (nội bộ chưa cần):
- Heartbeat cấp proxy **liên tục** cho từng máy khách: hiện làm **theo yêu cầu** bằng nút "Chia cho nick"
  / endpoint `/assign`. Muốn tự động định kỳ thì đặt một cron gọi `/assign` mỗi vài phút.
- Attestation băm app (chống chạy app sửa): nội bộ chưa cần; thêm khi bán ra ngoài.

## Toàn bộ endpoint đã có

```
POST /v1/job-grant · GET /v1/pubkey · POST /v1/job · GET /v1/job/{id} · POST /v1/job/downloaded
GET/POST /api/admin/proxies · POST /api/admin/proxies/check · /prune · /assign · DELETE /{id}
POST /api/admin/global-proxy
```

Tất cả nằm sau v1.0.12 — chạy từ mã nguồn (npm start / uvicorn) là có ngay; bản cài cần tag v1.0.13.
