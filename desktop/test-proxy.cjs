// Self-check cho proxy.cjs. Chạy: node desktop/test-proxy.cjs
const assert = require("assert");
const { parseProxy, describeNetError, accountProxy } = require("./proxy.cjs");

// Mọi định dạng browser.py hỗ trợ đều phải ra host/port/scheme đúng.
const cases = [
  ["http://1.2.3.4:8080",            { scheme: "http",   host: "1.2.3.4", port: "8080", user: "", pass: "" }],
  ["https://p.jp:443",               { scheme: "https",  host: "p.jp",    port: "443",  user: "", pass: "" }],
  ["socks5://1.2.3.4:1080",          { scheme: "socks5", host: "1.2.3.4", port: "1080", user: "", pass: "" }],
  ["socks5h://1.2.3.4:1080",         { scheme: "socks5", host: "1.2.3.4", port: "1080", user: "", pass: "" }],
  ["1.2.3.4:8080",                   { scheme: "http",   host: "1.2.3.4", port: "8080", user: "", pass: "" }],
  ["us3r:p@ss@1.2.3.4:8080",         { scheme: "http",   host: "1.2.3.4", port: "8080", user: "us3r", pass: "p@ss" }],
  ["1.2.3.4:8080:us3r:p4ss",         { scheme: "http",   host: "1.2.3.4", port: "8080", user: "us3r", pass: "p4ss" }],
  ["http://us3r:p4ss@jp.proxy:3128", { scheme: "http",   host: "jp.proxy", port: "3128", user: "us3r", pass: "p4ss" }],
];
for (const [raw, want] of cases) {
  const got = parseProxy(raw);
  assert.ok(got, `parseProxy("${raw}") trả null`);
  for (const k of Object.keys(want)) {
    assert.strictEqual(got[k], want[k], `parseProxy("${raw}").${k}: ${got[k]} != ${want[k]}`);
  }
  assert.strictEqual(got.rules, `${want.scheme}://${want.host}:${want.port}`, `rules sai cho ${raw}`);
}

// Rác thì phải trả null, không được dựng proxy hỏng.
for (const bad of ["", "   ", "khong-phai-proxy", "1.2.3.4", "1.2.3.4:abc", "http://:8080", null, undefined]) {
  assert.strictEqual(parseProxy(bad), null, `parseProxy(${JSON.stringify(bad)}) phải là null`);
}

// Mã lỗi Chromium -> câu tiếng Việt (đây là thứ thay cho trang trắng).
assert.ok(/quá hạn|chặn/i.test(describeNetError(-118, "")), "thiếu gợi ý cho -118");
assert.ok(/proxy/i.test(describeNetError(-130, "")), "thiếu gợi ý cho -130");
assert.ok(/tên miền/i.test(describeNetError(-105, "")), "thiếu gợi ý cho -105");
assert.ok(/mã -999/.test(describeNetError(-999, "")), "mã lạ phải hiện nguyên mã");

// Không có proxy.txt và không có .env.local -> chuỗi rỗng, không ném lỗi.
assert.strictEqual(typeof accountProxy("/khong/ton/tai", "acc1"), "string");

console.log("ALL PASS (" + (cases.length + 8) + " assertions)");
