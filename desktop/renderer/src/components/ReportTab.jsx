import { useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw, Film, CheckCircle2, XCircle, TrendingUp, Play, FolderOpen } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SelectNative } from "@/components/ui/select-native";
import { api, report as fetchReport, fnameFromUrl, sttFromUrl } from "@/lib/api";

const fmtTime = (s) => {
  if (!s) return "—";
  try { return new Date(parseFloat(s) * 1000).toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" }); }
  catch { return "—"; }
};
const ago = (s) => {
  if (!s) return "";
  const d = Math.max(0, Date.now() / 1000 - parseFloat(s));
  if (d < 60) return "vừa xong";
  if (d < 3600) return `${Math.floor(d / 60)} phút trước`;
  if (d < 86400) return `${Math.floor(d / 3600)} giờ trước`;
  return `${Math.floor(d / 86400)} ngày trước`;
};

function Stat({ icon, label, value, sub, tone }) {
  return (
    <Card><CardContent className="flex items-center gap-3 p-4">
      <div className={"flex h-10 w-10 flex-none items-center justify-center rounded-lg " + (tone || "bg-muted")}>{icon}</div>
      <div className="min-w-0">
        <div className="text-2xl font-bold tabular-nums leading-none">{value}</div>
        <div className="mt-1 text-xs text-muted-foreground">{label}{sub ? <span className="ml-1 opacity-70">· {sub}</span> : null}</div>
      </div>
    </CardContent></Card>
  );
}

function Trend({ days }) {
  const max = Math.max(1, ...days.map((d) => d.completed + d.failed));
  return (
    <Card><CardContent className="p-4">
      <div className="mb-3 text-xs font-medium text-muted-foreground">7 ngày qua (xanh = thành công, đỏ = lỗi)</div>
      <div className="flex items-end justify-between gap-2" style={{ height: 90 }}>
        {days.map((d, i) => {
          const done = (d.completed / max) * 78, fail = (d.failed / max) * 78;
          return (
            <div key={i} className="group flex flex-1 flex-col items-center gap-1" title={`${d.day}: ${d.completed} ok / ${d.failed} lỗi`}>
              <div className="flex w-full max-w-[34px] flex-col justify-end overflow-hidden rounded" style={{ height: 78 }}>
                <div className="w-full bg-red-500/50" style={{ height: fail }} />
                <div className="w-full bg-emerald-500/80" style={{ height: done }} />
              </div>
              <span className="text-[10px] tabular-nums text-muted-foreground">{d.day}</span>
            </div>
          );
        })}
      </div>
    </CardContent></Card>
  );
}

const nickPill = (a) => a.login_ok === 0 ? <Badge variant="danger">cookie chết</Badge>
  : (a.remaining != null && a.remaining <= 0) ? <Badge variant="warn">hết điểm</Badge>
  : (a.limit != null && a.used_today >= a.limit) ? <Badge variant="warn">hết lượt ngày</Badge>
  : a.rate_limited ? <Badge variant="warn">hết lượt</Badge>
  : a.quota_blocked ? <Badge variant="warn">hết điểm</Badge>
  : <Badge variant="success">sẵn sàng</Badge>;

function VideoCard({ v, onPlay }) {
  const stt = sttFromUrl(v.video_url);
  return (
    <div className="group relative overflow-hidden rounded-lg border bg-black/40 transition hover:border-indigo-400/50 hover:shadow-lg hover:shadow-indigo-500/10">
      <div className="relative aspect-video w-full cursor-pointer bg-gradient-to-br from-zinc-800 to-zinc-900" onClick={() => onPlay?.(v.video_url)} title={fnameFromUrl(v.video_url)}>
        <video className="h-full w-full object-cover" src={v.video_url + "#t=0.8"} muted preload="metadata" />
        <div className="absolute inset-0 flex items-center justify-center bg-black/0 transition group-hover:bg-black/35">
          <span className="scale-90 rounded-full bg-white/85 p-2 opacity-0 shadow transition group-hover:scale-100 group-hover:opacity-100">
            <Play className="h-4 w-4 fill-black text-black" />
          </span>
        </div>
        <span className="absolute right-1.5 top-1.5 rounded bg-black/70 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-white">{v.duration}s</span>
      </div>
      <div className="flex items-center gap-1 px-2 py-1.5 text-[10.5px]">
        {stt && <span className="rounded bg-indigo-500/20 px-1 font-bold text-indigo-300">#{stt}</span>}
        <span className="truncate text-muted-foreground" title={v.account}>{v.account}</span>
        <span className="ml-auto flex-none text-[10px] text-muted-foreground/70">{fmtTime(v.finished_at)}</span>
      </div>
    </div>
  );
}

export default function ReportTab({ onPlay }) {
  const [r, setR] = useState(null);
  const [loading, setLoading] = useState(false);
  const [nick, setNick] = useState("");   // lọc theo nick
  const timer = useRef(null);
  const load = async () => { setLoading(true); setR(await fetchReport()); setLoading(false); };
  useEffect(() => { load(); timer.current = setInterval(load, 5000); return () => timer.current && clearInterval(timer.current); }, []);

  const recent = useMemo(() => (r?.recent || []).filter((v) => !nick || v.account === nick), [r, nick]);
  const accounts = useMemo(() => (r?.per_account || []).filter((a) => a.total > 0).map((a) => a.account), [r]);

  if (!r) return <div className="p-6 text-sm text-muted-foreground">{loading ? "Đang tải báo cáo…" : "Chưa có dữ liệu (server tắt?)."}</div>;
  const rate = r.success_rate != null ? Math.round(r.success_rate * 100) + "%" : "—";

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-muted-foreground">BÁO CÁO VIDEO</h2>
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={() => api.openDownloads?.()}><FolderOpen className="h-3.5 w-3.5" />Thư mục</Button>
        <Button variant="outline" size="sm" onClick={load}><RefreshCw className={"h-3.5 w-3.5 " + (loading ? "animate-spin" : "")} />Làm mới</Button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat icon={<Film className="h-5 w-5 text-indigo-300" />} tone="bg-indigo-500/15" label="Tổng video đã tạo" value={r.total_completed} />
        <Stat icon={<CheckCircle2 className="h-5 w-5 text-emerald-400" />} tone="bg-emerald-500/15" label="Video hôm nay" value={r.today_completed} sub={"lỗi " + r.today_failed} />
        <Stat icon={<TrendingUp className="h-5 w-5 text-sky-400" />} tone="bg-sky-500/15" label="Tỉ lệ thành công" value={rate} />
        <Stat icon={<XCircle className="h-5 w-5 text-red-400" />} tone="bg-red-500/15" label="Tổng lỗi" value={r.total_failed} />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {r.per_day && <Trend days={r.per_day} />}
        <Card><CardContent className="p-4">
          <div className="mb-3 text-xs font-medium text-muted-foreground">Phân loại (video thành công)</div>
          <div className="flex flex-wrap gap-2 text-xs">
            {Object.entries(r.by_model).map(([k, v]) => <Badge key={k} variant="secondary">{k}: {v}</Badge>)}
            {Object.entries(r.by_duration).map(([k, v]) => <Badge key={k} variant="outline">{k}s: {v}</Badge>)}
          </div>
        </CardContent></Card>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-xs font-medium text-muted-foreground">
              <th className="h-10 px-3">Nick</th><th className="px-3">Trạng thái</th>
              <th className="px-3 text-right">Tổng video</th><th className="px-3 text-right">Hôm nay</th>
              <th className="px-3 text-right">Lỗi</th><th className="px-3 text-right">Còn điểm</th>
              <th className="px-3 text-right">Giây TB</th><th className="px-3">Video cuối</th>
            </tr>
          </thead>
          <tbody>
            {r.per_account.map((a) => (
              <tr key={a.account} className={"border-b transition-colors hover:bg-white/[.03] cursor-pointer " + (nick === a.account ? "bg-indigo-500/10" : "")}
                  onClick={() => setNick(nick === a.account ? "" : a.account)} title="Bấm để lọc video của nick này">
                <td className="px-3 py-2 font-semibold">{a.account}</td>
                <td className="px-3">{nickPill(a)}</td>
                <td className="px-3 text-right font-bold tabular-nums text-indigo-300">{a.total}</td>
                <td className="px-3 text-right tabular-nums">{a.today || "—"}</td>
                <td className="px-3 text-right tabular-nums text-muted-foreground">{a.failed || "—"}</td>
                <td className="px-3 text-right tabular-nums">{a.remaining != null ? a.remaining : "—"}</td>
                <td className="px-3 text-right tabular-nums text-muted-foreground">{a.avg_dur ? a.avg_dur + "s" : "—"}</td>
                <td className="px-3 text-muted-foreground">{a.last_at ? ago(a.last_at) : "chưa có"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div>
        <div className="mb-2 flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">Video gần đây ({recent.length})</span>
          <span className="flex-1" />
          <SelectNative className="h-8 w-auto text-xs" value={nick} onChange={(e) => setNick(e.target.value)}>
            <option value="">Tất cả nick</option>
            {accounts.map((a) => <option key={a} value={a}>{a}</option>)}
          </SelectNative>
        </div>
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
          {recent.map((v, i) => <VideoCard key={i} v={v} onPlay={onPlay} />)}
          {recent.length === 0 && <div className="col-span-full rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">Chưa có video{nick ? ` của ${nick}` : ""}.</div>}
        </div>
      </div>
    </div>
  );
}
