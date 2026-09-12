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
    ],
  };
  const out = normalizeBundle(seed);
  assert.equal(out.kind, "dola-studio-accounts");
  assert.equal(out.source, "seedance");
  assert.equal(out.dupes, 1);
  assert.deepEqual(out.accounts.map((a) => a.name), ["Nick_1", "Nick_1_2", "fb_616"]);
  const [b, c, d] = out.accounts;
  assert.equal(b.cookies.sessionid, "NEW", "trùng dola_uid phải giữ bản created_at lớn hơn");
  assert.equal(b.proxy, "user:pw@1.2.3.4:8080");
  assert.equal(b.note, "FB 61594113147344");
  assert.equal(b.scheduling, true);
  assert.equal(c.scheduling, false, "enabled:false → tắt lịch");
  assert.equal(d.cookies.sessionid, "S4");
  assert.equal(seed.tai_khoan.length, 4, "không được sửa file gốc");

  console.log("test-bundle: OK");
})().catch((e) => { console.error(e); process.exit(1); });
