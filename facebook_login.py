"""Facebook OAuth login for Dola - Automated session establishment and profile setup.

Reconstructs and improves the automated Facebook OAuth flow from DomixHub-Seedance:
- Parses UID|PASS|2FA|COOKIE|UA or direct cookie strings.
- Injects Facebook session cookies (c_user, xs, datr, sb, fr) into persistent profile.
- Warmed session pre-check (detects checkpoint vs login-required).
- Automates Dola's Facebook OAuth button click, catches popup.
- Auto-confirms OAuth consent in multiple languages (Continue as / 続行 / Tiếp tục...).
- Passes age-gate confirmation (18+) if prompted.
- Extracts live Dola sessionid and updates account profile.
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from patchright.async_api import async_playwright

from browser import LAUNCH_ARGS, cookie_value, force_ui_language, persist_dola_cookies, pin_session_cookies
from add_account import totp  # reuse the RFC 6238 TOTP generator for Facebook 2FA
import config

FB_ICON = (
    "svg[data-icon='facebook'], svg.facebook-icon, "
    "[aria-label*='Facebook' i], [title*='Facebook' i], "
    "img[src*='facebook' i], button:has-text('Facebook'), [role='button']:has-text('Facebook')"
)
FB_ICON_INDEX = 0

LOGIN_LABELS = (
    "Log in", "Đăng nhập", "ログイン", "Sign in",
    "Continue with Facebook", "Facebookで続ける", "Tiếp tục với Facebook",
    "Log In", "Sign In"
)

CONTINUE_LABELS = (
    "Continue as", "Tiếp tục dưới tên", "Tiếp tục với tư cách", "Tiếp tục",
    "続行", "Đăng nhập bằng", "Log in as", "Continue", "同意して続行"
)


def extract_fb_credential(line: str) -> Dict[str, Any]:
    """Parses UID|PASS|2FA|COOKIE|UA or raw cookie line into structured dict."""
    line = line.strip()
    result = {
        "uid": None,
        "password": None,
        "totp": None,
        "cookie": "",
        "user_agent": None,
        "label": "",
    }
    if not line:
        return result

    if "|" in line:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) > 0 and parts[0]:
            result["uid"] = parts[0]
            result["label"] = parts[0]
        if len(parts) > 1 and parts[1]:
            result["password"] = parts[1]

        for idx, part in enumerate(parts):
            if any(k in part for k in ("c_user=", "xs=", "datr=", "sb=")):
                result["cookie"] = part
                if idx == 2 and parts[2] and "c_user=" not in parts[2] and "xs=" not in parts[2]:
                    result["totp"] = parts[2]
            elif any(ua_ind in part for ua_ind in ("Mozilla", "AppleWebKit", "Chrome", "Safari")):
                result["user_agent"] = part
            elif "@" in part and "." in part and not result.get("email"):
                result["email"] = part
                result["label"] = part


        if not result["cookie"]:
            for p in parts:
                if "=" in p:
                    result["cookie"] = p
                    break
    else:
        result["cookie"] = line

    return result


def parse_fb_cookies(raw: str, default_domain: str = ".facebook.com") -> List[Dict[str, Any]]:
    """Converts cookie string or netscape rows into Playwright cookie list for Facebook."""
    raw = raw.strip()
    if not raw:
        return []

    # If JSON
    if (raw.startswith("[") and raw.endswith("]")) or (raw.startswith("{") and raw.endswith("}")):
        try:
            items = json.loads(raw)
            if isinstance(items, dict):
                items = [items]
            out = []
            for it in items:
                if isinstance(it, dict) and "name" in it and "value" in it:
                    out.append({
                        "name": str(it["name"]).strip(),
                        "value": str(it["value"]).strip(),
                        "domain": it.get("domain", default_domain),
                        "path": it.get("path", "/"),
                        "httpOnly": bool(it.get("httpOnly", False)),
                        "secure": bool(it.get("secure", True)),
                        "sameSite": "Lax",
                    })
            if out:
                return out
        except Exception:
            pass

    # Normalize newlines
    semi_raw = raw.replace("\n", ";").replace("\r", ";")
    cookies = []
    for item in semi_raw.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, val = item.split("=", 1)
        name = key.strip()
        value = val.strip()
        if not name:
            continue
        is_http_only = name.lower() in ("xs", "datr", "fr", "sb", "c_user")
        cookies.append({
            "name": name,
            "value": value,
            "domain": default_domain,
            "path": "/",
            "secure": True,
            "httpOnly": is_http_only,
            "sameSite": "Lax",
        })
    return cookies


def _has_fb_session(cookies: List[Dict[str, Any]]) -> bool:
    """Returns True if essential Facebook session cookies c_user and xs exist."""
    names = {c["name"] for c in cookies if c.get("name") and c.get("value")}
    return "c_user" in names and "xs" in names


async def _wait_sessionid(ctx, seconds: float = 5.0, interval: float = 0.5) -> bool:
    """Chờ cookie Dola 'sessionid' xuất hiện, thoát NGAY khi có — như cách đối thủ bám sự kiện
    thay vì chờ cứng. Trả True nếu thấy phiên trong 'seconds' giây, False nếu hết giờ."""
    import time as _t
    deadline = _t.monotonic() + seconds
    while True:
        if cookie_value(await ctx.cookies("https://www.dola.com"), "sessionid"):
            return True
        if _t.monotonic() >= deadline:
            return False
        await asyncio.sleep(interval)


async def _warm_facebook_session(page, timeout: int = 15000) -> str:
    """Visits m.facebook.com or facebook.com to test if session is alive, checkpointed, or expired.
    
    Returns:
        'ok' - valid session
        'checkpoint' - identity verification challenge
        'login' - session invalid/expired, redirected to login
    """
    try:
        await page.goto("https://m.facebook.com", timeout=timeout, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
    except Exception:
        try:
            await page.goto("https://www.facebook.com", timeout=timeout, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
        except Exception:
            return "ok"  # proceed to Dola if fb direct check timed out

    curr_url = page.url.lower()
    text = ""
    try:
        text = (await page.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 500)")).lower()
    except Exception:
        pass

    if "/checkpoint/" in curr_url or "checkpoint" in curr_url:
        return "checkpoint"
    # Chốt "khoá tài khoản vì nghi bị hack — xác nhận đây là tài khoản của bạn để mở khoá":
    # đây là kiểm tra danh tính của Facebook, không tự vượt được (dẫn tới xác minh/CAPTCHA).
    if any(sig in text for sig in ("confirm you're human", "confirm your identity", "xác nhận danh tính",
                                   "we've detected unusual", "locked your account", "unlock it",
                                   "may have been hacked", "tài khoản đã bị khoá", "xác nhận đây là tài khoản")):
        return "checkpoint"
    if "/login" in curr_url or "login.php" in curr_url:
        return "login"

    return "ok"


# Toạ độ TÂM icon Facebook trong hộp thoại đăng nhập Dola. Trả None nếu không thấy.
# Đo thật 21/09/2026 trên dola.com: ba icon mạng xã hội là <div class="button-…">, KHÔNG phải <button>,
# không có aria-label, và màu là currentColor (đen) chứ không phải #1877F2 — nên mọi selector theo
# button/role/aria/màu đều trượt. Nhận diện bằng path glyph "f" của Facebook, dự phòng là icon GIỮA
# trong hàng 3 cái (điện thoại · Facebook · Apple).
# QUAN TRỌNG: chỉ TRẢ TOẠ ĐỘ, không .click() — đo được là element.click() bằng JS KHÔNG kích hoạt
# đăng nhập (trang im, không nạp SDK); phải bấm bằng chuột thật qua CDP mới ăn.
_FB_ICON_XY_JS = """() => {
  const dlg = document.querySelector('.semi-modal-wrap, [role="dialog"]');
  if (!dlg) return null;
  const center = (el) => {
    const r = el.getBoundingClientRect();
    return (r.width && r.height) ? {x: r.left + r.width / 2, y: r.top + r.height / 2} : null;
  };
  const p = dlg.querySelector('svg path[d^="M12 2C6.203 2 1.5 6.73"], svg path[fill="#0068FF" i], svg path[fill="#1877F2" i]');
  if (p) return center(p.closest('svg') || p);
  const icons = [...dlg.querySelectorAll('svg')].filter((sv) => {
    const r = sv.getBoundingClientRect();
    return r.width >= 16 && r.width <= 40 && Math.abs(r.width - r.height) <= 6;
  }).sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
  return icons.length === 3 ? center(icons[1]) : null;
}"""


async def _click_fb_icon(page) -> bool:
    """Bấm icon Facebook bằng chuột thật. True nếu đã bấm."""
    try:
        xy = await page.evaluate(_FB_ICON_XY_JS)
    except Exception:
        return False
    if not xy:
        return False
    try:
        await page.mouse.click(xy["x"], xy["y"])
        return True
    except Exception:
        return False


async def _click_continue(popup, timeout: int = 4000) -> bool:
    """Clicks Facebook's OAuth confirm button ('Continue as <name>' / '続行' / 'Tiếp tục')."""
    locs = []
    for label in CONTINUE_LABELS:
        locs.append(popup.get_by_role("button", name=label, exact=False).first)
        locs.append(popup.get_by_text(label, exact=False).first)

    locs.append(popup.locator("[role=button]:has-text('Continue')").first)
    locs.append(popup.locator("[role=button]:has-text('Tiếp tục')").first)
    locs.append(popup.locator("[role=button]:has-text('続行')").first)
    locs.append(popup.locator("button:has-text('Continue')").first)
    locs.append(popup.locator("button:has-text('Tiếp tục')").first)

    for loc in locs:
        try:
            if await loc.count() and await loc.is_visible():
                try:
                    await loc.click(timeout=timeout)
                    return True
                except Exception:
                    await loc.evaluate("e => e.click()")
                    return True
        except Exception:
            continue
    return False


async def _confirm_age_gate(page) -> bool:
    """Tự xác nhận cổng 18+ của Dola (màn hình đồng ý của chính Dola, tài khoản của người dùng).

    Chỉ bấm nút đồng ý khi trang đúng là cổng tuổi (nhắc 18 / age / 年齢 / độ tuổi). Nhận nhiều
    nhãn hơn bản cũ, và tick sẵn ô "tôi đủ 18" nếu có trước khi bấm. KHÔNG đụng tới chốt bảo mật
    của Facebook (khoá tài khoản, xác minh danh tính) — đó không phải cổng tuổi."""
    try:
        ok = await page.evaluate(r"""() => {
            const body = (document.body ? document.body.innerText : '');
            const isAge = /18|age|年齢|độ tuổi|đủ tuổi|tuổi/i.test(body)
                && /confirm|xác nhận|đồng ý|同意|agree|older|over|trở lên|至少|以上/i.test(body);
            if (!isAge) return false;
            // tick sẵn checkbox xác nhận tuổi nếu có
            for (const cb of document.querySelectorAll('input[type="checkbox"]')) {
                if (!cb.checked) { try { cb.click(); } catch (e) {} }
            }
            const YES = /^(ok|đồng ý|xác nhận|同意する|同意|accept|confirm|agree|continue|tiếp tục|続行|はい|có|tôi đủ 18|i am 18|i'?m 18|18\+)$/i;
            const els = [...document.querySelectorAll('button, [role="button"], a, span, div')];
            const t = els.find(e => {
                const txt = (e.textContent || '').trim();
                return txt.length <= 24 && YES.test(txt) && e.childElementCount === 0 && e.offsetParent !== null;
            });
            if (t) { t.click(); return true; }
            return false;
        }""")
        if ok:
            await page.wait_for_timeout(1000)
            return True
    except Exception:
        pass
    return False


def _fill_login_js(uid: str, password: str) -> str:
    """FB email+password autofill (ported from Seedance nick-login jsDienLogin).

    Only reports 'sai-mat-khau' once the form already carries the UID we typed, so a
    freshly rendered login page is never misread as a wrong-password result.
    """
    return """(() => {
  const UID = %s;
  const PASS = %s;
  const setVal = (el, val) => {
    try {
      const desc = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value');
      if (desc && desc.set) desc.set.call(el, val); else el.value = val;
    } catch (e) { el.value = val; }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const email = document.querySelector('input[name="email"], input#email, input[name="username"], input#m_login_email, input[type="email"]');
  const pass = document.querySelector('input[name="pass"], input#pass, input#m_login_password, input[type="password"]');
  if (!email || !pass) return { ok: false, why: 'no-form' };
  const body = ((document.body && document.body.innerText) || '').toLowerCase();
  if (email.value === UID &&
      /(password.{0,20}(incorrect|wrong)|incorrect.{0,20}password|wrong password|mật khẩu.{0,20}(sai|không đúng)|sai mật khẩu|couldn't find your account|không tìm thấy tài khoản)/i.test(body)) {
    return { ok: false, why: 'sai-mat-khau' };
  }
  if (email.value !== UID) setVal(email, UID);
  if (!pass.value) setVal(pass, PASS);
  const btn = document.querySelector('button[name="login"], #loginbutton, button[data-testid="royal_login_button"], button[type="submit"], input[type="submit"]')
    || [...document.querySelectorAll('button, [role="button"]')].find((b) => {
      const t = ((b.innerText || b.value || '') + '').replace(/\\s+/g, ' ').trim();
      return /^(log in|đăng nhập|anmelden|iniciar sesión|se connecter|masuk|entrar)$/i.test(t);
    });
  if (email.value && pass.value && btn) {
    const t = btn.closest('button, a, [role="button"]') || btn;
    try { t.click(); } catch (e) {}
    try { t.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window })); } catch (e2) {}
    return { ok: true, why: 'submit' };
  }
  return { ok: false, why: 'cho-nut' };
})()""" % (json.dumps(str(uid)), json.dumps(str(password)))


def _fill_2fa_js(code: str) -> str:
    """FB two-factor code autofill (ported from Seedance nick-login jsDien2fa)."""
    return """(() => {
  const MA = %s;
  const setVal = (el, val) => {
    try {
      const desc = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value');
      if (desc && desc.set) desc.set.call(el, val); else el.value = val;
    } catch (e) { el.value = val; }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const field = document.querySelector('input[name="approvals_code"], input#approvals_code, input[autocomplete="one-time-code"], input[name="code"], input[inputmode="numeric"], input[type="tel"]');
  if (!field) return { ok: false, why: 'no-2fa-field' };
  if (field.value !== MA) setVal(field, MA);
  const btn = document.querySelector('button[name="submit[Continue]"], #checkpointSubmitButton, button[name="submit[Submit Code]"], button[type="submit"]')
    || [...document.querySelectorAll('button, [role="button"]')].find((b) => {
      const t = ((b.innerText || b.value || '') + '').replace(/\\s+/g, ' ').trim();
      return /^(continue|tiếp tục|続行|submit|gửi|xác nhận|confirm)$/i.test(t);
    });
  if (btn) {
    const t = btn.closest('button, a, [role="button"]') || btn;
    try { t.click(); } catch (e) {}
    try { t.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window })); } catch (e2) {}
    return { ok: true, why: 'submit' };
  }
  return { ok: false, why: 'cho-nut' };
})()""" % json.dumps(str(code))


async def _fb_autofill_login(page, cred: Dict[str, Any], step: Callable[[str], None], timeout: int = 60) -> str:
    """Self-heal a dead Facebook cookie by auto-filling uid|pass[|2fa] when supplied.

    Ported from Seedance AI Studio's nick-login. Works on the main page or the OAuth
    popup (both expose .evaluate/.url/.context). Returns:
    'ok' | 'sai-mat-khau' | 'checkpoint' | 'no-creds' | 'timeout'.
    """
    uid = cred.get("uid") or cred.get("email") or ""
    password = cred.get("password") or ""
    if not (uid and password):
        return "no-creds"

    step("Cookie Facebook đã chết — thử tự điền đăng nhập (uid/mật khẩu)…")
    deadline = asyncio.get_event_loop().time() + timeout
    two_fa_done = False
    while asyncio.get_event_loop().time() < deadline:
        url = page.url.lower()
        text = ""
        try:
            text = (await page.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 500)")).lower()
        except Exception:
            pass

        # Real identity challenge (not a 2FA code prompt) → cannot pass automatically.
        if any(sig in text for sig in ("confirm you're human", "confirm your identity", "xác nhận danh tính", "we've detected unusual")):
            return "checkpoint"

        # Success: session cookie present and no longer on a login/checkpoint page.
        try:
            fb_cookies = await page.context.cookies("https://www.facebook.com")
        except Exception:
            fb_cookies = []
        if cookie_value(fb_cookies, "c_user") and not any(x in url for x in ("/login", "login.php", "/checkpoint")):
            step("Đăng nhập lại Facebook thành công (tự điền).")
            return "ok"

        # 2FA prompt: a code field is expected and we hold a TOTP secret.
        needs_2fa = ("two_factor" in url or "/checkpoint" in url or "approvals_code" in text
                     or any(k in text for k in ("login code", "mã đăng nhập", "two-factor", "xác thực hai")))
        if needs_2fa and not two_fa_done:
            secret = cred.get("totp")
            if not secret:
                return "checkpoint"  # 2FA demanded but no secret on the account line
            try:
                res = await page.evaluate(_fill_2fa_js(totp(secret)))
            except Exception:
                res = None
            if isinstance(res, dict) and res.get("ok"):
                step("Đã nhập mã 2FA Facebook.")
                two_fa_done = True
                await page.wait_for_timeout(4000)
                continue

        # 'Save this browser?' / 'Trust this device' interstitial → Continue.
        await _click_continue(page, timeout=2000)

        # Fill the email/password form.
        try:
            res = await page.evaluate(_fill_login_js(uid, password))
        except Exception:
            res = None
        if isinstance(res, dict) and res.get("why") == "sai-mat-khau":
            return "sai-mat-khau"
        await page.wait_for_timeout(3500)

    return "timeout"


LOG_DIR = Path("logs")   # logs/fb_login_<account>.log: every step + the final error
FAIL_HOLD_MS = 8000  # keep the headed window open this long after a failure so the user can see it


async def add_account_via_facebook(
    account: str,
    fb_cookie_line: str,
    on_step: Optional[Callable[[str], None]] = None,
    timeout: int = 75,
    visible: bool = True,
) -> str:
    """Logs into Dola through Facebook OAuth and stores persistent session in accounts/<account>.

    visible=True shows the Chrome window (login flows are interactive: checkpoints, consent
    screens) and, on failure, keeps it open for a few seconds plus saves fb_fail_<account>.png.
    Returns the Dola display name or email if read, otherwise "".
    Raises RuntimeError with a clear message on failure.
    """
    log_file = LOG_DIR / f"fb_login_{account}.log"

    def step(msg: str):
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass
        print(f"[{account}] {msg}", flush=True)
        try:  # keep the reason on disk even when the caller only shows an exit code
            LOG_DIR.mkdir(exist_ok=True)
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except OSError:
            pass

    cred = extract_fb_credential(fb_cookie_line)
    cookies = parse_fb_cookies(cred["cookie"] or fb_cookie_line)

    if not _has_fb_session(cookies):
        raise RuntimeError(
            "Cookie Facebook thiếu 'c_user' và 'xs' — hãy dán đầy đủ cookie hoặc dòng tài khoản (uid|pass|2fa|cookie|...)."
        )

    profile_dir = config.ACCOUNTS_DIR / account
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        kw = {
            "headless": False if visible else config.HEADLESS,
            "args": list(LAUNCH_ARGS),
            "locale": "ja-JP",
            "timezone_id": "Asia/Tokyo",
        }
        if config.BROWSER_CHANNEL:
            kw["channel"] = config.BROWSER_CHANNEL
        # Đăng nhập phải đi ĐÚNG proxy riêng của nick (kể cả proxy xoay — account_proxy tự giải ra IP thật).
        # Trước đây chỉ dùng proxy CHUNG: Facebook thấy nick đăng nhập từ một IP rồi hoạt động từ IP khác —
        # đúng thứ hệ thống chống gian lận soi kỹ nhất. account_proxy tự rơi về proxy chung nếu nick chưa gán.
        from browser import account_proxy
        proxy_cfg = await asyncio.to_thread(account_proxy, account)
        if proxy_cfg:
            kw["proxy"] = proxy_cfg

        ctx = await p.chromium.launch_persistent_context(str(profile_dir), **kw)
        try:
            step("Đang nạp cookie Facebook...")
            # Also add to www.facebook.com and m.facebook.com
            expanded_fb_cookies = []
            for c in cookies:
                expanded_fb_cookies.append({**c, "domain": ".facebook.com"})
                expanded_fb_cookies.append({**c, "domain": "www.facebook.com"})
                expanded_fb_cookies.append({**c, "domain": "m.facebook.com"})
            await ctx.add_cookies(expanded_fb_cookies)

            page = ctx.pages[0] if ctx.pages else await ctx.new_page()

            step("Đang kiểm tra cookie Facebook...")
            warm = await _warm_facebook_session(page)
            if warm == "checkpoint":
                # Facebook khoá/nghi hack và bắt tự xác minh danh tính — không tự vượt được (bấm tiếp
                # sẽ ra xác minh danh tính/CAPTCHA). Báo rõ để BỎ QUA nick này, chạy tiếp nick khác.
                raise RuntimeError(
                    "Facebook khoá tài khoản này (nghi bị hack / bắt xác minh danh tính). "
                    "Tool bỏ qua nick này — mở tay một lần trên trình duyệt để xác nhận, hoặc thay nick khác."
                )
            if warm == "login":
                outcome = await _fb_autofill_login(page, cred, step)
                if outcome == "sai-mat-khau":
                    raise RuntimeError(
                        "Sai mật khẩu Facebook — kiểm tra lại dòng tài khoản (uid|mật khẩu|2fa|cookie)."
                    )
                if outcome == "checkpoint":
                    raise RuntimeError(
                        "Facebook bắt xác minh danh tính / 2FA không qua được — hãy đăng nhập thủ công một lần."
                    )
                if outcome != "ok":
                    have = {c["name"] for c in cookies}
                    miss = [x for x in ("datr", "sb") if x not in have]
                    extra = f" (cookie đang thiếu {', '.join(miss)})" if miss else ""
                    hint = (
                        "Thêm mật khẩu + 2FA vào dòng tài khoản: uid|mật khẩu|2fa|cookie để tự đăng nhập lại."
                        if not cred.get("password")
                        else "Hãy dán ĐẦY ĐỦ cookie Facebook (gồm datr, sb, c_user, xs)."
                    )
                    raise RuntimeError(
                        f"Cookie Facebook không đăng nhập được — Facebook bắt đăng nhập lại{extra}. {hint}"
                    )

            step("Đang mở Dola...")
            await force_ui_language(ctx, "ja")
            await page.goto("https://www.dola.com/chat", timeout=60000, wait_until="domcontentloaded")
            # Nick có cookie FB hợp lệ thường tự đăng nhập Dola qua SSO ngay khi trang tải xong. Bám
            # sessionid (poll 500ms, tối đa 5s) thay vì chờ cứng 5s — nick đã có phiên thoát sớm ~5s,
            # nick chưa có phiên vẫn đợi đủ như trước. Có phiên rồi thì lưu luôn, khỏi mở hộp đăng nhập.
            if await _wait_sessionid(ctx, seconds=5.0):
                step("Tài khoản Dola đã có phiên hoạt động (đăng nhập bằng cookie Facebook).")
                await pin_session_cookies(ctx)
                persist_dola_cookies(account, await ctx.cookies("https://www.dola.com"))
                return cred.get("label") or ""

            # Dismiss consent banners if present
            for _c in ("OK", "Đồng ý", "同意する", "Accept"):
                try:
                    _cc = page.get_by_text(_c, exact=True).first
                    if await _cc.count() and await _cc.is_visible():
                        await _cc.click(timeout=2000)
                        await page.wait_for_timeout(400)
                        break
                except Exception:
                    pass

            # Multi-strategy search for Facebook login button / icon
            target = None

            # Check if Dola is already logged in
            curr_cookies = await ctx.cookies("https://www.dola.com")
            if cookie_value(curr_cookies, "sessionid"):
                step("Tài khoản Dola đã có phiên hoạt động từ trước.")
                await pin_session_cookies(ctx)
                persist_dola_cookies(account, curr_cookies)
                return cred.get("label") or ""

            # 1. Ensure login modal (.semi-modal-wrap or [role="dialog"]) is open
            modal_open = False
            try:
                modal = page.locator(".semi-modal-wrap, [role='dialog']").first
                if await modal.count() and await modal.is_visible():
                    modal_open = True
            except Exception:
                pass

            if not modal_open:
                step("Đang mở hộp thoại đăng nhập Dola...")
                for btn_sel in (
                    "text=ログイン", "button:has-text('ログイン')",
                    "text=Log in", "button:has-text('Log in')",
                    "text=Đăng nhập", "button:has-text('Đăng nhập')",
                    "header button", "header [role='button']"
                ):
                    try:
                        btn = page.locator(btn_sel).first
                        if await btn.count() and await btn.is_visible():
                            try:
                                await btn.click(timeout=4000)
                            except Exception:
                                await btn.evaluate("e => e.click()")
                            await page.wait_for_timeout(1500)
                            modal = page.locator(".semi-modal-wrap, [role='dialog']").first
                            if await modal.count() and await modal.is_visible():
                                modal_open = True
                                break
                    except Exception:
                        continue

            # 2. Inside modal, find and click the Facebook OAuth button
            clicked_fb = False
            for _attempt in range(8):
                # 2a. Check if already logged in
                curr_cookies = await ctx.cookies("https://www.dola.com")
                if cookie_value(curr_cookies, "sessionid"):
                    step("Tài khoản Dola đã có phiên hoạt động từ trước.")
                    await pin_session_cookies(ctx)
                    persist_dola_cookies(account, curr_cookies)
                    return cred.get("label") or ""

                # 2b. Bấm icon Facebook bằng CHUỘT THẬT (xem _FB_ICON_XY_JS: icon là <div>, không phải
                # <button>, và .click() bằng JS không kích hoạt được đăng nhập).
                clicked_fb = await _click_fb_icon(page)

                if clicked_fb:
                    break

                # 2c. Fallback CSS locator
                for sel in (
                    "[aria-label*='facebook' i]", "[title*='facebook' i]",
                    "svg.facebook-icon", "svg[data-icon='facebook']",
                    "button:has-text('Facebook')", "[role='button']:has-text('Facebook')",
                    "svg:has(path[fill*='0068FF' i])",
                    "svg:has(path[fill*='1877F2' i])", "svg:has(circle[fill*='1877F2' i])",
                ):
                    try:
                        loc = page.locator(sel).first
                        if await loc.count() and await loc.is_visible():
                            await loc.click(timeout=3000)
                            clicked_fb = True
                            break
                    except Exception:
                        continue
                if clicked_fb:
                    break

                await page.wait_for_timeout(1000)

            step("Đang đăng nhập qua Facebook...")
            login_codes: List[int] = []

            async def _read_login(resp):
                try:
                    if "login_only" in resp.url or "/auth/login" in resp.url:
                        data = await resp.json()
                        err_code = int(data.get("error_code") or data.get("code") or 0)
                        login_codes.append(err_code)
                except Exception:
                    pass

            page.on("response", _read_login)

            # Wait for popup or navigation
            popup = None
            try:
                # If popup was triggered by click
                for _w in range(15):
                    await page.wait_for_timeout(1000)
                    if len(ctx.pages) > 1:
                        popup = ctx.pages[-1]
                        break
                    # If not yet opened, retry evaluate click
                    if _w % 4 == 0:
                        await _click_fb_icon(page)   # thử lại bằng chuột thật
            except Exception:
                pass

            if not popup and len(ctx.pages) > 1:
                popup = ctx.pages[-1]




            if popup:
                try:
                    await popup.wait_for_load_state("domcontentloaded")
                    await popup.wait_for_timeout(3000)
                except Exception:
                    pass

                p_url = popup.url.lower()
                if "login.php" in p_url or "/login" in p_url:
                    outcome = await _fb_autofill_login(popup, cred, step)
                    if outcome == "sai-mat-khau":
                        raise RuntimeError("Sai mật khẩu Facebook — kiểm tra lại dòng tài khoản.")
                    if outcome != "ok":
                        raise RuntimeError(
                            "Facebook yêu cầu đăng nhập lại — cookie hết hạn và chưa có uid/mật khẩu để tự điền "
                            "(dùng dòng: uid|mật khẩu|2fa|cookie)."
                        )

                ptxt = ""
                try:
                    ptxt = (await popup.evaluate("() => (document.body.innerText||'').slice(0,400)")).lower()
                except Exception:
                    pass

                if "/checkpoint/" in p_url or any(x in ptxt for x in ("confirm you're human", "confirm your identity", "we've detected", "xác nhận danh tính")):
                    raise RuntimeError(
                        "Tài khoản Facebook này đang bị Facebook checkpoint (bắt xác minh danh tính / 'confirm you're human'). "
                        "Không thể đăng nhập tự động."
                    )

                # Attempt clicking Continue up to 4 times
                for _ in range(4):
                    if popup.is_closed():
                        break
                    clicked = await _click_continue(popup, timeout=4000)
                    await popup.wait_for_timeout(2500)

            await _confirm_age_gate(page)

            step("Đang chờ Dola cấp phiên...")
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                await page.wait_for_timeout(2000)

                # Check if popup is at checkpoint
                if popup and not popup.is_closed():
                    if "/checkpoint/" in popup.url.lower():
                        raise RuntimeError(
                            "Tài khoản Facebook này đang bị Facebook checkpoint (bắt xác minh danh tính). "
                            "Không thể đăng nhập tự động."
                        )
                    await _click_continue(popup, timeout=2000)

                await _confirm_age_gate(page)

                live = await ctx.cookies("https://www.dola.com")
                if cookie_value(live, "sessionid"):
                    await pin_session_cookies(ctx)
                    persist_dola_cookies(account, live)
                    step("Đăng nhập thành công! Đã lưu phiên Dola.")
                    user_label = cred.get("label") or ""
                    try:
                        await page.wait_for_timeout(2500)
                        email_match = await page.evaluate(
                            "() => (document.body.innerText||'').match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}/)?.[0] || ''"
                        )
                        if email_match:
                            user_label = email_match
                    except Exception:
                        pass
                    return user_label

                if 1011 in login_codes:
                    raise RuntimeError(
                        "Dola không tạo được tài khoản mới từ Facebook này (chưa qua được bước xác nhận tuổi/đăng ký). "
                        "Thử lại, hoặc dùng cookie Dola trực tiếp."
                    )

            raise RuntimeError("Không lấy được phiên Dola sau khi đăng nhập Facebook (hết thời gian chờ cấp sessionid).")
        except Exception as exc:
            # Leave evidence for debugging: a screenshot, and (when headed) the window itself.
            shot = f"fb_fail_{account}.png"
            try:
                await page.screenshot(path=shot)
                step(f"Lỗi: {str(exc)[:120]} — đã lưu {shot}")
            except Exception:
                step(f"Lỗi: {str(exc)[:120]}")
            if visible:
                try:
                    await page.wait_for_timeout(FAIL_HOLD_MS)
                except Exception:
                    pass
            raise
        finally:
            await ctx.close()


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Facebook OAuth login for Dola")
    parser.add_argument("account", help="Account name (e.g. acc1)")
    parser.add_argument("data", help="Cookie string, account line (UID|PASS|2FA|COOKIE|UA), or path to file")
    parser.add_argument("--visible", action="store_true", help="Run with browser window visible (non-headless)")
    parser.add_argument("--headless", action="store_true", help="Force headless execution")
    parser.add_argument("--proxy", default=None, help="Custom proxy URL (http://user:pass@ip:port)")
    parser.add_argument("--timeout", type=int, default=75, help="Timeout in seconds")
    args = parser.parse_args()

    account_name = args.account.strip()
    raw_data = args.data.strip()

    # Check if raw_data is an existing file (guard against long strings raising OSError)
    if len(raw_data) < 255 and not any(c in raw_data for c in ("\n", "\r", ";", "=", "|")):
        try:
            p = Path(raw_data)
            if p.is_file():
                raw_data = p.read_text(encoding="utf-8").strip()
                print(f"[*] Đã đọc dữ liệu từ file: {p.resolve()}")
        except (OSError, ValueError):
            pass


    if args.proxy:
        config.PROXY = args.proxy
    if args.visible:
        config.HEADLESS = False
    elif args.headless:
        config.HEADLESS = True

    try:
        user_name = await add_account_via_facebook(account_name, raw_data, timeout=args.timeout,
                                                   visible=not args.headless)
        print(f"[✓] Hoàn tất! Account {account_name} sẵn sàng (User: {user_name or 'Active'})")
    except Exception as e:
        print(f"[✗] Lỗi: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

