import { useEffect, useState } from "react";
import { FolderOpen, Folder } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, loadConfig } from "@/lib/api";

const Msg = ({ text }) => (text ? <div className={"mt-1 text-xs " + (text.startsWith("✓") ? "text-emerald-400" : "text-amber-400")}>{text}</div> : null);

export default function SettingsTab() {
  const [vid, setVid] = useState("…"); const [acc, setAcc] = useState("…"); const [msg, setMsg] = useState("");
  const [gp, setGp] = useState(""); const [gpMsg, setGpMsg] = useState("");
  const [rb, setRb] = useState(""); const [rk, setRk] = useState(""); const [ra, setRa] = useState(""); const [rMsg, setRMsg] = useState("");
  const [autoRetry, setAutoRetry] = useState(true); const [arMsg, setArMsg] = useState("");
  const load = () => {
    api.getVideoDir?.().then((r) => setVid(r?.abs || "downloads")).catch(() => {});
    api.getAccountsDir?.().then((r) => setAcc(r?.abs || r?.dir || "accounts")).catch(() => {});
    api.getGlobalProxy?.().then((r) => setGp(r?.proxy || "")).catch(() => {});
    api.getRemote?.().then((r) => { setRb(r?.base || ""); setRk(r?.apiKey || ""); setRa(r?.adminKey || ""); }).catch(() => {});
    api.getAutoRetry?.().then((r) => setAutoRetry(r?.on !== false)).catch(() => {});
  };
  useEffect(load, []);

  const toggleAutoRetry = async (e) => {
    const on = e.target.checked;
    setAutoRetry(on);
    const r = await api.setAutoRetry?.(on);
    if (!r?.ok) { setAutoRetry(!on); setArMsg("✗ " + (r?.error || "Bật server rồi thử lại")); return; }
    setArMsg(`✓ Đã ${on ? "BẬT" : "TẮT"} tự thử lại / xoay nick — áp dụng ngay cho server đang chạy.`);
  };

  const testProxy = async () => {
    setGpMsg("⏳ đang thử vào dola.com…");
    const r = await api.testProxy?.(gp);
    setGpMsg(r?.ok ? `✓ Vào được dola.com (HTTP ${r.status}) qua ${r.via}` : "✗ " + (r?.error || "lỗi không rõ"));
  };
  const saveProxy = async () => {
    const r = await api.setGlobalProxy?.(gp);
    setGpMsg(r?.ok ? `✓ Đã lưu proxy chung: ${r.proxy} → bấm "Tắt" rồi "Bật server" để áp dụng.` : "✗ " + (r?.error || "?"));
  };
  const testRemote = async () => {
    setRMsg("⏳ đang nối máy chủ…");
    const r = await api.testRemote?.(rb, rk, ra);
    setRMsg(r?.ok ? `✓ Nối được ${r.base} — ${r.nicks} nick trên máy chủ${r.available ? ", có nick sẵn sàng" : ""}` : "✗ " + (r?.error || "lỗi không rõ"));
  };
  const saveRemote = async () => {
    const r = await api.setRemote?.(rb, rk, ra);
    if (r?.ok) { await loadConfig(); load(); }   // cfg.base đổi ngay, mọi tab tự nói chuyện với máy chủ mới
    setRMsg(r?.ok ? `✓ Đã lưu: gateway = ${r.base}. ${rb.trim() ? "Không cần bấm Bật server nữa." : "Bấm \"Bật server\" để chạy trên máy này."}` : "✗ " + (r?.error || "?"));
  };

  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <CardHeader><CardTitle>Cài đặt</CardTitle></CardHeader>
        <CardContent className="space-y-5">
          <div>
            <Label>Máy chủ render từ xa (VPS Nhật/Hàn chạy server.py) — nhiều máy ở VN dùng chung, render không cần proxy</Label>
            <Input className="mt-1" value={rb} onChange={(e) => setRb(e.target.value)} placeholder="http://45.77.1.2:8000 — để trống = chạy server trên máy này" />
            <div className="mt-2 grid grid-cols-2 gap-2">
              <Input value={rk} onChange={(e) => setRk(e.target.value)} placeholder="API key (DOLA_API_KEYS)" />
              <Input value={ra} onChange={(e) => setRa(e.target.value)} placeholder="Admin key (DOLA_ADMIN_KEY)" />
            </div>
            <div className="mt-2 flex gap-2">
              <Button variant="outline" className="flex-1" onClick={testRemote}>Kiểm tra</Button>
              <Button variant="outline" onClick={saveRemote}>Lưu</Button>
            </div>
            <p className="mt-2 text-[11px] text-muted-foreground">Cài máy chủ: chạy <code>deploy/vps-setup.sh</code> trên VPS, nó in ra 3 dòng để dán vào đây. Đăng nhập nick vẫn làm trên máy này (cần proxy chung bên dưới), cookie tự đẩy lên máy chủ; video xong tự tải về thư mục video.</p>
            <Msg text={rMsg} />
          </div>
          <div>
            <Label>Proxy chung — ở Việt Nam BẮT BUỘC (exit node Nhật hoặc Hàn) khi server chạy trên máy này; dùng máy chủ từ xa thì chỉ cần cho cửa sổ đăng nhập</Label>
            <Input className="mt-1" value={gp} onChange={(e) => setGp(e.target.value)} placeholder="http://127.0.0.1:7890 · user:pass@host:port · host:port:user:pass · socks5://host:port" />
            <div className="mt-2 flex gap-2">
              <Button variant="outline" className="flex-1" onClick={testProxy}>Kiểm tra vào dola.com</Button>
              <Button variant="outline" onClick={saveProxy}>Lưu</Button>
            </div>
            <p className="mt-2 text-[11px] text-muted-foreground">Để trống = nối thẳng. Nick có proxy riêng (nút ⚙ ở từng nick) dùng proxy riêng.</p>
            <Msg text={gpMsg} />
          </div>
          <div>
            <label className="flex cursor-pointer items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={autoRetry} onChange={toggleAutoRetry} />
              <span>
                Tự thử lại / xoay nick khi lỗi
                <span className="block text-[11px] text-muted-foreground">
                  Bật: Dola báo "lỗi tạm thời" thì gửi lại 1 lần trên chính nick đó (tạo thêm 1 cuộc trò chuyện, không tốn lượt);
                  job không ghim nick thì thử nick khác. Tắt: lỗi là dừng ngay, không gửi lần 2.
                  Job trong bảng Studio luôn ghim đúng nick của dòng — không bao giờ tự sang nick khác.
                </span>
              </span>
            </label>
            <Msg text={arMsg} />
          </div>
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
