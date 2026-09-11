# Dola Studio — hướng dẫn cài

Tạo video AI hàng loạt bằng nhiều nick, tự xoá watermark. Chạy trên **Windows** và **macOS**.

## Cần cài trước
- **Windows:** không cần gì cả — file .exe đã gói sẵn Python + Chromium.
- **macOS:** cần **Python 3** (https://www.python.org/downloads/) và **Node.js LTS** (https://nodejs.org).

## Windows — 1 file .exe, bấm 1 lần là chạy
1. Tải **Dola Studio Setup.exe** (hoặc bản `DolaStudio-portable-*.exe` không cần cài).
2. Bấm đôi. Xong — không cần cài Python, Node hay Chrome: Python + Chromium đã nằm sẵn trong file.

Dữ liệu (nick, video, log) nằm ở `%APPDATA%\Dola Studio` nên app cập nhật không mất nick.

## macOS
Mở Terminal trong thư mục này:
```
bash setup.sh      # cài, 1 lần
bash run.sh        # chạy app
```

## Dùng app
Tab **Tài khoản** → thêm nick bằng Cookie Facebook. Tab **Tạo video** → nhập prompt → **Chạy**. Video lưu ở `downloads/`, đã tự xoá logo.

## Mẹo nhanh & hiệu quả
- Model **seedance-2.0** nhanh gấp 2 lần 2.5.
- **10 giây** nhanh gấp 2 + rẻ nửa lượt so với 30 giây.
- Prompt **tả cảnh ngắn 1–2 câu** (đừng bài văn dài) để khỏi bị chặn nội dung.
- Nick free: ~2–4 lượt + tối đa 4 video/ngày.

## Lưu ý
- Mỗi máy có khoá riêng (tạo tự động lúc cài). Không dùng chung nick với người khác.
