// Self-check cho proxy.cjs. Chạy: node desktop/test-proxy.cjs
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { parseProxy, describeNetError, accountProxy, globalProxy, DEFAULT_PROXY,
        applyProxyRaw, resolveProxy, fromServerDict } = require("./proxy.cjs");

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

// Không có proxy.txt và không có .env.local -> nối thẳng như config.py, không ném lỗi.
// KHÔNG được rơi về 127.0.0.1:7890 nữa: máy khách không chạy Clash từng chết mọi cửa sổ vì mặc định đó.
assert.strictEqual(DEFAULT_PROXY, "", "mặc định phải là nối thẳng");
assert.strictEqual(accountProxy("/khong/ton/tai", "acc1"), "", "thiếu DOLA_PROXY phải nối thẳng như Python");

// .env.local quyết định: DOLA_PROXY= (trống) là nối thẳng; có giá trị thì nick không có proxy riêng dùng nó.
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "dola-proxy-"));
fs.writeFileSync(path.join(tmp, ".env.local"), "DOLA_PROXY=\n");
assert.strictEqual(globalProxy(tmp), "", "DOLA_PROXY= trống phải là nối thẳng");
fs.writeFileSync(path.join(tmp, ".env.local"), "DOLA_PROXY=socks5://1.2.3.4:1080\n");
assert.strictEqual(accountProxy(tmp, "acc1"), "socks5://1.2.3.4:1080", "nick không có proxy riêng phải dùng proxy chung");
fs.mkdirSync(path.join(tmp, "accounts", "acc1"), { recursive: true });
fs.writeFileSync(path.join(tmp, "accounts", "acc1", "proxy.txt"), "9.9.9.9:8080\n");
assert.strictEqual(accountProxy(tmp, "acc1"), "9.9.9.9:8080", "proxy riêng của nick phải thắng proxy chung");

(async () => {
  // ── Proxy XOAY cho cửa sổ Electron ──────────────────────────────────────────
  // Trước đây applyProxyRaw thấy proxy xoay là setProxy({mode:"direct"}) rồi thôi: nick ĐĂNG NHẬP bằng IP máy
  // thật nhưng RENDER bằng IP proxy — đúng thứ hệ thống chống gian lận soi kỹ nhất. Giờ phải đi hỏi gateway.

  // 1) Dict của Python -> dạng parseProxy, mật khẩu GIỮ NGUYÊN (Chromium cần mật khẩu thô ở sự kiện "login";
  //    đi qua account_proxy_url() thì nó đã bị percent-encode -> sai mật khẩu với ký tự lạ).
  const fsd = fromServerDict({ server: "http://5.6.7.8:1234", username: "u", password: "p:@ss" });
  assert.strictEqual(fsd.rules, "http://5.6.7.8:1234", "rules sai");
  assert.strictEqual(fsd.pass, "p:@ss", "mật khẩu bị đổi (encode?) — Chromium sẽ xác thực proxy thất bại");
  assert.strictEqual(fromServerDict(null), null, "dict rỗng phải ra null");
  assert.strictEqual(fromServerDict({ server: "rác" }), null, "server sai định dạng phải ra null");

  // 2) Proxy xoay KHÔNG được lặng lẽ nối thẳng nữa (ai khôi phục nhánh direct là đỏ ngay).
  const fakeSes = { setProxy: async () => {} };
  let nem = false;
  try { await applyProxyRaw(fakeSes, "https://proxyxoay.shop/api/get.php?key=TEST", null); }
  catch (_) { nem = true; }
  assert.ok(nem, "proxy xoay phải NÉM, không được nối thẳng lặng lẽ");

  // 3) resolveProxy hỏi gateway và gắn đúng IP nhận về.
  const tmp2 = fs.mkdtempSync(path.join(os.tmpdir(), "dola-proxy2-"));
  fs.writeFileSync(path.join(tmp2, ".env.local"), "DOLA_PROXY=\nDOLA_PORT=8000\nDOLA_ADMIN_KEY=k\n");
  fs.mkdirSync(path.join(tmp2, "accounts", "n1"), { recursive: true });
  fs.writeFileSync(path.join(tmp2, "accounts", "n1", "proxy.txt"), "https://proxyxoay.shop/api/get.php?key=TEST\n");
  const fetchGoc = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({ ok: true, status: 200,
      json: async () => ({ proxy: { server: "http://5.6.7.8:1234", username: "u", password: "p" } }) });
    const p1 = await resolveProxy(tmp2, "n1");
    assert.strictEqual(p1.host, "5.6.7.8", "phải dùng IP gateway trả về");

    // 4) Gateway không nối được (chưa bật server) -> chỉ đúng chỗ cần sửa.
    globalThis.fetch = async () => { throw new Error("ECONNREFUSED"); };
    let e1 = null;
    try { await resolveProxy(tmp2, "n1"); } catch (e) { e1 = e; }
    assert.ok(e1 && /Bật server/.test(e1.message), "server chưa chạy thì phải bảo bật server: " + (e1 && e1.message));

    // 5) Gateway SỐNG và trả 409 (proxy đang có job chạy) -> KHÔNG được bảo đi bật server.
    globalThis.fetch = async () => ({ ok: false, status: 409,
      json: async () => ({ detail: "Proxy của nick này đang có 2 job chạy" }) });
    let e2 = null;
    try { await resolveProxy(tmp2, "n1"); } catch (e) { e2 = e; }
    assert.ok(e2 && !/Bật server/.test(e2.message), "server sống mà lại bảo đi bật server: " + (e2 && e2.message));
    assert.ok(/job chạy/.test(e2.message), "phải giữ lý do thật của server: " + e2.message);
  } finally {
    globalThis.fetch = fetchGoc;
  }

  console.log("ALL PASS (" + (cases.length + 12 + 9) + " assertions)");
})().catch((e) => { console.error(e); process.exit(1); });
