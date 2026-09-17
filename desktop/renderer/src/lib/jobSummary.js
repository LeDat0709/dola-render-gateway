// Thanh tổng kết phía trên bảng Studio: "✅ 8 xong · ⏳ 3 đang dựng · 🚫 4 bị chặn nội dung · ⚠ 5 lỗi tạm thời".
// Nhóm lỗi theo nhãn ngắn của fmtError; mỗi nhóm có MỘT cách xử lý:
//   edit  = Dola chặn/không hiểu prompt → chỉ cho sửa prompt, KHÔNG có nút chạy lại (chạy lại y nguyên lại bị chặn)
//   scan  = lệnh có thể đã tới Dola mà chưa rõ kết quả → quét video trên Dola trước (không tốn lượt)
//   retry = còn lại (lỗi tạm thời, mạng, hết lượt, cookie, nghỉ…) → chạy lại prompt đó trên nick KHÁC đang sẵn sàng
// Test: desktop/test-job-summary.cjs

const EDIT = /chặn nội dung|chân dung|không hiểu prompt/i;
const SCAN = /đã gửi, chưa xác nhận/i;

/** @param {string} label @returns {"edit"|"scan"|"retry"} */
export function groupAction(label) {
  if (EDIT.test(label)) return "edit";
  if (SCAN.test(label)) return "scan";
  return "retry";
}

/**
 * @param {string[]} nicks thứ tự đang hiển thị
 * @param {(n: string) => {phase?: string, errorRaw?: string}} rowOf
 * @param {(raw: string) => {icon: string, short: string}} fmt fmtError
 * @returns {{done: string[], running: string[], groups: {key: string, icon: string, label: string, action: string, nicks: string[]}[]}}
 */
export function summarizeJobs(nicks, rowOf, fmt) {
  const done = [], running = [], byLabel = new Map();
  for (const n of nicks) {
    const s = rowOf(n) || {};
    if (s.phase === "done") done.push(n);
    else if (s.phase === "running") running.push(n);
    else if (s.phase === "error") {
      const f = fmt(s.errorRaw || "");
      const g = byLabel.get(f.short) || { key: "err:" + f.short, icon: f.icon, label: f.short, action: groupAction(f.short), nicks: [] };
      byLabel.set(f.short, { ...g, nicks: [...g.nicks, n] });
    }
  }
  const groups = [...byLabel.values()].sort((a, b) => b.nicks.length - a.nicks.length);
  return { done, running, groups };
}

/** Nick thuộc bộ lọc đang bật ("done" | "running" | "err:<nhãn>"); null = không lọc. */
export function filterNicks(summary, key) {
  if (!key) return null;
  if (key === "done") return summary.done;
  if (key === "running") return summary.running;
  return summary.groups.find((g) => g.key === key)?.nicks || [];
}

/** Ghép job lỗi → nick sẵn sàng KHÁC (mỗi nick đích nhận 1 job, không lấy nick nguồn). */
export function pairRetries(sources, freeNicks) {
  const src = new Set(sources);
  const free = freeNicks.filter((n) => !src.has(n));
  const pairs = sources.slice(0, free.length).map((s, i) => [s, free[i]]);
  return { pairs, missing: sources.length - pairs.length };
}
