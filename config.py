"""Dola Pool Configuration: Environment variable based with smart defaults."""
import os
from pathlib import Path


def _load_local_env():
    """Load ignored .env.local for local/tunnel runs; real environment wins."""
    # Bản đóng gói: mã nằm trong Program Files (chỉ đọc), app ghi .env.local vào DATA_DIR = cwd.
    for path in (Path.cwd() / ".env.local", Path(__file__).with_name(".env.local")):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_local_env()
HOST = os.getenv("DOLA_HOST", "127.0.0.1")   # app desktop: mặc định localhost (không đòi admin key)
PORT = int(os.getenv("DOLA_PORT", "8000"))

# Service API keys (comma-separated; empty = no auth, local debug only)
API_KEYS = [k.strip() for k in os.getenv("DOLA_API_KEYS", "").split(",") if k.strip()]

# Account pool cookie file (one dola.com cookie per line)
COOKIES_FILE = os.getenv("DOLA_COOKIES_FILE", "cookies.txt")

# Thư mục chứa profile mỗi nick (accounts/<nick>). Đổi bằng DOLA_ACCOUNTS_DIR để chọn ổ/đường dẫn khác.
ACCOUNTS_DIR = Path(os.getenv("DOLA_ACCOUNTS_DIR", "accounts"))

# Số video/nick mỗi ngày khi CHƯA biết số credit thật (fallback). Biết credit thì dùng credit.
DAILY_LIMIT = int(os.getenv("DOLA_DAILY_LIMIT", "4"))

# Số Chrome mở cùng lúc để GỬI video (không phải số video cùng lúc). Với DOLA_HTTP_POLL=1 trình
# duyệt được trả lại ngay sau khi gửi (~20s) nên số video chạy song song = số nick sẵn sàng.
MAX_CONCURRENCY = int(os.getenv("DOLA_MAX_CONCURRENCY", "3"))

# Số trình duyệt đăng nhập / kiểm tra cookie chạy cùng lúc (nạp hàng loạt, verify-all, thêm nick)
LOGIN_CONCURRENCY = int(os.getenv("DOLA_LOGIN_CONCURRENCY", "3"))

# Global pending task queue limit (queued + processing), 0 = unlimited
MAX_PENDING_TASKS = int(os.getenv("DOLA_MAX_PENDING_TASKS", "100"))

# Video generation timeout in seconds
VIDEO_TIMEOUT = int(os.getenv("DOLA_VIDEO_TIMEOUT", "300"))

# Tự xóa watermark "Dola AI" ngay khi tải video xong (BẬT mặc định; DOLA_AUTO_REMOVE_WM=0 để tắt)
AUTO_REMOVE_WM = os.getenv("DOLA_AUTO_REMOVE_WM", "1").strip().lower() not in ("0", "false", "no", "off", "")

# SQLite database path
DB_PATH = os.getenv("DOLA_DB_PATH", "tasks.db")

# Video download storage directory (served statically by FastAPI)
DOWNLOAD_DIR = os.getenv("DOLA_DOWNLOAD_DIR", "downloads")

# Explicit browser proxy (must point to JP/KR egress; empty = system proxy)
PROXY = os.getenv("DOLA_PROXY", "http://127.0.0.1:7890")

# Run browser in headless mode (login always runs with head)
HEADLESS = os.getenv("DOLA_HEADLESS", "1") == "1"

# Base public URL for returning static video links
PUBLIC_BASE = os.getenv("DOLA_PUBLIC_BASE", f"http://127.0.0.1:{PORT}")

# Admin web dashboard password (empty = no auth)
ADMIN_KEY = os.getenv("DOLA_ADMIN_KEY", "")

# Dola 30s / Watermark Removal Chromium extension path
# Bản đóng gói chạy với thư mục làm việc = nơi chứa dữ liệu, còn extension nằm cạnh mã nguồn →
# đường dẫn tương đối phải neo vào file này, không neo vào cwd.
_PKG_DIR = Path(__file__).resolve().parent
EXTENSION_DIR = os.getenv("DOLA_EXTENSION_DIR", "") or str(_PKG_DIR / "extensions" / "dola30")
EXTENSION_ENABLED = os.getenv("DOLA_EXTENSION_ENABLED", "1") == "1"

# Unlock 30s by hijacking fetch in-page (Playwright init-script) instead of the chrome.debugger
# extension. This runs HEADLESS and shows no "debugging this browser" bar. Set to "0" to fall
# back to the debugger extension (which forces a headed window).
SKILLPACK_HIJACK = os.getenv("DOLA_SKILLPACK_HIJACK", "1") == "1"

# Poll + download over plain HTTP (no browser) after the signed submission — the browser is only
# needed ~5s for the submit, so this frees it immediately (big throughput gain). "0" keeps polling
# in the browser page (needed to auto-answer a rare duration-confirm prompt).
HTTP_POLL = os.getenv("DOLA_HTTP_POLL", "1") == "1"

# Giữ trình duyệt bao nhiêu giây SAU khi gửi trước khi chuyển sang theo dõi bằng HTTP. Dola hay hỏi
# lại ("xác nhận 30s?", menu thông số) ngay sau khi nhận việc — chỉ trang web trả lời được, nên
# đóng sớm là job chết. Chỉ nhả trình duyệt khi đã im tiếng hỏi.
HTTP_POLL_AFTER_SEC = int(os.getenv("DOLA_HTTP_POLL_AFTER", "60"))

# Dola UI language pinned on every profile at launch (worker selectors are Japanese)
UI_LANG = os.getenv("DOLA_UI_LANG", "ja").strip() or "ja"

# Erase the burned-in "Dola AI" corner logo (Seedance 2.0 / Dreamina renders) with ffmpeg delogo.
# Seedance 2.5 output carries no logo and is never touched. Box position: DOLA_WM_* (see watermark.py).
REMOVE_WATERMARK = os.getenv("DOLA_REMOVE_WATERMARK", "1") == "1"

# How a video is submitted: "fetch" = signed /chat/completion call from inside the logged-in page
# (no clicking, headless-capable, falls back to "ui" when submission is refused); "ui" = drive the
# composer with clicks and keystrokes (required for reference images and the 30s extension).
SUBMIT_MODE = os.getenv("DOLA_SUBMIT_MODE", "fetch").strip().lower()

# ability_param model keys accepted by /chat/completion. 2.0 was captured from the real UI;
# Seedance 2.5 needs an entitled account: run one 2.5 job via UI, copy the key from the
# "chat/completion ability_param=" log line, and set DOLA_MODEL_KEY_SEEDANCE25.
MODEL_KEY_SEEDANCE20 = os.getenv("DOLA_MODEL_KEY_SEEDANCE20", "seedance_v2.0")
MODEL_KEY_SEEDANCE25 = os.getenv("DOLA_MODEL_KEY_SEEDANCE25", "seedance_v2.5").strip()  # captured from live 2.5 submit

# Browser channel: "chrome" uses locally-installed Google Chrome (best stealth vs
# patchright's bundled Chromium). Set DOLA_BROWSER_CHANNEL="" to fall back to bundled
# Chromium on hosts without Chrome installed.
BROWSER_CHANNEL = os.getenv("DOLA_BROWSER_CHANNEL", "chrome").strip()

# Headless Chrome tags its UA with "HeadlessChrome" (a trivial detection tell). When empty,
# the launcher probes the real UA once and strips that token; set to pin an exact UA instead.
BROWSER_UA = os.getenv("DOLA_BROWSER_UA", "").strip()

# Daily quota reset timezone (Japan midnight by default)
LIMIT_RESET_TZ = os.getenv("DOLA_LIMIT_RESET_TZ", "Asia/Tokyo")

# Conservative credit check before video generation (default 2 points)
VIDEO_REQUIRED_POINTS = int(os.getenv("DOLA_VIDEO_REQUIRED_POINTS", "2"))

# Public reference image download limits
REFERENCE_IMAGE_MAX_BYTES = int(os.getenv("DOLA_REFERENCE_IMAGE_MAX_BYTES", str(15 * 1024 * 1024)))
REFERENCE_DOWNLOAD_TIMEOUT = int(os.getenv("DOLA_REFERENCE_DOWNLOAD_TIMEOUT", "60"))
REFERENCE_IMAGE_MAX_COUNT = int(os.getenv("DOLA_REFERENCE_IMAGE_MAX_COUNT", "30"))

# Extended generation window for reference image tasks (seconds)
REFERENCE_VIDEO_TIMEOUT = int(os.getenv("DOLA_REFERENCE_VIDEO_TIMEOUT", "900"))
