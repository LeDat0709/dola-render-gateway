"""Nạp thử toàn bộ app bằng runtime sắp đóng gói. Build gọi file này; lỗi ở đây = build đỏ.

Chạy như một script (không phải python -c) vì bản Python embeddable KHÔNG đưa thư mục làm việc
vào sys.path và bỏ qua PYTHONPATH khi có file ._pth — chỉ thư mục của script mới được thêm.
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
