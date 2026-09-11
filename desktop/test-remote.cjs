// Self-check cho remote.cjs. Chạy: node desktop/test-remote.cjs
const assert = require("assert");
const { normalizeRemoteBase, gatewayBase } = require("./remote.cjs");

assert.strictEqual(normalizeRemoteBase(""), "");
assert.strictEqual(normalizeRemoteBase("   "), "");
assert.strictEqual(normalizeRemoteBase("1.2.3.4:8000"), "http://1.2.3.4:8000", "thiếu scheme phải tự thêm http://");
assert.strictEqual(normalizeRemoteBase("https://vps.example.com/"), "https://vps.example.com", "bỏ / cuối");
assert.strictEqual(normalizeRemoteBase("http://vps:8000/dola/"), "http://vps:8000/dola", "giữ đường dẫn con");
assert.strictEqual(normalizeRemoteBase("khong phai url"), "", "rác phải là rỗng");

assert.deepStrictEqual(gatewayBase({}), { base: "http://127.0.0.1:8000", remote: false });
assert.deepStrictEqual(gatewayBase({ DOLA_PORT: "8010" }), { base: "http://127.0.0.1:8010", remote: false });
assert.deepStrictEqual(gatewayBase({ DOLA_REMOTE_BASE: "45.1.1.1:8000", DOLA_PORT: "8010" }),
  { base: "http://45.1.1.1:8000", remote: true }, "có DOLA_REMOTE_BASE thì đi VPS, bỏ qua cổng cục bộ");
assert.deepStrictEqual(gatewayBase({ DOLA_REMOTE_BASE: "" }), { base: "http://127.0.0.1:8000", remote: false },
  "DOLA_REMOTE_BASE= trống là cục bộ");

console.log("ALL PASS (10 assertions)");
