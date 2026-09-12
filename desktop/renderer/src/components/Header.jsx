import { useEffect, useRef, useState } from "react";
import { Power, PowerOff, FolderOpen, FileText, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, cfg } from "@/lib/api";

// Thanh công cụ của trang đang mở (tên trang + mô tả bên trái, nút thao tác bên phải).
// Dải trạng thái đã chuyển lên TitleBar, logo và điều hướng đã chuyển sang Sidebar.
export default function Header({ title, subtitle, onRefresh }) {
  const [logOpen, setLogOpen] = useState(false);
  const [log, setLog] = useState("");
  const timer = useRef(null);
  const remote = !!cfg.remote;

  useEffect(() => {
    if (!logOpen) { if (timer.current) clearInterval(timer.current); return; }
    const tick = async () => { try { const r = await api.tailLogs?.(400); setLog(r?.ok ? (r.text || "(log trống)") : "lỗi đọc log: " + (r?.error || "?")); } catch {} };
    tick(); timer.current = setInterval(tick, 2000);
    return () => timer.current && clearInterval(timer.current);
  }, [logOpen]);

  return (
    <>
      <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-background/95 px-6 backdrop-blur">
        <div className="min-w-0 leading-tight">
          <h1 className="truncate text-[15px] font-semibold tracking-tight">{title}</h1>
          {subtitle && <p className="truncate font-mono text-[11px] text-muted-foreground">{subtitle}</p>}
        </div>
        <span className="ml-auto" />
        {!remote && <>
          <Button variant="outline" size="sm" onClick={async () => { await api.startGateway?.(); setTimeout(onRefresh, 1200); }}><Power className="h-3.5 w-3.5" /> Bật server</Button>
          <Button variant="outline" size="sm" onClick={async () => { await api.stopGateway?.(); onRefresh(); }}><PowerOff className="h-3.5 w-3.5" /> Tắt</Button>
        </>}
        <Button variant="outline" size="sm" onClick={() => api.openDownloads?.()}><FolderOpen className="h-3.5 w-3.5" /> Thư mục video</Button>
        <Button variant={logOpen ? "secondary" : "outline"} size="sm" onClick={() => setLogOpen((v) => !v)}><FileText className="h-3.5 w-3.5" /> Log</Button>
      </header>
      {logOpen && (
        <div className="shrink-0 border-b bg-card px-6 py-3">
          <div className="mb-2 flex items-center gap-2 text-xs font-medium">
            Nhật ký gateway (mới nhất ở dưới, tự cập nhật)
            <span className="flex-1" />
            <Button variant="ghost" size="sm" onClick={() => api.openLogs?.()}>Mở file</Button>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setLogOpen(false)}><X className="h-4 w-4" /></Button>
          </div>
          <pre ref={(el) => el && (el.scrollTop = el.scrollHeight)} className="m-0 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-surface-lowest p-3 font-mono text-[11px] leading-relaxed text-on-variant">{log}</pre>
        </div>
      )}
    </>
  );
}
