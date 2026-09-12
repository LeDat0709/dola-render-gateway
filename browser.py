"""Patchright persistent context launcher: Explicit proxy and anti-detection parameters."""
import asyncio
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


def parse_proxy(raw: str) -> dict | None:
    """Turns a proxy string into a patchright proxy dict {server, username?, password?}.

    Accepts: scheme://user:pass@host:port, scheme://host:port, host:port,
    host:port:user:pass (the common account-shop format). Default scheme http.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
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


def account_proxy(account: str) -> dict | None:
    """Per-account proxy from accounts/<account>/proxy.txt, else the global config.PROXY.

    Lets each nick egress from its own IP (Dola flags many nicks on one IP; one dead IP
    then kills only that nick, not the whole pool).
    """
    try:
        f = config.ACCOUNTS_DIR / account / "proxy.txt"
        if f.exists():
            got = parse_proxy(f.read_text(encoding="utf-8"))
            if got:
                return got
    except OSError:
        pass
    return parse_proxy(config.PROXY)


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
    m = re.match(r"^(\w+://)?([^:@/]+):([^@/]+)@(.+)$", s)
    if m:
        return f"{m.group(1) or ''}{m.group(2)}:•••@{m.group(4)}"
    p = re.sub(r"^\w+://", "", s).split(":")
    return f"{p[0]}:{p[1]}:{p[2]}:•••" if len(p) >= 4 else s


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
    hijack_30s = use_extension and config.SKILLPACK_HIJACK
    if use_extension and not hijack_30s:
        # Legacy path: chrome.debugger extension (needs a headed window, shows the debug bar).
        if not config.EXTENSION_ENABLED:
            raise RuntimeError("Dola extension is disabled (DOLA_EXTENSION_ENABLED=0)")
        extension_dir = Path(config.EXTENSION_DIR).resolve()
        if not extension_dir.exists():
            raise FileNotFoundError(f"Dola extension directory does not exist: {extension_dir}")
        launch_headless = False
        args.extend([
            f"--disable-extensions-except={extension_dir}",
            f"--load-extension={extension_dir}",
        ])
    kwargs = {
        "headless": launch_headless,
        "args": args,
        "locale": "ja-JP",
        "timezone_id": "Asia/Tokyo",
    }
    # Real Chrome fingerprints far better than bundled Chromium. Empty channel =
    # fall back to bundled Chromium (hosts without Chrome installed).
    if config.BROWSER_CHANNEL:
        kwargs["channel"] = config.BROWSER_CHANNEL
    # Headless leaks "HeadlessChrome" in the UA; override with the de-headlessed UA.
    if launch_headless:
        kwargs["user_agent"] = await _headless_ua(p)
    proxy_cfg = account_proxy(account)
    if proxy_cfg:
        kwargs["proxy"] = proxy_cfg
    context = await p.chromium.launch_persistent_context(str(profile_dir), **kwargs)
    # Every flow (worker, verify, cookie import) must see the same Dola UI language.
    await force_ui_language(context)
    if hijack_30s:
        # Unlock 30s by patching the skill-pack / action-bar in-page (headless, no debugger).
        await context.add_init_script(_THIRTYSEC_HIJACK_JS)
    return context


async def verify_cookie_http(cookie_str: str, timeout: int = 20) -> tuple[bool, str]:
    """Check a Dola session with ONE plain HTTP call (no browser) — fast login verify.

    Hits the read-only /im/chain/recent_conv: a live session returns a downlink_body, a dead
    one returns status_code 712012001 ("登录" / login required). ~1s vs ~8s for a browser check.
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
                    return False, f"HTTP {r.status}"
                data = await r.json(content_type=None)
    except Exception as exc:
        return False, f"không kiểm tra được qua HTTP: {str(exc)[:80]}"
    code = data.get("status_code")
    desc = str(data.get("status_desc") or "")
    if code in (712012001,) or "登录" in desc or "login" in desc.lower():
        return False, "Cookie chưa đăng nhập / đã hết hạn (Dola đòi đăng nhập)."
    if data.get("downlink_body") is not None:
        return True, "Phiên Dola còn sống."
    return False, f"Phản hồi không rõ (code={code})."


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


async def check_login_state(account: str) -> bool:
    """Opens Dola in headless mode and checks whether session is active."""
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        context = await launch_account_context(p, account)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.dola.com/chat", timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            cookies = await context.cookies("https://www.dola.com")
            if not cookie_value(cookies, "sessionid"):
                return False
            return await page_logged_in(page)
        finally:
            await context.close()

