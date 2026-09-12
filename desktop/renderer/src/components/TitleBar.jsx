import { useEffect, useState } from "react";
import { cfg, inflightTasks } from "@/lib/api";

// Thanh tiêu đề tuỳ chỉnh = dải trạng thái cũ, kéo được cửa sổ (Electron titleBarStyle: hidden).
// macOS chừa chỗ cho 3 nút đèn giao thông bên trái; Windows chừa chỗ cho nút thu nhỏ/đóng overlay bên phải.
const IS_ELECTRON = typeof window !== "undefined" && !!window.api;
const IS_MAC = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);

export default function TitleBar({ health, latency }) {
  const up = !!health;
  const accs = health?.accounts || [];
  const credits = accs.reduce((s, a) => s + (a.remaining || 0), 0);
  const label = !up ? "Gateway tắt" : health.available ? "Gateway online" : accs.length === 0 ? "Online · chưa có nick" : "Online · nick bận/khoá";
  const remote = !!cfg.remote;
  const [running, setRunning] = useState(0);
  useEffect(() => {
    let alive = true;
    const tick = async () => { const t = await inflightTasks(); if (alive) setRunning(t.filter((x) => x.status === "processing").length); };
    tick(); const id = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(id); };
  }, [up]);
  const dot = up ? (health.available ? "bg-tertiary" : "bg-warn") : "bg-error";
  return (
    <div className="app-drag flex h-9 shrink-0 select-none items-center gap-3 border-b border-sidebar-border bg-surface-lowest font-mono text-[11.5px] text-muted-foreground"
      style={{ paddingLeft: IS_ELECTRON && IS_MAC ? 84 : 14, paddingRight: IS_ELECTRON && !IS_MAC ? 150 : 14 }}>
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
  );
}
