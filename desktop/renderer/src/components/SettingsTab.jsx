import { useEffect, useState } from "react";
import { FolderOpen, Folder, Server, Network, SlidersHorizontal, HardDrive } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, cfg, loadConfig, adminConfig, health as fetchHealth, setBurnNicks, setCdpLaunch, setHeadless } from "@/lib/api";

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

export default function SettingsTab({ active = true }) {
  const [vid, setVid] = useState("…"); const [acc, setAcc] = useState("…"); const [dirMsg, setDirMsg] = useState("");
  const [gp, setGp] = useState(""); const [gpMsg, setGpMsg] = useState("");
  const [lane, setLane] = useState(null); const [laneBusy, setLaneBusy] = useState(false);
  const [rb, setRb] = useState(""); const [rk, setRk] = useState(""); const [ra, setRa] = useState(""); const [rMsg, setRMsg] = useState("");
  const [autoRetry, setAutoRetry] = useState(true); const [arMsg, setArMsg] = useState("");
  const [burn, setBurn] = useState(false); const [burnMsg, setBurnMsg] = useState("");
  const [httpEngine, setHttpEngine] = useState(false); const [engMsg, setEngMsg] = useState("");
  const [cdp, setCdp] = useState(false); const [cdpMsg, setCdpMsg] = useState("");
  const [showChrome, setShowChrome] = useState(false); const [showChromeMsg, setShowChromeMsg] = useState("");
  const [oneNick, setOneNick] = useState(false); const [onMsg, setOnMsg] = useState(""); const [nicksPerIp, setNicksPerIp] = useState(2); const [parallelPerIp, setParallelPerIp] = useState(1);
  const [srv, setSrv] = useState(null);     // cấu hình server đang chạy (/api/admin/config) hoặc null khi server tắt
  const [up, setUp] = useState(false);
  const [ver, setVer] = useState(null);
  const [hstat, setHstat] = useState(null); // trạng thái sống của gateway đang dùng (/health) — cục bộ hay VPS
  const refreshHealth = () => fetchHealth().then((h) => {
    setUp(!!h);
    setHstat(h ? { nicks: (h.accounts || []).length, ready: (h.accounts || []).filter((a) => a.available !== false && !a.busy).length,
                   pending: h.pending_tasks || 0, avail: !!h.available } : null);
  }).catch(() => { setUp(false); setHstat(null); });
  const load = () => {
    api.getVideoDir?.().then((r) => setVid(r?.abs || "downloads")).catch(() => {});
    api.getAccountsDir?.().then((r) => setAcc(r?.abs || r?.dir || "accounts")).catch(() => {});
    api.getGlobalProxy?.().then((r) => setGp(r?.proxy || "")).catch(() => {});
    loadLane();
    api.getRemote?.().then((r) => { setRb(r?.base || ""); setRk(r?.apiKey || ""); setRa(r?.adminKey || ""); }).catch(() => {});
    api.getAutoRetry?.().then((r) => setAutoRetry(r?.on !== false)).catch(() => {});
    fetchHealth().then((h) => { setBurn(h?.burn_nicks === true); setHttpEngine(h?.submit_mode === "http"); setCdp(h?.cdp_launch === true); setShowChrome(h?.headless === false); }).catch(() => {});
    api.getOneNick?.().then((r) => { setOneNick(r?.on === true); if (r?.nicksPerIp) setNicksPerIp(r.nicksPerIp); if (r?.parallelPerIp) setParallelPerIp(r.parallelPerIp); }).catch(() => {});
    api.getVersion?.().then(setVer).catch(() => {});
    refreshHealth();
    adminConfig().then(setSrv).catch(() => setSrv(null));
  };
  // Nạp lại mỗi lần mở tab: server thường bật SAU khi app mở (tự bật mất 3–4s, hoặc bấm "Bật server"),
  // đọc một lần lúc mount thì khối Vận hành báo "server tắt" mãi dù server đã chạy.
  // Trạng thái máy chủ tự cập nhật mỗi 4s để thấy nick/job VPS thay đổi (ví dụ đang đồng bộ nick lên).
  useEffect(() => { if (!active) return; load(); const t = setInterval(refreshHealth, 4000); return () => clearInterval(t); }, [active]);   // eslint-disable-line react-hooks/exhaustive-deps

  const remote = rb.trim() !== "";
  const gpv = gp.trim();
  const isRotating = (/^https?:\/\//i.test(gpv) && (/get\.php/i.test(gpv) || /key=/i.test(gpv))) || /^tmproxy:\/\//i.test(gpv) || /^[a-f0-9]{32}$/i.test(gpv);
  // Thẻ làn proxy xoay theo link: server (proxyxoay.py) gọi nhà bán lấy IP hiện hành. Không phải link key → ẩn thẻ.
  const loadLane = () => api.getProxyLane?.().then((r) => setLane(r?.key_link ? r : null)).catch(() => setLane(null));
  const rotateIp = async () => {
    setLaneBusy(true);
    const r = await api.rotateProxy?.();
    setLaneBusy(false);
    if (r?.ok) { setLane({ key_link: true, ...r }); setGpMsg(`✓ Đã đổi IP: ${r.ip}${r.network ? ` · ${r.network}` : ""}`); }
    else setGpMsg("✗ " + (r?.error || "chưa đổi được IP — kiểm tra key/whitelist IP máy chủ"));
  };
  const toggleAutoRetry = async (e) => {
    const on = e.target.checked;
    setAutoRetry(on);
    const r = await api.setAutoRetry?.(on);
    if (!r?.ok) { setAutoRetry(!on); setArMsg("✗ " + (r?.error || "Bật server rồi thử lại")); return; }
    setArMsg(`✓ Đã ${on ? "BẬT" : "TẮT"} tự thử lại / xoay nick — áp dụng ngay cho server đang chạy.`);
  };
  const toggleHttpEngine = async (e) => {
    const on = e.target.checked;
    setHttpEngine(on);
    const r = await api.setSubmitMode?.(on ? "http" : "fetch");
    if (!r?.ok) { setHttpEngine(!on); setEngMsg("✗ " + (r?.error || "Bật server rồi thử lại")); return; }
    setEngMsg(on ? "✓ BẬT engine không-Chrome — gửi bằng cookie, không mở Chrome mỗi nick (nhẹ RAM, mở nick nhanh). Dola từ chối thì tự mở Chrome lại. Ảnh tham chiếu vẫn dùng Chrome."
                 : "✓ Về chế độ mở Chrome ký (ổn định).");
  };
  const toggleBurn = async (e) => {
    const on = e.target.checked;
    setBurn(on);
    try {
      await setBurnNicks(on);
      setBurnMsg(on ? "✓ Đã BẬT đốt nick — nick dùng hết lượt/điểm sẽ bị tắt lịch + ghi chú [ĐÃ ĐỐT]. Lọc 'Đã đốt' ở Kho tài khoản để xoá." : "✓ Đã TẮT đốt nick.");
    } catch (err) { setBurn(!on); setBurnMsg("✗ " + (err?.message || "Bật server rồi thử lại")); }
  };
  const toggleCdp = async (e) => {
    const on = e.target.checked;
    setCdp(on);
    const r = await setCdpLaunch(on);
    if (!r?.ok) { setCdp(!on); setCdpMsg("✗ " + (r?.error || "Bật server rồi thử lại")); return; }
    setCdpMsg(on ? "⚠ BẬT CDP — mở Chrome thật bằng subprocess + connect_over_cdp (kiểu đối thủ). MẤT stealth patchright + lộ --remote-debugging-port, dễ bị Dola phát hiện hơn. Chỉ dùng khi cách mở Chrome cũ hỏng (ví dụ Chrome 137+ không nạp được extension). Cần build lại config.so mới có tác dụng."
                : "✓ Đã TẮT CDP — về cách mở Chrome patchright (nhiều stealth hơn).");
  };
  const toggleShowChrome = async (e) => {
    const on = e.target.checked;                  // on = HIỆN cửa sổ  →  headless = !on
    setShowChrome(on);
    const r = await setHeadless(!on);
    if (!r?.ok) { setShowChrome(!on); setShowChromeMsg("✗ " + (r?.error || "Bật server rồi thử lại")); return; }
    setShowChromeMsg(on ? "✓ Sẽ HIỆN cửa sổ Chrome của từng nick từ job kế tiếp — job đang chạy không đổi. Chạy 5 luồng là 5 cửa sổ bung ra; xem xong nhớ tắt lại."
                       : "✓ Đã về chạy ẩn (headless) — không hiện cửa sổ nữa.");
  };
  const toggleOneNick = async (e) => {
    const on = e.target.checked;
    setOneNick(on);
    const r = await api.setOneNick?.(on, nicksPerIp, parallelPerIp);
    if (!r?.ok) { setOneNick(!on); setOnMsg("✗ " + (r?.error || "Bật server rồi thử lại")); return; }
    setOnMsg(`✓ Đã ${on ? "BẬT" : "TẮT"} xoay IP theo lô — áp dụng ngay. ${on ? `Mỗi IP chạy ${nicksPerIp} job (tối đa ${parallelPerIp} cùng lúc) rồi xoay (chỉ khi proxy chung là link/key xoay).` : ""}`);
  };
  const saveNicksPerIp = async (v) => {
    const n = Math.max(1, Math.min(50, parseInt(v, 10) || 1));
    setNicksPerIp(n);
    const r = await api.setOneNick?.(oneNick, n, parallelPerIp);
    setOnMsg(r?.ok ? `✓ Mỗi IP dùng cho ${n} job rồi xoay.` : "✗ " + (r?.error || "Bật server rồi thử lại"));
  };
  const saveParallelPerIp = async (v) => {
    const k = Math.max(1, Math.min(16, parseInt(v, 10) || 1));
    setParallelPerIp(k);
    const r = await api.setOneNick?.(oneNick, nicksPerIp, k);
    setOnMsg(r?.ok ? `✓ Tối đa ${k} job chạy cùng lúc trên 1 IP${k > 1 ? " — nhanh hơn, nhưng nhiều nick cùng IP dễ dính 710022002 hơn" : " (tuần tự)"}.` : "✗ " + (r?.error || "Bật server rồi thử lại"));
  };
  const testProxy = async () => {
    setGpMsg("⏳ đang thử vào dola.com…");
    const r = await api.testProxy?.(gp);
    setGpMsg(r?.ok ? `✓ Vào được dola.com (HTTP ${r.status}) qua ${r.via}` : "✗ " + (r?.error || "lỗi không rõ"));
  };
  const saveProxy = async () => {
    const r = await api.setGlobalProxy?.(gp);
    const applied = r?.remote || r?.live;   // đặt được cho server đang chạy → job mới dùng ngay
    setGpMsg(r?.ok ? (applied ? `✓ Đã lưu & áp dụng proxy chung: ${r.proxy} — job mới dùng ngay.`
                              : `✓ Đã lưu proxy chung: ${r.proxy} → bấm "Tắt" rồi "Bật server" để áp dụng.`) : "✗ " + (r?.error || "?"));
    if (r?.ok) loadLane();   // link key: kéo IP hiện hành về thẻ làn
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
          {/* Trạng thái sống của gateway đang dùng (cục bộ hay VPS) — tự cập nhật 4s */}
          <div className={"flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg px-3 py-2 text-[12px] " + (up ? "bg-surface" : "bg-error-container/20")}>
            <span className="flex items-center gap-1.5 font-medium">
              <span className={"h-1.5 w-1.5 rounded-full " + (up ? "bg-tertiary animate-pulse" : "bg-error")} />
              {up ? "Máy chủ đang chạy" : "Không nối được máy chủ"}
            </span>
            {hstat && <>
              <span className="font-mono"><b className="tabular-nums">{hstat.nicks}</b> nick</span>
              <span className="font-mono text-tertiary"><b className="tabular-nums">{hstat.ready}</b> sẵn sàng</span>
              <span className="font-mono text-primary"><b className="tabular-nums">{hstat.pending}</b> job đang chạy/chờ</span>
              <span className="font-mono text-muted-foreground">{gp.trim() ? "proxy chung ✓" : "nối thẳng"}</span>
            </>}
            {!up && <span className="text-error-on-container/90">{remote ? "Kiểm tra địa chỉ/khoá VPS bên dưới, hoặc VPS chưa mở cổng." : 'Bấm "Bật server" ở thanh trên.'}</span>}
          </div>
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
              right={<Badge variant={isRotating ? "info" : gp.trim() ? "info" : "secondary"}>{isRotating ? "proxy xoay theo link" : gp.trim() ? "có proxy chung" : "nối thẳng"}</Badge>}>
          <div className="space-y-1.5">
            <div className="text-[13px] font-medium">Proxy chung</div>
            <Input value={gp} onChange={(e) => setGp(e.target.value)} placeholder="user:pass@host:port · host:port:user:pass · https://…/get.php?key=… (xoay)" />
            <Help>Ở Việt Nam bắt buộc có exit node Nhật hoặc Hàn khi server chạy trên máy này. Dán nguyên link xoay <code className="font-mono text-primary">get.php?key=…</code> để tự lấy/đổi IP. Để trống = nối thẳng. Nick có proxy riêng (tab Proxy) dùng proxy riêng.</Help>
          </div>
          {isRotating && (
            <div className="space-y-2 rounded-lg bg-surface px-3 py-2.5">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-tertiary">IP đang dùng</span>
                <Button variant="outline" size="sm" className="ml-auto h-7" onClick={rotateIp} disabled={laneBusy}>{laneBusy ? "đang đổi…" : "Đổi IP"}</Button>
              </div>
              {lane?.ok ? (
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px]">
                  <span className="font-mono"><span className="text-muted-foreground">IP </span><b className="tabular-nums">{lane.ip || "—"}</b></span>
                  {lane.network && <span className="font-mono"><span className="text-muted-foreground">nhà mạng </span>{lane.network}</span>}
                  {lane.location && <span className="font-mono"><span className="text-muted-foreground">vị trí </span>{lane.location}</span>}
                  {lane.expiration && <span className="font-mono"><span className="text-muted-foreground">hạn </span>{lane.expiration}</span>}
                </div>
              ) : (
                <div className="text-[12px] text-warn">{lane?.error || "Chưa lấy được IP — bấm Lưu để server gọi nhà bán (IP máy chủ phải được whitelist trên trang proxy)."}</div>
              )}
              {lane?.message && <div className="text-[11px] leading-relaxed text-muted-foreground">{lane.message}</div>}
            </div>
          )}
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" onClick={testProxy} disabled={isRotating}>Kiểm tra vào dola.com</Button>
            <Button variant="outline" onClick={saveProxy}>Lưu</Button>
          </div>
          <Msg text={gpMsg} />
          <Help>{isRotating ? "IP giữ cố định suốt mỗi video; server tự đổi IP đầu mỗi nick và khi Dola chặn. Bấm \"Đổi IP\" để đổi tay." : "Đổi proxy xong: bấm \"Tắt\" rồi \"Bật server\" để áp dụng."}</Help>
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
              <span className="block text-[11px] leading-relaxed text-muted-foreground">Bật: Dola báo lỗi tạm thời thì gửi lại 1 lần trên chính nick đó (không tốn lượt); nếu nick của thẻ HẾT LƯỢT/CHẾT/bị chặn thì TỰ XOAY sang nick khác còn chạy được (nick của thẻ đang bận mà còn nick khác rảnh thì chạy luôn trên nick rảnh). Tắt: lỗi là dừng ngay, chỉ chạy đúng nick của thẻ (nick đang bận thì job CHỜ nick rảnh, tối đa 45 phút).</span></span>
          </label>
          <Msg text={arMsg} />
          <label className="flex cursor-pointer items-start gap-2.5 border-t border-surface-high pt-3 text-[13px]">
            <input type="checkbox" className="mt-1" checked={burn} onChange={toggleBurn} />
            <span><span className="font-medium">Đốt nick khi dùng hết lượt/điểm (nick dùng 1 lần)</span>
              <span className="block text-[11px] leading-relaxed text-muted-foreground">Bật: nick hết lượt ngày hoặc hết điểm thì tự TẮT LỊCH và gắn ghi chú <code className="font-mono">[ĐÃ ĐỐT dd/mm: lý do]</code> — mai không tự chạy lại. Dùng khi team xài nick Facebook 1 lần rồi bỏ; lọc "Đã đốt" ở Kho tài khoản để xoá hàng loạt. Chạy đích danh nick trong Studio vẫn tự mở lại. Tắt (mặc định): nick hết lượt tự mở lại sau 0h giờ Nhật.</span></span>
          </label>
          <Msg text={burnMsg} />
          <label className="flex cursor-pointer items-start gap-2.5 border-t border-surface-high pt-3 text-[13px]">
            <input type="checkbox" className="mt-1" checked={httpEngine} onChange={toggleHttpEngine} />
            <span><span className="font-medium">Engine gửi KHÔNG mở Chrome <span className="rounded bg-tertiary/15 px-1 text-[10px] font-semibold text-tertiary">mặc định</span></span>
              <span className="block text-[11px] leading-relaxed text-muted-foreground">Bật (mặc định): ký chữ ký bằng Python rồi gửi thẳng bằng cookie của nick — <b>không mở Chrome mỗi nick</b>, nhẹ RAM, mở nick nhanh, chạy được nhiều nick hơn, và <b>đăng nhập/nạp cookie không phải tắt cả mẻ đang chạy</b> (như đối thủ). Nếu Dola từ chối (cookie/captcha/đổi thuật toán ký) thì <b>tự mở Chrome ký lại</b>. Chữ ký ByteDance đổi theo quý nên có lúc phải cập nhật; video có ảnh tham chiếu vẫn dùng Chrome. Tắt: luôn mở Chrome ký (ổn định nhất, nhưng đăng nhập phải tạm dừng render).</span></span>
          </label>
          <Msg text={engMsg} />
          <label className="flex cursor-pointer items-start gap-2.5 border-t border-surface-high pt-3 text-[13px]">
            <input type="checkbox" className="mt-1" checked={showChrome} onChange={toggleShowChrome} />
            <span><span className="font-medium">Hiện cửa sổ Chrome khi chạy job <span className="rounded bg-tertiary/15 px-1 text-[10px] font-semibold text-tertiary">để soi lỗi</span></span>
              <span className="block text-[11px] leading-relaxed text-muted-foreground">Tắt chế độ ẩn (<code className="font-mono">headless</code>) để <b>nhìn tận mắt</b> job hỏng ở bước nào: trang Dola có vào được không, nick còn đăng nhập không, Dola có hỏi lại hay chặn không. Chỉ áp cho nick mở <b>sau khi bật</b> — job đang chạy không đổi. <b>Mỗi nick một cửa sổ</b>: chạy 5 luồng là 5 cửa sổ bung ra, xem xong nên tắt lại. Muốn mở riêng một nick để đăng nhập thì dùng nút <b>Mở profile</b> bên Kho tài khoản (không cần bật cái này).</span></span>
          </label>
          <Msg text={showChromeMsg} />
          <label className="flex cursor-pointer items-start gap-2.5 border-t border-surface-high pt-3 text-[13px]">
            <input type="checkbox" className="mt-1" checked={cdp} onChange={toggleCdp} />
            <span><span className="font-medium">Mở Chrome thật qua CDP <span className="rounded bg-warn/15 px-1 text-[10px] font-semibold text-warn">rủi ro</span></span>
              <span className="block text-[11px] leading-relaxed text-muted-foreground">Chỉ áp dụng ở <b>đường mở Chrome</b> (khi engine phải mở Chrome). Bật: mở Chrome thật bằng <code className="font-mono">subprocess + connect_over_cdp</code> như đối thủ, nạp extension qua CDP (chạy được cả Chrome 137+ đã bỏ <code className="font-mono">--load-extension</code>). <b>Đánh đổi: mất phần lớn stealth của patchright và lộ <code className="font-mono">--remote-debugging-port</code></b> — Dola dễ phát hiện automation hơn. Tắt (mặc định): dùng patchright (nhiều lớp chống phát hiện). Cần <b>build lại config.so</b> mới có tác dụng khi chạy thật.</span></span>
          </label>
          <Msg text={cdpMsg} />
          <label className="flex cursor-pointer items-start gap-2.5 border-t border-surface-high pt-3 text-[13px]">
            <input type="checkbox" className="mt-1" checked={oneNick} onChange={toggleOneNick} />
            <span><span className="font-medium">Xoay IP theo lô (1 key proxy xoay cho nhiều nick)</span>
              <span className="block text-[11px] leading-relaxed text-muted-foreground">Bật: mỗi IP chạy <b>N job</b> (tối đa <b>K job cùng lúc</b>, K=1 = tuần tự) rồi mới xoay; IP đủ lô thì job kế chờ các job còn chạy trên IP xong mới đổi IP — dùng khi 1 key/link proxy xoay gánh nhiều nick, tránh "nhiều nick một IP" (710022002). N=1 = mỗi nick một IP (an toàn nhất, chậm nhất); N lớn = ít xoay hơn, nhanh hơn. Chỉ có tác dụng khi Proxy chung là link/key xoay; proxy tĩnh/nối thẳng bỏ qua.</span></span>
          </label>
          <div className={"flex items-center gap-2 pl-7 text-[12px] " + (oneNick ? "" : "opacity-50")}>
            <span className="text-muted-foreground">Số nick mỗi IP rồi xoay:</span>
            <Input type="number" min={1} max={50} value={nicksPerIp} disabled={!oneNick}
              className="h-7 w-16 text-center" onChange={(e) => setNicksPerIp(e.target.value)}
              onBlur={(e) => saveNicksPerIp(e.target.value)} />
            <span className="text-muted-foreground">nick / IP</span>
            <span className="ml-3 text-muted-foreground">Chạy cùng lúc tối đa:</span>
            <Input type="number" min={1} max={16} value={parallelPerIp} disabled={!oneNick}
              className="h-7 w-16 text-center" onChange={(e) => setParallelPerIp(e.target.value)}
              onBlur={(e) => saveParallelPerIp(e.target.value)} />
            <span className="text-muted-foreground">job / IP{parallelPerIp > 1 ? " (đối thủ mặc định ≤6)" : " (tuần tự)"}</span>
          </div>
          {oneNick && !isRotating && (
            <div className="rounded-md border border-warn/40 bg-warn/10 px-2.5 py-1.5 text-[12px] text-warn">
              ⚠ Chưa có proxy chung xoay — chế độ này CHƯA CHẠY. Vào <b>Mạng → Proxy chung</b> dán 1 key/link xoay (nick sẽ tự dùng key chung). Proxy tĩnh/nối thẳng/khoá riêng mỗi nick → bỏ qua chế độ này.
            </div>
          )}
          <Msg text={onMsg} />
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
