// Key bản quyền THEO MÁY, kiểm OFFLINE bằng chữ ký Ed25519 (crypto có sẵn của Node, không thêm thư viện).
// Key = "DOLA1." + base64url(JSON {m: mã máy, e: hết hạn (ms), n: ghi chú}) + "." + base64url(chữ ký).
// App chỉ chứa KHOÁ CÔNG KHAI → không tự tạo được key. Khoá bí mật nằm ở máy người bán (scripts/license-admin.cjs).
// ponytail: kiểm ở app Electron — ai sửa được app.asar thì gỡ được; muốn chặt hơn cần máy chủ cấp phép / mã hoá lõi.
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const os = require("os");
const { execFileSync } = require("child_process");

// Khoá công khai do `node scripts/license-admin.cjs init` in ra (khoá bí mật: ~/.dola-license/private.pem máy người bán).
const PUBLIC_KEY_PEM = `-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA92H0xvEkqm1O0X8XGD9xF/w62Qm7oLs3TNHIHeg0V5Q=
-----END PUBLIC KEY-----`;

const PREFIX = "DOLA1";
const CLOCK_SKEW_MS = 24 * 3600 * 1000;   // đồng hồ lùi quá 1 ngày so với lần mở trước = chỉnh giờ để kéo dài key

// KHOÁ TỪ XA: danh sách key/máy bị khoá, KÝ bằng cùng khoá bí mật, đặt trên GitHub (license/revoked.json).
// Không tải được quá REVOKE_GRACE_MS kể từ lần kiểm thành công gần nhất → khoá (chặn GitHub không né được).
const REVOKE_URL = "https://raw.githubusercontent.com/LeDat0709/dola-render-gateway/main/license/revoked.json";
const REVOKE_PREFIX = "DOLAREV1";
const REVOKE_GRACE_MS = 3 * 86400000;
const REVOKE_FETCH_TIMEOUT_MS = 8000;

function b64u(buf) { return Buffer.from(buf).toString("base64").replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_"); }
function unb64u(s) { return Buffer.from(String(s).replace(/-/g, "+").replace(/_/g, "/"), "base64"); }

function rawMachineId() {
  try {
    if (process.platform === "darwin") {
      const out = execFileSync("ioreg", ["-rd1", "-c", "IOPlatformExpertDevice"], { encoding: "utf8" });
      const m = out.match(/"IOPlatformUUID"\s*=\s*"([^"]+)"/);
      if (m) return m[1];
    } else if (process.platform === "win32") {
      const out = execFileSync("reg", ["query", "HKLM\\SOFTWARE\\Microsoft\\Cryptography", "/v", "MachineGuid"],
        { encoding: "utf8", windowsHide: true });
      const m = out.match(/MachineGuid\s+REG_SZ\s+(\S+)/i);
      if (m) return m[1];
    } else {
      for (const f of ["/etc/machine-id", "/var/lib/dbus/machine-id"]) {
        if (fs.existsSync(f)) return fs.readFileSync(f, "utf8").trim();
      }
    }
  } catch (_) { /* rơi xuống dự phòng */ }
  return `${os.hostname()}|${(os.cpus()[0] || {}).model || ""}`;
}

// "ABCD-EF01-2345-6789": băm (không lộ UUID thật), đủ ngắn để khách đọc/gửi qua tin nhắn.
function machineCode(raw = rawMachineId()) {
  const h = crypto.createHash("sha256").update("dola-studio|" + raw).digest("hex").slice(0, 16).toUpperCase();
  return h.match(/.{4}/g).join("-");
}

function normCode(c) { return String(c || "").toUpperCase().replace(/[^0-9A-F]/g, ""); }

function signKey(privatePem, { machine, days, note = "", now = Date.now() }) {
  if (normCode(machine).length !== 16) throw new Error("Mã máy phải có 16 ký tự dạng XXXX-XXXX-XXXX-XXXX");
  if (!(days > 0)) throw new Error("Số ngày phải > 0");
  const payload = b64u(JSON.stringify({ m: normCode(machine), e: now + days * 86400000, n: String(note).slice(0, 60) }));
  const sig = crypto.sign(null, Buffer.from(`${PREFIX}.${payload}`), privatePem);
  return `${PREFIX}.${payload}.${b64u(sig)}`;
}

function verifyKey(key, machine, now = Date.now(), publicPem = PUBLIC_KEY_PEM) {
  const parts = String(key || "").trim().split(".");
  if (parts.length !== 3 || parts[0] !== PREFIX) return { ok: false, reason: "Key sai định dạng." };
  let valid = false;
  try {
    valid = crypto.verify(null, Buffer.from(`${parts[0]}.${parts[1]}`), publicPem, unb64u(parts[2]));
  } catch (_) { valid = false; }
  if (!valid) return { ok: false, reason: "Key không hợp lệ (sai chữ ký)." };
  let p;
  try { p = JSON.parse(unb64u(parts[1]).toString("utf8")); } catch (_) { return { ok: false, reason: "Key hỏng." }; }
  if (normCode(p.m) !== normCode(machine)) return { ok: false, reason: "Key này cấp cho máy khác." };
  if (!(Number(p.e) > now)) return { ok: false, reason: "Key đã hết hạn.", expiresAt: p.e, note: p.n };
  return { ok: true, expiresAt: p.e, note: p.n || "" };
}

function keyId(key) { return crypto.createHash("sha256").update(String(key || "").trim()).digest("hex").slice(0, 16); }

// Danh sách khoá: {list: b64u(JSON {t: lúc ký, keys: [{id, reason}], machines: [{m, reason}]}), sig}
function signRevocations(privatePem, { keys = [], machines = [], now = Date.now() }) {
  const list = b64u(JSON.stringify({ t: now, keys, machines: machines.map((x) => ({ ...x, m: normCode(x.m) })) }));
  return { list, sig: b64u(crypto.sign(null, Buffer.from(`${REVOKE_PREFIX}.${list}`), privatePem)) };
}

function verifyRevocations(doc, publicPem = PUBLIC_KEY_PEM) {
  try {
    if (!doc || !crypto.verify(null, Buffer.from(`${REVOKE_PREFIX}.${doc.list}`), publicPem, unb64u(doc.sig))) return null;
    const p = JSON.parse(unb64u(doc.list).toString("utf8"));
    return { t: Number(p.t) || 0, keys: p.keys || [], machines: p.machines || [] };
  } catch (_) { return null; }
}

function revokedReason(revList, key, machine) {
  if (!revList) return "";
  const hit = revList.keys.find((x) => x.id === keyId(key))
    || revList.machines.find((x) => normCode(x.m) === normCode(machine));
  return hit ? (hit.reason || "không ghi lý do") : "";
}

// ---- Lưu key + chống chỉnh lùi đồng hồ (thư mục dữ liệu của app) ----
function readJson(f) { try { return JSON.parse(fs.readFileSync(f, "utf8")); } catch (_) { return {}; } }
function writeJson(f, obj) { fs.writeFileSync(f, JSON.stringify(obj)); }

function checkLicense(dir, { now = Date.now(), machine = machineCode(), publicPem = PUBLIC_KEY_PEM } = {}) {
  const f = path.join(dir, "license.json");
  const saved = readJson(f);
  const base = { machineCode: machine };
  if (!saved.key) return { ...base, ok: false, reason: "Chưa nhập key." };
  if (saved.lastSeen && now < saved.lastSeen - CLOCK_SKEW_MS) {
    return { ...base, ok: false, reason: "Giờ hệ thống bị chỉnh lùi — sửa lại ngày giờ máy." };
  }
  const r = verifyKey(saved.key, machine, now, publicPem);
  if (!r.ok) return { ...base, ...r };
  const revoked = revokedReason(saved.revList, saved.key, machine);
  if (revoked) return { ...base, ok: false, reason: `Key đã bị khoá: ${revoked}` };
  if (now - (saved.revCheckedAt || 0) > REVOKE_GRACE_MS) {
    return { ...base, ok: false, reason: "Không kiểm tra được key quá 3 ngày — kết nối mạng (không chặn GitHub) rồi mở lại app." };
  }
  try { writeJson(f, { ...saved, lastSeen: Math.max(now, saved.lastSeen || 0) }); } catch (_) { /* chỉ đọc được thì thôi */ }
  return { ...base, ...r };
}

// Tải + kiểm chữ ký danh sách khoá, lưu vào license.json. true = cập nhật được. Lỗi mạng / chữ ký sai → false
// (giữ danh sách cũ; quá 3 ngày thì checkLicense khoá). Danh sách CŨ HƠN bản đã có (t nhỏ hơn) bị bỏ qua:
// chặn chiêu trả lại bản danh sách từ trước khi key bị khoá.
async function refreshRevocations(dir, { now = Date.now(), fetchFn = globalThis.fetch, url = REVOKE_URL, publicPem = PUBLIC_KEY_PEM } = {}) {
  const f = path.join(dir, "license.json");
  let doc;
  try {
    const r = await fetchFn(`${url}?t=${now}`, { signal: AbortSignal.timeout(REVOKE_FETCH_TIMEOUT_MS), cache: "no-store" });
    if (!r.ok) return false;
    doc = await r.json();
  } catch (_) { return false; }
  const list = verifyRevocations(doc, publicPem);
  const saved = readJson(f);
  if (!list || (saved.revList && list.t < saved.revList.t)) return false;
  fs.mkdirSync(dir, { recursive: true });
  writeJson(f, { ...saved, revList: list, revCheckedAt: now });
  return true;
}

function activateLicense(dir, key, opts = {}) {
  const machine = opts.machine || machineCode();
  const now = opts.now || Date.now();
  const r = verifyKey(key, machine, now, opts.publicPem || PUBLIC_KEY_PEM);
  if (!r.ok) return { machineCode: machine, ...r };
  fs.mkdirSync(dir, { recursive: true });
  const f = path.join(dir, "license.json");
  const prev = readJson(f);
  const revoked = revokedReason(prev.revList, key, machine);
  if (revoked) return { machineCode: machine, ok: false, reason: `Key đã bị khoá: ${revoked}` };
  // Chưa từng tải được danh sách khoá → hạn 3 ngày tính từ lúc kích hoạt
  writeJson(f, { ...prev, key: String(key).trim(), lastSeen: Math.max(now, prev.lastSeen || 0), revCheckedAt: prev.revCheckedAt || now });
  return { machineCode: machine, ...r };
}

module.exports = { machineCode, rawMachineId, signKey, verifyKey, checkLicense, activateLicense, keyId, signRevocations,
  verifyRevocations, revokedReason, refreshRevocations, PUBLIC_KEY_PEM, REVOKE_URL, REVOKE_GRACE_MS };
