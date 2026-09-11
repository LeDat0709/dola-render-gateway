// Proxy + chẩn đoán lỗi tải trang cho MỌI cửa sổ Electron.
//
// Trước đây các cửa sổ Electron (đăng nhập Facebook→Dola, đăng nhập cửa sổ app, tạo video
// nhanh) nối THẲNG: bỏ qua DOLA_PROXY lẫn proxy riêng của nick, vốn chỉ được phía Python
// (patchright) dùng. Khi mạng chặn dola.com thì cửa sổ chỉ hiện trang trắng, lỗi bị nuốt
// trong `catch (_) {}`, người dùng không biết vì sao.
const fs = require("fs");
const path = require("path");
// require("electron") nằm trong hàm: parseProxy/accountProxy test được bằng node thường.

// Đọc .env.local (bản rút gọn, khớp config.py): KEY=VALUE, dòng đầu thắng.
function readEnvLocal(repoRoot) {
  const out = {};
  const file = path.join(repoRoot, ".env.local");
  if (!fs.existsSync(file)) return out;
  for (const raw of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const idx = line.indexOf("=");
    const key = line.slice(0, idx).trim();
    const val = line.slice(idx + 1).trim().replace(/^["']|["']$/g, "");
    if (!(key in out)) out[key] = val;
  }
  return out;
}

// Chấp nhận đúng các định dạng browser.py hỗ trợ:
// http://host:port · socks5://host:port · user:pass@host:port · host:port:user:pass · host:port
function parseProxy(raw) {
  const s = String(raw || "").trim();
  if (!s) return null;
  let scheme = "http";
  let rest = s;
  const m = s.match(/^(https?|socks4|socks5h?):\/\/(.*)$/i);
  if (m) { scheme = m[1].toLowerCase() === "socks5h" ? "socks5" : m[1].toLowerCase(); rest = m[2]; }
  let user = "";
  let pass = "";
  const at = rest.lastIndexOf("@");
  if (at >= 0) {
    const cred = rest.slice(0, at);
    rest = rest.slice(at + 1);
    const i = cred.indexOf(":");
    user = i >= 0 ? cred.slice(0, i) : cred;
    pass = i >= 0 ? cred.slice(i + 1) : "";
  }
  const bits = rest.split(":");
  let host = bits[0] || "";
  let port = bits[1] || "";
  if (bits.length === 4) { host = bits[0]; port = bits[1]; user = bits[2]; pass = bits[3]; }
  if (!host || !/^\d+$/.test(port)) return null;
  return { scheme, host, port, user, pass, rules: `${scheme}://${host}:${port}` };
}

// Proxy riêng của nick (accounts/<nick>/proxy.txt) thắng; không có thì dùng DOLA_PROXY chung.
function accountProxy(repoRoot, name) {
  try {
    const f = path.join(repoRoot, "accounts", String(name || ""), "proxy.txt");
    if (fs.existsSync(f)) {
      const v = fs.readFileSync(f, "utf8").trim();
      if (v) return v;
    }
  } catch (_) { /* đọc lỗi -> rơi về proxy chung */ }
  return (readEnvLocal(repoRoot).DOLA_PROXY || "").trim();
}

// Proxy có user:pass -> Chromium hỏi credential qua sự kiện app "login".
const PROXY_CRED = new Map();
let authHooked = false;
function hookProxyAuth() {
  if (authHooked) return;
  authHooked = true;
  const { app } = require("electron");
  app.on("login", (event, _wc, _details, authInfo, callback) => {
    if (!authInfo || !authInfo.isProxy) return;
    const c = PROXY_CRED.get(`${authInfo.host}:${authInfo.port}`);
    if (!c) return;
    event.preventDefault();
    callback(c.user, c.pass);
  });
}

// Gắn proxy vào một session Electron. Trả về mô tả proxy đang dùng (null = nối thẳng).
async function applyProxy(ses, repoRoot, name, send) {
  const raw = accountProxy(repoRoot, name);
  const p = parseProxy(raw);
  if (!p) {
    if (send && raw) send(`⚠ Proxy "${raw}" sai định dạng — đang nối thẳng.`);
    try { await ses.setProxy({ mode: "direct" }); } catch (_) {}
    return null;
  }
  hookProxyAuth();
  if (p.user) PROXY_CRED.set(`${p.host}:${p.port}`, { user: p.user, pass: p.pass });
  await ses.setProxy({ proxyRules: p.rules, proxyBypassRules: "<local>" });
  if (send) send(`Proxy: ${p.scheme}://${p.host}:${p.port}${p.user ? " (có xác thực)" : ""}`);
  return p;
}

// Mã lỗi mạng của Chromium -> câu tiếng Việt nói rõ phải làm gì.
const NET_HINT = {
  "-21": "ERR_NETWORK_CHANGED — mạng vừa đổi (đổi Wi-Fi/VPN). Thử lại.",
  "-105": "ERR_NAME_NOT_RESOLVED — không phân giải được tên miền dola.com.",
  "-106": "ERR_INTERNET_DISCONNECTED — máy đang mất mạng.",
  "-118": "ERR_CONNECTION_TIMED_OUT — kết nối quá hạn. Mạng đang chặn dola.com, cần proxy exit node Nhật/Hàn.",
  "-130": "ERR_PROXY_CONNECTION_FAILED — không nối được proxy (proxy tắt, sai cổng, hoặc sai mật khẩu).",
  "-137": "ERR_NAME_RESOLUTION_FAILED — phân giải tên miền thất bại.",
  "-200": "ERR_CERT_COMMON_NAME_INVALID — chứng chỉ sai, có thể proxy đang can thiệp TLS.",
  "-201": "ERR_CERT_DATE_INVALID — sai giờ hệ thống hoặc proxy can thiệp TLS.",
  "-324": "ERR_EMPTY_RESPONSE — máy chủ đóng kết nối, thường do bị chặn giữa đường.",
};
function describeNetError(code, desc) {
  return NET_HINT[String(code)] || `${desc || "lỗi tải trang"} (mã ${code})`;
}

const escapeHtml = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// Thay trang trắng bằng trang báo lỗi đọc được, và đẩy lý do về giao diện.
function showLoadError(win, url, code, desc, send, proxyInfo) {
  const hint = describeNetError(code, desc);
  if (send) send(`✗ ${hint}`);
  if (!win || win.isDestroyed()) return;
  const pline = proxyInfo
    ? `Đang dùng proxy <b>${escapeHtml(proxyInfo.scheme + "://" + proxyInfo.host + ":" + proxyInfo.port)}</b> — kiểm tra proxy còn sống không.`
    : `Chưa cấu hình proxy. Đặt <code>DOLA_PROXY</code> trong <code>.env.local</code> (exit node Nhật hoặc Hàn), hoặc đặt proxy riêng cho nick, rồi thử lại.`;
  const html = `<meta charset="utf-8"><body style="margin:0;font:14px/1.65 -apple-system,system-ui,'Segoe UI',sans-serif;background:#0c0c0e;color:#fafafa;padding:28px">
<h2 style="margin:0 0 6px;font-size:16px">Không mở được trang</h2>
<p style="color:#a1a1aa;margin:0 0 14px;word-break:break-all">${escapeHtml(url)}</p>
<p style="background:#1c1c20;border:1px solid #27272a;border-radius:8px;padding:12px;margin:0 0 14px">${escapeHtml(hint)}</p>
<p style="color:#a1a1aa;margin:0">${pline}</p>
</body>`;
  win.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(html)).catch(() => {});
}

// Bắt lỗi tải của khung chính (bỏ qua iframe con và ERR_ABORTED do chuyển hướng).
function attachLoadErrorHandler(win, send, getProxyInfo) {
  win.webContents.on("did-fail-load", (_e, code, desc, url, isMainFrame) => {
    if (!isMainFrame) return;
    if (code === -3 || /ERR_ABORTED/i.test(String(desc))) return;
    if (String(url || "").startsWith("data:")) return;   // chính trang báo lỗi
    showLoadError(win, url, code, desc, send, typeof getProxyInfo === "function" ? getProxyInfo() : getProxyInfo);
  });
}

// Thử với tới một URL qua đúng session (tức là qua proxy của session đó).
function probeUrl(ses, url, timeoutMs = 12000) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (r) => { if (!done) { done = true; resolve(r); } };
    let req;
    const { net } = require("electron");
    try { req = net.request({ method: "GET", url, session: ses }); }
    catch (e) { return finish({ ok: false, error: String((e && e.message) || e) }); }
    const timer = setTimeout(() => {
      try { req.abort(); } catch (_) {}
      finish({ ok: false, error: "quá hạn kết nối (timeout)" });
    }, timeoutMs);
    req.on("response", (res) => {
      clearTimeout(timer);
      try { res.on("data", () => {}); res.on("end", () => {}); } catch (_) {}
      finish({ ok: true, status: res.statusCode });
    });
    req.on("error", (e) => { clearTimeout(timer); finish({ ok: false, error: String((e && e.message) || e) }); });
    try { req.end(); } catch (e) { clearTimeout(timer); finish({ ok: false, error: String((e && e.message) || e) }); }
  });
}

// Kiểm tra trước khi mở cửa sổ: vào được dola.com không? Trả về câu lỗi đã giải thích sẵn.
async function preflightDola(ses, repoRoot, name, url = "https://www.dola.com/") {
  const r = await probeUrl(ses, url);
  if (r.ok) return { ok: true };
  const p = parseProxy(accountProxy(repoRoot, name));
  const where = p
    ? `Proxy đang dùng: ${p.scheme}://${p.host}:${p.port} — kiểm tra proxy còn chạy không.`
    : `Chưa cấu hình proxy — mạng của bạn đang chặn dola.com. Đặt DOLA_PROXY (exit node Nhật/Hàn) trong .env.local rồi thử lại.`;
  return { ok: false, error: `Không vào được dola.com (${r.error}). ${where}` };
}

module.exports = {
  readEnvLocal, parseProxy, accountProxy, applyProxy, hookProxyAuth,
  describeNetError, showLoadError, attachLoadErrorHandler, probeUrl, preflightDola,
};
