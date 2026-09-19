# PROBE 2026-09-18: a_bogus cục bộ còn khớp bản Dola không?

## Kết quả: VẪN KHỚP ✅

| | |
|---|---|
| **Phương pháp** | `test_web_submit.py --nick acc_test --send --prompt "verify-only ping"` (chat chữ — **MIỄN PHÍ**, không tốn credit) |
| **Thuật toán ký** | `a_bogus_web.long_a_bogus` (port từ `5201213/doubao-free-api pure_signer.py`) |
| **TLS/JA3** | `curl_cffi impersonate="chrome"` |
| **Proxy** | `hrxrwo3e:sPlF1d0i@74.118.172.70:39762` (data-center IP, tự lấy từ `account_proxy_url("acc_test")`) |
| **Kết quả** | `HTTP 200` + `event: SSE_ACK` + `conversation_id: 38416552401283857` |
| **Thời gian round-trip** | 3.71s (curl→server→SSE_ACK) |

### Bằng chứng từ response

```text
id: 0
event: SSE_HEARTBEAT
data: {}

id: 0
event: SSE_ACK
data: {"query_list":[{"question_id":"1471322467569937",
                      "local_message_id":"c72d2ebb-656d-4c52-9d2b-a84677315064",
                      "message_index":26}],
       "ack_client_meta":{"conversation_id":"38416552401283857",
                          "local_conversation_id":"local_1789742241278",
                          "conversation_type":1,
                          "section_id":"38416552401284113",
                          "conversation_info":{...}}}
```

→ Server đã **tạo conversation thật** (id không phải 0, có section_id, message_index=26 = nick đã dùng trước đó).
→ Nếu thuật toán ký lệch bản, server sẽ trả **"a_bogus invalid" / verify / slide** (xem khảo sát lỗi trong `dola_error_codes.py`).

## Ý nghĩa

1. **Không cần Chrome** cho bước submit video nữa — giữ nguyên cấu trúc `submit_http.py` hiện tại.
2. **`a_bogus_web.py` KHÔNG cần update** — port từ tháng 6/2026 vẫn đang được Dola chấp nhận.
3. **Test T1 đã đi đúng hướng** — bước tiếp theo là update `.env.local`:

   ```
   DOLA_SUBMIT_GAP=18.0
   DOLA_SUBMIT_JITTER=6.0
   ```

   + restart server + submit 1 video thật để verify end-to-end.

## Repo tham khảo đã cân nhắc

Khi user hỏi về `Tencent/BrowserSkill` — đã đánh giá và **LOẠI**:

| Repo | Đánh giá |
|---|---|
| `Tencent/BrowserSkill` (Youtu-Agent) | **Không liên quan** — dùng CDP để agent điều khiển browser (scrape/click); repo này chỉ cần fingerprint spoofing, không cần navigate. |
| `John-Y-Coder/python-tiktok-sign` | Inactive 2 năm, sign cho TIKTOK khác algorithm với Dola `a_bogus`. |
| `gzehua/X-Bogus` (JS) | JS port, chạy qua Node — chậm hơn Python port đang có. |
| `davidteathers/tiktok-anti-bot-bypass` | Bypass TikTok dashboard, không liên quan Dola. |

Repo `5201213/doubao-free-api` (upstream đã bị DMCA) **chỉ có giá trị tham chiếu** — port Python đã có sẵn trong repo này (`a_bogus_web.py`) và được xác minh còn khớp.

## Lệnh để tái hiện

```bash
cd /Users/dat/Downloads/dola-render-gateway
.venv/bin/python test_web_submit.py --nick acc_test --send --prompt "verify-only ping"
# → kỳ vọng: HTTP 200 + SSE_ACK + conversation_id
```

> **Cảnh báo:** Tái hiện sẽ tiêu hao 1 lượt quota chat-text của `acc_test`. Không tốn credit. Đã xác minh 1 lần 2026-09-18 21:34 ICT.

## Phạm vi & giới hạn

- Chỉ probe **chat text** (endpoint `/chat/completion`) — KHÔNG phải video.
- Chỉ test **1 nick** (`acc_test`) với **1 proxy** (data-center).
- Chưa test nick mobile/CC, chưa test khi rotate qua nhiều proxy trong cùng phút.
- Chưa test dưới tải (≥10 nick đồng thời, gap=18s).

## Cross-ref

- `a_bogus_web.py:279-358` → `long_a_bogus()` (algorithm chính)
- `signer.py:60-90` → `sign()` (wrapper, có auto-fallback Chrome)
- `test_web_submit.py:108-128` → `send()` (chuẩn bị request + impersonate)
- `docs/NO_CHROME_PIPELINE.md` → Bức tranh pipeline tổng quan
- `docs/TMPROXY.md` → Quản lý proxy IP rotation
- `test_video_safe_envelope.py` → Test T1/T3/T4 tự động (dùng `acc_test`)
