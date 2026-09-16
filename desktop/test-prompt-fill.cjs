// Chia prompt trong Studio CHỈ cho nick chạy được ngay. Chạy: node desktop/test-prompt-fill.cjs
// Ảnh 16/09: prompt "3Young Groomer / 4Officer / 5Owner" nằm ở nick Tạm ngưng (còn 0 điểm), còn nick Sẵn sàng thì trống —
// các nút chia prompt duyệt MỌI nick trong bảng, trong khi "Chạy sẵn sàng" chỉ chạy nick sẵn sàng + đủ điểm.
const assert = require("assert");

(async () => {
  const { planPromptFill, splitLines, splitBlocks } = await import("./renderer/src/lib/promptFill.js");

  // bảng: a1 sẵn sàng, p1 tạm ngưng, a2 sẵn sàng, low thiếu điểm, busy đang chạy, a3 sẵn sàng, oldErr lỗi lần trước
  const nicks = ["a1", "p1", "a2", "low", "busy", "a3", "oldErr"];
  const ready = (n) => ["a1", "a2", "a3"].includes(n);
  const idle = (n) => !["busy", "oldErr"].includes(n);
  const bulk = "1Mother\n2Photographer\n3Young Groomer\n4Officer\n5Owner";

  // Mỗi dòng 1 nick: chỉ nick sẵn sàng nhận, theo thứ tự bảng
  let p = planPromptFill("lines", bulk, nicks, ready, idle);
  assert.deepStrictEqual(p.assign, [["a1", "1Mother"], ["a2", "2Photographer"], ["a3", "3Young Groomer"]], JSON.stringify(p.assign));
  assert.ok(!p.assign.some(([n]) => ["p1", "low", "busy", "oldErr"].includes(n)), "gắn prompt vào nick không chạy được");
  assert.strictEqual(p.extra, 2, "2 prompt dư phải được báo");
  assert.strictEqual(p.readyCount, 3);
  // nick không chạy được + chưa chạy dở → bỏ prompt cũ; đang chạy / đã có kết quả thì giữ nguyên
  assert.deepStrictEqual(p.clear, ["p1", "low"], JSON.stringify(p.clear));

  // Mỗi khối 1 nick
  p = planPromptFill("blocks", "cảnh A1\ncảnh A2\n\ncảnh B1", nicks, ready, idle);
  assert.deepStrictEqual(p.assign, [["a1", "cảnh A1\ncảnh A2"], ["a2", "cảnh B1"]]);
  assert.strictEqual(p.readyCount - p.assign.length, 1, "a3 sẵn sàng mà chưa có khối");

  // Điền tất cả / Cả khối: chỉ nick sẵn sàng
  p = planPromptFill("all", "dòng đầu\ndòng hai", nicks, ready, idle);
  assert.deepStrictEqual(p.assign.map(([n]) => n), ["a1", "a2", "a3"]);
  assert.ok(p.assign.every(([, x]) => x === "dòng đầu"));
  p = planPromptFill("whole", "dòng đầu\ndòng hai", nicks, ready, idle);
  assert.ok(p.assign.every(([, x]) => x === "dòng đầu\ndòng hai") && p.assign.length === 3);

  // Không có nick sẵn sàng → không gán gì
  p = planPromptFill("lines", bulk, nicks, () => false, idle);
  assert.strictEqual(p.assign.length, 0); assert.strictEqual(p.readyCount, 0);

  // tách dòng / khối giữ đúng hành vi cũ (dòng trống ĐÔI mới là ranh giới khi có)
  assert.deepStrictEqual(splitLines(" a \n\n b "), ["a", "b"]);
  assert.deepStrictEqual(splitBlocks("x1\n\nx2\n\n\ny1").blocks, ["x1\n\nx2", "y1"]);
  assert.strictEqual(splitBlocks("x1\n\nx2\n\n\ny1").doubleBlank, true);

  console.log("ALL PASS (prompt chỉ gắn cho nick chạy được)");
})().catch((e) => { console.error(e); process.exit(1); });
