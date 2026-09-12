// Self-check cho gateway.cjs với tiến trình giả. Chạy: node desktop/test-gateway.cjs
const assert = require("assert");
const { EventEmitter } = require("events");
const { createGateway } = require("./gateway.cjs");

const events = [];
function fakeProc(id) {
  const p = new EventEmitter();
  p.id = id; p.pid = 1000 + id; p.kills = 0;
  p.kill = () => { p.kills++; setTimeout(() => { events.push(`exit${id}`); p.emit("exit", null); }, 5); };
  return p;
}
function make(opts = {}) {
  let n = 0;
  const g = createGateway({
    spawnProc: () => { n++; events.push(`spawn${n}`); return { proc: fakeProc(n) }; },
    kill: (p) => p.kill(),        // test chạy trên mọi hệ điều hành, không gọi taskkill
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

  // Hai thao tác đăng nhập chồng nhau: lần đầu xong KHÔNG được bật gateway đè lên lần hai.
  g.pause(); g.pause();
  assert.strictEqual(await g.resume(), false, "còn 1 thao tác đang cần profile → chưa bật");
  assert.strictEqual(g.running(), false);
  assert.strictEqual(await g.resume(), true, "thao tác cuối xong mới bật");
  assert.strictEqual(g.running(), true);

  // Bấm "Bật server" / "Khởi động lại" giữa lúc đăng nhập: hoãn, hết pause mới bật.
  await g.stop(); g.pause();
  assert.deepStrictEqual(await g.start(), { ok: true, deferred: true });
  assert.strictEqual(g.running(), false, "đang pause thì không được spawn");
  assert.deepStrictEqual(await g.restart(), { ok: true, deferred: true });
  assert.strictEqual(await g.resume(), true);
  assert.strictEqual(g.running(), true, "hết pause thì bật theo yêu cầu đã hoãn");

  // stop chờ thoát hẳn; restart giữ thứ tự exit → spawn.
  events.length = 0;
  assert.strictEqual(await g.stop(), true);
  assert.strictEqual(g.running(), false);
  assert.strictEqual(await g.stop(), false, "đã tắt thì stop trả false");
  await g.start(); events.length = 0;
  assert.deepStrictEqual(await g.restart(), { ok: true });
  assert.deepStrictEqual(events.map((e) => e.replace(/\d+/, "")), ["exit", "spawn"]);

  // withPaused: handler ném lỗi vẫn bật lại gateway, lỗi vẫn nổi lên.
  const h = g.withPaused(async () => { g.pause(); throw new Error("đăng nhập hỏng"); });
  await assert.rejects(h(), /đăng nhập hỏng/);
  assert.strictEqual(g.running(), true, "sau lỗi gateway vẫn phải bật lại");
  // withPaused khi handler không pause (vd validate sai tên nick): không kill, không spawn.
  const before = spawned();
  assert.strictEqual(await g.withPaused(async () => 7)(), 7);
  assert.strictEqual(spawned(), before);

  // Spawn lỗi MUỘN (AV chặn python.exe): event 'error' không được làm sập app, gateway coi như tắt.
  const late = make({ spawnProc: () => { const p = fakeProc(99); setTimeout(() => p.emit("error", new Error("EPERM")), 5); return { proc: p }; } });
  assert.deepStrictEqual(await late.g.start(), { ok: true });
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual(late.g.running(), false, "spawn lỗi muộn → running=false, không throw");

  // Chế độ từ xa: không spawn. spawn lỗi ngay: báo lỗi, không treo.
  const r1 = make({ isRemote: () => true });
  assert.deepStrictEqual(await r1.g.start(), { ok: true, remote: true }); assert.strictEqual(r1.spawned(), 0);
  const r2 = createGateway({ spawnProc: () => ({ error: "thiếu python" }), kill: (p) => p.kill() });
  assert.deepStrictEqual(await r2.start(), { ok: false, error: "thiếu python" });
  assert.strictEqual(r2.running(), false);

  console.log("ALL PASS (29 assertions)");
})().catch((e) => { console.error(e); process.exit(1); });
