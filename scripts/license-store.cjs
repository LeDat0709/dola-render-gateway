// Kho key của NGƯỜI BÁN, dùng chung cho dòng lệnh (license-admin.cjs) và trang quản lý (license-admin-ui.cjs).
//   ~/.dola-license/private.pem · public.pem  — cặp khoá ký (KHÔNG vào repo)
//   ~/.dola-license/issued.json               — sổ key đã cấp (có cả key đầy đủ để copy lại; KHÔNG vào repo)
//   <repo>/license/revoked.json               — danh sách khoá đã ký, đẩy lên GitHub để app khách đọc
// Test: node scripts/test-license-store.cjs
const fs = require("fs");
const os = require("os");
const path = require("path");
const { signKey, verifyKey, keyId, signRevocations, verifyRevocations } = require("../desktop/license.cjs");

function storePaths(opts = {}) {
  const dir = opts.dir || process.env.DOLA_LICENSE_DIR || path.join(os.homedir(), ".dola-license");
  return {
    dir,
    priv: path.join(dir, "private.pem"),
    pub: path.join(dir, "public.pem"),
    issued: path.join(dir, "issued.json"),
    revoked: opts.revoked || process.env.DOLA_REVOKED_FILE || path.join(__dirname, "..", "license", "revoked.json"),
  };
}

const hasKeys = (p) => fs.existsSync(p.priv) && fs.existsSync(p.pub);
const normMachine = (s) => String(s || "").toUpperCase().replace(/[^0-9A-F]/g, "");
const fmtMachine = (m) => (normMachine(m).match(/.{4}/g) || []).join("-");

function loadRevocations(p) {
  if (!fs.existsSync(p.revoked)) return { t: 0, keys: [], machines: [] };
  const list = verifyRevocations(JSON.parse(fs.readFileSync(p.revoked, "utf8")), fs.readFileSync(p.pub, "utf8"));
  if (!list) throw new Error(`${p.revoked} sai chữ ký (không khớp khoá hiện tại) — không sửa tiếp.`);
  return list;
}

function saveRevocations(p, { keys, machines }) {
  if (!hasKeys(p)) throw new Error(`Chưa có ${p.priv} — chạy "init" trước.`);
  const doc = signRevocations(fs.readFileSync(p.priv, "utf8"), { keys, machines });
  fs.mkdirSync(path.dirname(p.revoked), { recursive: true });
  fs.writeFileSync(p.revoked, JSON.stringify(doc, null, 1) + "\n");
  return verifyRevocations(doc, fs.readFileSync(p.pub, "utf8"));
}

// KEY đầy đủ (DOLA1.…) → theo key; 16 ký tự hex (có/không gạch) → theo mã máy; "key:<16 hex>" → theo mã key trong sổ.
function parseTarget(arg) {
  const s = String(arg || "").trim();
  if (s.startsWith("DOLA1.")) return { kind: "keys", id: keyId(s) };
  if (/^key:[0-9a-f]{16}$/i.test(s)) return { kind: "keys", id: s.slice(4).toLowerCase() };
  const m = normMachine(s);
  if (m.length !== 16) throw new Error("Cần KEY đầy đủ (DOLA1.…) hoặc mã máy XXXX-XXXX-XXXX-XXXX");
  return { kind: "machines", m };
}

const sameTarget = (t) => (x) => (t.kind === "keys" ? x.id === t.id : x.m === t.m);

function applyRevoke(list, t, reason) {
  const kept = list[t.kind].filter((x) => !sameTarget(t)(x));
  const why = reason || "vi phạm điều khoản";
  const entry = t.kind === "keys" ? { id: t.id, reason: why } : { m: t.m, reason: why };
  return { keys: list.keys, machines: list.machines, [t.kind]: [...kept, entry] };
}

function applyUnrevoke(list, t) {
  const kept = list[t.kind].filter((x) => !sameTarget(t)(x));
  return { list: { keys: list.keys, machines: list.machines, [t.kind]: kept }, changed: kept.length !== list[t.kind].length };
}

function loadIssued(p) {
  try { const a = JSON.parse(fs.readFileSync(p.issued, "utf8")); return Array.isArray(a) ? a : []; } catch (_) { return []; }
}

function makeKey(p, { machine, days, note = "", now = Date.now() }) {
  if (!hasKeys(p)) throw new Error(`Chưa có ${p.priv} — chạy "init" trước.`);
  const key = signKey(fs.readFileSync(p.priv, "utf8"), { machine, days: Number(days), note, now });
  const check = verifyKey(key, machine, now, fs.readFileSync(p.pub, "utf8"));
  if (!check.ok) throw new Error("Tự kiểm key thất bại: " + check.reason);
  const record = { keyId: keyId(key), machine: normMachine(machine), days: Number(days), note: String(note).slice(0, 60),
    createdAt: now, expiresAt: check.expiresAt, key };
  fs.mkdirSync(p.dir, { recursive: true, mode: 0o700 });
  fs.writeFileSync(p.issued, JSON.stringify([...loadIssued(p), record], null, 1), { mode: 0o600 });
  return record;
}

// Trạng thái từng key đã cấp: bị khoá (theo key hoặc theo máy) > hết hạn > còn hạn.
function issuedWithStatus(p, now = Date.now()) {
  const rev = fs.existsSync(p.revoked) && hasKeys(p) ? loadRevocations(p) : { keys: [], machines: [] };
  return loadIssued(p).map((r) => {
    const byKey = rev.keys.find((x) => x.id === r.keyId);
    const byMachine = rev.machines.find((x) => x.m === r.machine);
    const status = byKey || byMachine ? "revoked" : r.expiresAt <= now ? "expired" : "active";
    return { ...r, status, revokedReason: (byKey || byMachine || {}).reason || "", revokedBy: byKey ? "key" : byMachine ? "machine" : "" };
  });
}

module.exports = { storePaths, hasKeys, normMachine, fmtMachine, loadRevocations, saveRevocations, parseTarget,
  applyRevoke, applyUnrevoke, loadIssued, makeKey, issuedWithStatus };
