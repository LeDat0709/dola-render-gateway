// Vòng đời tiến trình gateway (uvicorn) ở MỘT chỗ: bật, tắt-và-chờ-thoát, tạm dừng để nhường Chrome
// profile cho đăng nhập / nạp cookie rồi TỰ BẬT LẠI. Không phụ thuộc Electron → test bằng node thường.
//
// Lỗi 12/9 00:13: "Đăng nhập lại" / "Thêm bằng Facebook" kill gateway để giải phóng profile nhưng không
// bật lại → log "gateway exit null" rồi im, app báo "Gateway tắt", người dùng tưởng tool hỏng.
const EXIT_WAIT_MS = 8000;

function createGateway({ spawnProc, isRemote = () => false, log = () => {} }) {
  let proc = null;
  let paused = null;   // promise "đã dừng xong" của thao tác đang chiếm profile; null = không có

  async function start() {
    if (isRemote()) return { ok: true, remote: true };   // server chạy trên VPS, máy này không spawn Python
    if (proc) return { ok: true, already: true };
    const r = spawnProc();                                // { proc } hoặc { error }
    if (!r || r.error || !r.proc) return { ok: false, error: (r && r.error) || "không spawn được gateway" };
    const p = (proc = r.proc);
    p.on("exit", (code) => { log(`===== gateway exit ${code} =====`); if (proc === p) proc = null; });
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
      p.kill();
    });
  }

  function pause() { if (proc) paused = stop(); }
  async function resume() {
    if (!paused) return false;
    const p = paused; paused = null;
    await p;
    if (!proc) await start();
    return true;
  }
  async function restart() { await stop(); return start(); }
  // Bọc handler IPC: xong (kể cả ném lỗi) thì bật lại gateway nếu handler đã pause().
  const withPaused = (fn) => async (...a) => { try { return await fn(...a); } finally { await resume(); } };

  return { start, stop, pause, resume, restart, withPaused, running: () => !!proc };
}

module.exports = { createGateway, EXIT_WAIT_MS };
