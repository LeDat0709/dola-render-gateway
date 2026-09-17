// Self-check kho key người bán. Chạy: node scripts/test-license-store.cjs (dùng thư mục tạm, không đụng khoá thật)
const assert = require("assert");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const S = require("./license-store.cjs");
const { verifyKey } = require("../desktop/license.cjs");

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lic-store-"));
const p = S.storePaths({ dir, revoked: path.join(dir, "repo", "revoked.json") });
assert.strictEqual(S.hasKeys(p), false);
assert.throws(() => S.makeKey(p, { machine: "ABCD-1234-EF56-7890", days: 30 }), /init/);

const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
fs.writeFileSync(p.priv, privateKey.export({ type: "pkcs8", format: "pem" }));
fs.writeFileSync(p.pub, publicKey.export({ type: "spki", format: "pem" }));
const pub = fs.readFileSync(p.pub, "utf8");

const now = Date.UTC(2026, 8, 17);
const r1 = S.makeKey(p, { machine: "abcd1234ef567890", days: 30, note: "khach A", now });
S.makeKey(p, { machine: "1111-2222-3333-4444", days: 7, note: "khach B", now: now - 10 * 86400000 });
assert.ok(verifyKey(r1.key, "ABCD-1234-EF56-7890", now, pub).ok, "key tạo ra phải hợp lệ với khoá công khai");
assert.strictEqual(S.loadIssued(p).length, 2, "sổ phải ghi đủ key đã cấp");
assert.strictEqual(S.fmtMachine(r1.machine), "ABCD-1234-EF56-7890");
assert.deepStrictEqual(S.issuedWithStatus(p, now).map((x) => x.status), ["active", "expired"]);

// Đích khoá: key đầy đủ, "key:<id>" từ sổ, mã máy
assert.deepStrictEqual(S.parseTarget("key:" + r1.keyId), { kind: "keys", id: r1.keyId });
assert.deepStrictEqual(S.parseTarget(r1.key), { kind: "keys", id: r1.keyId });
assert.deepStrictEqual(S.parseTarget("1111-2222-3333-4444"), { kind: "machines", m: "1111222233334444" });
assert.throws(() => S.parseTarget("abc"), /mã máy/);

let list = S.saveRevocations(p, S.applyRevoke(S.loadRevocations(p), S.parseTarget("key:" + r1.keyId), "chia sẻ key"));
let st = S.issuedWithStatus(p, now)[0];
assert.deepStrictEqual([st.status, st.revokedReason, st.revokedBy], ["revoked", "chia sẻ key", "key"]);
list = S.saveRevocations(p, S.applyRevoke(list, S.parseTarget(r1.key), "lý do mới"));
assert.deepStrictEqual(list.keys.map((k) => k.reason), ["lý do mới"], "khoá lại cùng đích không nhân đôi");

const un = S.applyUnrevoke(list, S.parseTarget("key:" + r1.keyId));
assert.ok(un.changed);
list = S.saveRevocations(p, un.list);
assert.strictEqual(S.applyUnrevoke(list, S.parseTarget("key:" + r1.keyId)).changed, false);
assert.strictEqual(S.issuedWithStatus(p, now)[0].status, "active");

S.saveRevocations(p, S.applyRevoke(list, S.parseTarget("ABCD1234EF567890"), "máy gian lận"));
assert.strictEqual(S.issuedWithStatus(p, now)[0].revokedBy, "machine");

// Danh sách ký bằng khoá khác → từ chối sửa tiếp (không ghi đè nhầm)
fs.writeFileSync(p.pub, crypto.generateKeyPairSync("ed25519").publicKey.export({ type: "spki", format: "pem" }));
assert.throws(() => S.loadRevocations(p), /sai chữ ký/);

console.log("ALL PASS (kho key người bán)");
