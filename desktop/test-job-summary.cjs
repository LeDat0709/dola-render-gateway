// Thanh tổng kết lỗi Studio. Chạy: node desktop/test-job-summary.cjs
const assert = require("assert");

(async () => {
  const { summarizeJobs, filterNicks, pairRetries, groupAction } = await import("./renderer/src/lib/jobSummary.js");
  const fmt = (raw) => { const i = raw.indexOf(" "); return { icon: raw.slice(0, i), short: raw.slice(i + 1) }; };   // "⚠ Dola lỗi tạm thời" → icon + nhãn
  const rows = {
    a: { phase: "done" }, b: { phase: "running" }, c: { phase: "error", errorRaw: "⚠ Dola lỗi tạm thời" },
    d: { phase: "error", errorRaw: "🚫 Bị chặn nội dung" }, e: { phase: "error", errorRaw: "⚠ Dola lỗi tạm thời" },
    f: { phase: "idle" }, g: { phase: "error", errorRaw: "📨 Đã gửi, chưa xác nhận" },
  };
  const sum = summarizeJobs(Object.keys(rows), (n) => rows[n], fmt);
  assert.deepStrictEqual(sum.done, ["a"]);
  assert.deepStrictEqual(sum.running, ["b"]);
  assert.deepStrictEqual(sum.groups.map((g) => [g.label, g.nicks, g.action]), [
    ["Dola lỗi tạm thời", ["c", "e"], "retry"],   // nhóm đông nhất đứng đầu
    ["Bị chặn nội dung", ["d"], "edit"],
    ["Đã gửi, chưa xác nhận", ["g"], "scan"],
  ]);
  assert.deepStrictEqual(filterNicks(sum, "err:Dola lỗi tạm thời"), ["c", "e"]);
  assert.deepStrictEqual(filterNicks(sum, "done"), ["a"]);
  assert.strictEqual(filterNicks(sum, null), null, "không lọc = hiện tất cả");
  assert.deepStrictEqual(filterNicks(sum, "err:không có"), [], "nhóm đã hết lỗi → bảng rỗng, không hiện tất cả");

  // Bị chặn / chân dung / không hiểu prompt: CHỈ sửa prompt, không có nút chạy lại
  for (const l of ["Bị chặn nội dung", "Chặn bảo vệ chân dung", "Dola không hiểu prompt"]) assert.strictEqual(groupAction(l), "edit", l);
  for (const l of ["Dola lỗi tạm thời", "Hết lượt hôm nay", "Cookie hết hạn", "Quá giờ chưa ra video"]) assert.strictEqual(groupAction(l), "retry", l);

  // Chạy lại trên nick KHÁC: không bao giờ đưa job về chính nick nguồn, thiếu nick thì báo phần thiếu
  assert.deepStrictEqual(pairRetries(["c", "e"], ["c", "x", "y"]), { pairs: [["c", "x"], ["e", "y"]], missing: 0 });
  assert.deepStrictEqual(pairRetries(["c", "e"], ["x"]), { pairs: [["c", "x"]], missing: 1 });
  assert.deepStrictEqual(pairRetries(["c"], []), { pairs: [], missing: 1 });
  console.log("ALL PASS (thanh tổng kết lỗi)");
})().catch((e) => { console.error(e); process.exit(1); });
