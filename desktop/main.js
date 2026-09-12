// Electron main process for the Dola Render Gateway desktop GUI.
// Spawns the gateway from the repo's .venv, and runs cookie-login imports.
const { app, BrowserWindow, ipcMain, dialog, shell, session } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");

const REPO_ROOT = path.dirname(__dirname); // desktop/ lives inside the repo
// Bản đóng gói (.exe) nằm trong Program Files → KHÔNG ghi được. Tách đôi:
//   APP_DIR  = mã Python + web/extensions (chỉ đọc, nằm trong resources)
//   DATA_DIR = accounts / downloads / logs / tasks.db / .env.local (ghi được, %APPDATA%)
// Chạy từ source (dev) thì cả hai vẫn là thư mục repo như cũ.
const PACKAGED = app.isPackaged;
const APP_DIR = PACKAGED ? path.join(process.resourcesPath, "app-python") : REPO_ROOT;
const DATA_DIR = PACKAGED ? app.getPath("userData") : REPO_ROOT;
const { fetchGenerate } = require("./fetch-generate.cjs");
const { applyProxy, attachLoadErrorHandler, preflightDola, readEnvLocal: _readEnvLocal,
        parseProxy, globalProxy, testProxy, PROXY_FORMATS } = require("./proxy.cjs");
const { gatewayBase, normalizeRemoteBase, testRemote, getAccountProxy, setAccountProxy, getRemoteConfig } = require("./remote.cjs");
const { createGateway } = require("./gateway.cjs");
const _IS_WIN = process.platform === "win32";
const _VENV_BIN = _IS_WIN ? "Scripts" : "bin";   // Windows: .venv\\Scripts, macOS/Linux: .venv/bin
const VENV_PY = PACKAGED
  ? path.join(process.resourcesPath, "python", _IS_WIN ? "python.exe" : "bin/python3")
  : path.join(REPO_ROOT, ".venv", _VENV_BIN, _IS_WIN ? "python.exe" : "python");
const BROWSERS_DIR = PACKAGED ? path.join(process.resourcesPath, "browsers") : "";
const CHROME_WIN = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

function pyEnv() {
  const env = { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" };
  if (BROWSERS_DIR) {
    env.PLAYWRIGHT_BROWSERS_PATH = BROWSERS_DIR;   // dùng Chromium đóng gói kèm
    env.PATCHRIGHT_BROWSERS_PATH = BROWSERS_DIR;
    // Mặc định worker mở Chrome thật (giả lập tốt hơn). Máy chưa cài Chrome thì phải
    // rơi về Chromium kèm theo, nếu không mọi nick đều lỗi ngay từ lần mở đầu.
    if (_IS_WIN && !fs.existsSync(CHROME_WIN)) env.DOLA_BROWSER_CHANNEL = "";
  }
  return env;
}

// Mọi lệnh Python đi qua đây: script nằm ở APP_DIR, còn dữ liệu ghi vào DATA_DIR.
function spawnPy(args, opts = {}) {
  const a = args.slice();
  if (a[0] && a[0].endsWith(".py")) a[0] = path.join(APP_DIR, a[0]);
  return spawn(VENV_PY, a, { cwd: DATA_DIR, env: pyEnv(), ...opts });
}

// Vòng đời uvicorn ở một chỗ (gateway.cjs): đăng nhập / nạp cookie tạm dừng gateway rồi TỰ BẬT LẠI.
const gateway = createGateway({
  isRemote: () => isRemote(),
  log: (m) => process.stdout.write(`[${logTs()}] ${m}\n`),
  spawnProc: () => {
    if (!fs.existsSync(VENV_PY)) return { error: `Không thấy Python tại ${VENV_PY} — chạy setup trước (bản dev) hoặc cài lại app.` };
    const port = config().base.split(":").pop();
    fs.mkdirSync(DATA_DIR, { recursive: true });
    const p = spawnPy(["-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", port, "--app-dir", APP_DIR]);
    openLog(); pipeLog(p.stdout, ""); pipeLog(p.stderr, " ⚠");
    return { proc: p };
  },
});
// Handler cần Chrome profile của nick rảnh: gọi gateway.pause() trong thân, xong tự bật lại.
const pausedHandle = (channel, fn) => ipcMain.handle(channel, gateway.withPaused(fn));

// --- Log capture: pipe everything the gateway prints to logs/gateway.log (timestamped). ---
// Without this the subprocess stdout/stderr is discarded (and a full 64KB pipe can stall the gateway).
const LOG_DIR = path.join(DATA_DIR, "logs");
const LOG_FILE = path.join(LOG_DIR, "gateway.log");
const LOG_MAX = 5 * 1024 * 1024; // ponytail: rotate at 5MB into one .1 backup; add a rotation lib only if ops needs more
const logTs = () => new Date().toTimeString().slice(0, 8);
function openLog() {
  try { fs.mkdirSync(LOG_DIR, { recursive: true }); } catch (_) {}   // Python (server.py) ghi nội dung
}
function pipeLog(stream, tag) {
  let buf = "";
  stream.on("data", (d) => {
    buf += d.toString();
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = `[${logTs()}]${tag} ${buf.slice(0, i)}\n`;
      buf = buf.slice(i + 1);
      process.stdout.write(line);   // file logs/gateway.log do server.py (Python) tự ghi
    }
  });
}

// .env.local parser sống trong proxy.cjs (một bản duy nhất, dùng chung).
const readEnvLocal = () => _readEnvLocal(DATA_DIR);

function config() {
  const env = readEnvLocal();
  const { base, remote } = gatewayBase(env);
  const apiKey = (env.DOLA_API_KEYS || "").split(",").map((s) => s.trim()).filter(Boolean)[0] || "";
  const downloadsDir = path.isAbsolute(env.DOLA_DOWNLOAD_DIR || "")
    ? env.DOLA_DOWNLOAD_DIR
    : path.join(DATA_DIR, env.DOLA_DOWNLOAD_DIR || "downloads");
  const adminKey = env.DOLA_ADMIN_KEY || "";
  return { base, remote, apiKey, adminKey, downloadsDir };
}
const isRemote = () => config().remote;
const REMOTE_ONLY = { ok: false, error: "Đang dùng máy chủ từ xa — thao tác này cần Python trên máy này. Dùng \"Đăng nhập\" (cửa sổ app) hoặc dán cookie: nick sẽ tự được đẩy lên máy chủ." };

function createWindow() {
  const win = new BrowserWindow({
    width: 1080,
    height: 760,
    title: "Dola Studio",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      // Local-only tool: renderer talks to http://127.0.0.1:<port>, which is a
      // cross-origin request from file://. Relax web security for localhost calls.
      webSecurity: false,
    },
  });
  // Giao diện React + shadcn/ui đã build (renderer/dist). Bản cũ index.html giữ làm dự phòng.
  const reactUI = path.join(__dirname, "renderer", "dist", "index.html");
  win.loadFile(fs.existsSync(reactUI) ? reactUI : path.join(__dirname, "index.html"));
}

// ---- IPC ----

ipcMain.handle("config:get", () => config());

// Fast generate: submit the prompt over HTTP from the nick's own logged-in Electron
// partition (bdms signs it), poll and download — no gateway, no clicking.
ipcMain.handle("video:fetchGenerate", async (_e, { name, prompt, model, duration, ratio, showBrowser }) => {
  if (!NAME_RE.test(name || "")) return { ok: false, error: "Tên nick chỉ gồm chữ, số, _ hoặc - (1-32 ký tự)" };
  if (!prompt || !prompt.trim()) return { ok: false, error: "Chưa nhập mô tả (prompt)" };
  try {
    return await fetchGenerate({
      name, prompt: prompt.trim(), model, duration, ratio,
      downloadsDir: config().downloadsDir,
      showBrowser: showBrowser !== false,
      onStep: (m) => { try { _e.sender.send("video:fetchStep", { name, line: m }); } catch (x) {} },
    });
  } catch (err) {
    return { ok: false, error: String(err && err.message ? err.message : err) };
  }
});

ipcMain.handle("gateway:start", () => gateway.start());
ipcMain.handle("gateway:stop", async () => { await gateway.stop(); return { ok: true, remote: isRemote() }; });
// Khởi động lại: chờ tiến trình cũ nhả cổng rồi mới spawn (bật ngay là "address already in use").
ipcMain.handle("gateway:restart", async () => { await gateway.stop(); return gateway.start(); });

const NAME_RE = /^[A-Za-z0-9_-]{1,32}$/;

// Máy chủ từ xa: máy này không có Python/profile — đẩy cookie lên VPS qua /api/admin/accounts/import-cookie
// (server tự nạp vào profile nick và kiểm tra phiên). Cùng dạng trả về với runImport để mọi luồng đăng nhập không đổi.
async function runImportRemote(name, file, lang) {
  const c = config();
  const headers = { "Content-Type": "application/json" };
  if (c.apiKey) headers.Authorization = "Bearer " + c.apiKey;
  if (c.adminKey) headers["x-admin-key"] = c.adminKey;
  try {
    const r = await fetch(c.base + "/api/admin/accounts/import-cookie", {
      method: "POST", headers,
      body: JSON.stringify({ name, cookies: fs.readFileSync(file, "utf8"), ui_lang: lang || "ja" }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, error: j.detail || ("HTTP " + r.status) };
    if (!j.ok) return { ok: false, error: j.message || "máy chủ không nhận cookie (phiên Dola không hợp lệ)" };
    return { ok: true, output: j.message || `Đã đẩy cookie nick ${name} lên máy chủ ${c.base}`, error: "" };
  } catch (e) {
    return { ok: false, error: `Không nối được máy chủ ${c.base}: ${String((e && e.message) || e).slice(0, 120)}` };
  }
}

// Inject a Netscape cookie file into accounts/<name> via import_cookies.py.
function runImport(name, file, lang) {
  if (isRemote()) return runImportRemote(name, file, lang);
  const args = ["import_cookies.py", name, file, lang || "ja"];
  return new Promise((resolve) => {
    const proc = spawnPy(args);
    let out = "", err = "";
    proc.stdout.on("data", (d) => (out += d));
    proc.stderr.on("data", (d) => (err += d));
    proc.on("exit", (code) =>
      resolve({ ok: code === 0, output: out.trim(), error: err.trim() || (code ? `exit ${code}` : "") })
    );
    proc.on("error", (e) => resolve({ ok: false, error: String(e) }));
  });
}

// Electron cookie objects -> Netscape cookie file text (import_cookies.py reads this).
function toNetscape(cookies) {
  const lines = ["# Netscape HTTP Cookie File"];
  for (const c of cookies) {
    const incSub = c.domain.startsWith(".") ? "TRUE" : "FALSE";
    const secure = c.secure ? "TRUE" : "FALSE";
    const expiry = c.expirationDate ? Math.floor(c.expirationDate) : 0;
    const prefix = c.httpOnly ? "#HttpOnly_" : "";
    lines.push([prefix + c.domain, incSub, c.path || "/", secure, expiry, c.name, c.value].join("\t"));
  }
  return lines.join("\n") + "\n";
}

// Cookie login by picking a Netscape .txt file.
ipcMain.handle("account:import", async (_e, { name, lang }) => {
  if (!NAME_RE.test(name || "")) return { ok: false, error: "Tên nick chỉ gồm chữ, số, _ hoặc - (1-32 ký tự)" };
  const picked = await dialog.showOpenDialog({
    title: `Chọn file cookie cho nick "${name}"`,
    filters: [{ name: "Cookie file", extensions: ["txt"] }],
    properties: ["openFile"],
  });
  if (picked.canceled || !picked.filePaths[0]) return { ok: false, canceled: true };
  return runImport(name, picked.filePaths[0], lang);
});

// Direct cookie string import
ipcMain.handle("account:importText", async (_e, { name, cookies, lang }) => {
  if (!NAME_RE.test(name || "")) return { ok: false, error: "Tên nick chỉ gồm chữ, số, _ hoặc - (1-32 ký tự)" };
  if (!cookies || !cookies.trim()) return { ok: false, error: "Chưa nhập chuỗi cookie" };
  const tmpFile = path.join(os.tmpdir(), `dola-raw-${name}-${Date.now()}.txt`);
  fs.writeFileSync(tmpFile, cookies, "utf8");
  try {
    return await runImport(name, tmpFile, lang);
  } finally {
    try { fs.unlinkSync(tmpFile); } catch (_) {}
  }
});

// Facebook OAuth import using facebook_login.py
ipcMain.handle("account:importFacebook", async (_e, { name, line }) => {
  if (isRemote()) return REMOTE_ONLY;
  if (!NAME_RE.test(name || "")) return { ok: false, error: "Tên nick chỉ gồm chữ, số, _ hoặc - (1-32 ký tự)" };
  if (!line || !line.trim()) return { ok: false, error: "Chưa nhập chuỗi cookie / tài khoản Facebook" };
  const tmpFile = path.join(os.tmpdir(), `dola-fb-${name}-${Date.now()}.txt`);
  fs.writeFileSync(tmpFile, line.trim(), "utf8");
  try {
    const args = ["-u", "facebook_login.py", name, tmpFile];   // -u: unbuffered, steps stream live
    return await new Promise((resolve) => {
      const proc = spawnPy(args);
      let out = "", err = "";
      proc.stdout.on("data", (d) => {
        out += d;
        // Forward each "[name] step..." line so the renderer can show progress while Chrome is open.
        for (const line of String(d).split(/\r?\n/)) {
          if (line.trim()) _e.sender.send("account:fbStep", { name, line: line.trim() });
        }
      });
      proc.stderr.on("data", (d) => (err += d));
      proc.on("exit", (code) => {
        // facebook_login.py prints its reason on stdout ("[✗] Lỗi: ..."); surface that, not "exit 1".
        const last = out.trim().split(/\r?\n/).filter((l) => l.trim()).pop() || "";
        resolve({ ok: code === 0, output: out.trim(), error: code ? (last || err.trim() || `exit ${code}`) : "" });
      });
      proc.on("error", (e) => resolve({ ok: false, error: String(e) }));
    });
  } finally {
    try { fs.unlinkSync(tmpFile); } catch (_) {}
  }
});

// Electron's default UA advertises "<app>/x.y.z Electron/x.y.z"; Facebook and Dola treat that as
// an automation tell. Present the same Chrome UA a normal browser of this version would.
function cleanUserAgent(ses) {
  const ua = ses.getUserAgent().replace(/ [A-Za-z0-9_-]+\/\d+\.\d+\.\d+/g, (m) => (/ Chrome\/|Safari\//.test(m) ? m : ""))
    .replace(/ Electron\/\S+/, "");
  ses.setUserAgent(ua);
  return ua;
}

// ---- Facebook OAuth automation inside the Electron window (mirrors facebook_login.py) ----
// Every snippet is self-contained and returns a small string/bool so the main process can log it.
const JS_OPEN_DOLA_LOGIN = `(() => {
  const dlg = document.querySelector('.semi-modal-wrap, [role="dialog"]');
  if (dlg && dlg.getBoundingClientRect().height > 0) return 'open';
  const labels = ['ログイン', 'Log in', 'Log In', 'Sign in', 'Đăng nhập', '登录'];
  // Header login: <button class="semi-button semi-button-primary ..."><span class="semi-button-content">ログイン</span>
  const b = [...document.querySelectorAll('button,[role=button]')].find(x => labels.includes((x.textContent||'').trim()));
  if (b) { b.click(); return 'clicked'; }
  return 'no-button';
})()`;

const JS_CLICK_FACEBOOK = `(() => {
  const dialog = document.querySelector('.semi-modal-wrap, [role="dialog"]');
  if (!dialog) return 'no-dialog';
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const clickIcon = (svg) => { (svg.closest('button, [role="button"], a') || svg.parentElement || svg).click(); };
  // 0) exact markup (Dola 2026-09): <svg class="size-24" viewBox="0 0 24 25"><path fill="#0068FF" d="M12 2C6.203 2 1.5 6.73…
  const fbPath = dialog.querySelector('svg path[fill="#0068FF"], svg path[fill="#0068ff"], svg path[d^="M12 2C6.203 2 1.5 6.73"]');
  if (fbPath && vis(fbPath.closest('svg'))) { clickIcon(fbPath.closest('svg')); return 'exact'; }
  // 1) other Facebook markers: brand colours, name in svg/img/aria
  for (const el of dialog.querySelectorAll('svg, img, button, [role="button"], a')) {
    const h = (el.outerHTML || '').toLowerCase();
    if (vis(el) && (h.includes('#1877f2') || h.includes('rgb(24, 119, 242)') || h.includes('facebook'))) {
      (el.closest('button, [role="button"], a') || el).click(); return 'marker';
    }
  }
  // 1b) the social row is three 24px svgs (phone, facebook, apple) under the Google button: take the middle one
  const icons = [...dialog.querySelectorAll('svg.size-24')].filter(vis).filter(s => !s.closest('[aria-label="close"]'))
    .sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
  if (icons.length === 3) { clickIcon(icons[1]); return 'row-3'; }
  // 2) the row of round social buttons under "Googleで続ける": phone / facebook / apple
  const google = [...dialog.querySelectorAll('button, [role="button"], div')].find(b => /Google/.test(b.innerText || ''));
  const top = google ? google.getBoundingClientRect().bottom : 0;
  let round = [...dialog.querySelectorAll('button, [role="button"], div')].filter(el => {
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    const radius = parseFloat(cs.borderRadius) || 0;
    return r.width >= 34 && r.width <= 64 && Math.abs(r.width - r.height) < 4 && r.top > top
      && (cs.borderRadius.includes('%') || radius >= r.width / 2 - 2);
  });
  round = round.filter(el => !round.some(o => o !== el && o.contains(el)));   // outermost only
  round.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
  if (round.length === 3) { round[1].click(); return 'round-3'; }
  if (round.length === 4) { round[3].click(); return 'round-4'; }
  return 'not-found:' + round.length;
})()`;

// Guest mode (fresh partition) has NO login button; Dola only opens the modal (openLoginModal)
// when you attempt an auth action. This types into the composer and clicks send to trigger it.
const JS_LOGIN_MODAL_OPEN = `(() => {
  const m = document.querySelector('.login-modal-SJYLEV, [class*="login-modal"], [role="dialog"]');
  if (m && m.getBoundingClientRect().width > 0
      && [...m.querySelectorAll('*')].some(e => /Google|Facebook|Apple|続ける|Continue|Tiếp tục/.test(e.textContent||''))) return true;
  return !![...document.querySelectorAll('*')].find(e =>
    /Googleで続ける|Continue with Google|Tiếp tục với Google/.test(e.textContent||'') && e.children.length === 0 && e.getBoundingClientRect().width > 0);
})()`;

const JS_TRIGGER_BY_SEND = `(() => {
  const box = document.querySelector('[contenteditable="true"]') || document.querySelector('textarea');
  if (!box) return 'no-composer';
  box.focus();
  try {
    if (box.tagName === 'TEXTAREA') { box.value = 'hi'; box.dispatchEvent(new Event('input', { bubbles: true })); }
    else { document.execCommand('insertText', false, 'hi'); }
  } catch (e) {}
  const bs = [...document.querySelectorAll('button')].filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.top > innerHeight * 0.55; });
  const send = bs[bs.length - 1];   // the arrow/send button sits bottom-right of the composer
  if (send) { send.click(); return 'sent'; }
  box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  return 'enter';
})()`;

const JS_CLICK_CONTINUE = `(() => {
  const labels = ['continue as', 'tiếp tục dưới tên', 'tiếp tục với tư cách', 'tiếp tục', '続行', 'đăng nhập bằng',
                  'log in as', 'continue', '同意して続行', '以', 'weiter als'];
  const cands = [...document.querySelectorAll('button, [role="button"], a, input[type=submit]')];
  for (const el of cands) {
    const t = ((el.innerText || el.value || el.getAttribute('aria-label') || '') + '').trim().toLowerCase();
    if (!t || t.length > 80) continue;
    const r = el.getBoundingClientRect(); if (r.width === 0 || r.height === 0) continue;
    if (labels.some(l => t.startsWith(l) || t === l)) { el.click(); return 'clicked:' + t.slice(0, 30); }
  }
  return 'none';
})()`;

const JS_AGE_GATE = `(() => {
  const body = document.body ? document.body.innerText : '';
  if (!(body.includes('18') || body.includes('Age') || body.includes('年齢') || body.includes('tuổi'))) return 'no-gate';
  const els = [...document.querySelectorAll('button, [role="button"], div, span')];
  const ok = els.find(e => { const t = (e.textContent || '').trim();
    return ['OK', 'Đồng ý', '同意する', 'Accept', 'はい', 'Yes', 'Confirm', '確認'].includes(t) && e.childElementCount === 0; });
  if (ok) { ok.click(); return 'confirmed'; }
  return 'gate-no-button';
})()`;

// win.loadURL rejects with ERR_ABORTED (-3) when the page client-redirects mid-load
// (e.g. facebook.com/me -> /profile.php) even though the page loads fine. Swallow just that.
async function safeLoad(win, url) {
  try {
    if (win.isDestroyed()) return false;
    await win.loadURL(url);
    return true;
  } catch (e) {
    const code = (e && (e.code || e.errno)) || "";
    if (String(code) === "-3" || /ERR_ABORTED/.test(String(e))) return true; // redirect, not a failure
    throw e;
  }
}

async function runJS(webContents, js) {
  try {
    if (!webContents || webContents.isDestroyed()) return null;
    return await webContents.executeJavaScript(js, true);
  } catch (_) { return null; }   // page navigating / closed: try again next tick
}

// After Dola issues sessionid, reload once so the bdms SDK re-sets the signing cookies
// (msToken, s_v_web_id) the render worker needs, then harvest every dola.com cookie and
// hand them to import_cookies.py. Verifies sessionid is present before saving.
async function harvestDolaSession(win, ses, name, lang, send, DOLA_URL) {
  if (send) send("Dola đã cấp phiên — đang nạp đủ cookie…");
  try { await safeLoad(win, DOLA_URL); } catch (_) {}
  let dola = [];
  for (let i = 0; i < 10; i++) {
    await sleep(1000);
    dola = (await ses.cookies.get({})).filter((c) => (c.domain || "").includes("dola.com"));
    const names = new Set(dola.map((c) => c.name));
    const ready = names.has("sessionid") && names.has("msToken") && names.has("s_v_web_id");
    if (ready || (names.has("sessionid") && i >= 5)) break;   // sessionid is enough; signing cookies are a bonus
  }
  const names = new Set(dola.map((c) => c.name));
  if (!names.has("sessionid")) {
    if (!win.isDestroyed()) win.close();
    return { ok: false, error: "Bắt cookie thất bại: phiên Dola không có sessionid (thử lại)." };
  }
  if (send) send(`Đã bắt ${dola.length} cookie Dola (sessionid✓${names.has("msToken") ? " msToken✓" : ""}${names.has("s_v_web_id") ? " s_v_web_id✓" : ""}) — đang lưu vào nick…`);
  const file = path.join(os.tmpdir(), `dola-${name}-${Date.now()}.txt`);
  fs.writeFileSync(file, toNetscape(dola));
  if (!win.isDestroyed()) win.close();
  const res = await runImport(name, file, lang);
  fs.unlink(file, () => {});
  return res;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ─────────────────────────────────────────────────────────────────────────────
// Tự đăng nhập lại Facebook khi cookie chết (port từ Seedance nick-login jsDienLogin/jsDien2fa,
// xem SEEDANCE_STUDIO_ANALYSIS.md mục 4/6). Điền email/mật khẩu từ dòng uid|pass[|2fa],
// phát hiện "sai mật khẩu", tự sinh mã 2FA (TOTP) khi Facebook hỏi 2 lớp.
const crypto = require("crypto");
function _b32decode(s) {
  s = String(s).replace(/=+$/, "").replace(/\s/g, "").toUpperCase();
  const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = ""; const out = [];
  for (const c of s) { const v = A.indexOf(c); if (v < 0) continue; bits += v.toString(2).padStart(5, "0"); }
  for (let i = 0; i + 8 <= bits.length; i += 8) out.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(out);
}
function fbTotp(secret, period = 30, digits = 6) {
  const key = _b32decode(secret);
  const c = Math.floor(Date.now() / 1000 / period);
  const b = Buffer.alloc(8); b.writeUInt32BE(Math.floor(c / 2 ** 32), 0); b.writeUInt32BE(c >>> 0, 4);
  const h = crypto.createHmac("sha1", key).update(b).digest();
  const o = h[19] & 15;
  const code = (h.readUInt32BE(o) & 0x7fffffff) % (10 ** digits);
  return String(code).padStart(digits, "0");
}

// Tách uid | mật khẩu | 2fa(base32) từ dòng tài khoản (chấp nhận cả định dạng
// uid|pass|2fa|cookie|ua lẫn uid|pass|cookie|token|email|app1=;). Bỏ cookie/token/email/appN.
function parseFacebookCred(line) {
  const raw = String(line || "").trim();
  if (!raw.includes("|")) return { uid: "", password: "", totp: "" };
  const parts = raw.split("|").map((s) => s.trim());
  let uid = parts.find((p) => /^\d{5,}$/.test(p)) || "";
  if (!uid) { const m = raw.match(/c_user=(\d{5,})/); if (m) uid = m[1]; }
  const isCookie = (p) => (p.includes("=") && /(c_user|datr|xs|sb|fr)=/i.test(p)) || /^[a-z0-9_]+=[^;]*;/.test(p);
  const isToken = (p) => /^EAA[A-Za-z0-9]/.test(p);
  const isEmail = (p) => p.includes("@");
  const isApp = (p) => /^app\d*=/.test(p);
  const isB32 = (p) => { const s = p.replace(/\s+/g, ""); return s.length >= 16 && /^[A-Z2-7]+$/.test(s); };
  const leftovers = parts.filter((p) => p && p !== uid && !isCookie(p) && !isToken(p) && !isEmail(p) && !isApp(p));
  const password = leftovers[0] || "";
  const totp = (leftovers.slice(1).find(isB32) || "").replace(/\s+/g, "");
  return { uid, password, totp };
}

const JS_FB_FILL_LOGIN = (uid, pass) => String.raw`(() => {
  const UID = ${JSON.stringify(String(uid))};
  const PASS = ${JSON.stringify(String(pass))};
  const setVal = (el, val) => {
    try { const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value'); if (d && d.set) d.set.call(el, val); else el.value = val; }
    catch (e) { el.value = val; }
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
      const t = ((b.innerText || b.value || '') + '').replace(/\s+/g, ' ').trim();
      return /^(log in|đăng nhập|anmelden|iniciar sesión|se connecter|masuk|entrar)$/i.test(t);
    });
  if (email.value && pass.value && btn) {
    const t = btn.closest('button, a, [role="button"]') || btn;
    try { t.click(); } catch (e) {}
    try { t.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window })); } catch (e2) {}
    return { ok: true, why: 'submit' };
  }
  return { ok: false, why: 'cho-nut' };
})()`;

const JS_FB_FILL_2FA = (code) => String.raw`(() => {
  const MA = ${JSON.stringify(String(code))};
  const setVal = (el, val) => {
    try { const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value'); if (d && d.set) d.set.call(el, val); else el.value = val; }
    catch (e) { el.value = val; }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const field = document.querySelector('input[name="approvals_code"], input#approvals_code, input[autocomplete="one-time-code"], input[name="code"], input[inputmode="numeric"], input[type="tel"]');
  if (!field) return { ok: false, why: 'no-2fa-field' };
  if (field.value !== MA) setVal(field, MA);
  const btn = document.querySelector('button[name="submit[Continue]"], #checkpointSubmitButton, button[name="submit[Submit Code]"], button[type="submit"]')
    || [...document.querySelectorAll('button, [role="button"]')].find((b) => {
      const t = ((b.innerText || b.value || '') + '').replace(/\s+/g, ' ').trim();
      return /^(continue|tiếp tục|続行|submit|gửi|xác nhận|confirm)$/i.test(t);
    });
  if (btn) {
    const t = btn.closest('button, a, [role="button"]') || btn;
    try { t.click(); } catch (e) {}
    try { t.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window })); } catch (e2) {}
    return { ok: true, why: 'submit' };
  }
  return { ok: false, why: 'cho-nut' };
})()`;

// Một bước tự-chữa (gọi trong vòng lặp OAuth). Trả: ok/2fa/filled/waiting/sai-mat-khau/checkpoint/no-creds.
async function fbAutofillStep(wc, cred, twoFaRef, send) {
  if (!wc || wc.isDestroyed()) return "waiting";
  let text = "";
  try { text = String((await runJS(wc, "(((document.body&&document.body.innerText)||'')).slice(0,500).toLowerCase()")) || ""); } catch (_) {}
  if (/confirm you're human|confirm your identity|xác nhận danh tính|we've detected unusual/.test(text)) return "checkpoint";
  if (!cred.uid || !cred.password) return "no-creds";
  const url = (wc.getURL() || "").toLowerCase();
  const needs2fa = url.includes("two_factor") || url.includes("/checkpoint") || text.includes("approvals_code")
    || /login code|mã đăng nhập|two-factor|xác thực hai/.test(text);
  if (needs2fa && !twoFaRef.done) {
    if (!cred.totp) return "checkpoint";
    const r = await runJS(wc, JS_FB_FILL_2FA(fbTotp(cred.totp)));
    if (r && r.ok) { twoFaRef.done = true; send("Đã nhập mã 2FA Facebook."); await sleep(4000); return "2fa"; }
  }
  await runJS(wc, JS_CLICK_CONTINUE);       // 'Save browser?' / 'Trust device' interstitial
  const r = await runJS(wc, JS_FB_FILL_LOGIN(cred.uid, cred.password));
  if (r && r.why === "sai-mat-khau") return "sai-mat-khau";
  if (r && r.ok) { send("Đã tự điền đăng nhập Facebook (uid/mật khẩu)…"); return "filled"; }
  return "waiting";
}


// "uid|pass|2fa|cookie|ua" account line or a plain "k=v; k=v" Facebook cookie header -> cookie pairs.
function parseFacebookLine(line) {
  let cookieStr = line.trim();
  if (cookieStr.includes("|")) {
    const parts = cookieStr.split("|").map((s) => s.trim());
    cookieStr = parts.find((p) => p.includes("c_user=") || p.includes("xs=")) || parts.find((p) => p.includes("=")) || "";
  }
  const pairs = [];
  for (const item of cookieStr.replace(/\n/g, ";").split(";")) {
    const idx = item.indexOf("=");
    if (idx <= 0) continue;
    const name = item.slice(0, idx).trim();
    let value = item.slice(idx + 1).trim();
    // Nick từ panel thường mã hoá URL (xs=7%3A...%3A-1, pas=uid%3A...). FB cần giá trị thật.
    if (value.includes("%")) { try { value = decodeURIComponent(value); } catch (_) {} }
    if (name && !name.startsWith("#")) pairs.push({ name, value });
  }
  return pairs;
}

// Facebook OAuth inside the app's own Electron window: cookies go into the nick's partition,
// the user watches (and can fix a Facebook re-login / checkpoint by hand), and the Dola
// session is harvested into accounts/<name> the moment sessionid appears.
pausedHandle("account:importFacebookElectron", async (_e, { name, line, lang }) => {
  if (!NAME_RE.test(name || "")) return { ok: false, error: "Tên nick chỉ gồm chữ, số, _ hoặc - (1-32 ký tự)" };
  const pairs = parseFacebookLine(line || "");
  const cred = parseFacebookCred(line || "");   // uid|pass[|2fa] để tự đăng nhập lại khi cookie chết
  const names = new Set(pairs.map((p) => p.name));
  if (!names.has("c_user") || !names.has("xs")) {
    return { ok: false, error: "Cookie Facebook thiếu c_user / xs — dán đầy đủ cookie hoặc dòng uid|pass|2fa|cookie|ua" };
  }
  gateway.pause();   // profile của nick phải rảnh; pausedHandle bật lại gateway khi xong

  const partition = `persist:dola-${name}`;
  const ses = session.fromPartition(partition);
  cleanUserAgent(ses);
  const send = (msg) => { try { _e.sender.send("account:fbStep", { name, line: `[${name}] ${msg}` }); } catch (_) {} };

  // Cửa sổ Electron phải đi qua ĐÚNG proxy như phía Python, nếu không mạng chặn dola.com
  // là cửa sổ trắng trơn. Kiểm tra với tới dola.com TRƯỚC khi mở cửa sổ, để không bật ra
  // một loạt cửa sổ trắng khi nạp nhiều nick cùng lúc.
  let proxyInfo = null;
  try { proxyInfo = await applyProxy(ses, DATA_DIR, name, send); } catch (err) { send(`⚠ đặt proxy lỗi: ${String(err).slice(0, 80)}`); }
  const pre = await preflightDola(ses, DATA_DIR, name);
  if (!pre.ok) { send(`✗ ${pre.error}`); return { ok: false, error: pre.error }; }

  send("Đang nạp cookie Facebook vào cửa sổ app…");
  const httpOnly = new Set(["xs", "datr", "sb", "fr", "c_user"]);
  for (const { name: cname, value } of pairs) {
    try {
      await ses.cookies.set({ url: "https://www.facebook.com", name: cname, value, domain: ".facebook.com", path: "/",
        secure: true, httpOnly: httpOnly.has(cname), expirationDate: Math.floor(Date.now() / 1000) + 180 * 86400 });
    } catch (err) { send(`(bỏ qua cookie ${cname}: ${String(err).slice(0, 60)})`); }
  }

  const win = new BrowserWindow({
    width: 560, height: 800, title: `Facebook → Dola — ${name}`,
    autoHideMenuBar: true,
    webPreferences: { partition, contextIsolation: true, nodeIntegration: false },
  });
  // Facebook's OAuth consent opens as a popup; let it open in the same partition.
  win.webContents.setWindowOpenHandler(() => ({ action: "allow" }));
  attachLoadErrorHandler(win, send, () => proxyInfo);   // trang trắng -> trang báo lỗi đọc được
  let closedByUser = false;
  win.on("closed", () => { closedByUser = true; });

  try {
    // Cookie FB đã nạp vào partition → vào THẲNG Dola và bấm "đăng nhập bằng Facebook".
    // Popup OAuth của Facebook tự nhận cookie (không hiện màn đăng nhập FB), nhanh hơn ~5s vì
    // bỏ hẳn bước mở facebook.com/me + chờ 3.5s. Cookie chết thì popup sẽ hiện login/checkpoint
    // và vòng chờ bên dưới đã cảnh báo để xử lý tay.
    send("Đang mở Dola (đăng nhập thẳng bằng cookie Facebook)…");
    if (win.isDestroyed()) return { ok: false, canceled: true };
    let popup = null;
    win.webContents.on("did-create-window", (child) => { popup = child; });
    await safeLoad(win, LOGIN_URL);
    await sleep(1500);

    const hasSession = async () =>
      (await ses.cookies.get({ url: "https://www.dola.com" })).some((c) => c.name === "sessionid" && c.value);

    if (!(await hasSession())) {
      send("Đang mở hộp đăng nhập Dola…");
      let fbClicked = "";
      for (let attempt = 1; attempt <= 10 && !fbClicked; attempt++) {
        // 1) explicit login button (only shows when Dola already recognises the device)
        await runJS(win.webContents, JS_OPEN_DOLA_LOGIN);
        await sleep(1000);
        // 2) guest mode: poke the composer so Dola opens its own login modal
        if (!(await runJS(win.webContents, JS_LOGIN_MODAL_OPEN))) {
          const t = await runJS(win.webContents, JS_TRIGGER_BY_SEND);
          if (attempt === 1) send(`Đang kích hoạt hộp đăng nhập (gửi thử tin nhắn: ${t})…`);
          await sleep(2500);
        }
        // 3) modal is up → click the Facebook icon
        if (await runJS(win.webContents, JS_LOGIN_MODAL_OPEN)) {
          const r = await runJS(win.webContents, JS_CLICK_FACEBOOK);
          if (r && !String(r).startsWith("no-") && !String(r).startsWith("not-found")) fbClicked = String(r);
        }
        if (!fbClicked) await sleep(1200);
      }
      send(fbClicked ? `Đã bấm Facebook (${fbClicked}), chờ popup OAuth…`
                     : "Chưa tự mở được hộp đăng nhập — trong cửa sổ Dola hãy gõ 1 tin nhắn rồi bấm nút Facebook. App vẫn theo dõi và tiếp tục.");
    }

    let continueClicks = 0;
    let warnedFbLogin = false;
    const fbTwoFa = { done: false };
    const deadline = Date.now() + LOGIN_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (closedByUser) return { ok: false, canceled: true, error: "Đã đóng cửa sổ trước khi Dola cấp phiên" };
      // OAuth popup: press "Continue as …"; warn if Facebook wants a password / checkpoint instead.
      if (popup && !popup.isDestroyed()) {
        const purl = popup.webContents.getURL();
        if (/login\.php|\/login\/|checkpoint|two_factor/.test(purl)) {
          const r = await fbAutofillStep(popup.webContents, cred, fbTwoFa, send);
          if (r === "sai-mat-khau") { if (!win.isDestroyed()) win.close(); return { ok: false, error: `Sai mật khẩu Facebook cho ${cred.uid || name} — cập nhật lại dòng tài khoản (uid|mật khẩu).` }; }
          if ((r === "checkpoint" || r === "no-creds") && !warnedFbLogin) {
            warnedFbLogin = true;
            send(r === "checkpoint" ? "Facebook đòi xác minh danh tính / 2FA nhưng dòng không có mã — xử lý tay trong popup, app tự tiếp tục."
                                   : "Popup Facebook đòi đăng nhập nhưng dòng thiếu mật khẩu — xử lý tay trong popup.");
          }
        } else if (continueClicks < 6) {
          const r = await runJS(popup.webContents, JS_CLICK_CONTINUE);
          if (r && String(r).startsWith("clicked")) { continueClicks++; send(`Đã bấm ${String(r).slice(8)} trong popup Facebook…`); await sleep(2500); }
        }
      }
      // Một số bản Dola điều hướng CHÍNH cửa sổ sang FB login thay vì mở popup → tự điền luôn.
      if (!win.isDestroyed()) {
        const wurl = (win.webContents.getURL() || "").toLowerCase();
        if (/facebook\.com\/(login|checkpoint)|login\.php|two_factor/.test(wurl)) {
          const r = await fbAutofillStep(win.webContents, cred, fbTwoFa, send);
          if (r === "sai-mat-khau") { win.close(); return { ok: false, error: `Sai mật khẩu Facebook cho ${cred.uid || name} — cập nhật lại dòng tài khoản.` }; }
        }
      }
      const gate = await runJS(win.webContents, JS_AGE_GATE);
      if (gate === "confirmed") send("Đã xác nhận 18+ trên Dola…");
      const current = await ses.cookies.get({ url: "https://www.dola.com" });
      if (current.some((c) => c.name === "sessionid" && c.value)) {
        return await harvestDolaSession(win, ses, name, lang, send, LOGIN_URL);
      }
      await new Promise((r) => setTimeout(r, LOGIN_POLL_MS));
    }
    if (!win.isDestroyed()) win.close();
    return { ok: false, error: "Hết giờ chờ (5 phút) mà Dola chưa cấp sessionid." };
  } catch (e) {
    if (!win.isDestroyed()) win.close();
    return { ok: false, error: String(e) };
  }
});


// Login inside an Electron window (own partition), then harvest cookies into accounts/<name>.
const LOGIN_URL = "https://www.dola.com/chat";
const LOGIN_TIMEOUT_MS = 5 * 60 * 1000;
const LOGIN_POLL_MS = 3000;

pausedHandle("account:loginElectron", async (_e, { name, lang }) => {
  if (!NAME_RE.test(name || "")) return { ok: false, error: "Tên nick chỉ gồm chữ, số, _ hoặc - (1-32 ký tự)" };
  gateway.pause();   // profile của nick phải rảnh; pausedHandle bật lại gateway khi xong

  const partition = `persist:dola-${name}`;
  const ses = session.fromPartition(partition);
  cleanUserAgent(ses);
  let proxyInfo = null;
  try { proxyInfo = await applyProxy(ses, DATA_DIR, name, null); } catch (_) {}
  const pre = await preflightDola(ses, DATA_DIR, name);
  if (!pre.ok) return { ok: false, error: pre.error };
  const win = new BrowserWindow({
    width: 480, height: 760, title: `Đăng nhập dola.com — ${name}`,
    autoHideMenuBar: true,
    webPreferences: { partition, contextIsolation: true, nodeIntegration: false },
  });
  attachLoadErrorHandler(win, null, () => proxyInfo);
  let closedByUser = false;
  win.on("closed", () => { closedByUser = true; });

  try {
    await safeLoad(win, LOGIN_URL);
    const deadline = Date.now() + LOGIN_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (closedByUser) return { ok: false, canceled: true };
      const current = await ses.cookies.get({ url: "https://www.dola.com" });
      if (current.some((c) => c.name === "sessionid" && c.value)) {
        return await harvestDolaSession(win, ses, name, lang, null, LOGIN_URL);
      }
      await new Promise((r) => setTimeout(r, LOGIN_POLL_MS));
    }
    if (!win.isDestroyed()) win.close();
    return { ok: false, error: "Hết giờ chờ đăng nhập (5 phút)." };
  } catch (e) {
    if (!win.isDestroyed()) win.close();
    return { ok: false, error: String(e) };
  }
});

// Manual login: open accounts/<name> in a headed browser; you log in by hand,
// login_profile.py auto-captures the cookies. Gateway must release the profile first.
pausedHandle("account:login", async (_e, { name, lang }) => {
  if (isRemote()) return REMOTE_ONLY;
  if (!name || !/^[A-Za-z0-9_-]{1,32}$/.test(name)) {
    return { ok: false, error: "Tên nick chỉ gồm chữ, số, _ hoặc - (1-32 ký tự)" };
  }
  gateway.pause();   // profile của nick phải rảnh; pausedHandle bật lại gateway khi xong
  const args = ["login_profile.py", name, lang || "ja"];
  return await new Promise((resolve) => {
    const proc = spawnPy(args);
    let out = "", err = "";
    proc.stdout.on("data", (d) => (out += d));
    proc.stderr.on("data", (d) => (err += d));
    proc.on("exit", (code) =>
      resolve({ ok: code === 0, output: out.trim(), error: err.trim() || (code ? `exit ${code}` : "") })
    );
    proc.on("error", (e) => resolve({ ok: false, error: String(e) }));
  });
});

// Wipe a nick's cookies (reset to logged-out); profile kept. Fails if the profile is
// currently open in a live Chromium window (assert_profile_free in clear_cookies.py).
ipcMain.handle("account:clearCookies", async (_e, { name }) => {
  if (isRemote()) return REMOTE_ONLY;
  if (!name || !/^[A-Za-z0-9_-]{1,32}$/.test(name)) {
    return { ok: false, error: "Tên nick chỉ gồm chữ, số, _ hoặc - (1-32 ký tự)" };
  }
  const args = ["clear_cookies.py", name];
  return await new Promise((resolve) => {
    const proc = spawnPy(args);
    let out = "", err = "";
    proc.stdout.on("data", (d) => (out += d));
    proc.stderr.on("data", (d) => (err += d));
    proc.on("exit", (code) =>
      resolve({ ok: code === 0, output: out.trim(), error: err.trim() || (code ? `exit ${code}` : "") })
    );
    proc.on("error", (e) => resolve({ ok: false, error: String(e) }));
  });
});

// Delete a nick entirely (profile dir + pool metadata). Refuses if its window is open.
ipcMain.handle("account:delete", async (_e, { name }) => {
  if (!name || !/^[A-Za-z0-9_-]{1,32}$/.test(name)) {
    return { ok: false, error: "Tên nick chỉ gồm chữ, số, _ hoặc - (1-32 ký tự)" };
  }
  const args = ["delete_account.py", name];
  return await new Promise((resolve) => {
    const proc = spawnPy(args);
    let out = "", err = "";
    proc.stdout.on("data", (d) => (out += d));
    proc.stderr.on("data", (d) => (err += d));
    proc.on("exit", (code) =>
      resolve({ ok: code === 0, output: out.trim(), error: err.trim() || (code ? `exit ${code}` : "") })
    );
    proc.on("error", (e) => resolve({ ok: false, error: String(e) }));
  });
});

ipcMain.handle("video:removeWatermark", async (_e, { file }) => {
  const name = path.basename(String(file || ""));   // chỉ nhận tên file, chống path traversal
  if (!name || !name.toLowerCase().endsWith(".mp4")) return { ok: false, error: "Tên file video không hợp lệ" };
  const src = path.join(config().downloadsDir, name);
  if (!fs.existsSync(src)) return { ok: false, error: "Không thấy file video: " + name };
  const args = ["watermark.py", "--auto", src];
  return await new Promise((resolve) => {
    const proc = spawnPy(args);
    let out = "", err = "";
    proc.stdout.on("data", (d) => (out += d));
    proc.stderr.on("data", (d) => (err += d));
    proc.on("exit", (code) => {
      const m = out.match(/->\s*(\S+\.mp4)/);
      const outfile = m ? path.basename(m[1].trim()) : null;
      resolve({ ok: code === 0 && !!outfile, output: outfile, log: out.trim(),
                error: err.trim() || (code ? `exit ${code}` : (outfile ? "" : "Không dò thấy watermark")) });
    });
    proc.on("error", (e) => resolve({ ok: false, error: String(e) }));
  });
});

ipcMain.handle("account:setProxy", async (_e, { name, proxy }) => {
  if (!NAME_RE.test(name || "")) return { ok: false, error: "Tên nick không hợp lệ" };
  if (isRemote()) {   // proxy của nick nằm trên máy chủ, không phải file trên máy này
    const v = (proxy || "").trim();
    if (v && !parseProxy(v)) return { ok: false, error: `Proxy sai định dạng. Chấp nhận: ${PROXY_FORMATS}` };
    const c = config();
    return setAccountProxy(c.base, c.adminKey, name, v);
  }
  const dir = path.join(DATA_DIR, "accounts", name);
  try {
    fs.mkdirSync(dir, { recursive: true });
    const f = path.join(dir, "proxy.txt");
    const v = (proxy || "").trim();
    if (v) fs.writeFileSync(f, v, "utf8");
    else if (fs.existsSync(f)) fs.unlinkSync(f);
    return { ok: true, proxy: v || "(chung)" };
  } catch (e) { return { ok: false, error: String(e) }; }
});

ipcMain.handle("account:bulkImport", async (_e, { json, verify }) => {
  if (isRemote()) return REMOTE_ONLY;
  const text = (json || "").trim();
  if (!text) return { ok: false, error: "Chưa dán nội dung JSON export" };
  try { JSON.parse(text); } catch (e) { return { ok: false, error: "JSON không hợp lệ: " + String(e).slice(0, 80) }; }
  const tmp = path.join(os.tmpdir(), `dola-bulk-${Date.now()}.json`);
  fs.writeFileSync(tmp, text, "utf8");
  const args = ["-u", "import_seedance_export.py", tmp];
  if (!verify) args.push("--no-verify");
  return await new Promise((resolve) => {
    const proc = spawnPy(args);
    let out = "", err = "";
    proc.stdout.on("data", (d) => {
      out += d;
      for (const line of String(d).split(/\r?\n/)) if (line.trim()) _e.sender.send("account:bulkStep", { line: line.trim() });
    });
    proc.stderr.on("data", (d) => (err += d));
    proc.on("exit", (code) => { try { fs.unlinkSync(tmp); } catch (_) {}
      resolve({ ok: code === 0, output: out.trim(), error: code ? (err.trim() || `exit ${code}`) : "" }); });
    proc.on("error", (e) => { try { fs.unlinkSync(tmp); } catch (_) {} resolve({ ok: false, error: String(e) }); });
  });
});

ipcMain.handle("account:getProxy", async (_e, { name }) => {
  if (isRemote()) {
    const c = config();
    const r = await getAccountProxy(c.base, c.adminKey, name);
    return { ok: true, proxy: r.ok ? (r.proxy || "") : "" };
  }
  try {
    const f = path.join(DATA_DIR, "accounts", name, "proxy.txt");
    return { ok: true, proxy: fs.existsSync(f) ? fs.readFileSync(f, "utf8").trim() : "" };
  } catch (e) { return { ok: true, proxy: "" }; }
});

// Proxy chung = DOLA_PROXY trong .env.local (DATA_DIR). Python chỉ đọc lại khi Tắt/Bật server.
ipcMain.handle("proxy:getGlobal", async () => {
  if (isRemote()) {   // proxy chung ĐANG chạy trên máy chủ (đã che mật khẩu), không phải .env.local máy này
    const c = config();
    const r = await getRemoteConfig(c.base, c.adminKey);
    return { ok: true, proxy: r.ok ? (r.proxy || "") : "", remote: true };
  }
  return { ok: true, proxy: globalProxy(DATA_DIR) };
});

// Tự thử lại / xoay nick: đọc từ /health của server đang dùng (cục bộ hay VPS), đổi qua /api/admin/retry
// (áp dụng ngay), và nhớ vào .env.local cho lần khởi động sau khi server ở máy này.
ipcMain.handle("config:getAutoRetry", async () => {
  try {
    const r = await fetch(config().base + "/health", { cache: "no-store" });
    const j = await r.json();
    return { ok: true, on: j.auto_retry !== false };
  } catch (e) { return { ok: false, on: true, error: String(e).slice(0, 80) }; }
});
ipcMain.handle("config:setAutoRetry", async (_e, { on }) => {
  const c = config();
  const headers = { "Content-Type": "application/json" };
  if (c.adminKey) headers["x-admin-key"] = c.adminKey;
  try {
    const r = await fetch(c.base + "/api/admin/retry", { method: "POST", headers, body: JSON.stringify({ auto_retry: !!on }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, error: j.detail || ("HTTP " + r.status) };
    if (!c.remote) upsertEnvLocal("DOLA_AUTO_RETRY", on ? "1" : "0");
    return { ok: true, on: !!on, remote: c.remote };
  } catch (e) { return { ok: false, error: "Server chưa chạy? " + String(e).slice(0, 80) }; }
});
ipcMain.handle("proxy:setGlobal", (_e, { proxy }) => {
  if (isRemote()) return { ok: false, error: "Đang dùng máy chủ từ xa: đặt DOLA_PROXY trong .env.local trên máy chủ rồi khởi động lại dịch vụ dola-gateway." };
  const v = (proxy || "").trim();
  if (v && !parseProxy(v)) return { ok: false, error: `Proxy sai định dạng. Chấp nhận: ${PROXY_FORMATS}` };
  try { upsertEnvLocal("DOLA_PROXY", v); } catch (e) { return { ok: false, error: String(e) }; }
  return { ok: true, proxy: v || "(nối thẳng)" };
});
ipcMain.handle("proxy:test", async (_e, { proxy }) => {
  try { return await testProxy(proxy); } catch (e) { return { ok: false, error: String(e) }; }
});

// Máy chủ từ xa: DOLA_REMOTE_BASE + cùng bộ khoá DOLA_API_KEYS / DOLA_ADMIN_KEY (server cục bộ cũng đọc
// bộ khoá này nên đổi qua lại không lệch khoá). Trống = chạy server trên máy này như cũ.
ipcMain.handle("remote:get", () => {
  const env = readEnvLocal();
  return { ok: true, base: normalizeRemoteBase(env.DOLA_REMOTE_BASE),
           apiKey: (env.DOLA_API_KEYS || "").split(",")[0].trim(), adminKey: env.DOLA_ADMIN_KEY || "" };
});
ipcMain.handle("remote:set", (_e, { base, apiKey, adminKey }) => {
  const b = normalizeRemoteBase(base);
  if (String(base || "").trim() && !b) return { ok: false, error: "Địa chỉ máy chủ sai (vd: http://45.77.1.2:8000)" };
  try {
    upsertEnvLocal("DOLA_REMOTE_BASE", b);
    if (String(apiKey || "").trim()) upsertEnvLocal("DOLA_API_KEYS", String(apiKey).trim());
    if (String(adminKey || "").trim()) upsertEnvLocal("DOLA_ADMIN_KEY", String(adminKey).trim());
  } catch (e) { return { ok: false, error: String(e) }; }
  if (b) gateway.stop();   // chuyển sang máy chủ từ xa: server cục bộ không còn được dùng
  return { ok: true, base: b || "(máy này)" };
});
ipcMain.handle("remote:test", async (_e, { base, apiKey, adminKey }) => {
  try { return await testRemote(base, apiKey, adminKey); } catch (e) { return { ok: false, error: String(e) }; }
});

// Máy chủ từ xa: video nằm trên VPS — tải về thư mục video của máy này để "Mở thư mục" vẫn có file.
// Cục bộ thì server đã ghi thẳng vào thư mục đó → bỏ qua.
ipcMain.handle("video:save", async (_e, { url }) => {
  if (!isRemote()) return { ok: true, skipped: true };
  try {
    const u = new URL(String(url || ""));
    const name = path.basename(u.pathname);
    if (!/^[\w.-]+\.mp4$/i.test(name)) return { ok: false, error: "tên file lạ: " + name };
    const c = config();
    fs.mkdirSync(c.downloadsDir, { recursive: true });
    const dest = path.join(c.downloadsDir, name);
    if (fs.existsSync(dest)) return { ok: true, path: dest, existed: true };
    const r = await fetch(u.toString(), { headers: c.apiKey ? { Authorization: "Bearer " + c.apiKey } : {} });
    if (!r.ok) return { ok: false, error: "HTTP " + r.status };
    fs.writeFileSync(dest, Buffer.from(await r.arrayBuffer()));
    return { ok: true, path: dest };
  } catch (e) { return { ok: false, error: String((e && e.message) || e).slice(0, 160) }; }
});

ipcMain.handle("open:downloads", () => shell.openPath(config().downloadsDir));

// Ghi/đổi 1 khoá trong .env.local (dùng cho DOLA_ACCOUNTS_DIR).
function upsertEnvLocal(key, value) {
  const file = path.join(DATA_DIR, ".env.local");
  let lines = [];
  try { lines = fs.readFileSync(file, "utf8").split(/\r?\n/); } catch (_) {}
  const idx = lines.findIndex((l) => l.trim().startsWith(key + "="));
  const entry = `${key}=${value}`;
  if (idx >= 0) lines[idx] = entry; else lines.push(entry);
  fs.writeFileSync(file, lines.join("\n").replace(/\n+$/, "") + "\n");
}

ipcMain.handle("account:getAccountsDir", () => {
  const env = readEnvLocal();
  const dir = env.DOLA_ACCOUNTS_DIR || "accounts";
  const abs = path.isAbsolute(dir) ? dir : path.join(DATA_DIR, dir);
  return { dir, abs };
});

ipcMain.handle("account:chooseAccountsDir", async () => {
  const r = await dialog.showOpenDialog({
    properties: ["openDirectory", "createDirectory"],
    title: "Chọn thư mục lưu profile nick (accounts)",
  });
  if (r.canceled || !r.filePaths.length) return { ok: false, canceled: true };
  const dir = r.filePaths[0];
  try { upsertEnvLocal("DOLA_ACCOUNTS_DIR", dir); } catch (e) { return { ok: false, error: String(e) }; }
  return { ok: true, dir };
});

ipcMain.handle("open:accountsDir", () => {
  const env = readEnvLocal();
  const dir = env.DOLA_ACCOUNTS_DIR || "accounts";
  return shell.openPath(path.isAbsolute(dir) ? dir : path.join(DATA_DIR, dir));
});

ipcMain.handle("video:getDir", () => {
  const dir = config().downloadsDir;
  return { abs: dir };
});

ipcMain.handle("account:verifyAll", async (_e, names) => {
  try {
    const env = readEnvLocal();
    const headers = { "Content-Type": "application/json" };
    if (env.DOLA_ADMIN_KEY) headers["x-admin-key"] = env.DOLA_ADMIN_KEY;
    const r = await fetch(config().base + "/api/admin/verify-all", {
      method: "POST", headers,
      body: JSON.stringify({ names: Array.isArray(names) && names.length ? names : null }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, error: j.detail || ("HTTP " + r.status) };
    return j;
  } catch (e) { return { ok: false, error: String(e) }; }
});

// Đổi số luồng: áp dụng ngay cho server đang chạy, đồng thời nhớ cho lần khởi động sau.
ipcMain.handle("config:setConcurrency", async (_e, { send, login }) => {
  const body = {};
  if (Number.isFinite(send)) body.max_concurrency = Math.max(1, Math.min(24, Math.round(send)));
  if (Number.isFinite(login)) body.login_concurrency = Math.max(1, Math.min(12, Math.round(login)));
  if (!Object.keys(body).length) return { ok: false, error: "không có giá trị nào để đổi" };
  try {
    const env = readEnvLocal();
    const headers = { "Content-Type": "application/json" };
    if (env.DOLA_ADMIN_KEY) headers["x-admin-key"] = env.DOLA_ADMIN_KEY;
    const r = await fetch(config().base + "/api/admin/concurrency",
                          { method: "POST", headers, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, error: j.detail || ("HTTP " + r.status) };
    if (body.max_concurrency) upsertEnvLocal("DOLA_MAX_CONCURRENCY", body.max_concurrency);
    if (body.login_concurrency) upsertEnvLocal("DOLA_LOGIN_CONCURRENCY", body.login_concurrency);
    return j;
  } catch (e) { return { ok: false, error: String(e) }; }
});

ipcMain.handle("video:chooseDir", async () => {
  const r = await dialog.showOpenDialog({
    properties: ["openDirectory", "createDirectory"],
    title: "Chọn thư mục lưu video",
  });
  if (r.canceled || !r.filePaths.length) return { ok: false, canceled: true };
  const dir = r.filePaths[0];
  try { upsertEnvLocal("DOLA_DOWNLOAD_DIR", dir); } catch (e) { return { ok: false, error: String(e) }; }
  return { ok: true, dir };
});
ipcMain.handle("open:logs", () => shell.openPath(LOG_FILE));
ipcMain.handle("logs:tail", (_e, n) => {
  // ponytail: reads the whole file then slices; fine under the 5MB cap.
  try {
    if (!fs.existsSync(LOG_FILE)) return { ok: true, text: "(chưa có log — bấm \"Bật server\" rồi chạy 1 video)" };
    const lines = fs.readFileSync(LOG_FILE, "utf8").split("\n");
    return { ok: true, text: lines.slice(-(n || 400)).join("\n") };
  } catch (e) { return { ok: false, error: String(e) }; }
});

app.whenReady().then(createWindow);
app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
app.on("window-all-closed", () => {
  gateway.stop();
  if (process.platform !== "darwin") app.quit();
});
app.on("before-quit", () => { gateway.stop(); });
