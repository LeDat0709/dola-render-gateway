"""Patchright persistent context launcher: Explicit proxy and anti-detection parameters."""
import asyncio
import hashlib
import os
import shutil
import subprocess
import time
import tempfile
from pathlib import Path

import config

_HEADLESS_UA = None  # de-headlessed UA, probed once per process

# Playwright init-script that unlocks 30s in the composer the SAME way the dola30 extension does,
# but by hijacking window.fetch in the page (no chrome.debugger, so it runs HEADLESS and shows no
# "debugging this browser" bar). It PATCHES the real skill-pack + action-bar responses to add a 30s
# option, instead of the debugger's binary interception. Toggle with DOLA_SKILLPACK_HIJACK=0.
_THIRTYSEC_HIJACK_JS = r"""
(() => {
  if (window.__dola30Hijack) return; window.__dola30Hijack = true;
  const of = window.fetch;
  const m = (u, p) => (typeof u === 'string' ? u : (u && u.url) || '').includes(p);

  const patchSkillPack = (obj) => {
    try {
      const meta = obj && obj.data && obj.data.video_generation && obj.data.video_generation.meta;
      if (!meta) return obj;
      for (const opt of (meta.option_list || [])) {
        if (opt.value === 'duration' || /duration|时长|時間|長さ/i.test(opt.show_name || '')) {
          const opts = opt.options = opt.options || [];
          if (!opts.some(o => String(o.value) === '30'))
            opts.push({ show_name: '30s', value: '30', is_default: false, sub_display: '' });
        }
      }
      const cap = meta.model_capability || {};
      for (const k of Object.keys(cap)) {
        const d = (cap[k].supported_durations || []).map(String);
        if (!d.includes('30')) d.push('30');
        cap[k].supported_durations = d;
      }
    } catch (e) {}
    return obj;
  };

  // Walk action-bar JSON (and nested JSON strings) inserting a 30s option into duration selectors.
  const isDur = (label, key, options) => key === 'video-duration' || key === 'duration'
    || /时长|時間|長さ|duration/i.test(label || '')
    || (options.some(o => String((o && (o.option_key || o.value)) || '') === '5')
        && options.some(o => String((o && (o.option_key || o.value)) || '') === '10'));
  const patchAB = (v) => {
    if (!v || typeof v !== 'object') return v;
    if (Array.isArray(v)) { v.forEach(patchAB); return v; }
    const options = Array.isArray(v.option_list) ? v.option_list : [];
    const label = String(v.label || v.display_text || v.show_name || '');
    const key = String(v.key || v.value || '');
    if (options.length && isDur(label, key, options)) {
      if (!options.some(o => String((o && (o.option_key || o.value)) || '') === '30')) {
        const ti = options.findIndex(o => String((o && (o.option_key || o.value)) || '') === '10');
        const maxId = options.reduce((mx, o) => Number.isFinite(Number(o && o.id)) ? Math.max(mx, Number(o.id)) : mx, 0);
        options.splice(ti >= 0 ? ti + 1 : options.length, 0, { id: maxId + 1, display_text: '30s', message_text: '', option_key: '30' });
      }
    }
    for (const k of Object.keys(v)) {
      const c = v[k];
      if (typeof c === 'string' && (c.includes('"option_key"') || c.includes('"video-duration"') || c.includes('duration'))) {
        try { const j = JSON.parse(c); patchAB(j); v[k] = JSON.stringify(j); } catch (e) {}
      } else { patchAB(c); }
    }
    return v;
  };

  window.fetch = async function (input, init) {
    const r = await of.apply(this, arguments);
    try {
      if (m(input, 'samantha/skill/pack') || m(input, 'skill/pack')) {
        const j = await r.clone().json();
        return new Response(JSON.stringify(patchSkillPack(j)), { status: r.status, statusText: r.statusText, headers: r.headers });
      }
      if (m(input, 'action_bar_v3/get_item_conf') || m(input, 'get_item_conf') || m(input, 'slot/action_bar')) {
        const j = await r.clone().json();
        return new Response(JSON.stringify(patchAB(j)), { status: r.status, statusText: r.statusText, headers: r.headers });
      }
    } catch (e) {}
    return r;
  };
})();
"""



# NOTE: do NOT add "--disable-blink-features=AutomationControlled" here.
# patchright already neutralizes navigator.webdriver; passing that flag is itself a
# well-known detection signal (sites read the launch flags / resulting inconsistencies).
LAUNCH_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
]

# ── Antidetect init-scripts ──────────────────────────────────────
# #1 WebRTC: kể cả có cờ launch, chặn thêm ở JS — bỏ iceServers để không dò STUN/TURN lộ IP thật.
_WEBRTC_JS = r"""
(() => { try {
  const O = window.RTCPeerConnection || window.webkitRTCPeerConnection;
  if (!O) return;
  const W = function (cfg, ...rest) { if (cfg && cfg.iceServers) cfg = Object.assign({}, cfg, { iceServers: [] }); return new O(cfg, ...rest); };
  W.prototype = O.prototype;
  window.RTCPeerConnection = W; window.webkitRTCPeerConnection = W;
} catch (e) {} })();
"""

# #3 Fingerprint per-nick: GPU (WebGL vendor/renderer) + CPU + RAM cố định theo nick, KHÔNG động canvas
# (spoof canvas nửa vời còn dễ lộ hơn). Giá trị chọn từ danh sách THỰC TẾ theo seed = hash tên nick.
_WEBGL_FP = [
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
]
_FP_CORES = [4, 6, 8, 8, 12, 16]
_FP_MEM = [4, 8, 8, 16]
_FP_TEMPLATE = r"""
(() => {
  try {
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => %CORES% });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => %MEM% });
  } catch (e) {}
  try {
    const V = "%VENDOR%", R = "%RENDERER%";
    const patch = (proto) => { if (!proto || !proto.getParameter) return; const gp = proto.getParameter;
      proto.getParameter = function (p) { if (p === 37445) return V; if (p === 37446) return R; return gp.call(this, p); }; };
    patch(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
    patch(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
  } catch (e) {}
})();
"""


def _fp_seed(account: str) -> int:
    return int(hashlib.sha1(("dola-fp:" + str(account)).encode()).hexdigest()[:8], 16)


def _fingerprint_js(account: str) -> str:
    """JS gán fingerprint ổn định cho nick (cùng nick → luôn giống; khác nick → khác)."""
    s = _fp_seed(account)
    vendor, renderer = _WEBGL_FP[s % len(_WEBGL_FP)]
    return (_FP_TEMPLATE
            .replace("%CORES%", str(_FP_CORES[(s >> 3) % len(_FP_CORES)]))
            .replace("%MEM%", str(_FP_MEM[(s >> 6) % len(_FP_MEM)]))
            .replace("%VENDOR%", vendor).replace("%RENDERER%", renderer))


def _is_bare_tmproxy_key(raw: str) -> bool:
    """Key TMProxy trần: 32 ký tự hex (không có scheme/host/port). proxyxoay/khác luôn phải là link."""
    return len(raw) == 32 and all(c in "0123456789abcdefABCDEF" for c in raw)


def normalize_proxy_input(raw: str) -> str:
    """Chuẩn hoá đầu vào proxy trước khi phân tích/lưu. KEY TMProxy trần (32 hex) → 'tmproxy://KEY' để dán
    mỗi key là chạy (TMProxy xác thực bằng KEY, không whitelist IP). proxyxoay và nhà bán get.php khác phải
    dán NGUYÊN link vì xác thực theo IP máy. Các dạng còn lại giữ nguyên."""
    raw = (raw or "").strip()
    return f"tmproxy://{raw}" if _is_bare_tmproxy_key(raw) else raw


def is_rotating_proxy(raw: str) -> bool:
    """Proxy XOAY được (đổi IP theo yêu cầu): key/link TMProxy, hoặc link get.php?key=… (proxyxoay & tương tự).
    Proxy tĩnh (ip:port…) hoặc nối thẳng → False. Dùng để bật chế độ MỖI LẦN MỘT NICK đúng lúc."""
    s = normalize_proxy_input(raw)
    if s.lower().startswith("tmproxy://"):
        return True
    import proxyxoay
    return proxyxoay.is_key_link(s)


def _rotating_ent(raw: str, do_rotate: bool) -> dict | None:
    """IP hiện hành (do_rotate=False) hoặc xin IP mới (True) của proxy xoay — dùng chung TMProxy & proxyxoay.
    Trả {ip, network, location, expiration, message} cho thẻ làn; không phải proxy xoay → None. Ném lỗi nhà
    bán (TMProxyError/ProxyXoayError) để nơi gọi hiện thông báo."""
    s = normalize_proxy_input(raw)
    if s.lower().startswith("tmproxy://"):
        import tmproxy
        key = tmproxy.key_of(s)
        ent = tmproxy.rotate(key) if do_rotate else tmproxy.current(key)
        return {"ip": ent.get("public_ip") or ent.get("https", ""), "network": "", "location": "",
                "expiration": "", "message": ""}
    import proxyxoay
    if proxyxoay.is_key_link(s):
        ent = proxyxoay.rotate(s) if do_rotate else proxyxoay.current(s)
        return {k: ent.get(k, "") for k in ("ip", "network", "location", "expiration", "message")}
    return None


def rotating_lane(raw: str) -> dict | None:
    return _rotating_ent(raw, False)


def rotating_rotate(raw: str) -> dict | None:
    return _rotating_ent(raw, True)


def parse_proxy(raw: str) -> dict | None:
    """Turns a proxy string into a patchright proxy dict {server, username?, password?}.

    Accepts: scheme://user:pass@host:port, scheme://host:port, host:port,
    host:port:user:pass (the common account-shop format), tmproxy://KEY hoặc KEY TMProxy trần (32 hex),
    và link xoay get.php?key=…. Default scheme http.
    """
    raw = normalize_proxy_input(raw)
    if not raw:
        return None
    if raw.lower().startswith("tmproxy://"):
        import tmproxy   # proxy xoay theo API key: tool tự lấy IP hiện hành (cache), xem tmproxy.py
        return tmproxy.resolve_dict(raw)
    import proxyxoay   # proxy xoay theo LINK get.php?key=… (proxyxoay.shop và tương tự), xem proxyxoay.py
    if proxyxoay.is_key_link(raw):
        return proxyxoay.resolve_dict(raw)
    scheme = "http"
    if "://" in raw:
        scheme, raw = raw.split("://", 1)
    user = pw = None
    if "@" in raw:  # user:pass@host:port
        cred, raw = raw.rsplit("@", 1)
        if ":" in cred:
            user, pw = cred.split(":", 1)
        else:
            user = cred
    parts = raw.split(":")
    if len(parts) == 4 and user is None:  # host:port:user:pass
        host, port, user, pw = parts
    elif len(parts) >= 2:
        host, port = parts[0], parts[1]
    else:
        return None
    out = {"server": f"{scheme}://{host}:{port}"}
    if user:
        out["username"] = user
    if pw:
        out["password"] = pw
    return out


import secrets as _secrets

# Proxy xoay sticky-session: mỗi nick giữ 1 session id (=1 IP) suốt 1 video; đổi sau mỗi N video.
_proxy_sessions: dict[str, dict] = {}   # nick -> {"id": str, "count": int}


def _current_session(account: str) -> str:
    st = _proxy_sessions.get(account)
    if not st:
        st = _proxy_sessions[account] = {"id": _secrets.token_hex(4), "count": 0}
    return st["id"]


def rotate_proxy_session(account: str, every: int) -> str:
    """Gọi lúc BẮT ĐẦU mỗi job. Giữ session cũ trong N video; video thứ N+1 → session mới (IP mới).
    Trả session id hiện hành. every<=0 = không xoay."""
    st = _proxy_sessions.get(account)
    if not st:
        return _current_session(account)
    if every > 0:
        st["count"] += 1
        if st["count"] >= every:
            st["id"] = _secrets.token_hex(4)
            st["count"] = 0
            rotate_tmproxy_now(account)   # nick dùng tmproxy://KEY → xin IP mới thật sự
    return st["id"]


def _rotate_raw(raw: str, label: str) -> None:
    """Xin IP mới cho MỘT chuỗi proxy xoay (tmproxy://KEY, KEY trần, hoặc link get.php?key=…). Không phải
    proxy xoay hoặc lỗi mạng → giữ IP cũ. `label` chỉ để in log."""
    raw = normalize_proxy_input(raw)
    try:
        if raw.lower().startswith("tmproxy://"):
            import tmproxy
            ip = tmproxy.rotate(tmproxy.key_of(raw))["https"]
        else:
            import proxyxoay
            if not proxyxoay.is_key_link(raw):
                return
            ip = proxyxoay.rotate(raw)["ip"]
        print(f"[proxy] {label}: IP hiện hành {ip}", flush=True)
    except Exception as exc:
        print(f"[proxy] {label}: đổi IP thất bại, giữ IP cũ: {str(exc)[:100]}", flush=True)


def rotate_tmproxy_now(account: str) -> None:
    """Nick dùng proxy XOAY RIÊNG (proxy.txt) → xin IP mới. KHÔNG đụng proxy chung vì nhiều nick song song
    có thể đang dùng. Gọi lúc tới lượt xoay (rotate_proxy_session) và khi Dola báo 710022002 (chặn theo IP)."""
    _rotate_raw(account_proxy_raw(account), account)


def rotate_effective_proxy(account: str) -> None:
    """Đổi IP proxy nick ĐANG dùng: riêng (proxy.txt) nếu có, không thì PROXY CHUNG xoay. An toàn ở chế độ
    MỖI LẦN MỘT NICK vì chỉ 1 nick chạy tại một thời điểm nên đổi proxy chung không cắt IP của nick khác."""
    _rotate_raw(account_proxy_raw(account) or config.PROXY, account)


def _sub_session(raw: str, account: str) -> str:
    """Thay {SESSION} trong chuỗi proxy bằng session hiện hành của nick (giữ nguyên trong 1 video)."""
    return raw.replace("{SESSION}", _current_session(account)) if raw and "{SESSION}" in raw else raw


def account_proxy(account: str) -> dict | None:
    """Per-account proxy from accounts/<account>/proxy.txt, else the global config.PROXY.

    Lets each nick egress from its own IP (Dola flags many nicks on one IP; one dead IP
    then kills only that nick, not the whole pool).
    """
    try:
        f = config.ACCOUNTS_DIR / account / "proxy.txt"
        if f.exists():
            raw = f.read_text(encoding="utf-8")
            got = parse_proxy(_sub_session(raw, account))
            if got:
                return got
            if is_rotating_proxy(raw):
                # Proxy XOAY riêng (tmproxy://KEY, key trần, hoặc link get.php) không lấy được IP: KHÔNG lặng lẽ
                # rơi về proxy chung/IP máy — nick sẽ lộ IP thật và bị Dola gom chung. Báo lỗi rõ để sửa.
                raise RuntimeError(
                    f"Proxy xoay riêng của nick {account} không lấy được IP ({mask_proxy(raw)}) — kiểm tra "
                    "key/link, hạn dùng và whitelist IP trên trang nhà bán.")
    except OSError:
        pass
    return parse_proxy(config.PROXY)


def account_proxy_url(account: str) -> str:
    """Proxy của nick dưới dạng URL cho aiohttp: 'scheme://user:pass@host:port', hoặc '' nếu không có.

    Poll trạng thái và tải video (HTTP thuần) phải đi CÙNG proxy với lúc gửi (trình duyệt), nếu không
    Dola thấy cùng cuộc trò chuyện bị hỏi từ IP khác → nghi ngờ, và phần poll dồn về một IP chung."""
    p = account_proxy(account)
    if not p:
        return ""
    server = p["server"]                     # scheme://host:port
    user, pw = p.get("username"), p.get("password")
    if not user:
        return server
    from urllib.parse import quote
    scheme, host = server.split("://", 1)
    cred = quote(user, safe="") + ((":" + quote(pw, safe="")) if pw else "")
    return f"{scheme}://{cred}@{host}"


def account_proxy_raw(account: str) -> str:
    """Chuỗi proxy riêng của nick (accounts/<nick>/proxy.txt); "" = dùng proxy chung."""
    try:
        f = config.ACCOUNTS_DIR / account / "proxy.txt"
        return f.read_text(encoding="utf-8").strip() if f.exists() else ""
    except OSError:
        return ""


def mask_proxy(raw: str) -> str:
    """Che mật khẩu để trả ra giao diện: scheme://user:•••@host:port hoặc host:port:user:•••."""
    import re
    s = (raw or "").strip()
    if s.lower().startswith("tmproxy://"):
        import tmproxy
        return tmproxy.mask(s)
    import proxyxoay
    if proxyxoay.is_key_link(s):
        return proxyxoay.mask(s)
    m = re.match(r"^(\w+://)?([^:@/]+):([^@/]+)@(.+)$", s)
    if m:
        return f"{m.group(1) or ''}{m.group(2)}:•••@{m.group(4)}"
    p = re.sub(r"^\w+://", "", s).split(":")
    return f"{p[0]}:{p[1]}:{p[2]}:•••" if len(p) >= 4 else s


async def probe_proxy(raw: str, timeout: float = 3.0) -> str:
    """Mở thử TCP tới host:port của proxy. Trả "" nếu nối được, ngược lại là lý do ngắn.

    Chỉ kiểm tra cổng có mở không (không kiểm tra mật khẩu / exit node) — đủ để báo "proxy tắt,
    sai cổng" NGAY lúc khởi động, thay vì để từng nick chết ERR_PROXY_CONNECTION_FAILED.
    """
    import asyncio
    p = parse_proxy(raw)
    if not p:
        return "sai định dạng"
    host, port = p["server"].split("://", 1)[1].rsplit(":", 1)
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, int(port)), timeout)
        w.close()
        return ""
    except Exception as exc:
        return str(exc)[:60] or type(exc).__name__


def set_account_proxy(account: str, raw: str) -> None:
    """Writes/clears accounts/<account>/proxy.txt (empty raw removes it → back to global)."""
    d = config.ACCOUNTS_DIR / account
    d.mkdir(parents=True, exist_ok=True)
    f = d / "proxy.txt"
    if (raw or "").strip():
        f.write_text(raw.strip(), encoding="utf-8")
    elif f.exists():
        f.unlink()


def assert_profile_free(profile_dir: Path) -> None:
    """Fails fast when another live Chromium already holds this profile.

    Chromium writes SingletonLock as a symlink to "<host>-<pid>". If that pid is alive,
    a second launch silently hands off to it and exits, surfacing only as TargetClosedError.
    Stale locks from dead pids are cleaned up automatically.
    """
    lock = profile_dir / "SingletonLock"
    if not lock.is_symlink():
        return
    try:
        pid_text = os.readlink(lock).rsplit("-", 1)[-1]
    except OSError:
        return
    if not pid_text.isdigit():
        return
    pid = int(pid_text)

    def _clean():
        for f in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                (profile_dir / f).unlink(missing_ok=True)
            except Exception:
                pass

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        _clean()   # pid chết -> khoá cũ, dọn đi
        return
    except PermissionError:
        _clean()
        return
    # pid còn sống: có phải Chrome mồ côi đang giữ ĐÚNG profile này không?
    # Khoá per-account của pool đảm bảo không có job hợp lệ nào đang dùng khi tới đây,
    # nên nếu 1 tiến trình giữ profile này thì đó là mồ côi (job cũ crash / cửa sổ login sót) -> thu hồi.
    holds = False
    try:
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=4).stdout
        holds = str(profile_dir) in cmd or f"user-data-dir={profile_dir}" in cmd
    except Exception:
        holds = False
    if holds:
        try:
            os.kill(pid, 9)
        except Exception:
            pass
        time.sleep(0.6)
        _clean()
        print(f"[profile] thu hồi {profile_dir.name}: đã kill Chrome mồ côi pid {pid}", flush=True)
        return
    # pid sống nhưng không dùng profile này (pid bị tái sử dụng) -> khoá cũ, dọn đi
    _clean()



async def _headless_ua(p) -> str:
    """Returns a UA with the 'HeadlessChrome' tell stripped.

    Headless Chrome brands its user agent 'HeadlessChrome/<ver>', which sites flag
    instantly. We probe the real UA once (throwaway profile) and swap the token so it
    matches a normal headed Chrome of the same version. config.BROWSER_UA pins it instead.
    """
    # ponytail: no lock — a concurrent cold start may probe a few times before the cache
    # fills; harmless (idempotent) and only at startup. Add an asyncio.Lock if it bites.
    global _HEADLESS_UA
    if _HEADLESS_UA is not None:
        return _HEADLESS_UA
    if config.BROWSER_UA:
        _HEADLESS_UA = config.BROWSER_UA
        return _HEADLESS_UA
    tmp = tempfile.mkdtemp(prefix="ua-probe-")
    kwargs = {"headless": True, "args": list(LAUNCH_ARGS)}
    if config.BROWSER_CHANNEL:
        kwargs["channel"] = config.BROWSER_CHANNEL
    ctx = await p.chromium.launch_persistent_context(tmp, **kwargs)
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        raw = await page.evaluate("() => navigator.userAgent")
    finally:
        await ctx.close()
        shutil.rmtree(tmp, ignore_errors=True)
    _HEADLESS_UA = raw.replace("HeadlessChrome", "Chrome")
    return _HEADLESS_UA


async def launch_account_context(p, account: str, headless: bool = None, use_extension: bool = False):
    """Launches accounts/<account> profile, returns BrowserContext. Caller must close.

    p: async_playwright() instance
    headless: None = uses config.HEADLESS
    """
    profile_dir = config.ACCOUNTS_DIR / account
    if not profile_dir.exists():
        raise FileNotFoundError(
            f"Account profile does not exist: {profile_dir} (run python add_account.py {account} first)"
        )
    # Hàm này gọi `ps` và ngủ 0.6s đồng bộ; chạy thẳng trong coroutine là đứng cả vòng lặp sự kiện
    # (mọi nick khác, /health) tới ~4.6s cho mỗi profile có Chrome mồ côi sau crash.
    await asyncio.to_thread(assert_profile_free, profile_dir)
    launch_headless = config.HEADLESS if headless is None else headless
    args = list(LAUNCH_ARGS)
    if config.BLOCK_WEBRTC:   # #1: ép WebRTC đi qua proxy → không lộ IP thật của máy
        args.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
    ext_dirs = []
    hijack_30s = use_extension and config.SKILLPACK_HIJACK
    if use_extension and not hijack_30s:
        # Legacy path: chrome.debugger extension (needs a headed window, shows the debug bar).
        if not config.EXTENSION_ENABLED:
            raise RuntimeError("Dola extension is disabled (DOLA_EXTENSION_ENABLED=0)")
        extension_dir = Path(config.EXTENSION_DIR).resolve()
        if not extension_dir.exists():
            raise FileNotFoundError(f"Dola extension directory does not exist: {extension_dir}")
        ext_dirs.append(str(extension_dir))
    # Extension nạp vào MỌI profile nick (config.EXTRA_EXTENSION_DIR). MV3 không chạy headless → buộc có cửa sổ.
    if config.EXTRA_EXTENSION_DIR:
        extra = Path(config.EXTRA_EXTENSION_DIR).resolve()
        if extra.exists():
            ext_dirs.append(str(extra))
        else:
            print(f"[browser] DOLA_EXTRA_EXTENSION_DIR không tồn tại, bỏ qua: {extra}", flush=True)
    if ext_dirs:
        launch_headless = False
        args.append(f"--disable-extensions-except={','.join(ext_dirs)}")
        args.extend(f"--load-extension={d}" for d in ext_dirs)
    kwargs = {
        "headless": launch_headless,
        "args": args,
        "locale": config.BROWSER_LOCALE,        # #2: khớp vùng IP proxy (env DOLA_LOCALE)
        "timezone_id": config.BROWSER_TIMEZONE,  # #2: khớp vùng IP proxy (env DOLA_TIMEZONE)
    }
    # Real Chrome fingerprints far better than bundled Chromium. Empty channel =
    # fall back to bundled Chromium (hosts without Chrome installed).
    if config.BROWSER_CHANNEL:
        kwargs["channel"] = config.BROWSER_CHANNEL
    # Headless leaks "HeadlessChrome" in the UA; override with the de-headlessed UA.
    if launch_headless:
        kwargs["user_agent"] = await _headless_ua(p)
    # tmproxy://KEY → account_proxy gọi API TMProxy (đồng bộ, có cache) → đưa ra thread cho vòng lặp không khựng.
    proxy_cfg = await asyncio.to_thread(account_proxy, account)
    if proxy_cfg:
        kwargs["proxy"] = proxy_cfg
    context = await p.chromium.launch_persistent_context(str(profile_dir), **kwargs)
    if config.BLOCK_WEBRTC:                       # #1: chặn WebRTC lộ IP thật (song song cờ launch)
        await context.add_init_script(_WEBRTC_JS)
    if config.FINGERPRINT_PER_NICK:               # #3: fingerprint GPU/CPU/RAM cố định theo từng nick
        await context.add_init_script(_fingerprint_js(account))
    # Every flow (worker, verify, cookie import) must see the same Dola UI language.
    await force_ui_language(context)
    if hijack_30s:
        # Unlock 30s by patching the skill-pack / action-bar in-page (headless, no debugger).
        await context.add_init_script(_THIRTYSEC_HIJACK_JS)
    return context


async def verify_cookie_http(cookie_str: str, timeout: int = 20) -> tuple[bool | None, str]:
    """Check a Dola session with ONE plain HTTP call (no browser) — fast login verify.

    Hits the read-only /im/chain/recent_conv: a live session returns a downlink_body, a dead
    one returns status_code 712012001 ("登录" / login required). ~1s vs ~8s for a browser check.

    Trả (True, …) sống, (False, …) Dola nói CHƯA đăng nhập, (None, …) KHÔNG kiểm tra được (proxy
    chết, mất mạng, WAF trả HTTP lạ). Máy Windows mới chưa đặt proxy từng nhập 9 nick thì 8 nick
    bị ghi "cookie chết" oan chỉ vì proxy mặc định 127.0.0.1:7890 không chạy.
    """
    import json as _json
    import uuid as _uuid
    import aiohttp
    params = {"version_code": "20800", "language": "ja", "device_platform": "web",
              "doubao_device_platform": "web", "aid": "495671", "real_aid": "495671",
              "pkg_type": "release_version", "pc_version": "3.32.62", "doubao_pc_version": "3.32.62",
              "region": "JP", "sys_region": "JP", "samantha_web": "1", "web_platform": "browser",
              "use-olympus-account": "1", "web_tab_id": str(_uuid.uuid4())}
    body = {"cmd": 3200, "uplink_body": {"pull_recent_conv_chain_uplink_body": {
        "limit": 1, "message_count_per_conv": 1, "api_version": 1, "conv_version": 0, "direction": 3,
        "option": {"not_need_message": True, "need_complete_conversation": True}}},
        "sequence_id": str(_uuid.uuid4()), "channel": 2, "version": "1"}
    headers = {"Content-Type": "application/json; encoding=utf-8", "agw-js-conv": "str",
               "Accept": "*/*", "cookie": cookie_str}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post("https://www.dola.com/im/chain/recent_conv", params=params,
                                 data=_json.dumps(body), headers=headers, proxy=config.PROXY or None,
                                 timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status != 200:
                    return None, f"Dola/WAF trả HTTP {r.status} (chưa kết luận được cookie)"
                data = await r.json(content_type=None)
    except Exception as exc:
        return None, f"không tới được dola.com qua {config.PROXY or 'nối thẳng'}: {str(exc)[:80]}"
    code = data.get("status_code")
    desc = str(data.get("status_desc") or "")
    if code in (712012001,) or "登录" in desc or "login" in desc.lower():
        return False, "Cookie chưa đăng nhập / đã hết hạn (Dola đòi đăng nhập)."
    if data.get("downlink_body") is not None:
        return True, "Phiên Dola còn sống."
    return None, f"Phản hồi không rõ (code={code})."


def cookie_value(cookies: list, name: str) -> str:
    """Extracts cookie value from context.cookies() result."""
    return next((c["value"] for c in cookies if c["name"] == name and c["value"]), "")


def persist_dola_cookies(account: str, cookies: list) -> Path:
    """Persists extracted Dola cookies to accounts/<account>/cookies.json for backup and export."""
    profile_dir = config.ACCOUNTS_DIR / account
    profile_dir.mkdir(parents=True, exist_ok=True)
    out_file = profile_dir / "cookies.json"
    import json
    out_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_file


_FAR_FUTURE = 1800000000  # 2027-01-15: far-future cookie expiry so profiles keep sessions across restarts


async def force_ui_language(context_or_page, lang: str | None = None):
    """Pin Dola's UI language for THIS profile, whatever it was before.

    Every account then renders the same UI, so the automation only has to match one
    language (default Japanese, config.UI_LANG). Must run BEFORE navigating to dola.com.
    """
    lang = lang or config.UI_LANG
    ctx = context_or_page if hasattr(context_or_page, "add_cookies") else getattr(context_or_page, "context", None)
    if ctx is None:
        return
    cookies = [
        {"name": name, "value": lang, "domain": domain, "path": "/", "secure": True, "expires": _FAR_FUTURE}
        for name in ("i18next", "user_language_code")
        for domain in (".dola.com", "www.dola.com")
    ]
    try:
        await ctx.add_cookies(cookies)
    except Exception as exc:
        print(f"  (force_ui_language skipped: {str(exc)[:80]})", flush=True)


async def pin_session_cookies(context) -> None:
    """Re-save Dola cookies with a far-future expiry.

    A fresh login (Google/QR/Facebook) or a pasted cookie often lands `sessionid` as a
    *session* cookie with no expiry; Chromium keeps those only in memory and drops them
    when the persistent context closes, so the profile looks logged-out next run.
    """
    try:
        live = await context.cookies("https://www.dola.com")
    except Exception as exc:
        print(f"  (pin_session_cookies: cannot read cookies: {str(exc)[:80]})", flush=True)
        return
    pinned = []
    for c in live:
        same_site = c.get("sameSite")
        pinned.append({
            "name": c["name"], "value": c["value"],
            "domain": c.get("domain") or ".dola.com", "path": c.get("path") or "/",
            "secure": bool(c.get("secure", True)), "httpOnly": bool(c.get("httpOnly", False)),
            "sameSite": same_site if same_site in ("Strict", "Lax", "None") else "Lax",
            "expires": _FAR_FUTURE,
        })
    if not pinned:
        return
    try:
        await context.add_cookies(pinned)
    except Exception as exc:
        print(f"  (pin_session_cookies failed: {str(exc)[:80]})", flush=True)


# A stale sessionid cookie can survive a server-side logout (?from_logout=1), so the
# composer textarea alone is NOT proof of a live session — it renders even behind the
# login wall. Treat a visible login CTA (JP "please log in" / Google button) as logged-out.
_LOGGED_IN_JS = """() => {
    const body = document.body ? document.body.innerText : '';
    if (body.includes('ログインしてください')) return false;
    const btns = [...document.querySelectorAll('button, [role="button"]')];
    if (btns.some(b => /Googleで続ける|^ログイン$/.test((b.textContent||'').trim())))
        return false;
    return !!(document.querySelector('textarea')
        || document.querySelector('[contenteditable="true"]'));
}"""


async def page_logged_in(page) -> bool:
    """True when the currently-loaded Dola page shows a live, authenticated session."""
    return bool(await page.evaluate(_LOGGED_IN_JS))


class RegionBlockedError(RuntimeError):
    """Dola trả trang "この国または地域ではDolaは利用できません" (không khả dụng ở quốc gia/khu vực này).

    Lỗi của ĐƯỜNG RA MẠNG (proxy riêng chết/đổi vùng, hoặc không proxy nên đi thẳng từ VN), không
    phải của nick hay prompt. Trang này không có khung chat → mọi bước sau (tìm nút tạo video,
    bdms/msToken, kiểm tra đăng nhập) đều trượt với thông báo đánh lạc hướng ("không thấy nút
    Tạo video", "cookie chết"). Bắt ở chỗ mở trang để nói đúng bệnh.
    """


_REGION_BLOCKED_JS = """() => {
    if (document.querySelector('textarea') || document.querySelector('[contenteditable="true"]')) return 'ready';
    const body = document.body ? document.body.innerText : '';
    const marks = ['地域ではDolaは利用できません', 'not available in your country', 'not available in your region',
                   'không khả dụng ở quốc gia', 'không khả dụng tại quốc gia', '国家或地区', '國家或地區'];
    if (marks.some(m => body.includes(m))) return 'blocked';
    if (body.includes('ログインしてください')) return 'login';
    const btns = [...document.querySelectorAll('button, [role="button"]')];
    if (btns.some(b => /Googleで続ける|^ログイン$/.test((b.textContent||'').trim()))) return 'login';
    return '';
}"""


async def page_region_blocked(page, wait_s: float = 6.0) -> bool:
    """True khi trang hiện là màn chặn vùng của Dola. Đợi tối đa wait_s cho SPA vẽ xong: dừng sớm
    ngay khi thấy khung chat (bình thường) hoặc màn đăng nhập (để _is_logged_out lo)."""
    deadline = time.time() + wait_s
    while True:
        try:
            state = await page.evaluate(_REGION_BLOCKED_JS)
        except Exception:
            state = ""
        if state == "blocked":
            return True
        if state in ("ready", "login") or time.time() >= deadline:
            return False
        await asyncio.sleep(0.5)


def region_blocked_message(account: str = "") -> str:
    """Câu báo lỗi nói rõ nick đang ra mạng bằng đường nào và phải sửa ở đâu."""
    own = account_proxy_raw(account) if account else ""
    if own:
        via = f"proxy riêng của nick ({mask_proxy(own)}) đang thoát ở nước bị chặn hoặc đã chết → đổi proxy khác cho nick (tab Proxy)"
    elif config.PROXY:
        via = f"nick không có proxy riêng, đi proxy chung ({mask_proxy(config.PROXY)}) đang thoát ở nước bị chặn → đổi proxy chung hoặc gán proxy riêng (tab Proxy)"
    else:
        via = "nick không có proxy riêng và server đang nối thẳng (không proxy) → gán proxy Nhật/Mỹ cho nick ở tab Proxy"
    return ("Dola chặn vùng — trang báo 'Dola không khả dụng ở quốc gia/khu vực này'. "
            f"{via}. Không mất lượt.")


async def check_login_state(account: str) -> bool:
    """Opens Dola in headless mode and checks whether session is active."""
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        context = await launch_account_context(p, account)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.dola.com/chat", timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            if await page_region_blocked(page, wait_s=0):
                raise RegionBlockedError(region_blocked_message(account))   # không phải cookie chết
            cookies = await context.cookies("https://www.dola.com")
            if not cookie_value(cookies, "sessionid"):
                return False
            return await page_logged_in(page)
        finally:
            await context.close()

