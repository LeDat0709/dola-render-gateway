# BÁO CÁO KỸ THUẬT: CƠ CHẾ & LOGIC TẠO VIDEO DOLA (NO-CHROME PIPELINE)

> **Trạng thái:** Đã xác minh thực thi thành công ✅  
> **Tài liệu nguồn:** `submit_http.py`, `device_manager.py`, `video_worker_ui.py`, `a_bogus_web.c`  
> **Ngày cập nhật:** 2026-09-18

---

## 1. So Sánh: Đường Cũ vs Đường Mới

| Tiêu chí | Đường cũ (Browser Automation) | Đường mới (HTTP thuần) |
|---|---|---|
| **Chi phí RAM** | 0.5–1.5 GB / nick | ~30 MB |
| **Thời gian gửi lệnh** | 15–30s (chờ DOM) | < 1 giây |
| **Độ ổn định** | Crash, kẹt tab, đơ cửa sổ | Socket HTTP cực kỳ ổn định |
| **Năng lực mở rộng** | 2–4 nick đồng thời | Hàng trăm nick cùng lúc trên 1 VPS |
| **Chi phí CPU** | Cao (render DOM + JS) | Gần như bằng 0 |
| **Cần Chrome thật** | Bắt buộc | **Không** |

---

## 2. Ba Trụ Cột Bắt Buộc

### A. Chữ ký thuật toán `a_bogus`

**File:** `a_bogus_web.c` (Cython .so), `signer.py` (Python wrapper)

```
Query Params + Body JSON  →  nối "dhzx"  →  SM3(SM3(...))  →  RC4 biến thể
                                                                                  →  Base64 S4_ALPHABET (176 ký tự)
```

Code verify: `submit_http.py` gọi `signer.sign(query, body_json, aid, user_agent)` → URL signed.

**Lưu ý:** ByteDance xoay thuật toán ~mỗi quý. Test: `test_web_submit.py --send`.

### B. Bộ định danh thiết bị

**File:** `device_manager.py`

```python
def _deterministic_numeric_id(seed: str, length: int = 19, prefix: str = "7") -> str:
    """Sinh chuỗi số cố định từ seed (tên nick/máy), định dạng 19 ký tự bắt đầu bằng '7'."""
```

```python
# submit_http.py, build_query()
"device_id": dev.get("device_id", ""),
"web_id": dev.get("web_id", ""),
"tea_uuid": dev.get("tea_uuid", ""),
```

- `device_id`: **19 chữ số bắt đầu bằng `7`** (chuẩn ByteDance)
- `web_id`, `tea_uuid`: cùng format
- `tz_name`: **`Asia/Tokyo`** (khớp khu vực Dola xử lý)

### C. Dấu vân tay TLS giả lập Chrome

**File:** `submit_http.py`

```python
# signer.py: DEFAULT_AID = 495671 (verified, 497858 là SAI)
# UA thực tế dùng: Chrome/128.0.0.0 (a_bogus_web.c)
SUBMIT_IMPERSONATE = os.getenv("DOLA_SUBMIT_IMPERSONATE", "chrome124").strip() or "chrome124"
```

```python
# submit_http.py, curl_cffi.post()
impersonate=SUBMIT_IMPERSONATE, timeout=120
```

- Dùng **`curl_cffi`** với `impersonate="chrome124"` (mặc định, đổi qua env `DOLA_SUBMIT_IMPERSONATE`)
- Giả lập **100% JA3/JA4 fingerprint** giống Chrome thật
- Python thuần (`requests`, `aiohttp`) để lộ fingerprint OpenSSL → WAF từ chối ngay

> **Lưu ý:** `chrome124` ≠ UA `Chrome/128.0.0.0`. Khi nâng cấp UA, kiểm tra cả impersonate.

---

## 3. Payload — Directive Injection

**File:** `submit_http.py`

```python
if khan_mode:
    text = REPLY_FORMAT.replace("%s", prompt + (("、" + ratio) if ratio else ""))
else:
    orient = _ORIENT.get(ratio or "", "")
    spec = ((f"{int(duration)}秒" if duration else "")
            + (f"・アスペクト比{ratio}" + (f"（{orient}）" if orient else "") if ratio else ""))
    directive = (f"【この仕様で直接生成してください（{spec}）。長さ・比率は変更せず、追加の確認は不要です】\n"
                 if spec else "")
    text = directive + prompt
```

| Mode | Directive | Khác |
|---|---|---|
| **Khan (2.5, 30s)** | Không nhét `"30秒"` (né Dola hỏi lại) | `allow_free_queue=True`, `accept_queue=True` |
| **Thường (2.0, 5–15s)** | Có `【...】` ép thông số cứng | Normal flow |

```python
"chat_ability": {"ability_type": 17, "ability_param": json.dumps(ability, ensure_ascii=False), **khan}
```

```python
"need_create_conversation": True,
"conversation_id": ""  # tạo hội thoại sạch
```

- **`ability_type: 17`** — chế độ tạo video
- **`conversation_id: ""`** + **`need_create_conversation: True`** → tạo hội thoại sạch, không bị neo ngữ cảnh cũ

### Model key thật (verify từ `config.c`)

```python
MODEL_KEY_SEEDANCE20 = os.getenv("DOLA_MODEL_KEY_SEEDANCE20", "seedance_v2.0")
MODEL_KEY_SEEDANCE25 = os.getenv("DOLA_MODEL_KEY_SEEDANCE25", "seedance_v2.5")
```

⚠️ Format thật là **`seedance_v2.0`** (snake_case + underscore + version), KHÔNG phải `"Seedance 2.0"`. Nếu báo cáo ghi nhầm → developer khác copy vào code sẽ submit fail.

---

## 4. Vòng Đời 3 Bước

### Bước 1: Submit Lệnh (POST /chat/completion)

```
build_video_body(prompt)  →  build_signed(cookies, body)  →  curl_cffi.post(..., impersonate="chrome124")
                                                                              ↳ a_bogus signed URL
                                                              ↳ HTTP 200 + SSE ACK
                                                              ↳ ← { conversation_id: "38417932475091985", force_submit_review: 1 }
```

Thời gian: **~0.5–1 giây**

### Bước 2: Theo Dõi Ngầm (Poll /im/chain/single)

**File:** `video_worker_ui.py`

> "The browser is only needed for the signed submission; polling + download work with cookies alone (verified). This frees the browser in ~5s instead of holding it open for the whole render (up to 30 min for 30s)."

Endpoint **`/im/chain/single`** — **KHÔNG cần a_bogus**, chỉ cần:
- `Cookie` header
- `msToken`, `s_v_web_id` (từ cookies.json)

Trạng thái nhận diện qua `ext` của message:
- `force_submit_review: 1` → Dola đã trừ credit, đang dựng trên GPU
- `creation_list` xuất hiện → video đã xong

> ⚠️ **KHÔNG** dùng `/im/chain/send` cho submit. Repo chỉ có 2 endpoint:
> - Submit: `/chat/completion` (signed, cần `a_bogus`)
> - Poll: `/im/chain/single` (chỉ cookie)

**`conversation_type` & `limit` (tuỳ mode):**
- Poll video: `conversation_type=3, limit=20` (`POLL_IN_PAGE_JS`, `video_worker.py:38`)
- Poll text chat cũ: `conversation_type=1` (`dola_client.py:686`)

Khi debug, đảm bảo dùng đúng `conversation_type` cho từng mode.

Poll mỗi **5 giây**, tối đa **600s** (10 phút cho 30s Khan).

### Bước 3: Tải Video Không Watermark

**File:** `standalone_video_generator.py`

```python
decoded = base64.b64decode(main_url).decode("utf-8", "ignore")
if decoded.startswith("http"):
    bitrate = int(v.get("bitrate") or v.get("real_bitrate") or 0)
    candidates.append((bitrate, decoded))
...
candidates.sort(key=lambda x: x[0], reverse=True)
return candidates[0][1]   # main_url — bản gốc không watermark
```

| Trường | Ý nghĩa |
|---|---|
| `play_url` | Video xem trước (thường có logo Dola) |
| `main_url` | **Bản gốc chất lượng cao không watermark** ← Ưu tiên |

---

## 5. Kiến Trúc Chi Tiết (Từ Code Thực)

```
┌─────────────────────────────────────────────────────────────────┐
│                     NO-CHROME PIPELINE                           │
│                                                                  │
│  submit_http.py                                                  │
│  ┌──────────────────┐   ┌───────────────┐   ┌──────────────┐   │
│  │ device_manager   │   │ a_bogus_web  │   │ curl_cffi    │   │
│  │ (device_id 19s) │   │ (sign .so)   │   │ (TLS Chrome) │   │
│  └────────┬─────────┘   └──────┬───────┘   └──────┬───────┘   │
│           │                    │                   │            │
│           └──────── query + a_bogus + body ────────┘            │
│                          │                                      │
│              POST /chat/completion (signed)                     │
│                          │                                      │
│              ← SSE ACK: conversation_id                          │
│                          │                                      │
│  poll: /im/chain/single (cookie only, NO a_bogus)               │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Poll mỗi 5s → nhận diện creation_list → trích main_url    │ │
│  └────────────────────────────────────────────────────────────┘ │
│                          │                                      │
│              GET main_url → file MP4 không watermark             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. Lưu Ý Vận Hành

### Đã xác minh thực thi
- ✅ Video tạo thật trên máy chủ Dola (conversation_id `38417932475091985`)
- ✅ `force_submit_review: 1` xác nhận GPU render khởi động
- ✅ Directive injection ngăn Dola hỏi lại xác nhận thông số

### Cảnh báo
- ⚠️ **`a_bogus_web.c`** phải re-compile khi ByteDance xoay thuật toán (dấu hiệu: lỗi `param-error` / CAPTCHA tăng đột ngột)
- ⚠️ **`curl_cffi`** cần update lên Chrome version mới nhất (kiểm tra `DOLA_SUBMIT_IMPERSONATE`)
- ⚠️ **`msToken`** giả dễ bị chặn rate-limit (`710022002`). Ưu tiên dùng `msToken` thật từ cookies.
- ⚠️ **`/im/chain/single`** chỉ cần cookie nhưng proxy cần cùng IP lúc submit (Dola bind session ↔ proxy)

### Tuyên bố KHÔNG có trong code (chống ảo giác)

Một số báo cáo tham khảo ghi repo có:
- ❌ Tự động thêm "smooth motion keywords" (`slow steadycam`, `cinematic fluid motion`, `24fps`)
- ❌ Có endpoint `/im/chain/send` cho submit
- ❌ Có query param `app_name=samantha_web`

→ **Repo KHÔNG có** những tính năng này. User muốn smooth motion phải **tự viết** vào prompt.
