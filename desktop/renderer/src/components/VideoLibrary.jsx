import { useCallback, useEffect, useMemo, useState } from "react";
import { Play, FolderOpen, Copy, Eraser, Search, Film, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SelectNative } from "@/components/ui/select-native";
import { ViewToggle, useView } from "@/components/ui/view-toggle";
import { api, recentTasks, fnameFromUrl, sttFromUrl, fmtSec, timeAgo } from "@/lib/api";

// Kho video: mọi job đã ra video (tasks.db qua /api/admin/tasks), mỗi dòng ghi rõ nick nào, prompt nào.
// Không thêm endpoint mới; video xem/mở/copy bằng IPC đã có.
const POLL_MS = 10000;
const RANGES = [["1", "Hôm nay"], ["7", "7 ngày"], ["30", "30 ngày"], ["all", "Tất cả"]];
const th = "h-9 px-3 text-left font-mono text-[10px] font-semibold uppercase tracking-wider text-muted-foreground";

export default function VideoLibrary({ active = true, onPlay }) {
  const [tasks, setTasks] = useState([]);
  const [q, setQ] = useState("");
  const [nick, setNick] = useState("");
  const [range, setRange] = useState("7");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [view, pickView] = useView("dolaVideoView");
  const load = useCallback(async () => {
    setBusy(true);
    const all = await recentTasks(2000);   // server cũ cắt còn 200 — vẫn chạy, chỉ thấy ít video hơn
    setTasks(all.filter((t) => t.status === "completed" && t.video_url));
    setBusy(false);
  }, []);
  useEffect(() => { if (!active) return; load(); const id = setInterval(load, POLL_MS); return () => clearInterval(id); }, [load, active]);

  const nicks = useMemo(() => [...new Set(tasks.map((t) => t.account).filter(Boolean))].sort(), [tasks]);
  const rows = useMemo(() => {
    const kw = q.trim().toLowerCase();
    const since = range === "all" ? 0 : range === "1" ? new Date().setHours(0, 0, 0, 0) / 1000 : Date.now() / 1000 - Number(range) * 86400;
    return tasks
      .filter((t) => (t.finished_at || t.created_at || 0) >= since)
      .filter((t) => !nick || t.account === nick)
      .filter((t) => !kw || [t.prompt, t.account, fnameFromUrl(t.video_url)].some((v) => String(v || "").toLowerCase().includes(kw)));
  }, [tasks, q, nick, range]);
  const perNick = useMemo(() => { const m = new Map(); rows.forEach((t) => m.set(t.account, (m.get(t.account) || 0) + 1)); return m; }, [rows]);
  const todayCount = tasks.filter((t) => (t.finished_at || 0) >= new Date().setHours(0, 0, 0, 0) / 1000).length;

  const copy = (s, label) => navigator.clipboard.writeText(s).then(() => setMsg(`Đã copy ${label}.`)).catch(() => setMsg("Không copy được."));
  const removeWm = async (u) => {
    setMsg("Đang xoá logo… (dò watermark trôi)");
    try { const r = await api.removeWatermark?.(fnameFromUrl(u)); setMsg(r?.ok ? "✓ Đã xoá logo → " + r.output : "Xoá logo lỗi: " + (r?.error || "?")); }
    catch (e) { setMsg("Lỗi: " + (e?.message || e)); }
  };
  const renderSec = (t) => (t.finished_at && t.started_at ? t.finished_at - t.started_at : 0);

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2">
        {[["Video đã tạo", tasks.length, "text-foreground", `${todayCount} hôm nay`],
          ["Nick có video", nicks.length, "text-tertiary", "trong danh sách này"],
          ["Đang hiện", rows.length, "text-primary", nick ? `nick ${nick}` : `${perNick.size} nick`]].map(([l, v, tone, sub]) => (
          <div key={l} className="rounded-lg bg-surface-low p-3">
            <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{l}</div>
            <div className={"mt-0.5 text-2xl font-semibold tabular-nums " + tone}>{v}</div>
            <div className="font-mono text-[11px] text-muted-foreground">{sub}</div>
          </div>
        ))}
      </div>

      <div className="space-y-3 rounded-xl bg-surface p-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight"><Film className="h-4 w-4 text-primary" />Kho video</h2>
          <span className="rounded-full bg-primary/15 px-2 py-0.5 font-mono text-[11px] text-primary">{rows.length} video</span>
          <span className="ml-auto" />
          <Button variant="secondary" size="sm" onClick={() => api.openDownloads?.()}><FolderOpen className="h-3.5 w-3.5" />Thư mục video</Button>
          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={load} disabled={busy} title="Làm mới"><RefreshCw className={"h-3.5 w-3.5 " + (busy ? "animate-spin" : "")} /></Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[240px] flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-muted-foreground" />
            <Input className="h-8 bg-surface-lowest pl-8 text-xs" placeholder="Tìm theo prompt, nick, tên file…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <SelectNative className="h-8 w-auto text-xs" value={nick} onChange={(e) => setNick(e.target.value)}>
            <option value="">Tất cả nick ({nicks.length})</option>
            {nicks.map((n) => <option key={n} value={n}>{n}</option>)}
          </SelectNative>
          <SelectNative className="h-8 w-auto text-xs" value={range} onChange={(e) => setRange(e.target.value)}>{RANGES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</SelectNative>
          <ViewToggle value={view} onChange={pickView} />
        </div>
      </div>

      {view === "grid" && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
          {rows.map((t) => {
            const stt = sttFromUrl(t.video_url); const f = fnameFromUrl(t.video_url);
            return (
              <div key={t.id} className="flex flex-col gap-2 rounded-xl bg-surface-low p-2.5">
                <div className="group relative aspect-[9/16] max-h-[220px] w-full cursor-pointer overflow-hidden rounded-lg bg-surface-lowest" onClick={() => onPlay?.(t.video_url)} title={f}>
                  <video className="h-full w-full object-cover" src={t.video_url + "#t=0.6"} muted preload="metadata" />
                  <span className="absolute left-1.5 top-1.5 rounded bg-black/70 px-1.5 py-0.5 font-mono text-[11px] font-bold text-primary">{stt ? `#${stt}` : "—"}</span>
                  <span className="absolute right-1.5 top-1.5 rounded bg-black/70 px-1.5 py-0.5 font-mono text-[10px] text-white">{t.duration ? `${t.duration}s` : ""}</span>
                  <span className="absolute inset-0 flex items-center justify-center opacity-0 transition group-hover:bg-black/30 group-hover:opacity-100"><span className="rounded-full bg-primary p-2"><Play className="h-4 w-4 fill-primary-foreground text-primary-foreground" /></span></span>
                </div>
                <div className="flex items-center gap-1 font-mono text-[11.5px]"><span className="truncate font-semibold" title={t.account}>{t.account || "—"}</span><span className="ml-auto flex-none text-[10.5px] text-muted-foreground">{perNick.get(t.account)} video</span></div>
                <div className="line-clamp-3 text-[12px] leading-snug text-on-variant" title={t.prompt}>{t.prompt}</div>
                <div className="flex items-center gap-1 font-mono text-[10.5px] text-muted-foreground"><span className="truncate">{t.model || "—"} · {t.ratio || "—"}</span><span className="ml-auto flex-none">{timeAgo(t.finished_at)}{renderSec(t) ? ` · dựng ${fmtSec(renderSec(t))}` : ""}</span></div>
                <div className="flex items-center gap-0.5 border-t border-surface pt-1.5">
                  <button type="button" className="font-mono text-[10.5px] text-muted-foreground hover:text-primary" onClick={() => copy(t.prompt || "", "prompt")}>copy prompt</button>
                  <span className="ml-auto" />
                  <Button variant="ghost" size="icon" className="h-7 w-7" title="Xem" onClick={() => onPlay?.(t.video_url)}><Play className="h-3.5 w-3.5" /></Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7" title="Mở thư mục" onClick={() => api.openDownloads?.()}><FolderOpen className="h-3.5 w-3.5" /></Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7" title="Copy tên file" onClick={() => copy(f, "tên file")}><Copy className="h-3.5 w-3.5" /></Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7" title="Xoá logo Dola" onClick={() => removeWm(t.video_url)}><Eraser className="h-3.5 w-3.5" /></Button>
                </div>
              </div>
            );
          })}
          {!rows.length && <div className="col-span-full rounded-xl bg-surface-low px-3 py-8 text-center text-sm text-muted-foreground">{tasks.length ? "Không có video nào khớp bộ lọc." : "Chưa có video nào — sang tab Studio để tạo."}</div>}
        </div>
      )}

      {view === "table" && <div className="overflow-x-auto rounded-xl bg-surface-low">
        <table className="w-full border-collapse text-[12.5px]">
          <thead><tr className="bg-surface-lowest">
            <th className={th + " w-[72px]"}>Video</th><th className={th + " w-[64px]"}>Số</th><th className={th}>Nick</th><th className={th}>Prompt</th>
            <th className={th + " w-[150px]"}>Cấu hình</th><th className={th + " w-[120px]"}>Thời gian</th><th className={th + " w-[132px] text-right"}>Hành động</th>
          </tr></thead>
          <tbody>
            {rows.map((t) => {
              const stt = sttFromUrl(t.video_url); const f = fnameFromUrl(t.video_url);
              return (
                <tr key={t.id} className="border-t border-surface hover:bg-surface/60">
                  <td className="px-3 py-2"><video className="h-[46px] w-9 cursor-pointer rounded object-cover" src={t.video_url + "#t=0.6"} muted preload="metadata" onClick={() => onPlay?.(t.video_url)} title={f} /></td>
                  <td className="px-3 py-2 font-mono text-[12px] font-bold text-primary">{stt ? `#${stt}` : "—"}</td>
                  <td className="px-3 py-2 font-mono text-[12px]">{t.account || "—"}<div className="text-[10.5px] text-muted-foreground">{perNick.get(t.account)} video</div></td>
                  <td className="max-w-[420px] px-3 py-2">
                    <div className="line-clamp-2 leading-snug" title={t.prompt}>{t.prompt}</div>
                    <button type="button" className="mt-0.5 font-mono text-[10.5px] text-muted-foreground hover:text-primary" onClick={() => copy(t.prompt || "", "prompt")}>copy prompt</button>
                  </td>
                  <td className="px-3 py-2 font-mono text-[11px] text-muted-foreground">{t.model || "—"}<div>{t.ratio || "—"} · {t.duration ? `${t.duration}s` : "—"}</div></td>
                  <td className="px-3 py-2 font-mono text-[11px] text-muted-foreground">{timeAgo(t.finished_at)}<div>dựng {renderSec(t) ? fmtSec(renderSec(t)) : "—"}</div></td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Xem" onClick={() => onPlay?.(t.video_url)}><Play className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Mở thư mục" onClick={() => api.openDownloads?.()}><FolderOpen className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Copy tên file" onClick={() => copy(f, "tên file")}><Copy className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Xoá logo Dola" onClick={() => removeWm(t.video_url)}><Eraser className="h-3.5 w-3.5" /></Button>
                  </td>
                </tr>
              );
            })}
            {!rows.length && <tr><td colSpan={7} className="px-3 py-8 text-center text-sm text-muted-foreground">{tasks.length ? "Không có video nào khớp bộ lọc." : "Chưa có video nào — sang tab Studio để tạo."}</td></tr>}
          </tbody>
        </table>
      </div>}
      {msg && <div className="text-xs text-muted-foreground">{msg}</div>}
      {nick && <Badge variant="info" className="cursor-pointer" onClick={() => setNick("")}>Đang lọc nick {nick} ✕</Badge>}
    </div>
  );
}
