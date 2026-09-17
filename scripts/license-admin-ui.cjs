// Trang quản lý key của NGƯỜI BÁN: tạo key, xem sổ key đã cấp, khoá / gỡ khoá, đẩy danh sách khoá lên GitHub.
//   node scripts/license-admin-ui.cjs            → mở http://127.0.0.1:8790/?t=<mã truy cập>
// Chỉ nghe 127.0.0.1 + mã truy cập ngẫu nhiên mỗi lần chạy + kiểm Host → trang web khác trên máy không gọi vào được.
// Dữ liệu: scripts/license-store.cjs (khoá bí mật ~/.dola-license, sổ issued.json, license/revoked.json).
const http = require("http");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");
const S = require("./license-store.cjs");
const { verifyRevocations, machineCode, REVOKE_URL } = require("../desktop/license.cjs");

const PORT = Number(process.env.DOLA_LICENSE_UI_PORT || 8790);
const TOKEN = process.env.DOLA_LICENSE_UI_TOKEN || crypto.randomBytes(18).toString("hex");
const ROOT = path.join(__dirname, "..");
const P = S.storePaths();
const REVOKED_REL = path.relative(ROOT, P.revoked);
const MAX_BODY = 64 * 1024;

const git = (args) => execFileSync("git", args, { cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();

function gitState() {
  try {
    return {
      branch: git(["rev-parse", "--abbrev-ref", "HEAD"]),
      dirty: git(["status", "--porcelain", "--", REVOKED_REL]) !== "",
      ahead: Number(git(["rev-list", "--count", "mine/main..HEAD"])) || 0,
    };
  } catch (e) {
    return { error: String(e.stderr || e.message || e).slice(0, 200) };
  }
}

// Commit CHỈ license/revoked.json rồi đẩy. Nhánh còn commit khác chưa đẩy → dừng (không đẩy code dở lên GitHub thay anh).
function publish() {
  const g = gitState();
  if (g.error) throw new Error("git lỗi: " + g.error);
  if (g.branch !== "main") throw new Error(`Đang ở nhánh "${g.branch}" — chuyển về main rồi đẩy.`);
  if (g.dirty) {
    git(["add", "--", REVOKED_REL]);
    git(["commit", "-m", "chore(license): cập nhật danh sách khoá", "--", REVOKED_REL]);
  }
  const ahead = Number(git(["rev-list", "--count", "mine/main..HEAD"])) || 0;
  if (ahead === 0) return "Không có gì mới để đẩy — GitHub đã là bản này.";
  if (ahead > 1) throw new Error(`Đã commit danh sách khoá, nhưng nhánh main còn ${ahead - 1} commit khác chưa đẩy — kiểm tra rồi tự chạy: git push mine HEAD:main`);
  git(["push", "mine", "HEAD:main"]);
  return "Đã đẩy lên GitHub — app khách nhận trong tối đa ~1 giờ (GitHub cập nhật thêm vài phút).";
}

async function remoteState() {
  const local = S.loadRevocations(P);
  try {
    const r = await fetch(`${REVOKE_URL}?t=${Date.now()}`, { signal: AbortSignal.timeout(8000), cache: "no-store" });
    if (r.status === 404) return { status: "missing", localT: local.t };
    if (!r.ok) return { status: "error", error: "HTTP " + r.status, localT: local.t };
    const list = verifyRevocations(await r.json(), fs.readFileSync(P.pub, "utf8"));
    if (!list) return { status: "invalid", localT: local.t };
    return { status: list.t >= local.t ? "current" : "old", remoteT: list.t, localT: local.t,
             remoteCount: list.keys.length + list.machines.length };
  } catch (e) {
    return { status: "error", error: String(e.message || e).slice(0, 120), localT: local.t };
  }
}

function state() {
  if (!S.hasKeys(P)) return { hasKeys: false, dir: P.dir };
  const rev = S.loadRevocations(P);
  return {
    hasKeys: true, dir: P.dir, thisMachine: machineCode(),
    issued: S.issuedWithStatus(P).sort((a, b) => b.createdAt - a.createdAt),
    revoked: { t: rev.t, keys: rev.keys, machines: rev.machines.map((m) => ({ ...m, m: S.fmtMachine(m.m) })) },
    git: gitState(),
  };
}

const ACTIONS = {
  "GET /api/state": () => state(),
  "GET /api/remote": () => remoteState(),
  "POST /api/make": (b) => {
    const days = Number(b.days);
    if (!(days > 0 && days <= 3650)) throw new Error("Số ngày phải từ 1 đến 3650.");
    return S.makeKey(P, { machine: b.machine, days, note: String(b.note || "") });
  },
  "POST /api/revoke": (b) => S.saveRevocations(P, S.applyRevoke(S.loadRevocations(P), S.parseTarget(b.target), String(b.reason || "").slice(0, 80))),
  "POST /api/unrevoke": (b) => {
    const u = S.applyUnrevoke(S.loadRevocations(P), S.parseTarget(b.target));
    if (!u.changed) throw new Error("Không có trong danh sách khoá.");
    return S.saveRevocations(P, u.list);
  },
  "POST /api/publish": () => ({ message: publish() }),
};

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0; const chunks = [];
    req.on("data", (c) => { size += c.length; if (size > MAX_BODY) { reject(new Error("Dữ liệu quá lớn.")); req.destroy(); } else chunks.push(c); });
    req.on("end", () => { try { resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {}); } catch (_) { reject(new Error("JSON sai.")); } });
    req.on("error", reject);
  });
}

const send = (res, code, body, type = "application/json; charset=utf-8") => {
  res.writeHead(code, { "Content-Type": type, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY" });
  res.end(type.startsWith("application/json") ? JSON.stringify(body) : body);
};

const server = http.createServer(async (req, res) => {
  const host = req.headers.host || "";
  if (host !== `127.0.0.1:${PORT}` && host !== `localhost:${PORT}`) return send(res, 403, { error: "Host không hợp lệ." });
  const url = new URL(req.url, `http://${host}`);
  if (req.method === "GET" && url.pathname === "/") return send(res, 200, PAGE, "text/html; charset=utf-8");
  const fn = ACTIONS[`${req.method} ${url.pathname}`];
  if (!fn) return send(res, 404, { error: "Không có." });
  if (req.headers["x-admin-token"] !== TOKEN) return send(res, 401, { error: "Sai mã truy cập — mở đúng link in ra ở cửa sổ lệnh." });
  try {
    const body = req.method === "POST" ? await readBody(req) : {};
    send(res, 200, await fn(body));
  } catch (e) {
    send(res, 400, { error: String((e && e.message) || e).slice(0, 300) });
  }
});

const PAGE = `<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quản lý key Dola Studio</title>
<style>
:root{color-scheme:dark;--bg:#0f0f1a;--card:#171728;--line:#2c2c48;--fg:#e6e6f0;--mut:#a0a0c0;--pri:#7c6cf6;--ok:#7ee2a8;--warn:#f5c26b;--err:#ff8a8a}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--fg)}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px 60px}h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:0 0 10px}
.mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-top:16px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}input,select{background:#10101d;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit;max-width:100%}
.mono{font-family:ui-monospace,Menlo,monospace}button{background:var(--pri);color:#fff;border:0;border-radius:8px;padding:8px 12px;font:inherit;font-weight:600;cursor:pointer}
button.ghost{background:#232340}button.danger{background:#5a2330;color:#ffd0d6}button:disabled{opacity:.55;cursor:default}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:7px 6px;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut)}.tag{display:inline-block;border-radius:999px;padding:1px 8px;font-size:12px}
.active{background:#173a2b;color:var(--ok)}.expired{background:#2b2b40;color:var(--mut)}.revoked{background:#40202a;color:var(--err)}
.box{background:#10101d;border:1px dashed var(--line);border-radius:8px;padding:10px;word-break:break-all}.warn{color:var(--warn)}.err{color:var(--err)}.ok{color:var(--ok)}
#toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:#232340;border:1px solid var(--line);padding:10px 14px;border-radius:8px;display:none;max-width:90vw}
.scroll{overflow-x:auto}td.mono,td .tag{white-space:nowrap}
</style></head><body><div class="wrap">
<h1>Quản lý key Dola Studio</h1>
<div class="mut">Chỉ chạy trên máy anh. Khoá bí mật: <span class="mono" id="dir"></span></div>
<div id="nokeys" class="card err" style="display:none">Chưa có cặp khoá. Chạy <span class="mono">node scripts/license-admin.cjs init</span> rồi mở lại trang.</div>
<div id="main" style="display:none">
<div class="card"><h2>Tạo key mới</h2>
  <div class="row">
    <input id="machine" class="mono" placeholder="Mã máy khách: XXXX-XXXX-XXXX-XXXX" size="30">
    <select id="days"><option value="1">1 ngày</option><option value="7">7 ngày</option><option value="30" selected>30 ngày</option><option value="90">90 ngày</option><option value="180">180 ngày</option><option value="365">365 ngày</option></select>
    <input id="note" placeholder="Ghi chú (tên khách, gói…)" size="24" maxlength="60">
    <button id="make">Tạo key</button>
    <button class="ghost" id="self" title="Điền mã máy đang chạy trang này">Dùng mã máy này</button>
  </div>
  <div id="made" style="display:none;margin-top:12px"><div class="mut" id="madeInfo"></div><div class="box mono" id="madeKey"></div>
    <div class="row" style="margin-top:8px"><button class="ghost" id="copyMade">Copy key</button></div></div>
</div>
<div class="card"><h2>Key đã cấp <span class="mut" id="count"></span></h2>
  <div class="row" style="margin-bottom:8px"><input id="q" placeholder="Tìm theo ghi chú / mã máy" size="30">
    <select id="filter"><option value="">Tất cả</option><option value="active">Còn hạn</option><option value="expired">Hết hạn</option><option value="revoked">Bị khoá</option></select></div>
  <div class="scroll"><table><thead><tr><th>Ghi chú</th><th>Mã máy</th><th>Tạo</th><th>Hết hạn</th><th>Trạng thái</th><th></th></tr></thead><tbody id="issued"></tbody></table></div>
  <div class="mut" id="noIssued" style="display:none">Chưa có key nào trong sổ (key tạo trước khi có sổ không hiện ở đây — khoá bằng mã máy bên dưới).</div>
</div>
<div class="card"><h2>Khoá theo mã máy hoặc key</h2>
  <div class="row"><input id="target" class="mono" placeholder="Mã máy XXXX-XXXX-XXXX-XXXX hoặc key DOLA1.…" size="44">
    <input id="reason" placeholder="Lý do (hiện cho khách — KHÔNG ghi tên/SĐT)" size="34" maxlength="80"><button class="danger" id="revoke">Khoá</button></div>
  <h2 style="margin-top:16px">Đang bị khoá</h2>
  <div class="scroll"><table><thead><tr><th>Loại</th><th>Mã</th><th>Lý do</th><th></th></tr></thead><tbody id="revoked"></tbody></table></div>
  <div class="mut" id="noRevoked" style="display:none">Không có gì bị khoá.</div>
</div>
<div class="card"><h2>Đẩy danh sách khoá lên GitHub</h2>
  <div id="pubState" class="mut">…</div><div id="remote" class="mut" style="margin-top:4px">Đang kiểm tra bản trên GitHub…</div>
  <div class="row" style="margin-top:10px"><button id="publish">Đẩy lên GitHub</button><button class="ghost" id="recheck">Kiểm tra lại</button></div>
  <div class="mut" style="margin-top:8px">Khoá / gỡ khoá chỉ có hiệu lực SAU KHI đẩy lên. File danh sách nằm trong repo công khai.</div>
</div>
</div>
<div id="toast"></div>
<script>
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const dt = (ms) => ms ? new Date(ms).toLocaleString("vi-VN") : "—";
const fmtM = (m) => (String(m).replace(/-/g, "").match(/.{4}/g) || []).join("-");
const bare = (m) => String(m || "").replace(/-/g, "").toUpperCase();
let S = null;
function toast(msg, cls) { const t = $("toast"); t.textContent = msg; t.className = cls || ""; t.style.display = "block"; clearTimeout(t._h); t._h = setTimeout(() => t.style.display = "none", 4500); }
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { "x-admin-token": TOKEN, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}
const STATUS = { active: "Còn hạn", expired: "Hết hạn", revoked: "Bị khoá" };
function renderIssued() {
  const q = $("q").value.trim().toLowerCase(), f = $("filter").value;
  const rows = S.issued.filter((r) => (!f || r.status === f) && (!q || (r.note + " " + fmtM(r.machine)).toLowerCase().includes(q)));
  $("count").textContent = "(" + S.issued.length + ")";
  $("noIssued").style.display = S.issued.length ? "none" : "block";
  $("issued").innerHTML = rows.map((r) => '<tr><td>' + esc(r.note || "—") + '</td><td class="mono">' + fmtM(r.machine) + '</td><td>' + dt(r.createdAt) +
    '</td><td>' + dt(r.expiresAt) + '</td><td><span class="tag ' + r.status + '" title="' + esc(r.revokedReason) + '">' + STATUS[r.status] +
    (r.revokedBy ? " (theo " + (r.revokedBy === "key" ? "key" : "máy") + ")" : "") + '</span></td><td><div class="row">' +
    '<button class="ghost" data-copy="' + esc(r.keyId) + '">Copy key</button>' +
    (r.status === "revoked"
      ? '<button class="ghost" data-unrevoke="' + esc(r.revokedBy === "key" ? "key:" + r.keyId : r.machine) + '">Gỡ khoá</button>'
      : '<button class="danger" data-revoke-key="' + esc(r.keyId) + '">Khoá key</button><button class="danger" data-revoke-machine="' + esc(r.machine) + '">Khoá máy</button>') +
    '</div></td></tr>').join("");
}
function render() {
  $("dir").textContent = S.dir;
  if (!S.hasKeys) { $("nokeys").style.display = "block"; $("main").style.display = "none"; return; }
  $("main").style.display = "block";
  renderIssued();
  const rv = [...S.revoked.machines.map((m) => ({ kind: "Máy", code: m.m, reason: m.reason, target: m.m })),
              ...S.revoked.keys.map((k) => ({ kind: "Key", code: k.id, reason: k.reason, target: "key:" + k.id }))];
  $("noRevoked").style.display = rv.length ? "none" : "block";
  $("revoked").innerHTML = rv.map((x) => '<tr><td>' + x.kind + '</td><td class="mono">' + esc(x.code) + (bare(x.code) === bare(S.thisMachine) ? ' <span class="warn">(máy này!)</span>' : "") +
    '</td><td>' + esc(x.reason) + '</td><td><button class="ghost" data-unrevoke="' + esc(x.target) + '">Gỡ khoá</button></td></tr>').join("");
  const g = S.git || {};
  $("pubState").innerHTML = g.error ? '<span class="err">git: ' + esc(g.error) + '</span>'
    : (g.dirty ? '<span class="warn">Danh sách khoá đã đổi, CHƯA đẩy lên (ký lúc ' + dt(S.revoked.t) + ').</span>' : 'Danh sách khoá trên máy đã commit (ký lúc ' + dt(S.revoked.t) + ').')
      + (g.ahead ? ' · <span class="warn">' + g.ahead + ' commit chưa đẩy</span>' : "") + ' · nhánh ' + esc(g.branch);
}
async function load() { try { S = await api("GET", "/api/state"); render(); } catch (e) { toast(e.message, "err"); } }
async function checkRemote() {
  $("remote").textContent = "Đang kiểm tra bản trên GitHub…";
  try {
    const r = await api("GET", "/api/remote");
    const m = { missing: '<span class="err">Chưa có file trên GitHub — app khách sẽ KHOÁ sau 3 ngày. Bấm "Đẩy lên GitHub".</span>',
      invalid: '<span class="err">File trên GitHub sai chữ ký.</span>',
      old: '<span class="warn">GitHub đang là bản CŨ (ký ' + dt(r.remoteT) + ') — thay đổi chưa có hiệu lực.</span>',
      current: '<span class="ok">GitHub đã có bản mới nhất (ký ' + dt(r.remoteT) + ', ' + r.remoteCount + ' mục khoá) — đang có hiệu lực.</span>',
      error: '<span class="warn">Không kiểm tra được GitHub: ' + esc(r.error) + '</span>' };
    $("remote").innerHTML = m[r.status] || esc(r.status);
  } catch (e) { $("remote").textContent = e.message; }
}
async function act(fn, okMsg) { try { const r = await fn(); if (okMsg) toast(typeof okMsg === "function" ? okMsg(r) : okMsg, "ok"); await load(); checkRemote(); return r; } catch (e) { toast(e.message, "err"); } }
const isSelf = (t) => bare(t) === bare(S.thisMachine);
$("self").onclick = () => { $("machine").value = S.thisMachine; };
$("make").onclick = async () => {
  const r = await act(() => api("POST", "/api/make", { machine: $("machine").value, days: $("days").value, note: $("note").value }), "Đã tạo key");
  if (!r) return;
  $("made").style.display = "block";
  $("madeInfo").textContent = "Máy " + fmtM(r.machine) + " · " + r.days + " ngày · hết hạn " + dt(r.expiresAt) + (r.note ? " · " + r.note : "");
  $("madeKey").textContent = r.key;
};
$("copyMade").onclick = () => navigator.clipboard.writeText($("madeKey").textContent).then(() => toast("Đã copy key", "ok"));
$("q").oninput = renderIssued; $("filter").onchange = renderIssued;
document.body.addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.copy) { const r = S.issued.find((x) => x.keyId === b.dataset.copy); if (r) navigator.clipboard.writeText(r.key).then(() => toast("Đã copy key", "ok")); return; }
  const tgt = b.dataset.revokeKey ? "key:" + b.dataset.revokeKey : b.dataset.revokeMachine;
  if (tgt) {
    const byMachine = !!b.dataset.revokeMachine;
    if (byMachine && isSelf(tgt) && !confirm("Đây là MÁY ĐANG CHẠY trang này (máy của anh). Vẫn khoá?")) return;
    const reason = prompt((byMachine ? "Khoá cả MÁY (mọi key của máy này, kể cả key cấp sau)" : "Khoá KEY này") + "\\nLý do hiện cho khách (không ghi tên/SĐT):", "vi phạm điều khoản");
    if (reason === null) return;
    await act(() => api("POST", "/api/revoke", { target: tgt, reason }), "Đã khoá — nhớ bấm Đẩy lên GitHub");
    return;
  }
  if (b.dataset.unrevoke) { if (!confirm("Gỡ khoá " + b.dataset.unrevoke + "?")) return; await act(() => api("POST", "/api/unrevoke", { target: b.dataset.unrevoke }), "Đã gỡ khoá — nhớ bấm Đẩy lên GitHub"); }
});
$("revoke").onclick = async () => {
  const target = $("target").value.trim(); if (!target) return toast("Nhập mã máy hoặc key", "err");
  if (isSelf(target) && !confirm("Đây là MÁY ĐANG CHẠY trang này (máy của anh). Vẫn khoá?")) return;
  const r = await act(() => api("POST", "/api/revoke", { target, reason: $("reason").value.trim() }), "Đã khoá — nhớ bấm Đẩy lên GitHub");
  if (r) { $("target").value = ""; $("reason").value = ""; }
};
$("publish").onclick = async () => {
  if (!confirm("Commit riêng file danh sách khoá và đẩy lên GitHub (repo công khai)?")) return;
  $("publish").disabled = true;
  try { await act(() => api("POST", "/api/publish"), (r) => r.message); } finally { $("publish").disabled = false; }
};
$("recheck").onclick = checkRemote;
load().then(checkRemote);
</script></body></html>`;

server.listen(PORT, "127.0.0.1", () => {
  const link = `http://127.0.0.1:${PORT}/?t=${TOKEN}`;
  console.log(`Trang quản lý key: ${link}\n(Chỉ máy này mở được. Ctrl+C để tắt.)`);
  if (!process.env.DOLA_LICENSE_UI_NO_OPEN) {
    const opener = process.platform === "darwin" ? "open" : process.platform === "win32" ? "explorer" : "xdg-open";
    try { spawn(opener, [link], { stdio: "ignore", detached: true }).unref(); } catch (_) { /* người dùng tự mở link */ }
  }
});
