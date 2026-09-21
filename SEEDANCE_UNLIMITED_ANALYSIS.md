# Phân tích Seedance Unlimited v1.1.8 (macOS) — 20/09/2026

Nguồn: `SeedanceUnlimited_v1.1.8_macOS.dmg`, `com.seedance.unlimited`.
Backend là **Python thuần, KHÔNG làm rối** ở `Contents/Resources/backend/` — đọc thẳng được:
`engine/video.py` (140 KB), `profiles/manager.py` (48 KB), `proxy/manager.py` (24 KB), `main.py` (34 KB).
Phụ thuộc: `fastapi`, `uvicorn`, **`playwright 1.52.0`** (bản thường, KHÔNG phải patchright), `httpx`.

## 1. Cách mở trình duyệt (`profiles/manager.py:598`)

```python
chrome_cmd = [chrome_exe, f"--user-data-dir={user_data_dir}", "--profile-directory=Default",
              f"--remote-debugging-port={cdp_port}", "--remote-allow-origins=*",
              "--enable-unsafe-extension-debugging", "--no-first-run", "--no-default-browser-check",
              "--window-position=9999,9999", "--window-size=1365,900"]
subprocess.Popen(chrome_cmd) → p.chromium.connect_over_cdp(...)
```

**Điểm đáng học nhất: `--window-position=9999,9999`.** Họ KHÔNG chạy headless — họ mở Chrome có cửa sổ thật
rồi **đẩy cửa sổ ra ngoài màn hình**. Người dùng không thấy gì, nhưng trình duyệt là headed 100%, không mang
bất kỳ dấu vết headless nào. Tool mình hiện không có cờ này (`LAUNCH_ARGS` chỉ có `--no-first-run`,
`--no-default-browser-check`).

Phần còn lại giống hệt chế độ `DOLA_CDP_LAUNCH=1` của mình — và **mang đúng nhược điểm** mà giao diện mình
đã cảnh báo: mất stealth của patchright, lộ `--remote-debugging-port`. Họ không có lớp chống phát hiện nào
bù lại (playwright thường còn yếu hơn patchright).

## 2. Cách làm "30 giây" — chỉ là NHÃN trong prompt (`engine/video.py:1628`)

```python
# Format prompt with required Model, Ratio, and Duration tag: [Model Seedance 2.5, Tỷ lệ {ratio}, Độ dài video {duration}s]
dur_tag = f", Độ dài video {task.duration}s" if task.duration else ""
```

Họ **nhét chữ "Độ dài video 30s" vào TEXT tin nhắn** rồi để trợ lý Dola tự hiểu. Không đụng gì tới
`ability_param`, không có payload Khan, không ép `duration` ở tầng mạng.

Hệ quả: chính họ phải dò các câu Dola từ chối (`engine/video.py:2093-2100`):
`"durations from 4 to 15 seconds"`, `"nearest supported duration"`, `"supported duration of"`.
Tức là **Dola thường xuyên hạ độ dài của họ xuống**, y như đường UI cũ của mình trước khi có payload Khan.

`duration = max(4, min(30, duration))` (`:553`) — kẹp 4…30.

**Kết luận: về 30s, tool mình MẠNH HƠN.** Payload Khan (13 khoá rải 5 chỗ) khiến Dola tính 30s = 2 credit và
nhận thật; họ chỉ xin bằng lời rồi chấp nhận bị hạ.

## 3. `page.route` của họ là PASSIVE — không vá gì (`engine/video.py:1094`)

Comment ghi "patch duration" nhưng thân hàm chỉ ghi request ra file rồi `route.continue_()`; log của chính họ
in `PASSIVE INTERCEPT`. Họ chỉ **thu thập** request để chép lại, không sửa.

Dù vậy **ý tưởng `page.route` đáng cân nhắc cho mình**: vá body ở tầng mạng thì trang KHÔNG thấy gì
(không như `SKILLPACK_HIJACK` vá `fetch`/`XHR` trong trang, hay extension Khan để lại
`window.CHANNA_TARGET_RATIO`). **Vướng:** a_bogus ký trên body — sửa body sau khi ký là chữ ký sai. Chỉ dùng
được nếu vá TRƯỚC lúc trang ký, tức là vẫn phải ở trong trang. → Không port được cho đường ký.

## 4. Bắt video — nhiều đường song song, đáng học

`engine/video.py` bắt link video bằng **bốn** nguồn cùng lúc:
1. `response` có `dola.com` + `video` + (`mp4`|`download`|`mime_type=video`)
2. `/get_play_info` → `data.play_infos[0].main`
3. **Khung WebSocket** (`page.on("websocket")` → `framereceived`)
4. Sự kiện `DOLA_VIDEO_EXTRACTED` từ một extension họ gọi là "StudioRelay"

Điểm 4 cho thấy **họ dùng chung họ extension với Khan** — cùng tên sự kiện. Và init-script của họ để lại
`window.__seedanceInitDone`, `__seedanceVideoUrls`, `__seedanceVideoIds` — dấu vết toàn cục, cùng loại điểm
yếu như Khan.

Mình hiện chỉ bắt qua `/im/chain/single` (poll). **Đường WebSocket là thứ đáng port**: biết video xong sớm
hơn, không phải chờ vòng poll.

## 5. Đo độ dài thật — họ LÀM, mình chưa đủ (`engine/video.py:2487`)

```python
real_dur = _get_mp4_duration(local_file)      # đọc atom mvhd, không cần thư viện
task.duration = int(round(real_dur))          # GHI ĐÈ độ dài yêu cầu bằng độ dài THẬT
```

Họ đo mvhd (cùng kỹ thuật `watermark._mp4_duration` của mình) rồi **ghi đè `task.duration`** nên giao diện
luôn hiện số giây thật. Mình có `_short_video_note` nhưng nó **mất tác dụng ở đường cứu video**
(`server.py:521` ghi đè `error`) — đúng chỗ đã để lọt 1 video 12s hôm 20/09.

## 6. Cookie — nguồn sự thật là FILE JSON của họ, không phải jar Chrome (`profiles/manager.py:518`)

```python
async def inject_cookies_if_any(ctx):
    if profile.cookies:
        formatted = [{"name": …, "value": …, "domain": … or ".dola.com", "path": … or "/"} …]
        await ctx.add_cookies(formatted)
```

Nạp ở **mọi lần mở**, từ `profile.cookies` trong kho JSON của họ. Jar Chrome bị xoá/reset cũng không sao.
(Chất lượng thấp hơn mình: không có `expires`/`secure`/`httpOnly`/`sameSite` → cookie phiên, chết khi đóng
trình duyệt; nhưng vì nạp lại mỗi lần nên không thành vấn đề.)

**Mình đang ngược:** `pin_session_cookies` chỉ ghim lại cookie ĐANG CÓ trong jar. Jar mất `sessionid` là
coi như nick chết — không có đường phục hồi từ `accounts/<nick>/cookies.json`. Đây chính là chùm lỗi
`Bị đăng xuất khỏi Dola giữa chừng` tối 20/09.

## 6b. Proxy (`proxy/manager.py`, 24 KB) — mình hơn ở chỗ quan trọng nhất

Cấu trúc của họ: `ProxyItem` (pydantic) + `ProxyDB` (SQLite) + `ProxyManager`, gán proxy cho profile theo
`proxy_id`. Hỗ trợ 2 kiểu: `static` (host/port/user/pass) và `rotating_api` (key nhà bán).

### ⚠ Họ FAIL-OPEN — proxy chết thì chạy bằng IP thật

```python
async def get_effective_proxy_config(self, proxy_id):
    """Xác định cấu hình proxy cho Chrome / Playwright. Tự động fallback IP gốc nếu Proxy chết."""
    ...
    else:
        logger.warning("Rotating Proxy API Key %s unresolved, fallback to Direct IP", proxy.id[:8])
        return None          # ← None = Chrome mở KHÔNG proxy = IP nhà của người dùng
```

Docstring của chính họ nói thẳng. Proxy hết hạn / API lỗi / mạng chập → nick vẫn chạy, nhưng bằng **IP thật**,
mang theo cookie của nick đó. Dola gom nick theo IP, nên một lần fail-open là lộ cả cụm.

**Mình fail-closed** (`browser.account_proxy`): nick khai proxy riêng mà lấy không ra IP thì **ném lỗi**, pool
xoay nick khác, không bao giờ rơi về IP máy. Giữ nguyên, đừng bắt chước.

### `LocalProxyRelay` — thứ duy nhất đáng để ý (`:131`)

Một HTTP relay asyncio chạy tại `127.0.0.1:<port ngẫu nhiên>`: đọc khối header của Chrome, chèn
`Proxy-Authorization: Basic <base64>`, nối lên upstream rồi bơm byte hai chiều. Chrome chỉ thấy
`--proxy-server=127.0.0.1:<port>`, không cần biết user/pass.

Mình **đã có hai đường giải quyết việc này** và đều tốt hơn: patchright nhận thẳng
`proxy={"server","username","password"}`; đường CDP dùng `_cdp_proxy_auth` (`Fetch.continueWithAuth`).
→ Không cần port. (Relay của họ còn một lỗ: chỉ chèn auth cho khối header ĐẦU TIÊN mỗi kết nối — với
keep-alive HTTP thường thì request thứ hai mất auth. Chạy được vì Dola toàn https nên đi qua CONNECT.)

### Lấy IP từ nhà bán — họ làm ẩu hơn mình (`:381`)

```python
urls = [f"https://api.topproxy.vn/get-proxy?key={key}",
        f"https://proxyxoay.shop/api/get-proxy?key={key}",
        f"https://tmproxy.com/api/get-current-proxy?key={key}"]
for u in urls:  # thử lần lượt tới khi có cái trả lời
```

- **Rải cùng một key cho cả ba nhà bán** — key của mình gửi sang hai nhà không liên quan.
- Gọi tmproxy bằng **GET query**, trong khi API thật là **POST JSON** → nhánh tmproxy của họ nhiều khả năng
  không bao giờ chạy.
- **Không phân biệt "lấy IP mới" với "lấy IP đang dùng"**. Mình có `prefer_cached=True` đúng để tránh xoay IP
  giữa lúc đang dựng video (đổi IP = cắt cổng, mất lượt).
- **Không xử lý whitelist IP**. Mình có `rotating_whitelist_hint` cho nhà bán xác thực bằng whitelist
  (proxyxoay / proxy.vn / topproxy) — nói thẳng khi IP máy đổi theo từng kết nối vì VPN.

### Kiểm tra proxy sống (`:425`)

Họ: `urllib` qua `api.ipify.org` → đo độ trễ + tra quốc gia. Một lần một proxy, không nhớ kết quả.
Mình: `ensure_proxy_alive` (TCP connect, cache 5 phút, không tốn HTTP) + `proxy_status` đánh dấu IP bẩn
(WAF-flag, nghỉ 24h) + `DirtyIpWaitTimeout`. Mình đầy đủ hơn.

**Tóm lại phần proxy: không có gì đáng port.** Điểm khác biệt lớn nhất — fail-open vs fail-closed — mình
đang đúng, họ đang sai.

## 7. Nên port gì (xếp theo giá trị)

| # | Việc | Vì sao | Sức |
|---|---|---|---|
| 1 | **Nạp lại `cookies.json` mỗi lần mở profile** khi jar thiếu `sessionid` | Chữa thẳng lỗi mất phiên hàng loạt | nhỏ |
| 2 | **`--window-position=9999,9999`** thay cho headless khi cần headed | Giấu cửa sổ mà vẫn là trình duyệt thật, không dấu vết headless | 1 dòng |
| 3 | **Bắt video qua WebSocket** | Biết xong sớm hơn poll | vừa |
| 4 | **Đo độ dài thật ở MỌI đường** (kể cả cứu video) + ghi đè số hiển thị | Hết cảnh báo "xong" cho video 12s | nhỏ |

## 8. KHÔNG nên bắt chước

- **Bỏ patchright sang playwright thường + CDP.** Họ chấp nhận lộ; mình đang hơn ở chỗ này.
- **Xin 30s bằng lời trong prompt.** Đã đo là thua payload Khan.
- **Dấu vết toàn cục** (`window.__seedance*`). Mình cũng đang dính qua extension Khan — nên giảm, không thêm.
- Extension nạp qua `--load-extension` của họ chỉ để **xác thực proxy** (user/pass), không liên quan 30s.
