// Vòng đời tiến trình gateway (uvicorn) ở MỘT chỗ: bật, tắt-và-chờ-thoát, tạm dừng để nhường Chrome
// profile cho đăng nhập / nạp cookie rồi TỰ BẬT LẠI. Không phụ thuộc Electron → test bằng node thường.
//
// Lỗi 12/9 00:13: "Đăng nhập lại" / "Thêm bằng Facebook" kill gateway để giải phóng profile nhưng không
// bật lại → log "gateway exit null" rồi im, app báo "Gateway tắt", người dùng tưởng tool hỏng.
const { spawn } = require("child_process");

const EXIT_WAIT_MS = 8000;

// Windows: p.kill() luôn là TerminateProcess và KHÔNG giết tiến trình con → Chrome của các nick đang
// render thành mồ côi, giữ SingletonLock của profile, lần mở sau treo. taskkill /T /F giết cả cây.
function killTree(p) {
  if (process.platform === "win32" && p.pid) {
    try { spawn("taskkill", ["/pid", String(p.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true }); return; } catch (_) { /* rơi xuống kill thường */ }
  }
  try { p.kill(); } catch (_) { /* đã chết */ }
}

function createGateway({ spawnProc, isRemote = () => false, log = () => {}, kill = killTree }) {
  let proc = null;
  let pauses = 0;        // số thao tác đang cần profile rảnh (đăng nhập, nạp cookie) — đếm, không phải cờ:
                         // hai lần đăng nhập nối tiếp thì lần đầu xong không được bật gateway đè lên lần hai
  let stopping = null;   // promise "đã dừng xong" của lần pause đầu
  let wanted = false;    // hết pause thì có cần bật lại không (đang chạy lúc pause, hoặc bấm Bật giữa chừng)

  async function start() {
    if (isRemote()) return { ok: true, remote: true };   // server chạy trên VPS, máy này không spawn Python
    if (pauses > 0) { wanted = true; return { ok: true, deferred: true }; }   // đăng nhập đang cần profile: bật sau
    if (proc) return { ok: true, already: true };
    const r = spawnProc();                                // { proc } hoặc { error }
    if (!r || r.error || !r.proc) return { ok: false, error: (r && r.error) || "không spawn được gateway" };
    const p = (proc = r.proc);
    p.on("exit", (code) => { log(`===== gateway exit ${code} =====`); if (proc === p) proc = null; });
    // Không có listener 'error' thì spawn lỗi muộn (AV chặn python.exe, thiếu DLL) là sập cả app.
    p.on("error", (e) => { log(`===== gateway spawn error: ${(e && e.message) || e} =====`); if (proc === p) proc = null; });
    return { ok: true };
  }

  // Dừng và CHỜ thoát hẳn: bật lại khi cổng chưa nhả là uvicorn mới chết vì "address already in use".
  function stop() {
    const p = proc;
    if (!p) return Promise.resolve(false);
    proc = null;
    return new Promise((resolve) => {
      const t = setTimeout(() => { try { p.kill("SIGKILL"); } catch (_) {} resolve(true); }, EXIT_WAIT_MS);
      p.once("exit", () => { clearTimeout(t); resolve(true); });
      kill(p);
    });
  }

  function pause() {
    pauses++;
    if (proc) { wanted = true; stopping = stop(); }
  }
  async function resume() {
    if (pauses === 0) return false;
    pauses--;
    if (pauses > 0) return false;               // thao tác khác vẫn đang cần profile rảnh
    const s = stopping; stopping = null;
    if (s) await s;
    if (!wanted) return false;
    wanted = false;
    if (!proc) await start();
    return true;
  }
  async function restart() { await stop(); return start(); }
  // Bọc handler IPC: xong (kể cả ném lỗi) thì bật lại gateway nếu handler đã pause().
  const withPaused = (fn) => async (...a) => { try { return await fn(...a); } finally { await resume(); } };

  return { start, stop, pause, resume, restart, withPaused, running: () => !!proc, paused: () => pauses > 0 };
}

module.exports = { createGateway, killTree, EXIT_WAIT_MS };
