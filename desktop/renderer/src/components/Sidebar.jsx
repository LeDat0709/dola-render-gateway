import { useEffect, useState } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cfg } from "@/lib/api";
import { cn } from "@/lib/utils";

const KEY = "dolaSidebarCollapsed";

// Sidebar kiểu shadcn-admin: logo, điều hướng dọc (vẫn là TabsTrigger của Radix nên state tab không đổi),
// thu gọn còn icon và nhớ lựa chọn. Chân sidebar: trạng thái gateway.
export default function Sidebar({ items, health }) {
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem(KEY) === "1"; } catch { return false; } });
  useEffect(() => { try { localStorage.setItem(KEY, collapsed ? "1" : "0"); } catch {} }, [collapsed]);
  const up = !!health;
  const remote = !!cfg.remote;
  return (
    <aside className={cn("flex h-full shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-200", collapsed ? "w-14" : "w-56")}>
      <div className={cn("flex h-14 items-center gap-2.5 border-b border-sidebar-border", collapsed ? "justify-center px-0" : "px-3")}>
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-[#8B5CF6] to-[#3B82F6] text-[15px] font-bold text-white shadow-[0_4px_14px_rgba(139,92,246,0.35)]">D</span>
        {!collapsed && (
          <div className="min-w-0 leading-tight">
            <div className="flex items-center gap-1.5 text-[14px] font-semibold tracking-tight">Dola Studio
              <span className="rounded bg-primary/15 px-1.5 py-px font-mono text-[9.5px] font-semibold uppercase text-primary">{remote ? "remote" : "desktop"}</span>
            </div>
            <div className="truncate font-mono text-[10.5px] text-muted-foreground">Tạo video Dola hàng loạt</div>
          </div>
        )}
      </div>
      <TabsList className="flex h-auto flex-col items-stretch gap-0.5 rounded-none bg-transparent p-2">
        {items.map(({ value, label, icon: Icon }) => (
          <TabsTrigger key={value} value={value} title={collapsed ? label : undefined}
            className={cn("h-9 justify-start gap-2.5 rounded-md px-2.5 text-[13px] font-medium text-sidebar-foreground/70",
              "hover:bg-sidebar-accent/60 hover:text-sidebar-foreground",
              "data-[state=active]:bg-sidebar-accent data-[state=active]:text-sidebar-primary data-[state=active]:shadow-none",
              collapsed && "justify-center px-0")}>
            <Icon className="h-4 w-4 shrink-0" />
            {!collapsed && <span className="truncate">{label}</span>}
          </TabsTrigger>
        ))}
      </TabsList>
      <div className="mt-auto space-y-1 border-t border-sidebar-border p-2">
        <div className={cn("flex items-center gap-2 rounded-md px-2 py-1.5 font-mono text-[11px] text-muted-foreground", collapsed && "justify-center px-0")}
          title={up ? "Gateway đang chạy" : "Gateway tắt"}>
          <span className={cn("h-2 w-2 shrink-0 rounded-full", up ? (health.available ? "bg-tertiary" : "bg-warn") : "bg-error")} />
          {!collapsed && <span className="truncate">{up ? (health.available ? "Gateway online" : "Online · nick bận/khoá") : "Gateway tắt"}</span>}
        </div>
        <button type="button" onClick={() => setCollapsed((v) => !v)} title={collapsed ? "Mở rộng" : "Thu gọn"}
          className={cn("flex h-8 w-full items-center gap-2 rounded-md px-2 text-[12px] text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-foreground", collapsed && "justify-center px-0")}>
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          {!collapsed && "Thu gọn"}
        </button>
      </div>
    </aside>
  );
}
