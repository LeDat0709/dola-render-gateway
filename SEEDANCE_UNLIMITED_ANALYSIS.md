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
