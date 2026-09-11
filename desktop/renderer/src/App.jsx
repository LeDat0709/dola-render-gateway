import { useCallback, useEffect, useRef, useState } from "react";
import { Clapperboard, User, Settings2, BarChart3 } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import Header from "@/components/Header";
import StudioTab from "@/components/StudioTab";
import AccountsTab from "@/components/AccountsTab";
import SettingsTab from "@/components/SettingsTab";
import ReportTab from "@/components/ReportTab";
import { loadConfig, health as fetchHealth } from "@/lib/api";

export default function App() {
  const [health, setHealth] = useState(null);
  const [video, setVideo] = useState(null);
  const typingRef = useRef(false);
  const refresh = useCallback(async () => { setHealth(await fetchHealth()); }, []);

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
    <div className="min-h-screen">
      <Header health={health} onRefresh={refresh} />
      <main className="mx-auto max-w-[1500px] px-7 py-6">
        <Tabs defaultValue="make">
          <TabsList className="mb-5">
            <TabsTrigger value="make"><Clapperboard className="h-4 w-4" />Tạo video</TabsTrigger>
            <TabsTrigger value="acct"><User className="h-4 w-4" />Tài khoản</TabsTrigger>
            <TabsTrigger value="report"><BarChart3 className="h-4 w-4" />Báo cáo</TabsTrigger>
            <TabsTrigger value="settings"><Settings2 className="h-4 w-4" />Cài đặt</TabsTrigger>
          </TabsList>
          <TabsContent value="make" forceMount>
            <div className="rounded-xl border bg-card p-6">
              <h2 className="mb-4 text-sm font-semibold text-muted-foreground">STUDIO — mỗi nick một dòng, prompt riêng</h2>
              <StudioTab health={health} onRefresh={refresh} onPlay={(u) => setVideo(u)} />
            </div>
          </TabsContent>
          <TabsContent value="acct" forceMount><AccountsTab onRefresh={refresh} /></TabsContent>
          <TabsContent value="report" forceMount><ReportTab onPlay={(u) => setVideo(u)} /></TabsContent>
          <TabsContent value="settings" forceMount><SettingsTab /></TabsContent>
        </Tabs>
      </main>

      <Dialog open={!!video} onOpenChange={(o) => !o && setVideo(null)}>
        <DialogContent>
          {video && <video key={video} src={video} controls autoPlay playsInline className="max-h-[80vh] w-full rounded-md bg-black" />}
        </DialogContent>
      </Dialog>
    </div>
  );
}
