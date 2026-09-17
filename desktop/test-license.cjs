// Self-check cho license.cjs. Chạy: node desktop/test-license.cjs
const assert = require("assert");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const L = require("./license.cjs");

const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
const priv = privateKey.export({ type: "pkcs8", format: "pem" });
const pub = publicKey.export({ type: "spki", format: "pem" });
const other = crypto.generateKeyPairSync("ed25519").privateKey.export({ type: "pkcs8", format: "pem" });

const code = L.machineCode("uuid-may-a");
assert.match(code, /^[0-9A-F]{4}(-[0-9A-F]{4}){3}$/, "mã máy phải dạng XXXX-XXXX-XXXX-XXXX");
assert.strictEqual(code, L.machineCode("uuid-may-a"), "mã máy phải ổn định");
assert.notStrictEqual(code, L.machineCode("uuid-may-b"), "máy khác phải ra mã khác");
assert.match(L.machineCode(), /^[0-9A-F]{4}(-[0-9A-F]{4}){3}$/, "đọc được mã máy thật");

const now = Date.UTC(2026, 8, 17);
const key = L.signKey(priv, { machine: code, days: 30, note: "khach A", now });
assert.ok(L.verifyKey(key, code, now + 86400000, pub).ok, "key đúng máy, còn hạn phải qua");
assert.ok(L.verifyKey(key, code.toLowerCase().replace(/-/g, ""), now, pub).ok, "gõ thường / thiếu gạch vẫn nhận");
assert.match(L.verifyKey(key, L.machineCode("uuid-may-b"), now, pub).reason, /máy khác/);
assert.match(L.verifyKey(key, code, now + 31 * 86400000, pub).reason, /hết hạn/);
assert.match(L.verifyKey(L.signKey(other, { machine: code, days: 30, now }), code, now, pub).reason, /chữ ký/,
  "key ký bằng khoá lạ phải bị từ chối");
// Sửa payload (kéo dài hạn) mà giữ chữ ký cũ → hỏng chữ ký
const [p0, , s] = key.split(".");
const forged = Buffer.from(JSON.stringify({ m: code.replace(/-/g, ""), e: now + 9999 * 86400000, n: "" })).toString("base64url");
assert.match(L.verifyKey(`${p0}.${forged}.${s}`, code, now, pub).reason, /chữ ký/, "sửa hạn trong key phải bị phát hiện");
for (const bad of ["", "abc", "DOLA1.x", null]) assert.strictEqual(L.verifyKey(bad, code, now, pub).ok, false);

// Lưu key + chống chỉnh lùi giờ
const DAY = 86400000;
const opt = (t) => ({ now: t, machine: code, publicPem: pub });
const listDoc = (t, extra = {}) => L.signRevocations(priv, { now: t, ...extra });
const fakeFetch = (doc) => async () => ({ ok: true, json: async () => doc });
const refresh = (d, t, doc) => L.refreshRevocations(d, { now: t, publicPem: pub, fetchFn: fakeFetch(doc) });

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dola-lic-"));
  assert.match(L.checkLicense(dir, opt(now)).reason, /Chưa nhập/);
  assert.strictEqual(L.activateLicense(dir, "rác", opt(now)).ok, false);
  assert.ok(!fs.existsSync(path.join(dir, "license.json")), "key sai không được lưu");
  assert.ok(L.activateLicense(dir, key, opt(now)).ok);
  assert.ok(L.checkLicense(dir, opt(now + 2 * DAY)).ok, "chưa tải được danh sách: vẫn dùng được trong 3 ngày");
  assert.match(L.checkLicense(dir, opt(now + 4 * DAY)).reason, /quá 3 ngày/, "quá 3 ngày không kiểm được phải khoá");
  assert.ok(await refresh(dir, now + 5 * DAY, listDoc(now + 5 * DAY)), "danh sách hợp lệ phải được nhận");
  assert.ok(L.checkLicense(dir, opt(now + 5 * DAY)).ok, "kiểm được lại → mở khoá");
  assert.match(L.checkLicense(dir, opt(now + DAY)).reason, /chỉnh lùi/, "lùi giờ về trước lần mở gần nhất > 1 ngày phải khoá");
  assert.match(L.checkLicense(dir, opt(now + 40 * DAY)).reason, /hết hạn/);

  // KHOÁ TỪ XA theo key
  const t1 = now + 6 * DAY;
  assert.ok(await refresh(dir, t1, listDoc(t1, { keys: [{ id: L.keyId(key), reason: "chia sẻ key" }] })));
  assert.match(L.checkLicense(dir, opt(t1)).reason, /bị khoá: chia sẻ key/);
  // Trả lại danh sách CŨ (trước khi khoá) không mở khoá được
  assert.strictEqual(await refresh(dir, t1 + 1000, listDoc(now + 5 * DAY)), false, "danh sách cũ hơn phải bị bỏ qua");
  assert.match(L.checkLicense(dir, opt(t1 + 1000)).reason, /bị khoá/);
  // Danh sách giả (ký bằng khoá lạ) không được nhận
  const fake = L.signRevocations(other, { now: t1 + 2 * DAY });
  assert.strictEqual(await refresh(dir, t1 + 2000, fake), false, "danh sách ký khoá lạ phải bị từ chối");
  // Mất mạng: không đổi gì
  assert.strictEqual(await L.refreshRevocations(dir, { now: t1, publicPem: pub, fetchFn: async () => { throw new Error("offline"); } }), false);
  // Gỡ khoá (danh sách mới hơn, rỗng) → dùng lại được
  const t2 = t1 + DAY;
  assert.ok(await refresh(dir, t2, listDoc(t2)));
  assert.ok(L.checkLicense(dir, opt(t2)).ok, "gỡ khoá phải mở lại");

  // KHOÁ TỪ XA theo máy: cả key MỚI cho máy đó cũng không kích hoạt được
  const t3 = t2 + DAY;
  assert.ok(await refresh(dir, t3, listDoc(t3, { machines: [{ m: code.toLowerCase(), reason: "máy gian lận" }] })));
  assert.match(L.checkLicense(dir, opt(t3)).reason, /máy gian lận/);
  const key2 = L.signKey(priv, { machine: code, days: 30, now: t3 });
  assert.match(L.activateLicense(dir, key2, opt(t3)).reason, /máy gian lận/, "máy bị khoá thì key mới cũng không kích hoạt được");

  console.log("ALL PASS");
})().catch((e) => { console.error(e); process.exit(1); });
