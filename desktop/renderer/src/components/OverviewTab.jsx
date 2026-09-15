import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Server, Gauge, Timer, PieChart, Zap, Play, FolderOpen, ArrowRight, Send, CheckCircle2, XCircle, Users, Copy } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, cfg, adminAccounts, adminConfig, recentTasks, report as fetchReport, accChip, fmtError, fmtSec, maskProxy, fnameFromUrl, sttFromUrl, timeAgo, STAGE_TEXT } from "@/lib/api";
import { Insights, StagePipeline, NickGrid, HourlyBars, pipelineGroups, stageOf, MAYBE_CHARGED } from "@/components/OverviewViz";

const POLL_MS = 5000;
const isToday = (ts) => !!ts && new Date(ts * 1000).toDateString() === new Date().toDateString();
const quantile = (arr, q) => { if (!arr.length) return 0; const s = [...arr].sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.round(q * (s.length - 1)))]; };
const TONE = { tertiary: "text-tertiary", primary: "text-primary", error: "text-error", warn: "text-warn", info: "text-info", muted: "text-muted-foreground" };
const BAR = { tertiary: "bg-tertiary", primary: "bg-primary", error: "bg-error", warn: "bg-warn", info: "bg-info", muted: "bg-outline" };

function Chip({ icon, tag, value, label, tone = "muted", onClick }) {
  return (
    <button type="button" onClick={onClick} className="relative flex flex-col gap-1 overflow-hidden rounded-lg bg-surface-low p-3 text-left transition hover:bg-surface">
      <span className={"absolute inset-x-0 top-0 h-[2px] " + BAR[tone]} />
      <span className="flex w-full items-center justify-between text-muted-foreground">{icon}<span className={"rounded bg-surface-high px-1 font-mono text-[10px] font-semibold uppercase " + TONE[tone]}>{tag}</span></span>
      <span className={"text-2xl font-semibold tabular-nums " + TONE[tone]}>{value}</span>
      <span className="text-[11px] text-muted-foreground">{label}</span>
    </button>
  );
}

// Biểu đồ vòng (donut) thuần SVG — trực quan hoá tỉ lệ, không cần thư viện. segments: [{value, cls, label}].
function Donut({ size = 108, stroke = 13, segments, center, sub }) {
  const total = segments.reduce((s, x) => s + x.value, 0);
  const r = (size - stroke) / 2, c = 2 * Math.PI * r;
  let off = 0;
  return (
    <div className="relative flex-none" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} stroke="currentColor" className="text-surface-high" />
        {total > 0 && segments.filter((s) => s.value > 0).map((s, i) => {
          const len = (s.value / total) * c;
          const el = <circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} stroke="currentColor" className={s.cls} strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-off} />;
          off += len; return el;
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
        <span className="text-2xl font-semibold tabular-nums text-foreground">{center}</span>
        {sub && <span className="mt-1 text-[10px] text-muted-foreground">{sub}</span>}
      </div>
    </div>
  );
}

function Stat({ label, value, sub, tone = "text-foreground", icon }) {
  return (
    <div className="flex flex-col justify-between rounded-xl bg-surface-low p-4">
      <div className="flex items-center justify-between text-xs text-muted-foreground">{label}{icon}</div>
      <div className={"my-2 text-3xl font-semibold tabular-nums " + tone}>{value}</div>
      <div className="font-mono text-[11px] text-muted-foreground">{sub}</div>
    </div>
  );
}

function statusChip(t) {
  if (t.status === "completed") return <Badge variant="success">✓ Thành công</Badge>;
  if (t.status === "processing") return <Badge variant="default"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />{(STAGE_TEXT[stageOf(t)] || "Đang tạo").replace(/…$/, "")}</Badge>;
  if (t.status === "queued") return <Badge variant="secondary">Chờ</Badge>;
  const e = fmtError(t.error);
  return <Badge variant={e.kind === "account" ? "danger" : "warn"} title={t.error}>{e.icon} {e.short}</Badge>;
}

function VideoCard({ v, onPlay }) {
  const stt = sttFromUrl(v.video_url);
  return (
    <div className="group relative overflow-hidden rounded-lg bg-surface-lowest transition hover:shadow-lg hover:shadow-primary/10">
      <div className="relative aspect-video w-full cursor-pointer" onClick={() => onPlay?.(v.video_url)} title={fnameFromUrl(v.video_url)}>
        <video className="h-full w-full object-cover" src={v.video_url + "#t=0.8"} muted preload="metadata" />
        <div className="absolute inset-0 flex items-center justify-center transition group-hover:bg-black/35">
          <span className="scale-90 rounded-full bg-primary p-2 opacity-0 shadow transition group-hover:scale-100 group-hover:opacity-100"><Play className="h-4 w-4 fill-primary-foreground text-primary-foreground" /></span>
        </div>
        <span className="absolute right-1.5 top-1.5 rounded bg-black/70 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-white">{v.duration}s</span>
      </div>
      <div className="flex items-center gap-1 px-2 py-1.5 text-[10.5px]">
        {stt && <span className="rounded bg-primary/15 px-1 font-mono font-bold text-primary">#{stt}</span>}
        <span className="truncate text-muted-foreground">{v.account}</span>
        <span className="ml-auto flex-none font-mono text-[10px] text-muted-foreground/70">{timeAgo(v.finished_at)}</span>
      </div>
    </div>
  );
}

export default function OverviewTab({ health, onPlay, onGo, active = true }) {
  const [accsRaw, setAccs] = useState([]);
  // adminAccounts (cần admin key) lỗi/chưa nạp → lấy tạm nick từ /health (giống dải trên cùng) để Tổng quan không hiện "0 nick".
  const accs = accsRaw.length ? accsRaw : (health?.accounts || []).map((x) => ({ ...x, name: x.name || x.account }));
  const [tasks, setTasks] = useState([]);
  const [rep, setRep] = useState(null);
  const [gproxy, setGproxy] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    setBusy(true);
    const [a, t] = await Promise.all([adminAccounts(), recentTasks(200)]);
    if (a.ok) setAccs(a.accounts);
    setTasks(t);
    setBusy(false);
  }, []);
  useEffect(() => {
    if (!active) return;   // tab ẩn không poll (mọi tab giữ mount để không mất state)
    load(); const id = setInterval(load, POLL_MS);
    const rid = setInterval(async () => setRep(await fetchReport()), 15000);
    fetchReport().then(setRep);
    adminConfig().then((c) => c ? setGproxy(c.proxy || "")
      : Promise.resolve(api.getGlobalProxy?.()).then((r) => setGproxy(r?.proxy || ""))).catch(() => {});
    return () => { clearInterval(id); clearInterval(rid); };
  }, [load, active]);

  const chips = useMemo(() => {
    const m = { total: accs.length, ready: 0, busy: 0, credit: 0, day: 0, dead: 0, cooling: 0, off: 0 };
    for (const a of accs) {
      const c = accChip(a);
      if (c.st === "quota") m[c.text === "Hết credit" ? "credit" : "day"]++; else m[c.st]++;
    }
    return m;
  }, [accs]);

  const today = useMemo(() => tasks.filter((t) => isToday(t.created_at)), [tasks]);
  const done = today.filter((t) => t.status === "completed");
  const failed = today.filter((t) => t.status === "failed");
  const running = tasks.filter((t) => t.status === "processing");
  const queued = tasks.filter((t) => t.status === "queued");
  const [reasonFilter, setReasonFilter] = useState("");
  const reasonKey = (t) => { const e = fmtError(t.error); return e.icon + " " + e.short; };
  const shownTasks = reasonFilter ? failed.filter((t) => reasonKey(t) === reasonFilter) : tasks;
  const renders = done.filter((t) => t.started_at && t.finished_at).map((t) => t.finished_at - t.started_at);
  const median = quantile(renders, 0.5), p90 = quantile(renders, 0.9), fastest = renders.length ? Math.min(...renders) : 0;
  // [nhãn, số lần, gợi ý xử lý, có thể đã trừ lượt?]
  const reasons = useMemo(() => {
    const m = new Map();
    for (const t of failed) { const e = fmtError(t.error); const k = e.icon + " " + e.short; const v = m.get(k) || [k, 0, e.hint, MAYBE_CHARGED.test(e.short)]; v[1]++; m.set(k, v); }
    return [...m.values()].sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [failed]);
  const groups = pipelineGroups(running);
  const resetAt = health?.limit_reset_at ? new Date(health.limit_reset_at * 1000).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" }) : "";
  const conc = health?.max_concurrency || 1;
  const pending = health?.pending_tasks || 0;
  const eta = pending && median ? Math.ceil(pending / conc) * median : 0;
  const colors = ["bg-error", "bg-warn", "bg-primary", "bg-info", "bg-outline", "bg-tertiary"];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight">Tổng quan hệ thống</h1>
        <span className="flex items-center gap-1.5 rounded bg-tertiary/10 px-2 py-1 font-mono text-[11px] text-tertiary"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-tertiary" />Tự cập nhật mỗi 5s</span>
        <span className="ml-auto" />
        <Button variant="outline" size="sm" onClick={load} disabled={busy}><RefreshCw className={"h-3.5 w-3.5 " + (busy ? "animate-spin" : "")} />Làm mới ngay</Button>
        {!cfg.remote && <Button variant="secondary" size="sm" className="text-primary" onClick={() => api.restartGateway?.()}>Khởi động lại Gateway</Button>}
      </div>

      <Insights today={today} failed={failed} groups={groups} accs={accs} queued={queued} resetAt={resetAt} onGo={onGo} />

      {/* Địa chỉ gateway / proxy chung đã có ở dải trạng thái trên cùng và tab Cài đặt — ở đây chỉ còn dòng chảy job + hàng chờ. */}
      <div className="grid gap-3 lg:grid-cols-12">
        <div className="lg:col-span-7">
          <StagePipeline groups={groups} done={done.length} failed={failed.length} conc={conc} stats={[
            ["Dựng trung vị", median ? fmtSec(median) : "—"], ["P90", p90 ? fmtSec(p90) : "—", "text-warn"],
            ["Nhanh nhất", fastest ? fmtSec(fastest) : "—", "text-tertiary"], ["Mẫu", renders.length],
            ["Tự thử lại", health ? (health.auto_retry ? "bật" : "tắt") : "—", health?.auto_retry ? "text-tertiary" : "text-warn"]]} />
        </div>
        <div className="flex flex-col gap-3 rounded-xl bg-surface-low p-4 lg:col-span-5">
          <div className="flex items-center gap-2"><span className="flex items-center gap-2 text-[15px] font-medium"><Timer className="h-4 w-4 text-info" />Hàng chờ</span>
            <Badge variant={queued.length ? "default" : "secondary"}>{queued.length} tác vụ</Badge><span className="ml-auto" />
            <span className="font-mono text-[11px] text-muted-foreground">{eta ? <>cả đợt xong lúc <b className="text-tertiary">~{new Date(Date.now() + eta * 1000).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}</b></> : "hàng chờ trống"}</span></div>
          <div className="flex flex-col gap-1.5 font-mono text-[12px]">
            {queued.slice(0, 4).map((t, i) => (
              <div key={t.id} className="flex items-center gap-2">
                <span className="text-muted-foreground">#{String(t.id).slice(0, 8)}</span><span>{t.account || "—"}</span>
                <span className="min-w-0 flex-1 truncate text-muted-foreground" title={t.prompt}>{t.prompt}</span>
                <span className="text-muted-foreground">{median ? `~${new Date(Date.now() + Math.ceil((i + 1) / conc) * median * 1000).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}` : "chờ slot"}</span>
              </div>
            ))}
            {queued.length > 4 && <div className="text-[11px] text-muted-foreground">+{queued.length - 4} tác vụ nữa</div>}
            {!queued.length && <div className="text-[11px] text-muted-foreground">Không có tác vụ nào chờ — {median > 0 ? `mỗi video mất ~${fmtSec(median)}` : "gửi prompt ở tab Studio"}.</div>}
          </div>
        </div>
      </div>

      <div>
        <div className="mb-1.5 flex items-center justify-between font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          <span>Trạng thái nick ({accs.length})</span>
          <button className="flex items-center gap-1 normal-case text-primary hover:underline" onClick={() => onGo?.("acct")}>Quản lý kho <ArrowRight className="h-3 w-3" /></button>
        </div>
        <div className="mb-2 flex h-2.5 gap-0.5 overflow-hidden rounded-full bg-surface" title="Tỉ lệ trạng thái nick">
          {[[chips.ready, "bg-tertiary"], [chips.busy, "bg-primary"], [chips.credit, "bg-error"], [chips.day, "bg-warn"], [chips.dead, "bg-outline"], [chips.cooling, "bg-info"]].map(([v, c], i) =>
            v > 0 ? <div key={i} className={"h-full " + c} style={{ width: (v / Math.max(1, chips.total)) * 100 + "%" }} /> : null)}
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
          <Chip icon={<Users className="h-4 w-4" />} tag="tất cả" value={chips.total} label="Nick trên server" onClick={() => onGo?.("acct")} />
          <Chip icon={<CheckCircle2 className="h-4 w-4 text-tertiary" />} tag="ready" tone="tertiary" value={chips.ready} label="Sẵn sàng nhận prompt" />
          <Chip icon={<Zap className="h-4 w-4 text-primary" />} tag="active" tone="primary" value={chips.busy} label="Đang render" />
          <Chip icon={<XCircle className="h-4 w-4 text-error" />} tag="credit" tone="error" value={chips.credit} label="Hết credit" />
          <Chip icon={<Timer className="h-4 w-4 text-warn" />} tag="ngày" tone="warn" value={chips.day} label="Hết lượt hôm nay" />
          <Chip icon={<Users className="h-4 w-4 text-outline" />} tag="auth" value={chips.dead} label="Chưa đăng nhập" />
          <Chip icon={<Timer className="h-4 w-4 text-info" />} tag="nghỉ" tone="info" value={chips.cooling} label="Nghỉ sau risk-control" />
        </div>
        <div className="mt-2"><NickGrid accs={accs} today={today} running={running} onGo={onGo} /></div>
      </div>

      <div className="grid gap-3 lg:grid-cols-12">
        <div className="flex flex-col gap-3 lg:col-span-7">
          <div className="flex flex-col items-center gap-4 rounded-xl bg-surface-low p-4 sm:flex-row">
            <Donut center={today.length} sub="đã gửi" segments={[
              { value: done.length, cls: "text-tertiary", label: "Xong" },
              { value: failed.length, cls: "text-error", label: "Lỗi" },
              { value: running.length, cls: "text-primary", label: "Đang chạy" },
              { value: queued.length, cls: "text-outline", label: "Chờ" },
            ]} />
            <div className="grid w-full flex-none grid-cols-2 gap-x-4 gap-y-2.5 sm:w-44 sm:grid-cols-1">
              {[["Xong", done.length, "text-tertiary", "bg-tertiary", today.length ? `${Math.round((done.length / today.length) * 100)}% thành công` : "chưa có job"],
                ["Lỗi", failed.length, "text-error", "bg-error", today.length ? `${Math.round((failed.length / today.length) * 100)}% thất bại` : "—"],
                ["Đang chạy", running.length, "text-primary", "bg-primary", queued.length ? `${queued.length} chờ slot` : "không có job chờ"]].map(([l, v, tone, dot, sub]) => (
                <div key={l} className="flex items-center gap-2.5">
                  <span className={"h-2.5 w-2.5 flex-none rounded-sm " + dot} />
                  <span className={"text-xl font-semibold tabular-nums " + tone}>{v}</span>
                  <div className="min-w-0 leading-tight"><div className="text-[12px] font-medium">{l}</div><div className="truncate font-mono text-[10.5px] text-muted-foreground">{sub}</div></div>
                </div>
              ))}
            </div>
            <HourlyBars today={today} />
          </div>
          {reasons.some(([k]) => /gửi quá dày/i.test(k)) && <button type="button" className="rounded-xl bg-surface-low p-3 text-left text-xs text-primary hover:underline" onClick={() => onGo?.("proxy")}>Lỗi "gửi quá dày" giảm khi chia proxy riêng → mở tab Proxy</button>}
        </div>
        <div className="flex flex-col gap-3 rounded-xl bg-surface-low p-4 lg:col-span-5">
          <div className="flex items-center justify-between"><span className="flex items-center gap-2 text-[15px] font-medium"><PieChart className="h-4 w-4 text-error" />Nguyên nhân lỗi ({failed.length})</span><span className="font-mono text-[11px] text-muted-foreground">hôm nay · bấm để lọc bảng</span></div>
          <div className="flex h-2.5 gap-0.5 overflow-hidden rounded-full bg-surface">
            {reasons.map(([k, n], i) => <div key={k} className={"h-full " + colors[i % colors.length]} style={{ width: (n / failed.length) * 100 + "%" }} title={`${k}: ${n}`} />)}
          </div>
          <div className="flex flex-col gap-1">
            {reasons.map(([k, n, hint, charged], i) => (
              <button type="button" key={k} onClick={() => setReasonFilter((f) => (f === k ? "" : k))} title={hint}
                className={"flex w-full flex-col gap-1 rounded px-1.5 py-1.5 text-left hover:bg-surface " + (reasonFilter === k ? "bg-surface-high ring-1 ring-primary/35" : "")}>
                <span className="flex w-full items-center justify-between gap-2 text-sm">
                  <span className="flex min-w-0 items-center gap-2"><span className={"h-2.5 w-2.5 flex-none rounded-sm " + colors[i % colors.length]} /><span className="truncate">{k}</span>
                    {charged && <span className="flex-none rounded bg-warn/15 px-1 font-mono text-[9.5px] text-warn">có thể đã trừ lượt</span>}</span>
                  <span className="flex-none font-mono text-[11px] text-muted-foreground">{n} lần · {Math.round((n / failed.length) * 100)}%</span>
                </span>
                <span className="h-1 overflow-hidden rounded-full bg-surface"><span className={"block h-full rounded-full " + colors[i % colors.length]} style={{ width: (n / reasons[0][1]) * 100 + "%" }} /></span>
                {hint && <span className="truncate pl-[18px] text-[11px] text-muted-foreground">{hint}</span>}
              </button>
            ))}
            {!failed.length && <div className="text-xs text-muted-foreground">Chưa có lỗi nào hôm nay.</div>}
          </div>
        </div>
      </div>

      <div>
        <div className="mb-2 flex items-center gap-2 text-[15px] font-medium"><Zap className="h-4 w-4 text-tertiary" />Tác vụ gần nhất
          {reasonFilter && <button type="button" onClick={() => setReasonFilter("")} title="Bỏ lọc"><Badge variant="warn">Lọc: {reasonFilter} ✕</Badge></button>}
          <span className="ml-auto font-mono text-[11px] font-normal text-muted-foreground">{reasonFilter ? `${shownTasks.length} tác vụ lỗi này hôm nay` : "tự cập nhật"}</span></div>
        <div className="overflow-x-auto rounded-xl bg-surface-low">
          <table className="w-full text-left text-[13px]">
            <thead><tr className="bg-surface-lowest font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              <th className="px-3 py-2.5">Mã job</th><th className="px-3 py-2.5">Prompt</th><th className="px-3 py-2.5">Nick</th><th className="px-3 py-2.5 text-right">Thời gian</th><th className="px-3 py-2.5">Trạng thái</th><th className="px-3 py-2.5 text-center">Mở</th>
            </tr></thead>
            <tbody>
              {shownTasks.slice(0, 8).map((t) => {
                const secs = t.status === "processing" ? Date.now() / 1000 - (t.started_at || t.created_at) : (t.finished_at && t.started_at ? t.finished_at - t.started_at : 0);
                return (
                  <tr key={t.id} className="border-t border-surface hover:bg-surface/60">
                    <td className="px-3 py-2.5 font-mono text-[11.5px] text-primary">#{String(t.id).slice(0, 8)}</td>
                    <td className="max-w-xs px-3 py-2.5"><div className="truncate" title={t.prompt}>{t.prompt}</div></td>
                    <td className="px-3 py-2.5 font-medium">{t.account || "—"}</td>
                    <td className={"px-3 py-2.5 text-right font-mono text-[11.5px] " + (t.status === "processing" ? "text-primary" : "text-muted-foreground")}>{secs ? fmtSec(secs) : "—"}</td>
                    <td className="px-3 py-2.5">{statusChip(t)}</td>
                    <td className="px-3 py-2.5 text-center">{t.video_url && <button className="rounded p-1 text-muted-foreground hover:bg-surface-high hover:text-tertiary" onClick={() => onPlay?.(t.video_url)} title="Xem video"><Play className="h-4 w-4" /></button>}</td>
                  </tr>
                );
              })}
              {!shownTasks.length && <tr><td colSpan={6} className="px-3 py-6 text-center text-sm text-muted-foreground">{reasonFilter ? "Không còn tác vụ nào với lỗi này." : "Chưa có tác vụ nào — sang tab Studio để gửi prompt."}</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {rep && (
        <div className="grid gap-3 lg:grid-cols-12">
          <div className="rounded-xl bg-surface-low p-4 lg:col-span-5">
            <div className="mb-3 text-xs font-medium text-muted-foreground">7 ngày qua (xanh = thành công, đỏ = lỗi) · tổng {rep.total_completed} video</div>
            <div className="flex items-end justify-between gap-2" style={{ height: 90 }}>
              {(rep.per_day || []).map((d, i) => { const max = Math.max(1, ...rep.per_day.map((x) => x.completed + x.failed)); return (
                <div key={i} className="flex flex-1 flex-col items-center gap-1" title={`${d.day}: ${d.completed} ok / ${d.failed} lỗi`}>
                  <div className="flex w-full max-w-[34px] flex-col justify-end overflow-hidden rounded" style={{ height: 78 }}>
                    <div className="w-full bg-error/60" style={{ height: (d.failed / max) * 78 }} /><div className="w-full bg-tertiary" style={{ height: (d.completed / max) * 78 }} />
                  </div><span className="font-mono text-[10px] text-muted-foreground">{d.day}</span>
                </div>); })}
            </div>
          </div>
          <div className="lg:col-span-7">
            <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted-foreground">Video gần đây<span className="flex-1" /><Button variant="ghost" size="sm" onClick={() => api.openDownloads?.()}><FolderOpen className="h-3.5 w-3.5" />Thư mục</Button></div>
            <div className="grid grid-cols-3 gap-2 md:grid-cols-4">
              {(rep.recent || []).slice(0, 8).map((v, i) => <VideoCard key={i} v={v} onPlay={onPlay} />)}
              {!(rep.recent || []).length && <div className="col-span-full rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">Chưa có video.</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
