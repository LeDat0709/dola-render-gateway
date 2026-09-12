// Chuẩn hoá file "Nhập kho" về một dạng duy nhất trước khi đưa từng nick qua import-cookie:
//   - gói xuất từ Kho tài khoản của Dola Studio (kind: "dola-studio-accounts") → giữ nguyên
//   - file xuất của tool khác (loai: "seedance-accounts", tai_khoan[] với cookies là {tên: giá_trị})
//     → đổi tên nick về dạng server chấp nhận, bỏ nick trùng tài khoản Dola, giữ proxy/bật-tắt.
// Thuần hàm, không đụng window/fetch → test được bằng node (desktop/test-bundle.cjs).
const NAME_RE = /^[A-Za-z0-9_-]{1,32}$/;   // khớp NAME_RE của server.py
const NAME_MAX = 32;

export function safeName(raw, fallback) {
  const n = String(raw || "").trim().replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, NAME_MAX);
  return NAME_RE.test(n) ? n : fallback;
}

// Hai tên khác nhau có thể trùng sau khi làm sạch ("Nick 1" và "Nick_1") → thêm hậu tố để không
// nick nào ghi đè nick kia (import-cookie nạp lại cookie khi trùng tên chứ không báo lỗi).
function uniqueName(name, used) {
  let n = name, k = 2;
  while (used.has(n)) n = `${name.slice(0, NAME_MAX - 3)}_${k++}`;
  used.add(n);
  return n;
}

function fromSeedance(b) {
  const byUid = new Map();   // cùng dola_uid = cùng một tài khoản Dola → giữ bản đăng nhập mới nhất
  for (const t of b.tai_khoan) {
    if (!t || typeof t !== "object") continue;
    const key = t.dola_uid || t.fb_uid || t.id;
    const prev = byUid.get(key);
    if (!prev || (t.created_at || 0) >= (prev.created_at || 0)) byUid.set(key, t);
  }
  const used = new Set();
  const accounts = [...byUid.values()].map((t, i) => ({
    name: uniqueName(safeName(t.name, `fb_${t.fb_uid || i + 1}`), used),
    cookies: t.cookies || {},
    proxy: t.proxy || "",
    note: t.fb_uid ? `FB ${t.fb_uid}` : "",
    scheduling: t.enabled !== false,
  }));
  return { kind: "dola-studio-accounts", version: 1, accounts, source: "seedance",
           dupes: b.tai_khoan.length - accounts.length };
}

export function normalizeBundle(b) {
  if (b?.kind === "dola-studio-accounts" && Array.isArray(b.accounts)) return b;
  if (b?.loai === "seedance-accounts" && Array.isArray(b.tai_khoan)) return fromSeedance(b);
  throw new Error("không phải file xuất từ Kho tài khoản của Dola Studio, cũng không phải file seedance-accounts");
}
