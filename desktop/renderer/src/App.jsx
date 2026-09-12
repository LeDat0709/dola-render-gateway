import { useCallback, useEffect, useRef, useState } from "react";
import { Clapperboard, Users, Settings2, LayoutDashboard, Network, Film } from "lucide-react";
import VideoLibrary from "@/components/VideoLibrary";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import Header from "@/components/Header";
import StudioTab from "@/components/StudioTab";
import AccountsTab from "@/components/AccountsTab";
import SettingsTab from "@/components/SettingsTab";
import OverviewTab from "@/components/OverviewTab";
import ProxyTab from "@/components/ProxyTab";
import { loadConfig, health as fetchHealth } from "@/lib/api";

export default function App() {
  const [health, setHealth] = useState(null);
  const [latency, setLatency] = useState(null);
  const [video, setVideo] = useState(null);
  const [tab, setTab] = useState("overview");
  const typingRef = useRef(false);
  const refresh = useCallback(async () => {
    const t0 = performance.now();
    const h = await fetchHealth();
    setHealth(h); setLatency(h ? Math.round(performance.now() - t0) : null);
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => { await loadConfig(); if (alive) refresh(); })();
    const t = setInterval(() => { if (!typingRef.current) refresh(); }, 1500);
    return () => { alive = false; clearInterval(t); };
  }, [refresh]);

  // Chỉ tạm ngừng /health poll khi đang gõ Ô GHI CHÚ (data-note). Trước đây ngừng cho MỌI
  // input/select/textarea nên gõ ô tìm kiếm hay mở dropdown lọc là bảng đứng hình luôn.
  useEffect(() => {
    const on = (e) => (typingRef.current = !!e.target?.hasAttribute?.("data-note"));
    const off = () => (typingRef.current = false);
    document.addEventListener("focusin", on); document.addEventListener("focusout", off);
    return () => { document.removeEventListener("focusin", on); document.removeEventListener("focusout", off); };
  }, []);

  return (
    <Tabs value={tab} onValueChange={setTab} className="min-h-screen">
      <Header health={health} latency={latency} onRefresh={refresh}>
        <TabsList>
          <TabsTrigger value="overview"><LayoutDashboard className="h-4 w-4" />Tổng quan</TabsTrigger>
          <TabsTrigger value="make"><Clapperboard className="h-4 w-4" />Studio</TabsTrigger>
          <TabsTrigger value="video"><Film className="h-4 w-4" />Video</TabsTrigger>
          <TabsTrigger value="acct"><Users className="h-4 w-4" />Kho tài khoản</TabsTrigger>
          <TabsTrigger value="proxy"><Network className="h-4 w-4" />Proxy</TabsTrigger>
          <TabsTrigger value="settings"><Settings2 className="h-4 w-4" />Cài đặt</TabsTrigger>
        </TabsList>
      </Header>
      <main className="mx-auto max-w-[1500px] px-6 py-5">
        <TabsContent value="overview" forceMount><OverviewTab health={health} onPlay={(u) => setVideo(u)} onGo={setTab} active={tab === "overview"} /></TabsContent>
        <TabsContent value="make" forceMount>
          <div className="rounded-xl bg-surface-low p-5">
            <h2 className="mb-4 font-mono text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Studio — mỗi nick một thẻ, prompt riêng</h2>
            <StudioTab health={health} onRefresh={refresh} onPlay={(u) => setVideo(u)} />
          </div>
        </TabsContent>
        <TabsContent value="video" forceMount><VideoLibrary active={tab === "video"} onPlay={(u) => setVideo(u)} /></TabsContent>
        <TabsContent value="acct" forceMount><AccountsTab onRefresh={refresh} active={tab === "acct"} /></TabsContent>
        <TabsContent value="proxy" forceMount><ProxyTab active={tab === "proxy"} /></TabsContent>
        <TabsContent value="settings" forceMount><SettingsTab active={tab === "settings"} /></TabsContent>
      </main>

      <Dialog open={!!video} onOpenChange={(o) => !o && setVideo(null)}>
        <DialogContent>
          {video && <video key={video} src={video} controls autoPlay playsInline className="max-h-[80vh] w-full rounded-md bg-black" />}
        </DialogContent>
      </Dialog>
    </Tabs>
  );
}
