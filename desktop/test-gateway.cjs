// Self-check cho gateway.cjs với tiến trình giả. Chạy: node desktop/test-gateway.cjs
const assert = require("assert");
const { EventEmitter } = require("events");
const { createGateway } = require("./gateway.cjs");

const events = [];
function fakeProc(id) {
  const p = new EventEmitter();
  p.id = id; p.kills = 0;
  p.kill = () => { p.kills++; setTimeout(() => { events.push(`exit${id}`); p.emit("exit", null); }, 5); };
  return p;
}
function make(opts = {}) {
  let n = 0;
  const g = createGateway({
    spawnProc: () => { n++; events.push(`spawn${n}`); return { proc: fakeProc(n) }; },
    ...opts,
  });
  return { g, spawned: () => n };
}

(async () => {
  // Bật hai lần: lần hai báo already, không spawn thêm.
  let { g, spawned } = make();
  assert.deepStrictEqual(await g.start(), { ok: true });
  assert.deepStrictEqual(await g.start(), { ok: true, already: true });
  assert.strictEqual(spawned(), 1);

  // Lỗi 12/9: pause() để đăng nhập rồi resume() PHẢI bật lại.
  g.pause();
  assert.strictEqual(g.running(), false, "pause phải dừng ngay");
  assert.strictEqual(await g.resume(), true);
  assert.strictEqual(g.running(), true, "resume phải bật lại");
  assert.strictEqual(spawned(), 2);
  assert.deepStrictEqual(events, ["spawn1", "exit1", "spawn2"], "spawn mới chỉ sau khi tiến trình cũ thoát");

  // resume khi không pause: không spawn bừa.
  assert.strictEqual(await g.resume(), false);
  assert.strictEqual(spawned(), 2);

  // stop chờ thoát hẳn; restart giữ thứ tự exit → spawn.
  events.length = 0;
  assert.strictEqual(await g.stop(), true);
  assert.strictEqual(g.running(), false);
  assert.strictEqual(await g.stop(), false, "đã tắt thì stop trả false");
  await g.start(); events.length = 0;
  assert.deepStrictEqual(await g.restart(), { ok: true });
  assert.deepStrictEqual(events, ["exit3", "spawn4"]);

  // withPaused: handler ném lỗi vẫn bật lại gateway, lỗi vẫn nổi lên.
  const h = g.withPaused(async () => { g.pause(); throw new Error("đăng nhập hỏng"); });
  await assert.rejects(h(), /đăng nhập hỏng/);
  assert.strictEqual(g.running(), true, "sau lỗi gateway vẫn phải bật lại");
  // withPaused khi handler không pause (vd validate sai tên nick): không kill, không spawn.
  const before = spawned();
  assert.strictEqual(await g.withPaused(async () => 7)(), 7);
  assert.strictEqual(spawned(), before);

  // Chế độ từ xa: không spawn. spawn lỗi: báo lỗi, không treo.
  const r1 = make({ isRemote: () => true });
  assert.deepStrictEqual(await r1.g.start(), { ok: true, remote: true }); assert.strictEqual(r1.spawned(), 0);
  const r2 = createGateway({ spawnProc: () => ({ error: "thiếu python" }) });
  assert.deepStrictEqual(await r2.start(), { ok: false, error: "thiếu python" });
  assert.strictEqual(r2.running(), false);

  console.log("ALL PASS (20 assertions)");
})().catch((e) => { console.error(e); process.exit(1); });
