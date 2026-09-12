import { useEffect, useState } from "react";
import { FolderOpen, Folder, Server, Network, SlidersHorizontal, HardDrive } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, cfg, loadConfig, adminConfig, health as fetchHealth } from "@/lib/api";

// Mỗi khối lưu riêng; mục nào cần Tắt rồi Bật server thì ghi ngay dưới nút Lưu.
// Chỉ hiện những gì backend thật có: máy chủ từ xa, proxy chung, tự thử lại, thư mục (IPC main.js) và
// tham số vận hành đọc từ /api/admin/config (chỉ đọc — số luồng đổi ở tab Studio, áp dụng ngay).
const Msg = ({ text }) => (text ? <div className={"text-xs " + (text.startsWith("✓") ? "text-tertiary" : text.startsWith("⏳") ? "text-info" : "text-warn")}>{text}</div> : null);
const Card = ({ icon, title, right, children }) => (
  <div className="flex flex-col gap-3.5 rounded-xl bg-surface-low p-5">
    <div className="flex items-center gap-2">
      {icon}<span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{title}</span>
      <span className="ml-auto" />{right}
    </div>
    {children}
  </div>
);
const Help = ({ children }) => <p className="text-[11px] leading-relaxed text-muted-foreground">{children}</p>;
const Row = ({ label, value, hint }) => (
  <div className="flex items-center gap-3 text-[13px]"><span className="flex-1">{label}{hint && <span className="ml-1 text-[11px] text-muted-foreground">{hint}</span>}</span>
    <span className="rounded-md border border-input px-2.5 py-1 font-mono text-[12.5px] tabular-nums">{value ?? "—"}</span></div>
);

export default function SettingsTab() {
  const [vid, setVid] = useState("…"); const [acc, setAcc] = useState("…"); const [dirMsg, setDirMsg] = useState("");
  const [gp, setGp] = useState(""); const [gpMsg, setGpMsg] = useState("");
  const [rb, setRb] = useState(""); const [rk, setRk] = useState(""); const [ra, setRa] = useState(""); const [rMsg, setRMsg] = useState("");
  const [autoRetry, setAutoRetry] = useState(true); const [arMsg, setArMsg] = useState("");
  const [srv, setSrv] = useState(null);     // cấu hình server đang chạy (/api/admin/config) hoặc null khi server tắt
  const [up, setUp] = useState(false);
  const [ver, setVer] = useState(null);
  const load = () => {
    api.getVideoDir?.().then((r) => setVid(r?.abs || "downloads")).catch(() => {});
    api.getAccountsDir?.().then((r) => setAcc(r?.abs || r?.dir || "accounts")).catch(() => {});
    api.getGlobalProxy?.().then((r) => setGp(r?.proxy || "")).catch(() => {});
    api.getRemote?.().then((r) => { setRb(r?.base || ""); setRk(r?.apiKey || ""); setRa(r?.adminKey || ""); }).catch(() => {});
    api.getAutoRetry?.().then((r) => setAutoRetry(r?.on !== false)).catch(() => {});
    api.getVersion?.().then(setVer).catch(() => {});
    fetchHealth().then((h) => setUp(!!h));
    adminConfig().then(setSrv).catch(() => setSrv(null));
  };
  useEffect(load, []);

  const remote = rb.trim() !== "";
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
  const saveRemote = async (base = rb) => {
    const r = await api.setRemote?.(base, rk, ra);
    if (r?.ok) { await loadConfig(); load(); }   // cfg.base đổi ngay, mọi tab tự nói chuyện với máy chủ mới
    setRMsg(r?.ok ? `✓ Đã lưu: gateway = ${r.base}. ${base.trim() ? "Không cần bấm Bật server nữa." : "Bấm \"Bật server\" để chạy trên máy này."}` : "✗ " + (r?.error || "?"));
  };
  const chooseDir = async (fn) => { const r = await fn?.(); if (r?.ok) { load(); setDirMsg("✓ Đã đổi thư mục → bấm \"Tắt\" rồi \"Bật server\" để áp dụng."); } };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight">Cài đặt</h1>
        <span className="font-mono text-[11px] text-muted-foreground">Mỗi khối lưu riêng · khối nào cần khởi động lại server có ghi chú ngay dưới nút Lưu</span>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card icon={<Server className="h-4 w-4 text-primary" />} title="Máy chủ"
              right={<Badge variant={remote ? "info" : up ? "success" : "secondary"}>{remote ? "Máy chủ từ xa" : up ? "Đang chạy trên máy này" : "Server trên máy này đang tắt"}</Badge>}>
          <label className="flex cursor-pointer items-start gap-2.5 text-[13px]">
            <input type="radio" className="mt-1" name="srv" checked={!remote} onChange={() => { if (remote) saveRemote(""); }} />
            <span><span className="font-medium">Chạy server trên máy này</span><span className="block text-[11px] text-muted-foreground">Cần proxy Nhật/Hàn ở khối Mạng khi máy ở Việt Nam.</span></span>
          </label>
          <label className="flex cursor-pointer items-start gap-2.5 text-[13px]">
            <input type="radio" className="mt-1" name="srv" checked={remote} onChange={() => setRb(rb || "http://")} />
            <span><span className="font-medium">Máy chủ từ xa (VPS Nhật/Hàn chạy server.py)</span><span className="block text-[11px] text-muted-foreground">Nhiều máy ở VN dùng chung, render không cần proxy.</span></span>
          </label>
          <div className={"space-y-2 " + (remote ? "" : "opacity-50")}>
            <Input value={rb} onChange={(e) => setRb(e.target.value)} placeholder="http://45.77.1.2:8000" />
            <div className="grid grid-cols-2 gap-2">
              <Input value={rk} onChange={(e) => setRk(e.target.value)} placeholder="Khoá API (DOLA_API_KEYS)" />
              <Input value={ra} onChange={(e) => setRa(e.target.value)} placeholder="Khoá admin (DOLA_ADMIN_KEY)" />
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" onClick={testRemote} disabled={!remote}>Kiểm tra</Button>
            <Button variant="outline" onClick={() => saveRemote()}>Lưu</Button>
          </div>
          <Msg text={rMsg} />
          <Help>Cài máy chủ: chạy <code className="font-mono text-primary">deploy/vps-setup.sh</code> trên VPS, nó in ra 3 dòng để dán vào đây. Đăng nhập nick vẫn làm trên máy này, cookie tự đẩy lên máy chủ; video xong tự tải về thư mục video.</Help>
        </Card>

        <Card icon={<Network className="h-4 w-4 text-tertiary" />} title="Mạng"
              right={<Badge variant={gp.trim() ? "info" : "secondary"}>{gp.trim() ? "có proxy chung" : "nối thẳng"}</Badge>}>
          <div className="space-y-1.5">
            <div className="text-[13px] font-medium">Proxy chung</div>
            <Input value={gp} onChange={(e) => setGp(e.target.value)} placeholder="user:pass@host:port · socks5://host:port · host:port:user:pass" />
            <Help>Ở Việt Nam bắt buộc có exit node Nhật hoặc Hàn khi server chạy trên máy này. Để trống = nối thẳng. Nick có proxy riêng (tab Proxy) dùng proxy riêng.</Help>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" onClick={testProxy}>Kiểm tra vào dola.com</Button>
            <Button variant="outline" onClick={saveProxy}>Lưu</Button>
          </div>
          <Msg text={gpMsg} />
          <Help>Đổi proxy xong: bấm "Tắt" rồi "Bật server" để áp dụng.</Help>
        </Card>

        <Card icon={<SlidersHorizontal className="h-4 w-4 text-info" />} title="Vận hành"
              right={<span className="font-mono text-[11px] text-muted-foreground">{srv ? "đọc từ server đang chạy" : "server tắt — chưa đọc được"}</span>}>
          <div className="space-y-2.5">
            <Row label="Nick chạy song song" value={srv?.max_concurrency} hint="đổi ở tab Studio, áp dụng ngay" />
            <Row label="Giãn nhịp gửi lệnh" value={srv ? `${srv.submit_gap}s + ngẫu nhiên 0–${srv.submit_jitter}s` : null} />
            <Row label="Giới hạn video mỗi nick mỗi ngày" value={srv?.daily_limit} hint="reset 0h giờ Nhật" />
            <Row label="Chờ video tối đa" value={srv ? `${Math.round(srv.video_timeout / 60)} phút` : null} />
            <Row label="Xoay nick tối đa mỗi job" value={srv?.max_rotate} />
          </div>
          <label className="flex cursor-pointer items-start gap-2.5 border-t border-surface-high pt-3 text-[13px]">
            <input type="checkbox" className="mt-1" checked={autoRetry} onChange={toggleAutoRetry} />
            <span><span className="font-medium">Tự thử lại / xoay nick khi lỗi</span>
              <span className="block text-[11px] leading-relaxed text-muted-foreground">Bật: Dola báo lỗi tạm thời thì gửi lại 1 lần trên chính nick đó (không tốn lượt); job không ghim nick thì thử nick khác. Tắt: lỗi là dừng ngay. Job trong Studio luôn ghim đúng nick của thẻ.</span></span>
          </label>
          <Msg text={arMsg} />
          <Help>Các số khác nằm trong <code className="font-mono">.env.local</code> (DOLA_DAILY_LIMIT, DOLA_VIDEO_TIMEOUT, DOLA_SUBMIT_GAP) — đổi xong bấm "Tắt" rồi "Bật server".</Help>
        </Card>

        <Card icon={<HardDrive className="h-4 w-4 text-warn" />} title="Thư mục">
          <div className="space-y-1.5">
            <div className="text-[13px] font-medium">Thư mục lưu video <span className="text-[11px] font-normal text-muted-foreground">(tên file có số thứ tự)</span></div>
            <div className="break-all font-mono text-[12px] text-muted-foreground">{vid}</div>
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => chooseDir(api.chooseVideoDir)}><FolderOpen className="h-4 w-4" />Chọn thư mục…</Button>
              <Button variant="outline" onClick={() => api.openDownloads?.()}>Mở</Button>
            </div>
          </div>
          <div className="space-y-1.5">
            <div className="text-[13px] font-medium">Thư mục lưu profile nick</div>
            <div className="break-all font-mono text-[12px] text-muted-foreground">{acc}</div>
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => chooseDir(api.chooseAccountsDir)}><Folder className="h-4 w-4" />Chọn thư mục…</Button>
              <Button variant="outline" onClick={() => api.openAccountsDir?.()}>Mở</Button>
            </div>
          </div>
          <Msg text={dirMsg} />
          <Help>Đổi thư mục xong: bấm "Tắt" rồi "Bật server" để áp dụng.</Help>
        </Card>
      </div>

      <div className="flex flex-wrap items-center gap-3 px-1 font-mono text-[11px] text-muted-foreground">
        <span>Dola Studio {ver?.version || "—"}{ver ? ` · ${ver.platform} ${ver.arch}${ver.packaged ? "" : " · chạy từ mã nguồn"}` : ""}</span>
        <span>·</span>
        <span>gateway {cfg.base}</span>
        <span className="ml-auto" />
        <Button variant="ghost" size="sm" className="h-7" onClick={() => api.openLogs?.()}>Mở file log</Button>
      </div>
    </div>
  );
}
