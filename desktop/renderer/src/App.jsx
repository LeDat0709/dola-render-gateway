import { useCallback, useEffect, useRef, useState } from "react";
import { Clapperboard, Users, Settings2, LayoutDashboard, Network, Film } from "lucide-react";
import VideoLibrary from "@/components/VideoLibrary";
import { Tabs, TabsContent } from "@/components/ui/tabs";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Toaster } from "@/components/ui/toast";
import TitleBar from "@/components/TitleBar";
import Sidebar from "@/components/Sidebar";
import Header from "@/components/Header";
import StudioTab from "@/components/StudioTab";
import AccountsTab from "@/components/AccountsTab";
import SettingsTab from "@/components/SettingsTab";
import OverviewTab from "@/components/OverviewTab";
import ProxyTab from "@/components/ProxyTab";
import { loadConfig, health as fetchHealth } from "@/lib/api";

const NAV = [
  { value: "overview", label: "Tổng quan", icon: LayoutDashboard, subtitle: "Máy chủ, nick và job đang chạy", group: "Sáng tạo" },
  { value: "make", label: "Studio", icon: Clapperboard, subtitle: "Mỗi nick một thẻ, prompt riêng", group: "Sáng tạo" },
  { value: "video", label: "Video", icon: Film, subtitle: "Thư viện video đã tạo", group: "Sáng tạo" },
  { value: "acct", label: "Kho tài khoản", icon: Users, subtitle: "Nick, cookie, credit, proxy riêng", group: "Quản lý" },
  { value: "proxy", label: "Proxy", icon: Network, subtitle: "Kho proxy và phân bổ theo nick", group: "Quản lý" },
  { value: "settings", label: "Cài đặt", icon: Settings2, subtitle: "Server, proxy chung, tuỳ chọn", group: "Hệ thống" },
];

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

  const current = NAV.find((n) => n.value === tab) || NAV[0];
  return (
    <Tabs value={tab} onValueChange={setTab} orientation="vertical" className="flex h-screen flex-col overflow-hidden bg-background">
      <TitleBar health={health} latency={latency} />
      <div className="flex min-h-0 flex-1">
        <Sidebar items={NAV} health={health} />
        <div className="flex min-w-0 flex-1 flex-col">
          <Header title={current.label} subtitle={current.subtitle} onRefresh={refresh} />
          <main className="min-h-0 flex-1 overflow-auto px-6 py-5">
            <div className="mx-auto max-w-[1500px]">
              <TabsContent value="overview" forceMount className="mt-0"><OverviewTab health={health} onPlay={(u) => setVideo(u)} onGo={setTab} active={tab === "overview"} /></TabsContent>
              <TabsContent value="make" forceMount className="mt-0">
                <div className="rounded-xl border bg-card p-5"><StudioTab health={health} onRefresh={refresh} onPlay={(u) => setVideo(u)} /></div>
              </TabsContent>
              <TabsContent value="video" forceMount className="mt-0"><VideoLibrary active={tab === "video"} onPlay={(u) => setVideo(u)} /></TabsContent>
              <TabsContent value="acct" forceMount className="mt-0"><AccountsTab onRefresh={refresh} active={tab === "acct"} /></TabsContent>
              <TabsContent value="proxy" forceMount className="mt-0"><ProxyTab active={tab === "proxy"} /></TabsContent>
              <TabsContent value="settings" forceMount className="mt-0"><SettingsTab active={tab === "settings"} /></TabsContent>
            </div>
          </main>
        </div>
      </div>

      <Dialog open={!!video} onOpenChange={(o) => !o && setVideo(null)}>
        <DialogContent>
          {video && <video key={video} src={video} controls autoPlay playsInline className="max-h-[80vh] w-full rounded-md bg-black" />}
        </DialogContent>
      </Dialog>
      <Toaster />
    </Tabs>
  );
}
