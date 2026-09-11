// Cầu nối gateway HTTP + IPC (window.api). Giữ nguyên logic bản cũ.
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

export async function submitJob(prompt, body) {
  const r = await fetch(cfg.base + "/v1/videos/generations", {
    method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ ...body, prompt }),
  });
  const j = await r.json();
  if (!r.ok) throw new Error(j.detail || "HTTP " + r.status);
  return j.id;
}
export async function pollJob(id) {
  const r = await fetch(cfg.base + "/v1/videos/" + id, { headers: authHeaders(), cache: "no-store" });
  return r.json();
}

export const creditCost = (dur) => (parseInt(dur, 10) >= 30 ? 2 : 1);
export const firstLine = (v) => String(v || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean)[0] || "";
export const fnameFromUrl = (u) => String(u || "").split("?")[0].split("#")[0].split("/").pop();
export const sttFromUrl = (u) => (String(fnameFromUrl(u)).match(/^(\d+)_/) || [])[1];

// Chuẩn hoá lỗi: icon + nguyên nhân + gợi ý + phân loại (giống bản cũ).
export function fmtError(raw) {
  const r = String(raw || ""); const L = r.toLowerCase();
  const A = (icon, short, hint) => ({ icon, short, hint, kind: "account" });
  const T = (icon, short, hint) => ({ icon, short, hint, kind: "tool" });
  if (/failed to fetch|networkerror|load failed|ECONNREFUSED|ERR_CONNECTION_REFUSED/i.test(r)) return T("🔌", "Chưa nối được server", "Server đang bật lại hoặc tắt — chờ 2–3s rồi chạy lại.");
  if (/ERR_INTERNET|ERR_NETWORK|ERR_CONNECTION|ERR_PROXY|ERR_TIMED_OUT|ERR_NAME_NOT|mất mạng|net::/i.test(r)) return T("🌐", "Mất mạng tạm thời", "Kiểm tra internet/proxy rồi chạy lại.");
  if (/đang bận|đang tạo video khác/i.test(r)) return A("⏳", "Nick đang bận", "Chờ video hiện tại xong rồi chạy tiếp.");
  if (/không tồn tại/i.test(r)) return T("👻", "Nick không còn trong pool", "Bảng đang cũ (đã tự làm mới) — nick có thể vừa bị xoá.");
  if (/không sẵn sàng|tắt lịch/i.test(r)) return A("⏸", "Nick đang tắt lịch", 'Bấm "Bật lịch tất cả" rồi chạy lại.');
  if (/no available accounts|no accounts|không có nick/i.test(r)) return T("🚦", "Hết nick chạy được", "Chờ nick rảnh, hoặc bật lịch thêm nick.");
  if (/hết lượt tạo video hôm nay|daily limit|本日は|上限/i.test(r)) return A("📅", "Hết lượt hôm nay", "Mai chạy lại, hoặc dùng nick khác.");
  if (/không đủ lượt|hết điểm|insufficient|đủ điểm|quota|クレジット|残り|lượt cho video/i.test(r)) return A("💳", "Không đủ lượt/điểm", "Giảm giây hoặc đổi nick khác.");
  if (/chặn nội dung|content policy|bản quyền|ポリシー|著作/i.test(r)) return A("🚫", "Bị chặn nội dung", "Đổi prompt nhẹ hơn (không mất lượt).");
  if (/chân dung|portrait|顔/i.test(r)) return A("🧑", "Chặn bảo vệ chân dung", "Dùng ảnh mặt của chính bạn.");
  if (/đăng xuất|logged out|cookie.{0,10}chết|mất phiên|đăng nhập lại|log ?in/i.test(r)) return A("🔑", "Cookie hết hạn", "Bấm đăng nhập lại nick.");
  if (/lỗi tạm thời|エラーが発生|try again|システムエラー|問題が発生/i.test(r)) return T("⏳", "Dola lỗi tạm thời", "Đã tự thử lại; chạy lại nếu vẫn lỗi.");
  if (/thời lượng 30|chip 30|video 30s/i.test(r)) return T("🎞", "30s gửi chưa được", "Kiểm tra mạng/proxy rồi thử lại.");
  if (/server mới tắt|tự chạy lại \d+ lần|job quá cũ|khởi động lại server/i.test(r)) return T("🔁", "Server tắt giữa lúc chạy", "Kiểm tra dola.com; chưa có video thì chạy lại.");
  if (/traceback|exception|nameerror|attributeerror|keyerror/i.test(L)) return T("🐞", "Lỗi tool", "Xem Log, gửi cho dev.");
  if (/chưa ra video|timeout|hết giờ|hết \d+s/i.test(r)) return T("⌛", "Quá giờ chưa ra video", "Thử lại; mạng có thể chậm.");
  const m = r.replace(/^.*?Dola báo:\s*/i, "").replace(/\s+/g, " ").trim();
  return T("⚠", (m || r).slice(0, 44), "Rê chuột để xem chi tiết.");
}

// Giai đoạn job (server trả ở field `stage`) — gửi và render giờ chạy chồng nhau nên phải
// nói rõ nick đang ở khúc nào.
export const STAGE_TEXT = {
  queued: "đang xếp hàng…", opening: "đang mở nick…", submitting: "đang gửi prompt…",
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
  if (/(khoả thân|khỏa thân|khiêu dâm|裸|色情|性行为|nude|nsfw|erotic|sexual)/i.test(t)) return "nội dung người lớn";
  if (/(tự tử|tự sát|tự hại|自杀|自残|suicide|self-harm)/i.test(t)) return "tự hại";
  return "";
}

// Cookie chết là lý do hỏng job hay gặp nhất — loại trước khi chạy, thay vì mở nick rồi mới báo.
// Quá 12s (nick phải mở Chrome để kiểm tra) thì chạy luôn, không bắt người dùng chờ.
export async function deadNicks(names) {
  try {
    const r = await Promise.race([api.verifyAll?.(), new Promise((res) => setTimeout(() => res(null), 12000))]);
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
  quota: ["warn", "hết lượt/điểm"], off: ["secondary", "tắt lịch"], dead: ["danger", "cookie chết"],
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
  const r = await fetch(cfg.base + "/api/admin/accounts/" + encodeURIComponent(name) + path, {
    method, headers: adminHeaders(),
  });
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

export async function patchAccount(name, body) {
  await ensureConfig();
  const r = await fetch(cfg.base + "/api/admin/accounts/" + encodeURIComponent(name), {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...adminHeaders() },
    body: JSON.stringify(body),
  });
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
// Xoá nick / xoá cookie PHẢI đi qua server: pool trả 409 khi nick đang render, và
// clear-cookies còn đặt login_ok=False để pool ngừng xếp lịch nick vừa bị xoá cookie.
export const deleteAccount = (n) => adminFetch(n, "", "DELETE");
export const clearCookies = (n) => adminFetch(n, "/clear-cookies");

// "5 phút trước" dạng ngắn: 45s · 12p · 3g · 2n
export const timeAgo = (ts) => {
  if (!ts) return "—";
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "p";
  if (s < 86400) return Math.floor(s / 3600) + "g";
  return Math.floor(s / 86400) + "n";
};
