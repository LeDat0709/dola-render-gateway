#!/usr/bin/env node
// Kiểm tra bộ chuẩn hoá file "Nhập kho" (renderer/src/lib/bundle.js): node desktop/test-bundle.cjs
const assert = require("node:assert/strict");

(async () => {
  const { normalizeBundle, safeName } = await import("./renderer/src/lib/bundle.js");

  // gói của chính Dola Studio → giữ nguyên
  const own = { kind: "dola-studio-accounts", accounts: [{ name: "n1", cookies: [] }] };
  assert.equal(normalizeBundle(own), own);

  // file lạ → báo lỗi rõ
  assert.throws(() => normalizeBundle({ foo: 1 }), /không phải file/);
  assert.throws(() => normalizeBundle({ loai: "seedance-accounts" }), /không phải file/);

  // tên nick: bỏ ký tự lạ, cắt 32 ký tự, rỗng thì dùng dự phòng
  assert.equal(safeName("FB 61594283488769", "x"), "FB_61594283488769");
  assert.equal(safeName("l dolia 61594166964722", "x"), "l_dolia_61594166964722");
  assert.equal(safeName("   ", "fb_1"), "fb_1");
  assert.equal(safeName("a".repeat(40), "x").length, 32);

  // file đối thủ: 4 bản ghi, 2 trong đó cùng dola_uid → 3 nick, giữ bản mới nhất
  const seed = {
    loai: "seedance-accounts", phien_ban: 1,
    tai_khoan: [
      { id: "a", name: "FB 61594113147344", cookies: { sessionid: "OLD", i18next: "vi" }, enabled: true, proxy: "", fb_uid: "61594113147344", dola_uid: "768270", created_at: 100 },
      { id: "b", name: "Nick 1", cookies: { sessionid: "NEW", i18next: "vi" }, enabled: true, proxy: "user:pw@1.2.3.4:8080", fb_uid: "61594113147344", dola_uid: "768270", created_at: 200 },
      { id: "c", name: "Nick_1", cookies: { sessionid: "S3" }, enabled: false, proxy: "", fb_uid: "615", dola_uid: "768999", created_at: 50 },
      { id: "d", name: "", cookies: { sessionid: "S4" }, enabled: true, proxy: "", fb_uid: "616", dola_uid: "768111", created_at: 60 },
      { id: "e", name: "FB 700", cookies: { sessionid: "S5" }, enabled: true, proxy: "", fb_uid: "700", dola_uid: "768700", created_at: 70 },
    ],
  };
  const out = normalizeBundle(seed);
  assert.equal(out.kind, "dola-studio-accounts");
  assert.equal(out.source, "seedance");
  assert.equal(out.dupes, 1);
  // "FB <uid>" và tên trống → "fb<uid>" như nick đăng nhập Facebook trong app; tên tự đặt giữ nguyên
  assert.deepEqual(out.accounts.map((a) => a.name), ["Nick_1", "Nick_1_2", "fb616", "fb700"]);
  const [b, c, d] = out.accounts;
  assert.equal(b.cookies.sessionid, "NEW", "trùng dola_uid phải giữ bản created_at lớn hơn");
  assert.equal(b.proxy, "user:pw@1.2.3.4:8080");
  assert.equal(b.note, "FB 61594113147344");
  assert.equal(b.scheduling, true);
  assert.equal(c.scheduling, false, "enabled:false → tắt lịch");
  assert.equal(d.cookies.sessionid, "S4");
  assert.equal(seed.tai_khoan.length, 5, "không được sửa file gốc");

  // runPool: tối đa n việc cùng lúc, làm hết, done = số việc đã bắt đầu
  const { runPool } = await import("./renderer/src/lib/bundle.js");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let running = 0, peak = 0; const done = [];
  const r1 = await runPool([1, 2, 3, 4, 5, 6, 7], 3, async (x) => { running++; peak = Math.max(peak, running); await sleep(5); done.push(x); running--; });
  assert.equal(peak, 3, "phải chạy đúng 3 việc cùng lúc");
  assert.deepEqual([...done].sort(), [1, 2, 3, 4, 5, 6, 7]);
  assert.deepEqual(r1, { done: 7, stopped: false });
  // bấm Dừng: không nhận việc mới, việc đang chạy vẫn xong
  let started = 0;
  const r2 = await runPool([1, 2, 3, 4, 5, 6], 2, async () => { started++; await sleep(5); }, () => started >= 3);
  assert.equal(r2.stopped, true);
  assert.ok(started >= 3 && started <= 4, `dừng sau 3–4 việc, thực tế ${started}`);
  assert.equal(r2.done, started);

  console.log("test-bundle: OK");
})().catch((e) => { console.error(e); process.exit(1); });
