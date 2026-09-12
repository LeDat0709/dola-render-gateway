# Bộ prompt Google Stitch — cải tiến giao diện Dola Studio

Stitch không có ô "system prompt". Cách làm cho ra màn hình khớp app đang chạy:

1. Tạo project mới ở stitch.withgoogle.com → chọn **Web**. Trong bảng theme (nếu có): **Dark**,
   màu chính `#d0bcff`, font **Geist** (không có thì Inter), bo góc **8px**.
2. Dán **Prompt A (gốc)** làm lần tạo đầu tiên. Nó sinh màn "Tổng quan" và cố định bảng màu, font,
   thanh trên. Mọi màn sau tạo trong cùng project để Stitch giữ style.
3. Mỗi màn tiếp theo: bấm tạo màn mới, dán đúng một prompt B/C/D/E. Nếu lệch style, dán thêm
   **Prompt F (chỉnh)**.
4. Xong: Export → HTML, gửi cho Claude kèm câu "Chuyển sang React theo docs/UI_SYSTEM_PROMPT.md,
   không đổi token". Đó là cách đã làm ở đợt 1 (5 màn Stitch → giao diện hiện tại).

Mỗi prompt được giữ dưới ~1.900 ký tự vì Stitch giới hạn độ dài ô nhập. Prompt viết tiếng Anh
(Stitch hiểu ổn định hơn), chữ trên giao diện bắt buộc tiếng Việt và đã ghi sẵn trong prompt.

---

## Prompt A — gốc (dán đầu tiên, sinh màn Tổng quan)

```
Dark desktop web app "Dola Studio" (1280×800, dark only) for a Vietnamese video studio that batch-generates AI videos on dola.com with many accounts called "nick". Operators, not developers: dense admin UI, no marketing style, no stock photos.

EXACT colors: page #0b0e15, card #191c23, inner #1d2027, hover #272a31, highest #32353c; text #e0e2ec, secondary #cbc3d7, border #494454; primary #d0bcff (text on it #3c0091); success #4edea3; info #adc6ff; warning #f59e0b; error #ffb4ab. Fonts: Geist; JetBrains Mono for numbers, labels, codes. Section titles tiny uppercase mono gray. Radius 8px, cards 12px, no shadows. Separate blocks by surface levels, not borders. One purple primary button per block; others small outlined with thin icons.

Status pills: "Sẵn sàng" green, "Đang chạy" purple, "Hết credit" red, "Hết lượt hôm nay" amber, "Nghỉ đến 22:00" blue, "Tắt lịch" gray, "⚠ Chưa đăng nhập" gray.

ALL UI text Vietnamese. Realistic data: nicks like fb61594178164998, durations "4p 17s", "2g" ago, "1/4".

Sticky header on every screen: 32px mono strip (green dot "Gateway online", "http://127.0.0.1:8000", chip "13ms", right "51 nick • 38 credit • 3 đang render"); then row: gradient purple→blue logo "D", "Dola Studio" + tag "DESKTOP", tabs "Tổng quan · Studio · Kho tài khoản · Proxy · Cài đặt", right small outlined "Bật server", "Tắt", "Thư mục video", "Log".

Screen "Tổng quan": cards "Tải xử lý" (bar "3 / 10 nick") and "Hàng chờ" (3 jobs, ETA "xong lúc ~14:05"); 7 KPI tiles "Tất cả 51 · Sẵn sàng 4 · Đang render 3 · Hết credit 32 · Hết lượt hôm nay 12 · Chưa đăng nhập 0 · Nghỉ 0"; tiles "Đã gửi 44 / Xong 19 / Lỗi 22", "Dựng trung vị 6p 00s · P90 8p 26s"; card "Nguyên nhân lỗi (22)" with stacked color bar and rows "Hết lượt hôm nay 10 · 45%"; table "Tác vụ gần nhất"; 7-day bar chart; grid "Video gần đây" (thumbnail, "10s", "#0092", nick, "2g").
```

## Prompt B — Studio (ưu tiên 1)

```
New screen "Studio" (tab active), same header and style. Goal: replace a 51-row form table with per-nick cards and a 3-group toolbar.

Top to bottom:
1) Card "PROMPT": large textarea placeholder "Nhiều prompt, mỗi dòng 1 cái", below it small outlined buttons "Điền tất cả", "Mỗi dòng 1 nick", a mono counter "182 ký tự" and an amber hint chip "Có từ dễ bị chặn: 击".
2) Row "CẤU HÌNH MẶC ĐỊNH": selects "seedance-2.5", "15s", "9:16", button "Đồng bộ mặc định"; right side a mono estimate box "Sẽ dùng 12 nick × 4 credit · ~6 phút/video · xong lúc ~14:05".
3) Action bar: text links "Chọn tất cả sẵn sàng" · "Chọn nick còn ≥ 4 credit"; outlined "Chạy sẵn sàng", "Chạy lại lỗi", red outlined "Dừng"; ONE purple primary "Chạy đã chọn (12)".
4) Grid of nick cards, 3 per row, dense 13–14px text. Each card: checkbox, nick "fb61594178164998", status chip, "1/4 hôm nay · còn 3 credit"; editable 2-line prompt; mini selects model/ratio/seconds; a 5-step horizontal timeline "Hàng đợi → Gửi → Dựng → Tải về → Xong" with the active step purple and elapsed "2p 14s"; footer icon buttons run/retry/settings/delete.
Show variety: 3 cards running (timeline at "Dựng"), 2 done (green, thumbnail with "10s"), 1 failed with red text "Dola tạm chặn vì gửi quá dày (710022002) — tự nghỉ 90s" and button "Chạy lại", 1 "Hết lượt hôm nay" dimmed, rest "Sẵn sàng".
```

## Prompt C — Cài đặt (ưu tiên 2)

```
New screen "Cài đặt" (tab active), same header and style. Replace the single narrow card with a 2-column grid of 4 cards, each with its own "Lưu" button and a small note "Cần Tắt rồi Bật server" where relevant.

Card 1 "MÁY CHỦ": radio "Chạy trên máy này" (selected) / "Máy chủ từ xa (VPS Nhật/Hàn)"; when remote: inputs "http://45.77.1.2:8000", "API key", "Admin key"; status line green "✓ Nối được — 46 nick trên máy chủ"; buttons "Kiểm tra", "Lưu".
Card 2 "MẠNG": input "Proxy chung" placeholder "user:pass@host:port · socks5://host:port", helper "Ở Việt Nam cần exit node Nhật/Hàn; để trống = nối thẳng", buttons "Kiểm tra vào dola.com", "Lưu"; result line "✓ Vào được dola.com (HTTP 200) qua 45.77.1.2".
Card 3 "VẬN HÀNH": number inputs with mono values "Nick chạy song song 10", "Giãn nhịp gửi 3s + ngẫu nhiên 3s", "Giới hạn video/nick/ngày 4", "Thời gian chờ video 15 phút"; toggle "Tự thử lại / xoay nick khi lỗi" (on) with a 2-line gray explanation; toggle "Thông báo khi xong đợt".
Card 4 "THƯ MỤC": rows "Thư mục video" and "Thư mục profile nick", each with mono path "/Users/dat/…/downloads" and buttons "Chọn…", "Mở".
Footer strip: "Dola Studio 1.0.8 · macOS arm64", link "Kiểm tra bản mới", "Mở file .env.local".
```

## Prompt D — Kho tài khoản (nhập kho hàng loạt)

```
New screen "Kho tài khoản" (tab active), same header and style. Keep the existing structure: 6 KPI tiles "Tổng số nick 51 · Sẵn sàng 4 · Đang render 3 · Tổng credit còn 38 · Hết credit / lượt 44 · Chưa đăng nhập 0"; toolbar with small outlined buttons "Nhập cookie", "Chia proxy tự động", "Kiểm tra phiên", "Xuất kho", "Nhập kho" and ONE purple primary "Thêm bằng Facebook"; search "Tìm theo tên nick, email, ghi chú, IP proxy…", filter "Tất cả trạng thái", sort "Trạng thái"; dense table with columns "Tên nick & email · Trạng thái · Credit · Lượt hôm nay · Proxy · Lần cuối · Ghi chú · Lịch · Hành động", 2-digit avatar, status chip, toggle switch, 5 icon actions.

Add a new sticky panel at the top of the table (shown while importing a big file): title "Đang nhập kho — 37/130", progress bar, current line "FB_61594283488769 … kiểm tra phiên", red outlined "Dừng"; below it a summary row with chips "✓ 31 đã vào", "? 4 chưa kiểm tra được phiên", "✗ 2 lỗi" and a button "Kiểm tra phiên các nick chưa rõ". Also show a selection bar when 3 rows are checked: "Đã chọn 3 · Kiểm tra phiên · Đổi proxy · Xoá cookie · Xoá nick".
```

## Prompt E — Proxy (cảnh báo quá tải)

```
New screen "Proxy" (tab active), same header and style. Title "Quản lý Proxy" with chip "tối đa 5 nick / IP" and a gray one-line explanation. 4 KPI tiles "Proxy riêng đang dùng 0 · Nick dùng proxy chung 51 · IP đang bị Dola soi 0 · Tổng nick 51".

Add a red warning banner right under the title: icon, "Proxy chung đang gánh 51 nick (tối đa 5) — đây là lý do Dola báo gửi quá dày (710022002)", buttons "Chia proxy tự động" (primary) and "Nhập danh sách proxy".

Table grouped by proxy: columns "Proxy · Giao thức · Tải nick · Nick · Trạng thái Dola · Tác vụ". First row "Proxy chung của gateway — nối thẳng (không proxy)", load "51 / 5" with red chip "Quá tải", chips "4 sẵn sàng · 3 chạy · 44 khác", gray "Chưa kiểm tra", actions "Kiểm tra", assign icon. Add 2 example rows of private proxies "http://45.77.1.2:8080 (JP)" load "5 / 5" green "OK 180ms", and "socks5://103.1.2.3:1080 (KR)" load "3 / 5" amber "Chậm 900ms".
Bottom card "Đẩy nick kèm proxy từ máy khác" with a mono code line ".venv/bin/python deploy/push_nicks.py http://127.0.0.1:8000 ADMIN_KEY --proxies proxies.txt --per-ip 5".
```

## Prompt F — chỉnh khi Stitch lệch style (dán sau bất kỳ màn nào)

```
Keep everything, only fix style: use exactly page #0b0e15, cards #191c23, inner blocks #1d2027, borders #494454, primary #d0bcff, success #4edea3, warning #f59e0b, error #ffb4ab, info #adc6ff. Make the header identical to the first screen. Section titles tiny uppercase mono gray. Numbers and times in JetBrains Mono. Denser: 13–14px text, 8px vertical padding in table rows, cards 12px radius with no drop shadows. Exactly one purple primary button per block; all other buttons small outlined. All labels in Vietnamese, no English words except Dola, Facebook, proxy, credit, prompt, model.
```

## Prompt G — biến thể nhanh (tuỳ chọn)

- "Same Studio screen, but show the empty state: no nick yet — centered card 'Chưa có nick — bấm Thêm bằng Facebook hoặc Nhập kho' with the two buttons."
- "Same Tổng quan screen when the gateway is off: red dot 'Gateway tắt', all KPIs dimmed, centered message 'Chưa nối được server — bấm Bật server ở thanh trên'."
- "Same Kho tài khoản screen with a confirmation dialog on top: 'XOÁ HẲN 3 nick? … Không hoàn tác.' with buttons 'Huỷ' and red 'Xoá'."

---

## Sau khi có HTML từ Stitch

Gửi cho Claude cùng lúc: (1) file HTML Stitch xuất, (2) `docs/UI_SYSTEM_PROMPT.md`, và câu lệnh:
"Chuyển màn này sang React trong `desktop/renderer/src/components/…`, dùng đúng token và component
đang có, giữ logic gọi API như file hiện tại, không thêm thư viện." Kiểm tra bằng
`npm --prefix desktop run build:ui` và mở thử app.
