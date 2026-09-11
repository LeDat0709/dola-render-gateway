import { useEffect, useState } from "react";
import { FolderOpen, Folder } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";

export default function SettingsTab() {
  const [vid, setVid] = useState("…"); const [acc, setAcc] = useState("…"); const [msg, setMsg] = useState("");
  const load = () => { api.getVideoDir?.().then((r) => setVid(r?.abs || "downloads")).catch(() => {}); api.getAccountsDir?.().then((r) => setAcc(r?.abs || r?.dir || "accounts")).catch(() => {}); };
  useEffect(load, []);
  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <CardHeader><CardTitle>Cài đặt</CardTitle></CardHeader>
        <CardContent className="space-y-5">
          <div>
            <Label>Thư mục lưu video (tên có số thứ tự)</Label>
            <div className="mb-2 mt-1 break-all text-xs text-muted-foreground">{vid}</div>
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={async () => { const r = await api.chooseVideoDir?.(); if (r?.ok) { load(); setMsg("Đã đổi thư mục video → Tắt rồi Bật server để áp dụng."); } }}><FolderOpen className="h-4 w-4" />Chọn thư mục…</Button>
              <Button variant="outline" onClick={() => api.openDownloads?.()}>Mở</Button>
            </div>
          </div>
          <div>
            <Label>Thư mục lưu profile nick</Label>
            <div className="mb-2 mt-1 break-all text-xs text-muted-foreground">{acc}</div>
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={async () => { const r = await api.chooseAccountsDir?.(); if (r?.ok) { load(); setMsg("Đã đổi thư mục profile → Tắt rồi Bật server để áp dụng."); } }}><Folder className="h-4 w-4" />Chọn thư mục…</Button>
              <Button variant="outline" onClick={() => api.openAccountsDir?.()}>Mở</Button>
            </div>
            <p className="mt-2 text-[11px] text-muted-foreground">Đổi xong bấm "Tắt" rồi "Bật server" để áp dụng.</p>
          </div>
          {msg && <div className="text-xs text-emerald-400">{msg}</div>}
        </CardContent>
      </Card>
    </div>
  );
}
