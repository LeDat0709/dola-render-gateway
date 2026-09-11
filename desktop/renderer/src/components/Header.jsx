import { useEffect, useRef, useState } from "react";
import { Power, PowerOff, FolderOpen, FileText, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

export default function Header({ health, onRefresh }) {
  const up = !!health;
  const label = up ? (health.available ? "server chạy · có nick sẵn sàng" : "server chạy · nick bận/khoá") : "server tắt";
  const [logOpen, setLogOpen] = useState(false);
  const [log, setLog] = useState("");
  const timer = useRef(null);

  useEffect(() => {
    if (!logOpen) { if (timer.current) clearInterval(timer.current); return; }
    const tick = async () => { try { const r = await api.tailLogs?.(400); setLog(r?.ok ? (r.text || "(log trống)") : "lỗi đọc log: " + (r?.error || "?")); } catch {} };
    tick(); timer.current = setInterval(tick, 2000);
    return () => timer.current && clearInterval(timer.current);
  }, [logOpen]);

  return (
    <>
      <header className="sticky top-0 z-20 flex items-center gap-3 border-b bg-background/85 px-6 py-3 backdrop-blur">
        <div className="mr-auto flex items-center gap-2 text-[15px] font-semibold tracking-tight">
          <span className="text-indigo-400">◆</span> Dola Studio
        </div>
        <span className={"h-2.5 w-2.5 rounded-full " + (up ? (health.available ? "bg-emerald-500 shadow-[0_0_0_3px_rgba(16,185,129,.18)]" : "bg-amber-500") : "bg-red-500")} />
        <span className="mr-2 text-xs text-muted-foreground">{label}</span>
        <Button variant="outline" size="sm" onClick={async () => { await api.startGateway?.(); setTimeout(onRefresh, 1200); }}>
          <Power className="h-3.5 w-3.5" /> Bật server
        </Button>
        <Button variant="outline" size="sm" onClick={async () => { await api.stopGateway?.(); onRefresh(); }}>
          <PowerOff className="h-3.5 w-3.5" /> Tắt
        </Button>
        <Button variant="outline" size="sm" onClick={() => api.openDownloads?.()}>
          <FolderOpen className="h-3.5 w-3.5" /> Thư mục video
        </Button>
        <Button variant="outline" size="sm" onClick={() => setLogOpen((v) => !v)}>
          <FileText className="h-3.5 w-3.5" /> Log
        </Button>
      </header>
      {logOpen && (
        <div className="mx-auto max-w-[1500px] px-7 pt-3">
          <div className="rounded-lg border bg-muted/40 p-3">
            <div className="mb-2 flex items-center gap-2 text-xs font-medium">
              Nhật ký gateway (mới nhất ở dưới, tự cập nhật)
              <span className="flex-1" />
              <Button variant="ghost" size="sm" onClick={() => api.openLogs?.()}>Mở file</Button>
              <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setLogOpen(false)}><X className="h-4 w-4" /></Button>
            </div>
            <pre ref={(el) => el && (el.scrollTop = el.scrollHeight)} className="m-0 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-black/50 p-3 font-mono text-[11px] leading-relaxed text-zinc-300">{log}</pre>
          </div>
        </div>
      )}
    </>
  );
}
