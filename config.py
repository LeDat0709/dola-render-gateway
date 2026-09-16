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


def env_local_path() -> Path:
    """File .env.local đang dùng (ưu tiên cwd — nơi systemd/app chạy)."""
    for p in (Path.cwd() / ".env.local", Path(__file__).with_name(".env.local")):
        if p.exists():
            return p
    return Path.cwd() / ".env.local"


def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
    """Ghi file kiểu tmp + rename: app/tiến trình tắt giữa chừng KHÔNG để lại file cụt (JSON/proxy/env dở dang)."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding=encoding)
    try:
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def upsert_env_local(key: str, value: str) -> None:
    """Ghi/đổi một dòng KEY=VALUE trong .env.local để cấu hình đổi lúc chạy còn giữ sau khi khởi động lại."""
    f = env_local_path()
    lines = f.read_text(encoding="utf-8-sig").splitlines() if f.exists() else []
    out, done = [], False
    for raw in lines:
        if raw.strip().startswith(f"{key}=") and not raw.strip().startswith("#"):
            out.append(f"{key}={value}"); done = True
        else:
            out.append(raw)
    if not done:
        out.append(f"{key}={value}")
    atomic_write_text(f, "\n".join(out) + "\n")


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

# Job báo "đã gửi, chưa xác nhận" thì tự đi nhặt video về: chỉ nhận hội thoại tạo trong khoảng này
# quanh lúc gửi. Rộng quá dễ vớ nhầm video của job trước/sau trên cùng nick; hẹp quá thì bỏ sót
# video dựng lâu. 20 phút phủ được cả video 30s (Dola dựng ~15 phút).
CUU_VIDEO_CUA_SO_SEC = int(os.getenv("DOLA_CUU_VIDEO_CUA_SO", "1200"))

# Số Chrome mở cùng lúc để GỬI video (không phải số video cùng lúc). Với DOLA_HTTP_POLL=1 trình
# duyệt được trả lại ngay sau khi gửi (~20s) nên số video chạy song song = số nick sẵn sàng.
MAX_CONCURRENCY = int(os.getenv("DOLA_MAX_CONCURRENCY", "3"))

# Số trình duyệt đăng nhập / kiểm tra cookie chạy cùng lúc (nạp hàng loạt, verify-all, thêm nick)
LOGIN_CONCURRENCY = int(os.getenv("DOLA_LOGIN_CONCURRENCY", "3"))

# Global pending task queue limit (queued + processing), 0 = unlimited
MAX_PENDING_TASKS = int(os.getenv("DOLA_MAX_PENDING_TASKS", "100"))

# Video generation timeout in seconds. Đo thực tế 11/9 (19 video seedance 2.5, 10s): 147–743s, trung vị 281s;
# 300s trừ ~60s Dola hỏi lại chỉ còn ~230s → quá nửa video "quá giờ" dù Dola vẫn dựng xong (cháy credit).
VIDEO_TIMEOUT = int(os.getenv("DOLA_VIDEO_TIMEOUT", "900"))
# 30s (Seedance 2.5) dựng LÂU HƠN — Dola báo "hoàn tất trong ~15 phút", Dola dựng 30s 9–35 phút (thất thường), nên 900s/1500s cắt oan
# → job 30s hay "hết giờ" oan (đã kiểm 13/09: extension tạo được 30,04s thật; tool trượt vì chờ chưa đủ).
VIDEO_TIMEOUT_30S = int(os.getenv("DOLA_VIDEO_TIMEOUT_30S", "2400"))

# Tự thử lại 1 lần khi Dola lỗi tạm thời (tạo thêm 1 cuộc trò chuyện) và xoay sang nick khác cho job
# không ghim nick. Tắt (DOLA_AUTO_RETRY=0) = lỗi là dừng ngay. Đổi được lúc chạy qua POST /api/admin/retry.
AUTO_RETRY = os.getenv("DOLA_AUTO_RETRY", "1").strip().lower() not in ("0", "false", "no", "off")
# ĐỐT NICK (nick dùng 1 lần, như tool Seedance): nick dùng HẾT lượt/điểm thì tắt lịch + ghi chú "[ĐÃ ĐỐT …]" để không chạy
# lại mai (team dùng nick FB 1 lần rồi bỏ). Mặc định TẮT. Đổi lúc chạy qua POST /api/admin/burn-nicks.
BURN_NICKS = os.getenv("DOLA_BURN_NICKS", "0").strip().lower() in ("1", "true", "yes", "on")

# Giãn nhịp giữa các lần gửi lệnh lên Dola (toàn server, mọi nick): cố định + ngẫu nhiên 0..JITTER giây.
# Học từ DomixHub ("Nghỉ giữa các job" + jitter 2–5s). Bắn 10 nick trong cùng 1 giây là dấu hiệu bot rõ nhất
# và trùng với các đợt "Dola lỗi tạm thời" (43 lần/ngày 11/9).
SUBMIT_GAP_SEC = float(os.getenv("DOLA_SUBMIT_GAP", "3"))
SUBMIT_JITTER_SEC = float(os.getenv("DOLA_SUBMIT_JITTER", "3"))

# Job KHÔNG ghim nick: thử tối đa N nick rồi dừng (DomixHub: 3). Trước đây duyệt hết danh sách →
# 1 lỗi hệ thống = mở Chrome trên cả chục nick, đốt sạch lượt.
MAX_ROTATE = int(os.getenv("DOLA_MAX_ROTATE", "3"))

# Proxy xoay IP kiểu STICKY-SESSION: trong proxy.txt của nick, đặt {SESSION} vào chỗ phần đổi
# (vd: host:port:user-session-{SESSION}:pass). Tool giữ nguyên session suốt 1 video (submit+poll cùng IP),
# rồi đổi session sau mỗi N video → IP mới. 0 = tắt (không đổi). "2 video đổi 1 phát" → đặt =2.
PROXY_ROTATE_EVERY = int(os.getenv("DOLA_PROXY_ROTATE_EVERY", "0"))

# TMProxy (tmproxy.com): dán `tmproxy://API_KEY` làm proxy của nick / vào Kho proxy → tool tự gọi API lấy IP
# hiện hành, tự đổi IP sau mỗi PROXY_ROTATE_EVERY video và ngay khi Dola báo 710022002. id_location/id_isp
# theo tài liệu TMProxy (0 = tự chọn). Xem tmproxy.py và docs/TMPROXY.md.
TMPROXY_ID_LOCATION = int(os.getenv("DOLA_TMPROXY_LOCATION", "0"))
TMPROXY_ID_ISP = int(os.getenv("DOLA_TMPROXY_ISP", "0"))

# Tắt CHẾ ĐỘ NGHỈ: nick KHÔNG bị bench sau 710022002 / captcha và KHÔNG dừng gửi theo proxy → nick luôn
# sẵn sàng, chạy tới khi Dola thật sự chặn (hết credit / cookie chết / chặn nội dung). Vẫn giữ giãn nhịp
# SUBMIT_GAP giữa các lần gửi. CẢNH BÁO: dội liên tục vào 710022002/captcha có thể làm Dola soi nick mạnh hơn.
NO_COOLDOWN = os.getenv("DOLA_NO_COOLDOWN", "0").strip().lower() in ("1", "true", "yes", "on")

# MỖI LẦN MỘT NICK: chỉ 1 nick chạy trọn (gửi + render) tại một thời điểm, đổi IP đầu mỗi nick. Dành cho
# 1 KEY/LINK proxy xoay dùng cho NHIỀU nick — vì 1 key = 1 IP sống → chạy tuần tự để không "nhiều nick một
# IP" (710022002). Mặc định TẮT. Chỉ ăn thua khi proxy chung là key/link xoay; proxy tĩnh/nối thẳng bỏ qua.
ONE_NICK = os.getenv("DOLA_ONE_NICK", "0").strip().lower() in ("1", "true", "yes", "on")

# Mỗi KHOÁ proxy xoay (proxy riêng của nick, hoặc proxy chung) dùng N LƯỢT rồi mới xin IP mới. Đếm theo LƯỢT
# THỬ MỞ NICK, không theo nick khác nhau và cũng không theo lệnh đã gửi: nick hỏng trước lúc gửi vẫn đốt một
# lượt của khoá. N=1 an toàn nhất nhưng gọi nhà bán nhiều nhất. Chạy CẢ KHI DOLA_PROXY rỗng (proxy riêng từng
# nick) — trước đây cờ này nằm trong nhánh ONE_NICK nên PROXY rỗng là vô tác dụng hoàn toàn.
NICKS_PER_IP = max(1, int(os.getenv("DOLA_NICKS_PER_IP", "2")))
# Bật NICKS_PER_IP cho PROXY RIÊNG TỪNG NICK (DOLA_PROXY rỗng). Mặc định TẮT vì nó đổi giao kèo cũ: khi
# không bật, tool CHỈ xoay IP khi cần (IP sắp hết tuổi, IP bẩn, Dola chặn 710022002) — xoay chủ động tốn
# nhịp của nhà bán. Bật khi nhiều nick dùng chung ít proxy và hay dính 710022002.
XOAY_THEO_LUOT = os.getenv("DOLA_XOAY_THEO_LUOT", "0").strip().lower() in ("1", "true", "yes", "on")
# Trong lô đó, tối đa K job CHẠY SONG SONG trên cùng 1 IP (đối thủ v1.0.88 "IP chung mẻ": ≤6 job/IP). 1 = tuần tự như cũ.
# IP đủ N job thì job kế CHỜ các job còn chạy trên IP xong mới đổi IP (đổi lúc đang chạy = cắt IP của chúng).
PARALLEL_PER_IP = max(1, int(os.getenv("DOLA_PARALLEL_PER_IP", "1")))
# Tối đa job CHẠY CÙNG LÚC trên MỘT IP ra: proxy riêng của nick, proxy chung, hoặc IP máy khi không proxy. Job giữ chỗ
# từ lúc gửi tới lúc tải xong; hết chỗ thì CHỜ chứ không mở nick (đối thủ v1.0.88: "Đang chờ chỗ trên IP chung").
# 0 = không giới hạn (mặc định, như cũ). Nhiều nick chung một IP mà hay dính 710022002 → đặt 2–3.
MAX_JOBS_PER_IP = max(0, int(os.getenv("DOLA_MAX_JOBS_PER_IP", "0")))
# Nick ĐÃ khai proxy thì mọi đường tới Dola/CDN (gửi, theo dõi, quét, tải) đi đúng proxy đó — lỗi thì chờ/báo lỗi, KHÔNG
# rơi về IP máy. Ngoại lệ DUY NHẤT, mặc định tắt: tải file video từ CDN đi thẳng khi proxy cắt ngang lúc tải (proxy rẻ
# hay cắt file lớn). Bật = 1 nếu video hay "tải hỏng" qua proxy; đổi lại CDN thấy IP máy.
DIRECT_DOWNLOAD_FALLBACK = os.getenv("DOLA_DIRECT_DOWNLOAD_FALLBACK", "0").strip().lower() in ("1", "true", "yes", "on")


def _float_list(raw: str) -> tuple[float, ...]:
    out = []
    for part in (raw or "").replace(" ", "").split(","):
        try:
            v = float(part)
        except ValueError:
            continue
        if v > 0:
            out.append(v)
    return tuple(out)


# Dola trả 710022002 mà CHẮC CHẮN chưa nhận lệnh → chờ lần lượt các mốc này (giây) rồi thử lại CÙNG nick, hết mốc mới
# xoay nick như cũ (ManixAITools: 15s, 30s). Trống/0 = tắt, xoay nick ngay.
RATE_LIMIT_RETRY_WAITS = _float_list(os.getenv("DOLA_RATE_LIMIT_RETRY_WAITS", "15,30"))
# Giãn nhịp CHUNG cho MỌI proxy, ngay trước lúc gửi thật: hai lần gửi bất kỳ (khác nick, khác proxy) cách nhau ít nhất
# GAP + ngẫu nhiên(0..JITTER) giây. SUBMIT_GAP_SEC chỉ giãn trong CÙNG proxy và lúc BẮT ĐẦU job → 8 nick trên 8 proxy
# vẫn gửi trong cùng một giây (16/09). 0 = tắt.
SUBMIT_GAP_GLOBAL_SEC = max(0.0, float(os.getenv("DOLA_SUBMIT_GAP_GLOBAL", "4") or 0))
SUBMIT_JITTER_GLOBAL_SEC = max(0.0, float(os.getenv("DOLA_SUBMIT_JITTER_GLOBAL", "3") or 0))

# Tự xóa watermark "Dola AI" ngay khi tải video xong (BẬT mặc định; DOLA_AUTO_REMOVE_WM=0 để tắt)
AUTO_REMOVE_WM = os.getenv("DOLA_AUTO_REMOVE_WM", "1").strip().lower() not in ("0", "false", "no", "off", "")

# SQLite database path
DB_PATH = os.getenv("DOLA_DB_PATH", "tasks.db")

# Video download storage directory (served statically by FastAPI)
DOWNLOAD_DIR = os.getenv("DOLA_DOWNLOAD_DIR", "downloads")

# Proxy chung cho trình duyệt + HTTP (exit node Nhật/Hàn). Trống = nối thẳng.
# Mặc định TRỐNG: trước đây thiếu khoá là rơi về 127.0.0.1:7890 (Clash của máy dev) → máy khách
# không chạy Clash lỗi ERR_PROXY_CONNECTION_FAILED trên MỌI nick. Dev muốn Clash thì ghi
# DOLA_PROXY=http://127.0.0.1:7890 vào .env.local. Phải khớp DEFAULT_PROXY trong desktop/proxy.cjs.
PROXY = os.getenv("DOLA_PROXY", "").strip()

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
# Extension nạp vào MỌI profile nick (mặc định extensions/khan). Đặt DOLA_EXTRA_EXTENSION_DIR="" để tắt.
# LƯU Ý: nạp extension buộc Chrome chạy CÓ CỬA SỔ (MV3 không chạy headless) → mỗi nick một Chrome hiện hình.
# MẶC ĐỊNH TẮT ("") vì đã kiểm 13/09: patchright KHÔNG nạp extension chưa đóng gói (fetch không bị bọc,
# content-script không chạy) — bật lên chỉ tổ buộc Chrome headed (tốn RAM, dễ timeout) mà extension vẫn im.
# Đặt DOLA_EXTRA_EXTENSION_DIR=/đường/dẫn nếu sau này dùng trình duyệt nạp được extension.
EXTRA_EXTENSION_DIR = os.getenv("DOLA_EXTRA_EXTENSION_DIR", "")

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
# Mặc định "http": ký a_bogus bằng Python + gửi thẳng bằng cookies.json, KHÔNG mở Chrome mỗi nick (nhẹ RAM,
# nhiều nick, đăng nhập không phải giết gateway). Nick thiếu/hỏng cookies.json hoặc Dola từ chối ký → tự rơi
# về đường Chrome (fetch) an toàn. Đặt DOLA_SUBMIT_MODE=fetch để ép luôn dùng Chrome.
SUBMIT_MODE = os.getenv("DOLA_SUBMIT_MODE", "http").strip().lower()

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

# ── Antidetect (chống phát hiện) ─────────────────────────────────
# Ngôn ngữ + múi giờ trình duyệt cho MỌI nick. PHẢI khớp vùng của IP proxy (IP Nhật thì để Tokyo/ja-JP;
# IP Việt thì Asia/Ho_Chi_Minh + vi-VN) — IP một nơi mà giờ/ngôn ngữ nơi khác là dấu hiệu dễ bị soi.
# Dola khoá vùng Nhật nên mặc định Tokyo/ja-JP; dùng proxy Nhật/Hàn để nhất quán.
BROWSER_TIMEZONE = os.getenv("DOLA_TIMEZONE", "Asia/Tokyo").strip()
BROWSER_LOCALE = os.getenv("DOLA_LOCALE", "ja-JP").strip()
# Chặn WebRTC lộ IP THẬT của máy (proxy vô nghĩa nếu WebRTC rò IP thật). Mặc định BẬT.
BLOCK_WEBRTC = os.getenv("DOLA_BLOCK_WEBRTC", "1").strip().lower() in ("1", "true", "yes", "on")
# Gán fingerprint (WebGL/CPU/RAM…) theo TỪNG nick bằng JS override. MẶC ĐỊNH TẮT: tool KHÔNG có nhân
# Chromium vá, nên JS tampering (defineProperty/getParameter) DỄ BỊ PHÁT HIỆN hơn là để native — hại
# nhiều hơn lợi. Fingerprint per-nick thật sự chỉ nên đạt bằng MÁY/VM khác nhau hoặc antidetect browser vá
# nhân. Chỉ bật (DOLA_FINGERPRINT=1) khi bạn hiểu rủi ro và Dola không dò tampering sâu.
FINGERPRINT_PER_NICK = os.getenv("DOLA_FINGERPRINT", "0").strip().lower() in ("1", "true", "yes", "on")

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
