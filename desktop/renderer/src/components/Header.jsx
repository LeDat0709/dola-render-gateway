import { useEffect, useRef, useState } from "react";
import { Power, PowerOff, FolderOpen, FileText, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, cfg, inflightTasks } from "@/lib/api";

// Thanh trên theo bản Stitch: dải trạng thái (gateway, độ trễ, số nick, credit, đang render) + tên app.
// Số liệu lấy từ /health (đã có sẵn ở App) và /api/admin/tasks (mỗi 5s) — không thêm endpoint.
export default function Header({ health, latency, onRefresh, children }) {
  const up = !!health;
  const accs = health?.accounts || [];
  const credits = accs.reduce((s, a) => s + (a.remaining || 0), 0);
  const label = !up ? "Gateway tắt" : health.available ? "Gateway online" : accs.length === 0 ? "Online · chưa có nick" : "Online · nick bận/khoá";
  const [running, setRunning] = useState(0);
  const [logOpen, setLogOpen] = useState(false);
  const [log, setLog] = useState("");
  const timer = useRef(null);
  const remote = !!cfg.remote;

  useEffect(() => {
    let alive = true;
    const tick = async () => { const t = await inflightTasks(); if (alive) setRunning(t.filter((x) => x.status === "processing").length); };
    tick(); const id = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(id); };
  }, [up]);

  useEffect(() => {
    if (!logOpen) { if (timer.current) clearInterval(timer.current); return; }
    const tick = async () => { try { const r = await api.tailLogs?.(400); setLog(r?.ok ? (r.text || "(log trống)") : "lỗi đọc log: " + (r?.error || "?")); } catch {} };
    tick(); timer.current = setInterval(tick, 2000);
    return () => timer.current && clearInterval(timer.current);
  }, [logOpen]);

  const dot = up ? (health.available ? "bg-tertiary" : "bg-warn") : "bg-error";
  return (
    <>
      <header className="sticky top-0 z-20 border-b border-surface-high bg-surface-low/95 backdrop-blur">
        <div className="flex h-8 items-center gap-3 bg-surface-lowest px-5 font-mono text-[11.5px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className={"h-1.5 w-1.5 rounded-full " + dot + (up && health.available ? " animate-pulse" : "")} />
            <span className={up ? "text-tertiary" : "text-error"}>{label}</span>
          </span>
          <span className="truncate">{cfg.base}</span>
          {latency != null && up && <span className="rounded bg-surface-high px-1.5 py-0.5 text-tertiary">{latency}ms</span>}
          {remote && <span className="rounded bg-info/15 px-1.5 py-0.5 text-info">server từ xa</span>}
          <span className="mx-auto" />
          <span><b className="text-foreground">{accs.length}</b> nick</span>
          <span>•</span>
          <span><b className="text-foreground">{credits}</b> credit</span>
          <span>•</span>
          <span className="flex items-center gap-1.5">
            {running > 0 && <span className="h-1.5 w-1.5 animate-ping rounded-full bg-primary" />}
            <b className={running ? "text-primary" : "text-foreground"}>{running}</b> đang render
          </span>
        </div>
        <div className="flex items-center gap-4 px-5 py-2.5">
          <div className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-[#8B5CF6] to-[#3B82F6] text-[15px] font-bold text-white">D</span>
            <div className="leading-tight">
              <div className="flex items-center gap-1.5 text-[15px] font-semibold tracking-tight">Dola Studio
                <span className="rounded bg-primary-container px-1.5 py-px font-mono text-[10px] font-semibold uppercase text-[#340080]">{remote ? "remote" : "desktop"}</span>
              </div>
              <div className="font-mono text-[11px] text-muted-foreground">Tạo video Dola hàng loạt</div>
            </div>
          </div>
          {children}
          <span className="ml-auto" />
          {!remote && <>
            <Button variant="outline" size="sm" onClick={async () => { await api.startGateway?.(); setTimeout(onRefresh, 1200); }}><Power className="h-3.5 w-3.5" /> Bật server</Button>
            <Button variant="outline" size="sm" onClick={async () => { await api.stopGateway?.(); onRefresh(); }}><PowerOff className="h-3.5 w-3.5" /> Tắt</Button>
          </>}
          <Button variant="outline" size="sm" onClick={() => api.openDownloads?.()}><FolderOpen className="h-3.5 w-3.5" /> Thư mục video</Button>
          <Button variant="outline" size="sm" onClick={() => setLogOpen((v) => !v)}><FileText className="h-3.5 w-3.5" /> Log</Button>
        </div>
      </header>
      {logOpen && (
        <div className="mx-auto max-w-[1500px] px-6 pt-3">
          <div className="rounded-lg border bg-surface-low p-3">
            <div className="mb-2 flex items-center gap-2 text-xs font-medium">
              Nhật ký gateway (mới nhất ở dưới, tự cập nhật)
              <span className="flex-1" />
              <Button variant="ghost" size="sm" onClick={() => api.openLogs?.()}>Mở file</Button>
              <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setLogOpen(false)}><X className="h-4 w-4" /></Button>
            </div>
            <pre ref={(el) => el && (el.scrollTop = el.scrollHeight)} className="m-0 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-surface-lowest p-3 font-mono text-[11px] leading-relaxed text-on-variant">{log}</pre>
          </div>
        </div>
      )}
    </>
  );
}
