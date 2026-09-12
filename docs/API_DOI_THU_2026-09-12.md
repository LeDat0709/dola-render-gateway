# Nghiên cứu API đối thủ — api.seedancevip.top (Seedance AI Studio) — 12/09/2026

Cách dò: chỉ GET/OPTIONS công khai (không gửi job, không thử vượt xác thực) + giải mã module & file dev
họ để sót trong bản v1.0.48. Bản v1.0.72 khoá mật khẩu nên không đọc thêm được mã.

## 1. Kiến trúc 4 lớp

```
App Electron (client) ──X-Seedance-Local-Secret──> Python backend NỘI BỘ (Cython .pyd, chạy trên máy khách)
                                                          │
                                                          ▼  (ký + attestation)
                                            api.seedancevip.top  (LICENSE + JOB BROKER)
                                                          │  app "seedance-license", cổng 17900, Caddy sau Cloudflare
                                                          ▼
                                                    engine render thật → trả video
```

- Client KHÔNG gọi thẳng Dola. Nó gọi backend Python nội bộ trên máy khách, backend đó mới gọi
  `api.seedancevip.top`. File dev họ để sót (`_dev/engine_sniffer.py`) ghi rõ: `UPSTREAM =
  "https://api.seedancevip.top"` là "engine thật", sniffer chuyển tiếp mọi `/v1/*`.
- `veomedia.top` là trang bán hàng ("Veomedia | Chatbot, Workflow, Auto App", đầy nút mua/bán) — nơi
  họ bán license/credit, không phải endpoint kỹ thuật.

## 2. Bản đồ endpoint (api.seedancevip.top)

| Endpoint | Method | Trả về khi chưa có license |
|---|---|---|
| `/health` | GET | `{ok:true, app:"seedance-license", port:17900, signing:true}` — **công khai** |
| `/v1/pubkey` | GET | `{ok:true, key:"iBmjkAlrg2Gs…TJ5b3I="}` — **khoá công khai Ed25519 (32 byte base64)**, công khai |
| `/v1/job-grant` | POST | cấp "giấy phép job" đã ký; GET → 404 (chỉ POST) |
| `/v1/job` | POST | nhận job tạo video; GET → 404 |
| `/v1/job/{id}` | GET | trạng thái job → `401 {ok:false, loi:"Chưa kích hoạt"}` |
| `/v1/job/downloaded` | POST | báo đã tải xong → 401 |
| `/v1/login-url` | GET/POST | lấy URL đăng nhập Dola/Facebook → 401 |
| `/v1/check-account` | POST | kiểm tra tài khoản; GET → 404 |
| `/v1/kho/tai` | POST | tải "kho" video; GET → 404 |

Không có `/docs`, `/openapi.json`, `/v1/version`, `/v1/activate`, `/v1/me` (đều 404) — API đóng, không
phơi tài liệu.

## 3. Cơ chế bảo mật (vì sao KHÔNG dùng ké được)

Ba lớp khoá, phải qua cả ba mới gọi được `/v1/job`:

1. **Kích hoạt license**: thiếu license mọi endpoint trả `401 "Chưa kích hoạt"`. License bán ở veomedia.top.
2. **Chữ ký Ed25519**: server ký "job-grant", client kiểm bằng `/v1/pubkey`. Không có khoá riêng của họ thì
   không tự tạo grant hợp lệ.
3. **Attestation ứng dụng**: `attestation.pyd` băm SHA-256 `app.asar` (manifest hash) + `nonce`. Server chỉ
   cấp grant cho đúng app gốc chưa bị sửa. Sửa app hay gọi bằng script lạ → attestation sai → bị từ chối.

**Kết luận thẳng:** `api.seedancevip.top` là dịch vụ TRẢ PHÍ RIÊNG của họ, khoá bằng license + chữ ký +
attestation. Bạn **không dùng API của họ được**. Muốn đọc payload thật của `/v1/job` (model, duration,
prompt) phải mua license, chạy app thật trên Windows rồi MITM bằng chính `engine_sniffer.py` của họ — và
kể cả vậy, engine render nằm sau hệ thống grant của họ.

## 4. Cái đáng học: HÌNH DẠNG để tự dựng broker của mình

Nghiên cứu này cho bạn bản thiết kế, không phải chìa khoá. Broker của bạn (nếu làm) nên có:

- `GET /v1/pubkey` + ký grant Ed25519 (chống client giả).
- `POST /v1/job-grant` → cấp vé job có hạn.
- `POST /v1/job` → nhận {model, duration, ratio, prompt} → gọi backend render.
- `GET /v1/job/{id}` → poll trạng thái.
- `POST /v1/job/downloaded` → trừ credit khi tải.
- Kích hoạt license theo key; trạng thái "Chưa kích hoạt" cho ai chưa mua.

Gateway của bạn ĐÃ có sẵn phần lớn: cổng job `/v1/videos/generations`, `api_key_hash`, `daily_limit`,
`concurrency_limit`. Chênh duy nhất là **backend render**: họ trỏ vào engine trả phí (Seedance 2.5 API →
30s mượt). Bạn thay chỗ đó bằng API Seedance 2.5 trả phí (BytePlus ModelArk / Volcengine / fal.ai) là ra
đúng mô hình họ, mà không cần chép dòng code nào của họ.
