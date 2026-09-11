// Đích gateway: Python trên máy này, hay máy chủ từ xa (VPS Nhật/Hàn chạy server.py).
// Giao diện chỉ dùng cfg.base nên đổi base là mọi fetch đi sang VPS — không cần sửa từng màn.

// Chuẩn hoá địa chỉ VPS: thiếu scheme thì thêm http://, bỏ dấu / cuối; rác → "".
function normalizeRemoteBase(raw) {
  let s = String(raw || "").trim();
  if (!s) return "";
  if (!/^https?:\/\//i.test(s)) s = "http://" + s;
  try {
    const u = new URL(s);
    if (!u.hostname) return "";
    const sub = u.pathname.replace(/\/+$/, "");
    return u.origin + (sub === "" ? "" : sub);
  } catch (_) { return ""; }
}

// .env.local → base thật. DOLA_REMOTE_BASE có giá trị = từ xa; thiếu hoặc trống = cục bộ.
function gatewayBase(env, defaultPort = "8000") {
  const remote = normalizeRemoteBase(env.DOLA_REMOTE_BASE);
  if (remote) return { base: remote, remote: true };
  return { base: `http://127.0.0.1:${env.DOLA_PORT || defaultPort}`, remote: false };
}

// Thử địa chỉ + khoá (chưa cần lưu). /health không cần khoá; /v1/videos/x trả 404 = API key đúng,
// 401 = sai; /api/admin/accounts trả 401 = admin key sai. Thuần fetch → test được bằng node thường.
async function testRemote(base, apiKey, adminKey, timeoutMs = 10000) {
  const b = normalizeRemoteBase(base);
  if (!b) return { ok: false, error: "Địa chỉ máy chủ sai (vd: http://45.77.1.2:8000)" };
  const get = async (p, headers) => {
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), timeoutMs);
    try { return await fetch(b + p, { headers, cache: "no-store", signal: ctl.signal }); } finally { clearTimeout(t); }
  };
  try {
    const h = await get("/health");
    if (!h.ok) return { ok: false, error: `Máy chủ trả HTTP ${h.status} ở /health — có phải gateway không?` };
    const hj = await h.json().catch(() => ({}));
    const v = await get("/v1/videos/kiem-tra-khoa", { Authorization: "Bearer " + String(apiKey || "").trim() });
    if (v.status === 401) return { ok: false, error: "Nối được máy chủ nhưng API key sai" };
    const a = await get("/api/admin/accounts", { "x-admin-key": String(adminKey || "").trim() });
    if (a.status === 401 || a.status === 403) return { ok: false, error: "Nối được máy chủ nhưng Admin key sai" };
    const nicks = a.ok ? (((await a.json().catch(() => ({}))).accounts) || []).length : 0;
    return { ok: true, base: b, nicks, available: !!hj.available };
  } catch (e) {
    return { ok: false, error: `Không nối được ${b}: ${String((e && e.message) || e).slice(0, 120)}` };
  }
}

// Gọi API admin trên máy chủ từ xa (proxy theo nick, cấu hình đang chạy). Trả { ok, ...json } hoặc
// { ok:false, status?, error }. Thuần fetch → test-remote.cjs dựng http server giả để kiểm.
async function adminFetch(base, adminKey, path, { method = "GET", body, timeoutMs = 10000 } = {}) {
  const b = normalizeRemoteBase(base);
  if (!b) return { ok: false, error: "Địa chỉ máy chủ sai" };
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(b + path, {
      method, cache: "no-store", signal: ctl.signal,
      headers: { "Content-Type": "application/json", "x-admin-key": String(adminKey || "").trim() },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, status: r.status, error: j.detail || `HTTP ${r.status}` };
    return { ok: true, ...j };
  } catch (e) {
    return { ok: false, error: `Không nối được ${b}: ${String((e && e.message) || e).slice(0, 120)}` };
  } finally { clearTimeout(t); }
}
const getAccountProxy = (base, adminKey, name) =>
  adminFetch(base, adminKey, `/api/admin/accounts/${encodeURIComponent(name)}/proxy`);
const setAccountProxy = (base, adminKey, name, proxy) =>
  adminFetch(base, adminKey, `/api/admin/accounts/${encodeURIComponent(name)}/proxy`, { method: "POST", body: { proxy: proxy || "" } });
const getRemoteConfig = (base, adminKey) => adminFetch(base, adminKey, "/api/admin/config");

module.exports = { normalizeRemoteBase, gatewayBase, testRemote, adminFetch, getAccountProxy, setAccountProxy, getRemoteConfig };
