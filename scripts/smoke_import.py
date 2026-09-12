"""Nạp thử toàn bộ app bằng runtime sắp đóng gói. Build gọi file này; lỗi ở đây = build đỏ.

Bản Python embeddable có file ._pth chạy isolated: sys.path CHỈ gồm các dòng trong ._pth — không có
thư mục làm việc, không có cả thư mục của script, và bỏ qua PYTHONPATH. Vì thế phải tự chèn gốc repo
vào sys.path ở đây (lúc build chưa có resources\\app-python nên dòng ..\\app-python trong ._pth chưa
trỏ tới đâu; bước kiểm tra sau đóng gói trong build-win.ps1 mới chứng minh dòng đó đúng).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import browser_pool  # noqa: E402,F401
import cookie_service  # noqa: E402,F401
import facebook_login  # noqa: E402,F401
import server  # noqa: E402,F401
import video_worker_ui  # noqa: E402,F401

print("server import OK")
