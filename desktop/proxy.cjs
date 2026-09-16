// Proxy + chẩn đoán lỗi tải trang cho MỌI cửa sổ Electron.
//
// Trước đây các cửa sổ Electron (đăng nhập Facebook→Dola, đăng nhập cửa sổ app, tạo video
// nhanh) nối THẲNG: bỏ qua DOLA_PROXY lẫn proxy riêng của nick, vốn chỉ được phía Python
// (patchright) dùng. Khi mạng chặn dola.com thì cửa sổ chỉ hiện trang trắng, lỗi bị nuốt
// trong `catch (_) {}`, người dùng không biết vì sao.
const fs = require("fs");
const path = require("path");
// remote.cjs thuần fetch (không require electron) nên proxy.cjs vẫn test được bằng node thường.
const { gatewayBase, adminFetch } = require("./remote.cjs");
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

// Proxy xoay theo LINK KEY (proxyxoay.shop và tương tự): "https://…/get.php?key=…". Khác proxy tĩnh —
// phải GỌI link mới ra IP thật, việc đó do server Python (proxyxoay.py, IP whitelist) làm. Ở JS chỉ
// nhận diện để: (1) không báo "sai định dạng" khi lưu, (2) cửa sổ Electron nối thẳng thay vì kẹt lỗi.
function isKeyLink(raw) {
  const s = String(raw || "").trim().toLowerCase();
  return /^https?:\/\//.test(s) && (s.includes("get.php") || s.includes("key=") || s.includes("access_token="));
}
function isBareTmproxyKey(raw) { return /^[a-f0-9]{32}$/i.test(String(raw || "").trim()); }
// Proxy XOAY (server tự gọi nhà bán lấy IP): link get.php?key=, tmproxy://KEY, hoặc KEY TMProxy trần (32 hex).
function isRotating(raw) {
  const s = String(raw || "").trim().toLowerCase();
  // topproxy:// proxyvn:// proxyxoay:// KEY: server tự chuyển thành link get.php (browser.normalize_proxy_input)
  return isKeyLink(s) || s.startsWith("tmproxy://") || isBareTmproxyKey(s) || /^(topproxy|proxyvn|proxyxoay|tmproxy|shoplike):(\/\/)?./.test(s);
}
// KEY TMProxy trần → tmproxy://KEY để lưu/dùng thống nhất (khớp browser.normalize_proxy_input phía Python).
function normalizeProxyInput(raw) {
  const s = String(raw || "").trim();
  return isBareTmproxyKey(s) ? "tmproxy://" + s : s;
}

// Khớp config.py: thiếu khoá DOLA_PROXY hoặc trống → nối thẳng. Trước đây thiếu khoá là rơi về
// 127.0.0.1:7890 (Clash của máy dev) → máy khách không chạy Clash lỗi ERR_PROXY_CONNECTION_FAILED
// ở mọi cửa sổ. Muốn Clash thì ghi DOLA_PROXY=http://127.0.0.1:7890 vào .env.local.
const DEFAULT_PROXY = "";
function globalProxy(repoRoot) {
  const env = readEnvLocal(repoRoot);
  return ("DOLA_PROXY" in env) ? env.DOLA_PROXY.trim() : DEFAULT_PROXY;
}

// Proxy riêng của nick (accounts/<nick>/proxy.txt) thắng; không có thì dùng proxy chung.
function accountProxy(repoRoot, name) {
  try {
    const f = path.join(repoRoot, "accounts", String(name || ""), "proxy.txt");
    if (fs.existsSync(f)) {
      const v = fs.readFileSync(f, "utf8").trim();
      if (v) return v;
    }
  } catch (_) { /* đọc lỗi -> rơi về proxy chung */ }
  return globalProxy(repoRoot);
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

// Gắn một proxy ĐÃ phân tích vào session (null = nối thẳng). Tách ra để đường proxy tĩnh và đường
// proxy xoay dùng chung đúng MỘT cách gắn credential.
async function applyParsed(ses, p, send) {
  if (!p) { try { await ses.setProxy({ mode: "direct" }); } catch (_) {} return null; }
  hookProxyAuth();
  if (p.user) PROXY_CRED.set(`${p.host}:${p.port}`, { user: p.user, pass: p.pass });
  await ses.setProxy({ proxyRules: p.rules, proxyBypassRules: "<local>" });
  if (send) send(`Proxy: ${p.scheme}://${p.host}:${p.port}${p.user ? " (có xác thực)" : ""}`);
  return p;
}

// Gắn một chuỗi proxy TĨNH vào session Electron. Proxy xoay không mang IP trong chuỗi (nó là key/link)
// nên phải đi qua resolveProxy để hỏi gateway — NÉM chứ không lặng lẽ nối thẳng như trước.
async function applyProxyRaw(ses, raw, send) {
  if (isRotating(raw)) throw new Error("Proxy xoay phải giải qua gateway — dùng resolveProxy/applyProxy.");
  const p = parseProxy(raw);
  // Có chuỗi mà đọc không ra proxy → NÉM. Trước đây báo "đang nối thẳng" rồi đi IP máy thật.
  if (!p && String(raw || "").trim()) throw new Error(`Proxy "${raw}" sai định dạng — KHÔNG nối thẳng bằng IP máy. Chấp nhận: ${PROXY_FORMATS}`);
  return applyParsed(ses, p, send);
}

// {server,username,password} (dạng patchright của Python) → dạng parseProxy. Nhận DICT chứ không nhận
// chuỗi "user:pass@host:port": account_proxy_url() percent-encode mật khẩu, mà Chromium cần mật khẩu THÔ
// ở callback sự kiện "login" — đi qua chuỗi là sai mật khẩu với proxy có ký tự lạ.
function fromServerDict(d) {
  const m = String((d && d.server) || "").match(/^(\w+):\/\/([^:/]+):(\d+)$/);
  if (!m) return null;
  return { scheme: m[1], host: m[2], port: m[3], user: (d && d.username) || "", pass: (d && d.password) || "",
           rules: `${m[1]}://${m[2]}:${m[3]}` };
}

// Proxy cửa sổ Electron của nick, ĐÃ giải sẵn. Proxy xoay → hỏi gateway (nơi DUY NHẤT giữ cache IP và giữ
// IP whitelist với nhà bán). Lấy không được thì NÉM: nối thẳng ở đây nghĩa là mọi nick đăng nhập bằng IP
// nhà rồi render bằng IP proxy — đúng dấu hiệu chống gian lận (facebook_login.py đã cảnh báo).
// BẮT BUỘC gọi TRƯỚC freeProfileForLogin(): ở chế độ gửi "fetch" hàm đó GIẾT gateway.
async function resolveProxy(repoRoot, name) {
  const raw = accountProxy(repoRoot, name);
  if (!isRotating(raw)) {
    const p = parseProxy(raw);
    // Rỗng → null = nối thẳng (nick không khai proxy nào). CÓ chuỗi mà sai định dạng → NÉM: trước đây trả null, cửa sổ
    // nick nối thẳng bằng IP máy trong khi người dùng tưởng đã gắn proxy (khớp browser.account_proxy bên Python).
    if (!p && String(raw || "").trim()) {
      throw new Error(`Proxy của nick "${name}" sai định dạng ("${raw}") — sửa proxy của nick. KHÔNG nối thẳng bằng IP máy.`);
    }
    return p;
  }
  const env = readEnvLocal(repoRoot);
  // 20s chứ không phải 10s mặc định: một lần lấy IP lạnh của proxyxoay tốn tới 5s (hỏi IP máy để tự khai
  // whitelist) + 12s (gọi get.php) → 10s là abort oan rồi báo "gateway không trả lời".
  const r = await adminFetch(gatewayBase(env).base, env.DOLA_ADMIN_KEY || "",
    `/api/admin/accounts/${encodeURIComponent(name)}/proxy/current`, { timeoutMs: 20000 });
  if (!r.ok) {
    // Có status = server SỐNG và đã trả lý do thật (409 đang bận / 502 nhà bán lỗi) — đừng bảo đi bật server.
    throw new Error(r.status ? `Chưa lấy được IP proxy của nick: ${r.error}`
      : `Chưa lấy được IP proxy của nick (${r.error || "gateway không trả lời"}). `
        + "Bật server ở thanh trên rồi đăng nhập lại.");
  }
  return fromServerDict(r.proxy);   // null = nick không gán proxy nào → nối thẳng
}

// Gắn proxy của nick vào session. NÉM khi proxy xoay không lấy được IP — nơi gọi PHẢI huỷ mở cửa sổ.
const applyProxy = async (ses, repoRoot, name, send) => applyParsed(ses, await resolveProxy(repoRoot, name), send);

const PROXY_FORMATS = "host:port · user:pass@host:port · host:port:user:pass · socks5://host:port";

// Thử một chuỗi proxy (chưa cần lưu) có vào được dola.com không. Session tạm trong RAM, không dính cookie nick.
async function testProxy(raw, url = "https://www.dola.com/") {
  const s = String(raw || "").trim();
  if (isRotating(s)) return { ok: false, error: "Proxy xoay theo key/link — bấm \"Đổi IP\" ở thẻ làn để lấy/kiểm IP (server gọi nhà bán)." };
  if (s && !parseProxy(s)) return { ok: false, error: `Proxy sai định dạng. Chấp nhận: ${PROXY_FORMATS}` };
  const { session } = require("electron");
  const ses = session.fromPartition(`proxy-test-${Date.now()}`);
  const p = await applyProxyRaw(ses, s, null);
  const via = p ? `${p.scheme}://${p.host}:${p.port}` : "nối thẳng (không proxy)";
  const r = await probeUrl(ses, url);
  if (r.ok) return { ok: true, via, status: r.status };
  return { ok: false, via, error: `Không vào được dola.com qua ${via}: ${r.error}` };
}

// Tải file (video) QUA SESSION của nick → đi đúng proxy của session (kể cả proxy có mật khẩu, qua sự kiện "login").
// Trước đây fetch-generate tải bằng https/http của Node: module đó KHÔNG theo proxy của session → file video luôn tải
// bằng IP máy dù cửa sổ nick đi proxy. request tiêm được để test bằng node thường.
function downloadViaSession(url, dest, ses, request) {
  const req0 = request || ((opts) => require("electron").net.request(opts));
  return new Promise((resolve, reject) => {
    let req;
    try { req = req0({ method: "GET", url, session: ses, redirect: "follow" }); } catch (e) { return reject(e); }
    req.on("response", (res) => {
      if (res.statusCode !== 200) return reject(new Error("HTTP " + res.statusCode));
      const file = fs.createWriteStream(dest);
      res.on("data", (chunk) => file.write(chunk));
      res.on("end", () => file.end(() => resolve(dest)));
      res.on("error", (e) => { file.destroy(); fs.unlink(dest, () => {}); reject(e); });
    });
    req.on("error", reject);
    req.end();
  });
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
    ? `Đang dùng proxy <b>${escapeHtml(proxyInfo.scheme + "://" + proxyInfo.host + ":" + proxyInfo.port)}</b> — proxy tắt, sai cổng hoặc sai mật khẩu. Sửa hoặc xoá trống ở <b>Cài đặt → Proxy chung</b> (hoặc nút ⚙ của nick), rồi Tắt/Bật server.`
    : `Đang nối thẳng, không qua proxy. Vào tab <b>Cài đặt → Proxy chung</b> (exit node Nhật hoặc Hàn), hoặc đặt proxy riêng cho nick (nút ⚙), rồi thử lại.`;
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
async function preflightDola(ses, repoRoot, name, url = "https://www.dola.com/", proxyInfo = null) {
  const r = await probeUrl(ses, url);
  if (r.ok) return { ok: true };
  const raw = accountProxy(repoRoot, name);
  // proxyInfo = proxy VỪA GẮN thật. Với proxy xoay, parseProxy(raw) luôn null (raw là link get.php) nên
  // không nhận proxyInfo thì câu lỗi nói sai là "đang nối thẳng" → người dùng đi sửa nhầm chỗ.
  const p = proxyInfo || parseProxy(raw);
  const where = p
    ? `Proxy đang dùng: ${p.scheme}://${p.host}:${p.port} — ` + (isRotating(raw)
        ? 'IP proxy xoay vừa lấy không vào được: IP MÁY NÀY có thể chưa whitelist trên trang nhà bán, hoặc IP vừa chết. Bấm "Đổi IP" ở Kho proxy rồi thử lại.'
        : "proxy tắt, sai cổng hoặc sai mật khẩu. Sửa hoặc xoá trống ở Cài đặt → Proxy chung (hoặc nút ⚙ của nick), rồi Tắt/Bật server.")
    : `Đang nối thẳng — mạng của bạn đang chặn dola.com. Vào Cài đặt → Proxy chung (exit node Nhật/Hàn) rồi thử lại.`;
  return { ok: false, error: `Không vào được dola.com (${r.error}). ${where}` };
}

module.exports = {
  readEnvLocal, parseProxy, isKeyLink, isRotating, normalizeProxyInput, globalProxy, accountProxy, applyProxyRaw, applyProxy, hookProxyAuth,
  describeNetError, showLoadError, attachLoadErrorHandler, probeUrl, preflightDola, testProxy,
  applyParsed, resolveProxy, fromServerDict, downloadViaSession,   // main.js gọi 2 hàm đầu; test-proxy.cjs kiểm đường proxy xoay
  DEFAULT_PROXY, PROXY_FORMATS,
};
