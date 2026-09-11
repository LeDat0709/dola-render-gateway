// Self-check cho remote.cjs. Chạy: node desktop/test-remote.cjs
const assert = require("assert");
const http = require("http");
const { normalizeRemoteBase, gatewayBase, adminFetch, getAccountProxy, setAccountProxy, getRemoteConfig } = require("./remote.cjs");

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

// Máy chủ giả mô phỏng đúng 3 endpoint server.py mà app dùng ở chế độ từ xa cho proxy.
(async () => {
  const store = {};
  const srv = http.createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    if (req.headers["x-admin-key"] !== "adm") { res.writeHead(401); return res.end(JSON.stringify({ detail: "bad admin key" })); }
    let body = ""; req.on("data", (d) => (body += d)); req.on("end", () => {
      const m = req.url.match(/^\/api\/admin\/accounts\/([^/]+)\/proxy$/);
      if (m && req.method === "POST") { store[m[1]] = JSON.parse(body).proxy || ""; return res.end(JSON.stringify({ ok: true, proxy: store[m[1]] || "(global)" })); }
      if (m) { if (!(m[1] in store)) { res.writeHead(404); return res.end(JSON.stringify({ detail: "account not found" })); } return res.end(JSON.stringify({ proxy: store[m[1]] })); }
      if (req.url === "/api/admin/config") return res.end(JSON.stringify({ proxy: "http://u:•••@jp:8080", max_concurrency: 2 }));
      res.writeHead(404); res.end("{}");
    });
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  const base = `127.0.0.1:${srv.address().port}`;          // thiếu scheme: adminFetch phải tự chuẩn hoá
  let r = await setAccountProxy(base, "adm", "n1", "http://u:p@1.2.3.4:8080");
  assert.strictEqual(r.ok, true, "đặt proxy qua máy chủ");
  r = await getAccountProxy(base, "adm", "n1");
  assert.strictEqual(r.proxy, "http://u:p@1.2.3.4:8080", "đọc lại đúng proxy vừa đặt");
  r = await setAccountProxy(base, "adm", "n1", "");
  assert.strictEqual(r.ok, true); r = await getAccountProxy(base, "adm", "n1"); assert.strictEqual(r.proxy, "", "trống = về proxy chung");
  r = await getAccountProxy(base, "adm", "n2");
  assert.strictEqual(r.ok, false); assert.strictEqual(r.status, 404, "nick lạ → 404 kèm detail");
  assert.strictEqual(r.error, "account not found");
  r = await getAccountProxy(base, "sai", "n1");
  assert.strictEqual(r.status, 401, "admin key sai → 401");
  r = await getRemoteConfig(base, "adm");
  assert.strictEqual(r.proxy, "http://u:•••@jp:8080", "proxy chung của máy chủ (đã che mật khẩu)");
  r = await adminFetch("khong phai url", "adm", "/x");
  assert.strictEqual(r.ok, false, "địa chỉ rác không được ném exception");
  srv.close();
  console.log("ALL PASS (22 assertions)");
})().catch((e) => { console.error(e); process.exit(1); });
