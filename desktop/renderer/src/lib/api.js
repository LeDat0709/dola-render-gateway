// Cầu nối gateway HTTP + IPC (window.api). Giữ nguyên logic bản cũ.
import { normalizeBundle, runPool } from "./bundle.js";
export const api = window.api || {};
export let cfg = { base: "http://127.0.0.1:8000", apiKey: "" };
export async function loadConfig() {
  try { cfg = (await api.getConfig?.()) || cfg; } catch { /* ignore */ }
  return cfg;
}
export const authHeaders = () => (cfg.apiKey ? { Authorization: "Bearer " + cfg.apiKey } : {});

export async function health() {
  try {
    const r = await fetch(cfg.base + "/health", { cache: "no-store" });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}
export async function report() {
  try {
    const r = await fetch(cfg.base + "/api/report", { headers: authHeaders(), cache: "no-store" });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

// Gateway bị TẮT/BẬT lại lúc đăng nhập / nạp cookie (gateway.cjs pause()) hoặc khởi động lại: POST tạo job
// treo tới khi Chromium tự bỏ (~vài phút) → thẻ đứng ở "chờ server nhận job" hàng phút. Đặt timeout mỗi lần
// gửi + tự gửi lại vài lần để vượt qua lúc gateway đang lên. An toàn trừ lượt: tạo job chỉ là INSERT SQLite
// cục bộ (mili-giây) — chưa gửi Dola, chưa trừ lượt; quá SUBMIT_TIMEOUT_MS nghĩa là app CHƯA phục vụ (chưa
// tạo row) nên gửi lại không tạo job trùng. Việc trừ lượt xảy ra ở _run_task sau này, không ở bước tạo.
const SUBMIT_TIMEOUT_MS = 10000;   // 1 lần POST chờ tối đa 10s (tạo job cục bộ luôn xong dưới 1s)
const SUBMIT_RETRIES = 6;          // ~ vài chục giây; gateway bật lại thường < 15s
// Mọi fetch tới gateway đều có HẠN + nhận `signal` ngoài (nút Dừng / chạy lại nick) để hủy ngay. fetch treo vô hạn
// (15/09: nick bị coi "đang chạy dở" mãi, lệnh mới bị nuốt, thẻ đứng "chờ server nhận job" hàng phút) là gốc rễ
// của hầu hết cảnh "treo" phía client. Hết hạn → AbortError (fmtError → "Chưa nối được server").
export function fetchT(url, opts = {}, ms = 20000, signal = null) {
  const ac = new AbortController();
  const relay = () => ac.abort();
  if (signal) { if (signal.aborted) ac.abort(); else signal.addEventListener("abort", relay, { once: true }); }
  const t = setTimeout(() => ac.abort(), ms);
  return fetch(url, { ...opts, signal: ac.signal }).finally(() => { clearTimeout(t); signal?.removeEventListener("abort", relay); });
}
export const isAbort = (e) => e?.name === "AbortError";
export async function submitJob(prompt, body, signal = null) {
  let lastErr;
  for (let i = 0; i < SUBMIT_RETRIES; i++) {
    try {
      const r = await fetchT(cfg.base + "/v1/videos/generations", {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ ...body, prompt }),
      }, SUBMIT_TIMEOUT_MS, signal);
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || "HTTP " + r.status);   // server ĐÃ trả lời (hết điểm, nick lỗi…) → ném luôn, không gửi lại
      return j;   // cả prompt/status/charged — client nhận ra khi server trả job CŨ (trùng khóa) thay vì tạo mới
    } catch (e) {
      if (signal?.aborted) throw e;   // người dùng Dừng / chạy lại nick → thôi, không gửi lại
      // Chỉ gửi lại khi treo/không nối được (gateway đang bật lại). Lỗi có phản hồi HTTP = server sống nhưng từ chối → ném.
      const down = isAbort(e) || e?.name === "TypeError" || /failed to fetch|load failed|networkerror|econnrefused/i.test(e?.message || "");
      if (!down) throw e;
      lastErr = e;
      if (i < SUBMIT_RETRIES - 1) await new Promise((res) => setTimeout(res, 2000));   // chờ gateway lên rồi gửi lại
    }
  }
  throw new Error("Chưa nối được server sau nhiều lần thử (gateway đang bật lại?) — chờ vài giây rồi chạy lại. " + (lastErr?.message || ""));
}
// Ném Error kèm .status khi server trả lỗi (404 = job không còn) — trước đây trả JSON lỗi về như
// job bình thường, status undefined → dòng Studio quay vòng "đang chạy" vô tận.
export async function pollJob(id, signal = null) {
  const r = await fetchT(cfg.base + "/v1/videos/" + id, { headers: authHeaders(), cache: "no-store" }, 15000, signal);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(j.detail || "HTTP " + r.status); e.status = r.status; throw e; }
  return j;
}

// Điểm/video — khớp pool (_cost_for/_default_cost). `table` = health.credit_cost (giá server đã học); server cũ chưa
// gửi thì dùng giá đo 13/09: Seedance 2.5 → 30s = 2, 10/15s = 4 (ĐẮT hơn 30s); Seedance 2.0 = 1.
export const creditCost = (dur, model, table) => {
  const d = parseInt(dur, 10) || 10;
  return table?.[model]?.[String(d)] || (/2\.5/.test(String(model)) ? (d >= 30 ? 2 : 4) : 1);
};
// Gợi ý khi nick còn `rem` điểm mà không đủ: lựa chọn rẻ hơn thật sự vừa túi.
export const cheaperHint = (rem, table) => {
  if (rem >= creditCost(30, "seedance-2.5", table)) return `để Seedance 2.5 · 30s (${creditCost(30, "seedance-2.5", table)} điểm) hoặc đổi nick`;
  if (rem >= creditCost(10, "seedance-2.0", table)) return `chọn Seedance 2.0 (${creditCost(10, "seedance-2.0", table)} điểm/video) hoặc đổi nick`;
  return "đổi nick khác hoặc chờ reset 22h (0h giờ Nhật)";
};
export const firstLine = (v) => String(v || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean)[0] || "";
export const fnameFromUrl = (u) => String(u || "").split("?")[0].split("#")[0].split("/").pop();
export const sttFromUrl = (u) => (String(fnameFromUrl(u)).match(/^(\d+)_/) || [])[1];

// Chuẩn hoá lỗi: icon + nguyên nhân + gợi ý + phân loại (giống bản cũ).
export function fmtError(raw) {
  const r = String(raw || ""); const L = r.toLowerCase();
  const A = (icon, short, hint) => ({ icon, short, hint, kind: "account" });
  const T = (icon, short, hint) => ({ icon, short, hint, kind: "tool" });
  // ĐẦU TIÊN: lệnh có thể đã tới Dola (đã trừ lượt) — chữ bên trong có thể chứa "network error"/"failed to fetch".
  if (/KHÔNG gửi lại để tránh trừ lượt/i.test(r)) return A("📨", "Đã gửi, chưa xác nhận", "ĐỪNG chạy lại ngay — xem dola.com, chưa có video mới chạy lại (tránh trừ lượt 2 lần).");
  if (/failed to fetch|networkerror|load failed|ECONNREFUSED|ERR_CONNECTION_REFUSED/i.test(r)) return T("🔌", "Chưa nối được server", "Server đang bật lại hoặc tắt — chờ 2–3s rồi chạy lại.");
  if (/ERR_INTERNET|ERR_NETWORK|ERR_CONNECTION|ERR_PROXY|ERR_TIMED_OUT|ERR_NAME_NOT|mất mạng|net::/i.test(r)) return T("🌐", "Mất mạng tạm thời", "Kiểm tra internet/proxy rồi chạy lại.");
  if (/đang bận|đang tạo video khác/i.test(r)) return A("⏳", "Nick đang bận", "Chờ video hiện tại xong rồi chạy tiếp.");
  if (/đã thử \d+ nick|đều lỗi — dừng/i.test(r)) { const cuoi = (r.match(/Lỗi cuối:\s*([\s\S]+)$/i) || [])[1] || ""; return T("🔁", "Đã xoay nhiều nick, đều lỗi", (cuoi.trim() || "Xem chi tiết ở Log.").slice(0, 70)); }
  // Proxy/treo đứng TRƯỚC "no available accounts"/"tạm ngưng"/"quota": lý do nhà bán tự do có thể chứa các chữ đó.
  if (/không lấy được ip|proxy xoay riêng/i.test(r)) { const why = (r.match(/không lấy được IP \([^)]*\)(?::\s*([^—]+?))?\s*—/i) || [])[1] || ""; return A("🛰", "Proxy xoay chưa lấy được IP", (why.trim() ? why.trim().slice(0, 60) : "Whitelist IP máy trên trang bán proxy, hoặc kiểm tra hạn/key.")); }
  if (/treo quá \d+s trước khi gửi/i.test(r)) return A("🧊", "Nick treo lúc mở", "Chưa tốn lượt, đã tự xoay nick. Máy yếu: giảm 'Nick gửi cùng lúc'.");
  if (/không tồn tại/i.test(r)) return T("👻", "Nick không còn trong pool", "Bảng đang cũ (đã tự làm mới) — nick có thể vừa bị xoá.");
  if (/đang nghỉ chống risk-control|đang nghỉ \(/i.test(r)) return A("⏰", "Nick đang nghỉ", r.replace(/^.*?còn/, "Còn").slice(0, 60));
  if (/tạm ngưng|tắt lịch/i.test(r)) return A("⏸", "Nick đang tạm ngưng", "Bấm chạy nick này là tự mở lại; hoặc 'Cho chạy lại' ở Kho tài khoản.");
  if (/không chạy được|không sẵn sàng/i.test(r)) return A("🚫", "Nick chưa chạy được", r.split(":").pop().trim().slice(0, 60));
  if (/no available accounts|no accounts|không có nick/i.test(r)) return T("🚦", "Hết nick chạy được", "Chờ nick rảnh, hoặc bật lịch thêm nick.");
  if (/hết lượt tạo video hôm nay|daily limit|本日は|上限/i.test(r)) return A("📅", "Hết lượt hôm nay", "Mai chạy lại, hoặc dùng nick khác.");
  if (/không đủ lượt|hết điểm|insufficient|đủ điểm|quota|クレジット|残り|lượt cho video|giảm thời lượng|credit hôm nay|còn \d+ credit/i.test(r)) return A("💳", "Không đủ lượt/điểm", "2.5: 30s rẻ nhất (2 điểm) · Seedance 2.0 chỉ 1 điểm · hoặc đổi nick.");
  if (/không hiểu prompt|意味不明|内容が不明|内容が不足|not a valid prompt/i.test(r)) return T("✍️", "Dola không hiểu prompt", "Viết mô tả cảnh quay cụ thể (không mất lượt).");
  if (/chặn nội dung|content policy|bản quyền|ポリシー|著作/i.test(r)) return A("🚫", "Bị chặn nội dung", "Đổi prompt nhẹ hơn. Dola giấu video sau khi dựng thì lượt đã trừ.");
  if (/chân dung|portrait|顔/i.test(r)) return A("🧑", "Chặn bảo vệ chân dung", "Dùng ảnh mặt của chính bạn.");
  if (/chặn vùng|không khả dụng ở quốc gia|地域ではDolaは利用できません/i.test(r)) return A("🌏", "Dola chặn vùng (proxy)", "Nick đang ra mạng từ nước bị chặn — gán/đổi proxy cho nick ở tab Proxy.");
  if (/đăng xuất|logged out|cookie.{0,10}chết|mất phiên|đăng nhập lại|log ?in/i.test(r)) return A("🔑", "Cookie hết hạn", "Bấm đăng nhập lại nick.");
  if (/Dola chưa dựng/i.test(r)) return T("💬", "Dola không nhận dựng", "Chỉ trả lời chữ, thường chưa trừ lượt — prompt ≤15s, 1 phiên bản rồi chạy lại.");
  if (/lỗi tạm thời|エラーが発生|try again|システムエラー|問題が発生/i.test(r)) return T("⏳", "Dola lỗi tạm thời", "Tool không tự gửi lại (lượt có thể đã trừ) — bấm Chạy lại nếu muốn.");
  if (/thời lượng 30|chip 30|video 30s/i.test(r)) return T("🎞", "30s gửi chưa được", "Kiểm tra mạng/proxy rồi thử lại.");
  if (/server mới tắt|tự chạy lại \d+ lần|job quá cũ|khởi động lại server/i.test(r)) return T("🔁", "Server tắt giữa lúc chạy", "Kiểm tra dola.com; chưa có video thì chạy lại.");
  if (/traceback|exception|nameerror|attributeerror|typeerror|keyerror|valueerror|is not defined|has no attribute|not subscriptable|not callable|unexpected keyword|positional argument|indexerror|module .* has no/i.test(L)) return T("🐞", "Lỗi tool", "Lỗi phần mềm — gửi Log cho dev (không phải lỗi nick).");
  if (/chưa ra video|timeout|hết giờ|hết \d+s/i.test(r)) return T("⌛", "Quá giờ chưa ra video", "Thử lại; mạng có thể chậm.");
  const m = r.replace(/^.*?Dola báo:\s*/i, "").replace(/\s+/g, " ").trim();
  return T("⚠", (m || r).slice(0, 44), "Rê chuột để xem chi tiết.");
}

// Giai đoạn job (server trả ở field `stage`) — gửi và render giờ chạy chồng nhau nên phải
// nói rõ nick đang ở khúc nào.
export const STAGE_TEXT = {
  checking: "đang kiểm tra nick…", queued: "đang xếp hàng…", waiting: "chờ slot Chrome…", opening: "đang mở nick…", submitting: "đang gửi prompt…",
  rendering: "Dola đang dựng video…", processing: "đang tạo…",
};

// Dola duyệt theo NỘI DUNG cảnh quay, không theo từ khoá — bảng này chỉ để cảnh báo sớm,
// khỏi chờ 1–2 phút mới nhận "vi phạm chính sách". Viết lại cho nhẹ chữ không giúp gì.
export function riskyPrompt(p) {
  const t = String(p || "");
  const kid = /(trẻ em|em bé|mẫu giáo|\b\d{1,2}\s*tuổi|幼儿园|儿童|小孩|男孩|女孩|child|children|kid|toddler|kindergarten|\bboy\b|\bgirl\b)/i;
  const harm = /(đánh|đe doạ|đe dọa|bạo lực|hành hạ|dọa|khóc|sợ hãi|co rúm|殴打|抽打|打骂|威胁|恐吓|木尺|哭|吓|缩|abuse|beat|\bhit\b|threat|intimidat|scared|cry|slap|punish)/i;
  if (kid.test(t) && harm.test(t)) return "trẻ em trong cảnh bạo lực/đe doạ";
  if (/(máu me|giết|xác chết|tra tấn|đâm|súng|血腥|杀|尸体|拷问|枪|刺|gore|kill|murder|torture|stab|blood)/i.test(t)) return "bạo lực/máu me";
  // Hành động mạnh/gây hấn hay bị Dola chặn (vd prompt "violently shove/STRONGEST TRIGGER/angry ... slam")
  if (/(xô đẩy|giằng|xô ngã|đập|tát|hung hãn|gây hấn|nổi điên|\bviolent|violently|shove|slam|smash|punch|choke|strangle|assault|aggressiv|\brage\b|\battack|grab(bing)?|throw(ing)?|strongest trigger)/i.test(t)) return "hành động mạnh/gây hấn (dễ bị chặn)";
  if (/(khoả thân|khỏa thân|khiêu dâm|裸|色情|性行为|nude|nsfw|erotic|sexual)/i.test(t)) return "nội dung người lớn";
  if (/(tự tử|tự sát|tự hại|自杀|自残|suicide|self-harm)/i.test(t)) return "tự hại";
  return "";
}

// Prompt mô tả tới giây thứ N ("0–3.5秒", "25-30秒", "30s", "30 giây") nhưng chọn thời lượng ngắn hơn
// → Dola hỏi lại thời lượng vòng vo (369 vòng trong log 11/9) và có khi làm bản dài hơn rồi tính credit
// cao hơn. Phát hiện trước khi gửi. Trả về số giây prompt mô tả nếu vượt quá thời lượng chọn, else 0.
export function promptSeconds(p) {
  let max = 0;
  for (const m of String(p || "").matchAll(/(\d+(?:[.,]\d+)?)\s*(?:秒|giây|sec(?:ond)?s?\b|s\b)/gi)) max = Math.max(max, parseFloat(m[1].replace(",", ".")));
  return max;
}
// Prompt mở đầu bằng khai báo thời lượng ("18s ...", "20s ...") là Dola ĐỌC ĐƯỢC và sẽ cãi: "18秒はサポート
// されていないため、最長の15秒で生成します" — tốn lượt mà ra video sai độ dài. durationMismatch chỉ bắt khi prompt
// DÀI HƠN thời lượng chọn nên bỏ lọt trường hợp này (18 không lớn hơn 30). Trả số giây khai báo nếu khác lựa chọn.
export function leadingDuration(p, dur) {
  const m = String(p || "").match(/^["'\s]*(\d{1,3})\s*(?:s\b|sec\b|giây|秒)/i);
  if (!m) return 0;
  const s = parseInt(m[1], 10);
  return s && s !== Number(dur) ? s : 0;
}

export function durationMismatch(p, dur) {
  const s = promptSeconds(p);
  return s > Number(dur) * 1.2 ? Math.round(s) : 0;
}

// Cookie chết là lý do hỏng job hay gặp nhất — loại trước khi chạy, thay vì mở nick rồi mới báo.
// Quá 12s (nick phải mở Chrome để kiểm tra) thì chạy luôn, không bắt người dùng chờ.
export async function deadNicks(names) {
  try {
    const r = await Promise.race([api.verifyAll?.(names),   // chỉ kiểm tra nick đang cần chạy
                                  new Promise((res) => setTimeout(() => res(null), 12000))]);
    if (!r?.ok) return [];
    return (r.results || []).filter((x) => x.checked && !x.ok).map((x) => x.name).filter((n) => names.includes(n));
  } catch { return []; }
}

export const setConcurrency = (send, login) => api.setConcurrency?.(send, login);

// ── Trạng thái nick: MỘT nguồn sự thật cho MỌI tab ──────────────
// Trước đây accState (Kho), pill (Studio), nickPill (Báo cáo) tự tính theo 3 cách khác nhau
// nên cùng một nick lại hiện 3 kiểu; đặc biệt Studio bỏ sót "busy" -> bắn job vào nick
// đang render rồi báo lỗi. Giờ tất cả gọi accState() này.
export function accState(a) {
  if (a.login_ok === 0) return "dead";
  if (a.busy) return "busy";
  if (a.scheduling === false) return "off";
  if (a.rate_limited || a.quota_blocked) return "quota";
  if (a.remaining != null && a.remaining <= 0) return "quota";
  if (a.limit != null && a.used_today >= a.limit) return "quota";
  if (a.cooling) return "cooling";
  return "ready";
}
// state -> [variant Badge, nhãn tiếng Việt]
export const ACC_BADGE = {
  ready: ["success", "sẵn sàng"], busy: ["default", "đang chạy"], cooling: ["warn", "đang nghỉ"],
  quota: ["warn", "hết lượt/điểm"], off: ["secondary", "tạm ngưng"], dead: ["danger", "cookie chết"],
};
// Chỉ nick "ready" mới đáng bắn job — khớp đúng badge xanh, không phí lệnh vào nick bận/khoá.
export const canRunAccount = (a) => accState(a) === "ready";

// ── Kho tài khoản (admin API) ──────────────────────────────────
// /health chỉ trả bản rút gọn (khoá "account"); admin trả đủ:
// name, email, note, scheduling, login_ok, used_today, limit, remaining,
// rate_limited, quota_blocked, cooling, cooldown_until, last_used_at, busy,
// limit_reason, quota_reason, credit_balance.
export const adminHeaders = () => ({ ...authHeaders(), ...(cfg.adminKey ? { "x-admin-key": cfg.adminKey } : {}) });

// cfg được nạp trong App.jsx useEffect; lần gọi đầu của kho có thể chạy TRƯỚC đó →
// thiếu x-admin-key → 401 → bảng báo nhầm "server tắt". Tự nạp nếu chưa có.
async function ensureConfig() {
  if (cfg.adminKey === undefined) await loadConfig();
}

// Trả { ok:true, accounts } hoặc { ok:false, kind:"auth"|"http"|"net" }.
// Phân biệt "sai admin key" với "gateway tắt" — hai thứ cần hai câu báo khác nhau.
export async function adminAccounts() {
  await ensureConfig();
  try {
    const r = await fetch(cfg.base + "/api/admin/accounts", { headers: adminHeaders(), cache: "no-store" });
    if (r.status === 401 || r.status === 403) return { ok: false, kind: "auth" };
    if (!r.ok) return { ok: false, kind: "http", status: r.status };
    return { ok: true, accounts: (await r.json()).accounts || [] };
  } catch { return { ok: false, kind: "net" }; }
}

// Ném Error kèm .status và nguyên văn `detail` của FastAPI (409 = nick đang render…).
async function adminFetch(name, path, method = "POST") {
  await ensureConfig();
  // /verify mở Chrome kiểm cookie có thể mất ~1 phút → hạn rộng 120s, nhưng KHÔNG vô hạn.
  const r = await fetchT(cfg.base + "/api/admin/accounts/" + encodeURIComponent(name) + path, {
    method, headers: adminHeaders(),
  }, 120000);
  const text = await r.text();
  if (!r.ok) {
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch { /* body không phải JSON */ }
    const e = new Error(String(detail).slice(0, 200));
    e.status = r.status;
    throw e;
  }
  try { return JSON.parse(text); } catch { return { ok: true }; }
}

export async function patchAccount(name, body, signal = null) {
  await ensureConfig();
  const r = await fetchT(cfg.base + "/api/admin/accounts/" + encodeURIComponent(name), {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...adminHeaders() },
    body: JSON.stringify(body),
  }, 20000, signal);
  if (!r.ok) {
    const text = await r.text(); let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch { /* không phải JSON */ }
    const e = new Error(String(detail).slice(0, 200)); e.status = r.status; throw e;
  }
  return r.json();
}

// "Cookie chết" là CÂU TRẢ LỜI hợp lệ của /verify ({ok:false}), không phải lỗi gọi API.
export const verifyAccount = async (n) => ({ ok: true, alive: !!(await adminFetch(n, "/verify")).ok });
export const openProfile = (n) => adminFetch(n, "/open");
// Bỏ "đang nghỉ" (cooldown 30' sau khi captcha trượt) để chạy lại ngay.
export const wakeAccount = (n) => adminFetch(n, "/wake");

// Job đang chạy trên server (kể cả do lần mở app trước gửi): dùng để bám lại, nếu không thì
// bảng hiện "—" trong khi nick vẫn đang render → trông như treo.
export async function inflightTasks() {
  await ensureConfig();
  try {
    const r = await fetch(cfg.base + "/api/admin/tasks?limit=100", { headers: adminHeaders(), cache: "no-store" });
    if (!r.ok) return [];
    return ((await r.json()).tasks || []).filter((t) => t.status === "queued" || t.status === "processing");
  } catch { return []; }
}
// Xoá nick / xoá cookie PHẢI đi qua server: pool trả 409 khi nick đang render, và
// clear-cookies còn đặt login_ok=False để pool ngừng xếp lịch nick vừa bị xoá cookie.
export const deleteAccount = (n) => adminFetch(n, "", "DELETE");
export const clearCookies = (n) => adminFetch(n, "/clear-cookies");

// ── Proxy & job (Tổng quan / Kho / tab Proxy) ─────────────────────
const _isTmKey = (s) => /^tmproxy:\/\//i.test(s) || /^[a-f0-9]{32}$/i.test(s);
const _isKeyLink = (s) => /^https?:\/\//i.test(s) && (/get\.php/i.test(s) || /key=/i.test(s));
const _maskKey = (k) => (k.length <= 8 ? "••••" : k.slice(0, 4) + "…" + k.slice(-3));   // d3e4…f4a
// Che KEY/mật khẩu proxy khi hiện lên màn hình (không lộ key khi share màn): tmproxy://d3e4…f4a,
// link get.php?key=••••, scheme://user:•••@host:port, host:port:user:•••
export const maskProxy = (raw) => {
  const s = String(raw || "").trim(); if (!s) return "";
  if (_isTmKey(s)) return "tmproxy://" + _maskKey(s.replace(/^tmproxy:\/\//i, ""));
  if (_isKeyLink(s)) return s.replace(/(key=)[^&\s]+/i, "$1••••");
  const m = s.match(/^(\w+:\/\/)?([^:@/]+):([^@/]+)@(.+)$/);
  if (m) return `${m[1] || ""}${m[2]}:•••@${m[4]}`;
  const p = s.replace(/^\w+:\/\//, "").split(":");
  if (p.length >= 4) return `${p[0]}:${p[1]}:${p[2]}:•••`;
  return s;
};
// Nhãn ngắn cho cột PROXY (không lộ key): proxy xoay → "tmproxy ••f4a" / "xoay · <domain>"; proxy tĩnh → host:port
export const proxyHost = (raw) => {
  const s0 = String(raw || "").trim();
  if (_isTmKey(s0)) return "tmproxy ••" + s0.replace(/^tmproxy:\/\//i, "").slice(-3);
  if (_isKeyLink(s0)) { try { return "xoay · " + new URL(s0).host; } catch { return "proxy xoay"; } }
  const s = s0.replace(/^\w+:\/\//, ""); const at = s.lastIndexOf("@");
  return at >= 0 ? s.slice(at + 1) : s.split(":").slice(0, 2).join(":");
};
export async function recentTasks(limit = 200) {
  await ensureConfig();
  try {
    const r = await fetchT(cfg.base + `/api/admin/tasks?limit=${limit}`, { headers: adminHeaders(), cache: "no-store" }, 20000);
    if (!r.ok) return [];
    return (await r.json()).tasks || [];
  } catch { return []; }
}
// Video "đã về máy" = video_url trỏ về /videos/ của gateway (đã tải); còn link CDN = "chỉ trên Dola".
export const isLocalVideo = (u) => /\/videos\//.test(String(u || ""));
// Tải lại video đã xong (còn trên Dola) về máy — cho video chưa có file (proxy rớt lúc chạy).
// Đốt nick (nick dùng 1 lần): server tự nhớ vào .env.local, chạy cả khi nối máy chủ từ xa.
export async function setBurnNicks(on) {
  await ensureConfig();
  const r = await fetch(cfg.base + "/api/admin/burn-nicks", { method: "POST", headers: { ...adminHeaders(), "Content-Type": "application/json" }, body: JSON.stringify({ burn_nicks: !!on }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || ("HTTP " + r.status));
  return j;
}
// Check Video Nick: video đã dựng xong trên Dola của nick (server đọc lịch sử hội thoại, không tốn lượt).
export async function scanNickVideos(name, limit = 30) {
  await ensureConfig();
  const r = await fetch(cfg.base + `/api/admin/accounts/${encodeURIComponent(name)}/videos?limit=${limit}`, { headers: adminHeaders(), cache: "no-store" });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || (r.status === 401 ? "sai admin key" : "HTTP " + r.status));
  return j;
}
// Quét video trên Dola của NHIỀU nick (gọi lần lượt để không đánh sập gateway; mỗi nick server tự bung
// song song các hội thoại). onStep(i, tổng, tên nick) để hiện tiến độ. Nick cookie chết → ghi vào errors, chạy tiếp.
export async function scanAllNickVideos(names, limit = 50, onStep = null) {
  const videos = [], errors = [];
  for (let i = 0; i < names.length; i++) {
    onStep?.(i + 1, names.length, names[i]);
    try {
      const r = await scanNickVideos(names[i], limit);
      (r.videos || []).forEach((v) => videos.push({ ...v, account: v.account || names[i] }));
    } catch (e) { errors.push(`${names[i]}: ${e?.message || e}`); }
  }
  return { videos, errors };
}
export async function redownloadVideo(taskId, url, account = "", prompt = "") {
  await ensureConfig();
  const r = await fetch(cfg.base + "/api/admin/redownload", {
    method: "POST", headers: { ...adminHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: taskId, url, account, prompt }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || ("HTTP " + r.status));
  return j;
}
// Cấu hình ĐANG chạy trên server (proxy chung đã che mật khẩu, luồng, giãn nhịp…): ở chế độ từ xa
// app không đọc được .env.local trên VPS. Server cũ chưa có endpoint → null, caller tự lùi về IPC.
export async function adminConfig() {
  await ensureConfig();
  try {
    const r = await fetch(cfg.base + "/api/admin/config", { headers: adminHeaders(), cache: "no-store" });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}
// ── Kho proxy tập trung (admin API; chạy cả khi nối server từ xa) ──
async function poolFetch(path, method = "GET", body) {
  await ensureConfig();
  const opt = { method, headers: { ...adminHeaders(), ...(body ? { "Content-Type": "application/json" } : {}) }, cache: "no-store" };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(cfg.base + "/api/admin/proxies" + path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || (r.status === 401 ? "sai admin key" : "HTTP " + r.status));
  return j;
}
export const proxyPoolList = () => poolFetch("");
export const proxyPoolAdd = (text, provider = "") => poolFetch("", "POST", { text, provider });
export const proxyPoolCheck = () => poolFetch("/check", "POST");
export const proxyPoolPrune = () => poolFetch("/prune", "POST");
export const proxyPoolAssign = (per_ip, scope) => poolFetch("/assign", "POST", { per_ip, scope });
export const proxyPoolDelete = (id) => poolFetch("/" + encodeURIComponent(id), "DELETE");
export const proxyPoolRotate = (id) => poolFetch("/" + encodeURIComponent(id) + "/rotate", "POST");

// ── Mang nick sang máy khác ───────────────────────────────────────
export async function exportAccounts() {
  await ensureConfig();
  const r = await fetch(cfg.base + "/api/admin/accounts/export", { headers: adminHeaders(), cache: "no-store" });
  if (!r.ok) throw new Error(r.status === 401 ? "sai admin key" : "HTTP " + r.status);
  return r.json();
}
// Nhập gói: từng nick đi qua đúng import-cookie (server nạp cookie vào profile Chrome + kiểm tra
// phiên, ghi proxy riêng trước khi mở Chrome) rồi ghi chú / lịch. Chạy được cả khi nối server từ xa.
// onStep(text, progress) — progress = {i, total, name, ok, unverified, bad} để vẽ thanh tiến độ;
// isStopped() trả true thì dừng SAU nick đang nhập (không bỏ dở một nick giữa chừng).
// Mỗi nick server mở 1 Chrome headless (~3s) rồi kiểm tra phiên qua HTTP; 4 nick cùng lúc → 128 nick
// ≈ 2–3 phút thay vì hơn 10 phút. Không đẩy cao hơn: mỗi Chrome ~0.4GB RAM.
const IMPORT_PARALLEL = 4;
export async function importAccounts(bundle, onStep, isStopped) {
  await ensureConfig();
  const list = normalizeBundle(bundle).accounts;   // nhận cả file seedance-accounts của tool khác
  if (!list.length) throw new Error("file không có nick nào (đúng file xuất từ Kho tài khoản?)");
  const st = { ok: 0, unverified: 0, why: "", finished: 0, bad: [], paused: 0 };   // paused = file đánh dấu nick đang tắt
  const progress = (name) => onStep?.(`Nhập ${st.finished}/${list.length} · đang nạp ${name}… (${IMPORT_PARALLEL} nick cùng lúc, mỗi nick 3–10s)`,
                                      { i: st.finished, total: list.length, name, ok: st.ok, unverified: st.unverified, bad: st.bad.length });
  const one = async (a) => {
    progress(a.name);
    try {
      const r = await fetch(cfg.base + "/api/admin/accounts/import-cookie", {
        method: "POST", headers: { "Content-Type": "application/json", ...adminHeaders() },
        body: JSON.stringify({ name: a.name, cookies: JSON.stringify(a.cookies || []), proxy: a.proxy || "", email: a.email || "" }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || "HTTP " + r.status);
      if (a.note || a.scheduling === false) await patchAccount(a.name, { note: a.note || "", scheduling: a.scheduling !== false }).catch(() => {});
      st.ok++;
      if (a.scheduling === false) st.paused++;
      if (j.ok === false) st.bad.push(`${a.name}: cookie không còn đăng nhập`);
      else if (j.ok == null) { st.unverified++; st.why = j.message || st.why; }   // server không tới được Dola (proxy) — cookie vẫn đã lưu
    } catch (e) { st.bad.push(`${a.name}: ${String(e.message || e).slice(0, 80)}`); }
    st.finished++;
  };
  const { stopped } = await runPool(list, IMPORT_PARALLEL, one, isStopped);
  return { ok: st.ok, total: list.length, done: st.finished, bad: st.bad, unverified: st.unverified, why: st.why, paused: st.paused, stopped };
}
// Chip trạng thái theo bản Stitch: tách "hết credit" với "hết lượt hôm nay", ghi giờ hết nghỉ.
export function accChip(a) {
  const st = accState(a);
  if (st === "quota") {
    const dayFull = a.rate_limited || (a.limit != null && a.used_today >= a.limit);
    return dayFull ? { st, variant: "warn", text: "Hết lượt hôm nay" } : { st, variant: "danger", text: "Hết credit" };
  }
  if (st === "cooling") {
    const t = a.cooldown_until ? new Date(a.cooldown_until * 1000).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" }) : "";
    return { st, variant: a.quarantine ? "warn" : "info", text: (a.quarantine ? "Cách ly" : "Nghỉ") + (t ? ` đến ${t}` : ""), title: a.quarantine || "" };
  }
  const M = { ready: ["success", "Sẵn sàng"], busy: ["default", "Đang chạy"], off: ["secondary", "Tạm ngưng"], dead: ["secondary", "⚠ Chưa đăng nhập"] };
  return { st, variant: M[st][0], text: M[st][1] };
}
// Cookie nick: Dola nhận lệnh (hoặc bấm kiểm tra) thì server ghi login_ok=1 + login_checked_at. Xác nhận trong
// COOKIE_FRESH_SEC thì coi là sống chắc chắn → bấm Chạy bỏ qua bước "kiểm tra nick" cho nick đó.
export const COOKIE_FRESH_SEC = 3600;
export function cookieInfo(a) {
  if (a?.login_ok === 0) return { st: "dead", text: "cookie chết" };
  if (a?.login_ok !== 1) return { st: "unknown", text: "cookie chưa kiểm" };
  const at = a.login_checked_at || 0;
  if (at && Date.now() / 1000 - at < COOKIE_FRESH_SEC) return { st: "fresh", text: `cookie sống · ${timeAgo(at)} trước` };
  return { st: "stale", text: at ? `cookie sống · ${timeAgo(at)} trước` : "cookie sống" };
}
export const fmtSec = (s) => { s = Math.round(s || 0); return s >= 60 ? `${Math.floor(s / 60)}p ${String(s % 60).padStart(2, "0")}s` : `${s}s`; };

// "5 phút trước" dạng ngắn: 45s · 12p · 3g · 2n
export const timeAgo = (ts) => {
  if (!ts) return "—";
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "p";
  if (s < 86400) return Math.floor(s / 3600) + "g";
  return Math.floor(s / 86400) + "n";
};
