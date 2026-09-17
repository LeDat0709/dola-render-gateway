// Chia prompt cho các nick trong Studio. Tách khỏi StudioTab để test được bằng node (desktop/test-prompt-fill.cjs).
//
// CHỈ gắn prompt cho nick chạy được NGAY — đúng tập mà nút "Chạy sẵn sàng" sẽ chạy (sẵn sàng + đủ điểm cho video đang
// chọn). Trước đây các nút chia duyệt MỌI nick trong bảng: prompt rơi vào nick Tạm ngưng / Hết lượt / thiếu điểm (ảnh
// 16/09: "3Young Groomer / 4Officer / 5Owner" nằm ở nick còn 0 điểm) trong khi nick sẵn sàng thì trống.

export const splitLines = (bulk) => String(bulk || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean);

// Kịch bản hay có dòng trống ngăn TỪNG CẢNH (镜头1 / 音频 / 镜头2) chứ không chỉ ngăn giữa các kịch bản. Nếu văn bản có
// DÒNG TRỐNG ĐÔI thì coi đó mới là ranh giới kịch bản (cảnh vẫn cách nhau 1 dòng trống).
export function splitBlocks(bulk) {
  const s = String(bulk || "");
  const doi = s.split(/\r?\n\s*\r?\n\s*\r?\n/).map((b) => b.trim()).filter(Boolean);
  if (doi.length > 1) return { blocks: doi, doubleBlank: true };
  return { blocks: s.split(/\r?\n\s*\r?\n/).map((b) => b.trim()).filter(Boolean), doubleBlank: false };
}

/**
 * mode: "all" (dòng đầu cho mọi nick sẵn sàng) | "whole" (cả khối) | "lines" (mỗi dòng 1 nick) | "blocks" (mỗi khối 1 nick)
 * nicks: tên nick theo thứ tự bảng; ready(n): chạy được ngay; idle(n): chưa chạy / không có kết quả dở.
 * Trả { assign: [[nick, prompt]], clear: [nick], readyCount, extra, unit }:
 *   clear = nick KHÔNG chạy được và đang rảnh → bỏ prompt cũ, để lúc nick có lượt lại "Chạy sẵn sàng" không chạy nhầm
 *   prompt cũ (tạo lại video cũ, tốn lượt). Nick đang chạy / đã có kết quả thì giữ nguyên.
 */
export function planPromptFill(mode, bulk, nicks, ready, idle) {
  const readyNicks = nicks.filter(ready);
  let prompts;
  let unit = "prompt";
  if (mode === "all") {
    const first = splitLines(bulk)[0] || "";
    prompts = first ? readyNicks.map(() => first) : [];
  } else if (mode === "whole") {
    const whole = String(bulk || "").trim();
    prompts = whole ? readyNicks.map(() => whole) : [];
  } else if (mode === "lines") {
    prompts = splitLines(bulk);
  } else {
    const { blocks, doubleBlank } = splitBlocks(bulk);
    prompts = blocks;
    unit = doubleBlank ? "kịch bản (cách nhau DÒNG TRỐNG ĐÔI)" : "kịch bản";
  }
  const assign = readyNicks.slice(0, prompts.length).map((n, i) => [n, prompts[i]]);
  const clear = nicks.filter((n) => !ready(n) && idle(n));
  return { assign, clear, readyCount: readyNicks.length, extra: Math.max(0, prompts.length - readyNicks.length), unit };
}

// Thứ tự dòng trong Studio: nick CHẠY ĐƯỢC lên đầu, nick LỖI xuống dưới.
// 0 đang chạy → 1 sẵn sàng (đủ điểm) → 2 vừa xong → 3 lỗi (vẫn chạy lại được) → 4 nghỉ → 5 hết điểm/lượt → 6 tắt lịch → 7 còn lại.
// Trước đây "sẵn sàng + đủ điểm" được xét TRƯỚC phase nên dòng Lỗi/Xong chung nhóm với Sẵn sàng, xen kẽ theo tên (ảnh 17/9).
export function nickRank(phase, state, lowCredit) {
  if (phase === "running" || state === "busy") return 0;
  if (state === "ready" && !lowCredit) return phase === "error" ? 3 : phase === "done" ? 2 : 1;
  if (state === "ready") return 5;   // còn điểm nhưng KHÔNG đủ cho thời lượng này
  return { cooling: 4, quota: 5, off: 6 }[state] ?? 7;
}
