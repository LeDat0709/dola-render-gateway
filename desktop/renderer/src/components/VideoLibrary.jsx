import { useCallback, useEffect, useMemo, useState } from "react";
import { Play, FolderOpen, Copy, Eraser, Search, Film, RefreshCw, X, ChevronLeft, ChevronRight, ExternalLink, CloudDownload, HardDriveDownload, RadioTower, Layers } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SelectNative } from "@/components/ui/select-native";
import { ViewToggle, useView } from "@/components/ui/view-toggle";
import { api, recentTasks, fnameFromUrl, sttFromUrl, fmtSec, timeAgo, isLocalVideo, redownloadVideo, adminAccounts, scanAllNickVideos } from "@/lib/api";

// Kho video: mọi job đã ra video (tasks.db qua /api/admin/tasks), mỗi dòng ghi rõ nick nào, prompt nào.
// "Quét trên Dola" đọc thêm video CÓ THẬT trên nick mà tool không có job (job đã bị dọn, tải hỏng, tạo ở
// máy khác) — chỉ đọc lịch sử hội thoại, KHÔNG tốn lượt. "Gộp theo prompt" xếp các video cùng một prompt
// vào một nhóm để soi nhanh prompt nào ra được bao nhiêu video, nick nào còn thiếu.
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
  const [lb, setLb] = useState(null);   // chỉ số video đang xem lightbox (null = đóng)
  const [scanned, setScanned] = useState([]);   // video đọc từ Dola (không có job trong tool)
  const [scanning, setScanning] = useState("");
  const [byPrompt, setByPrompt] = useState(() => { try { return localStorage.getItem("dolaVideoGroup") === "1"; } catch { return false; } });
  useEffect(() => { try { localStorage.setItem("dolaVideoGroup", byPrompt ? "1" : "0"); } catch {} }, [byPrompt]);
  const [view, pickView] = useView("dolaVideoView");
  const load = useCallback(async () => {
    setBusy(true);
    const all = await recentTasks(2000);   // server cũ cắt còn 200 — vẫn chạy, chỉ thấy ít video hơn
    setTasks(all.filter((t) => t.status === "completed" && t.video_url));
    setBusy(false);
  }, []);
  useEffect(() => { if (!active) return; load(); const id = setInterval(load, POLL_MS); return () => clearInterval(id); }, [load, active]);

  // Quét mọi nick: lấy video ĐÃ CÓ trên Dola nhưng tool không có job (đọc lịch sử, không tốn lượt).
  const scanDola = async () => {
    setMsg(""); setScanning("Đang lấy danh sách nick…");
    try {
      const acc = await adminAccounts();          // { ok, accounts } — KHÔNG phải mảng
      if (!acc.ok) throw new Error(acc.kind === "auth" ? "sai admin key" : "không đọc được danh sách nick");
      // Nick cookie chết thì đọc lịch sử cũng lỗi — bỏ qua cho khỏi tốn 1 lượt gọi mỗi nick.
      const names = (acc.accounts || []).filter((a) => a.login_ok !== 0).map((a) => a.name).filter(Boolean);
      if (!names.length) throw new Error("không có nick nào còn cookie sống để quét");
      const { videos, errors } = await scanAllNickVideos(names, 50, (i, n, name) => setScanning(`Đang quét ${i}/${n}: ${name}`));
      setScanned(videos);
      setMsg(`✓ Quét xong ${names.length} nick: thấy ${videos.length} video trên Dola.` + (errors.length ? ` ${errors.length} nick không đọc được (${errors[0]}).` : ""));
    } catch (e) { setMsg("Quét lỗi: " + (e?.message || e)); }
    finally { setScanning(""); }
  };
  // Video trên Dola không khớp job nào trong tool → dựng dòng giả để hiện chung một bảng.
  const items = useMemo(() => {
    const seen = new Set(tasks.map((t) => String(t.conversation_id || "")).filter(Boolean));
    const extra = scanned
      .filter((v) => !seen.has(String(v.conversation_id)))
      .map((v) => ({ id: "dola:" + v.conversation_id, account: v.account || "", prompt: v.prompt || v.name || "",
                     video_url: v.local_url || v.video_url, finished_at: v.created_at || 0, started_at: 0,
                     model: "", ratio: "", duration: 0, conversation_id: v.conversation_id, onlyOnDola: true }));
    return [...tasks, ...extra];
  }, [tasks, scanned]);

  const nicks = useMemo(() => [...new Set(items.map((t) => t.account).filter(Boolean))].sort(), [items]);
  const rows = useMemo(() => {
    const kw = q.trim().toLowerCase();
    const since = range === "all" ? 0 : range === "1" ? new Date().setHours(0, 0, 0, 0) / 1000 : Date.now() / 1000 - Number(range) * 86400;
    return items
      .filter((t) => (t.finished_at || t.created_at || 0) >= since)
      .filter((t) => !nick || t.account === nick)
      .filter((t) => !kw || [t.prompt, t.account, fnameFromUrl(t.video_url)].some((v) => String(v || "").toLowerCase().includes(kw)));
  }, [items, q, nick, range]);
  const groups = useMemo(() => {
    if (!byPrompt) return null;
    const m = new Map();   // khoá gộp: prompt đã chuẩn hoá (bỏ khoảng trắng thừa, không phân biệt hoa thường)
    rows.forEach((t, i) => {
      const raw = String(t.prompt || "").trim();
      const key = raw.replace(/\s+/g, " ").toLowerCase() || "(không rõ prompt)";
      const g = m.get(key) || { label: raw || "(không rõ prompt — video tạo ngoài tool)", items: [] };
      g.items.push({ t, i });
      m.set(key, g);
    });
    return [...m.values()].sort((a, b) => b.items.length - a.items.length);
  }, [rows, byPrompt]);
  const perNick = useMemo(() => { const m = new Map(); rows.forEach((t) => m.set(t.account, (m.get(t.account) || 0) + 1)); return m; }, [rows]);
  const todayCount = items.filter((t) => (t.finished_at || 0) >= new Date().setHours(0, 0, 0, 0) / 1000).length;

  const copy = (s, label) => navigator.clipboard.writeText(s).then(() => setMsg(`Đã copy ${label}.`)).catch(() => setMsg("Không copy được."));
  const removeWm = async (u) => {
    setMsg("Đang xoá logo… (dò watermark trôi)");
    try { const r = await api.removeWatermark?.(fnameFromUrl(u)); setMsg(r?.ok ? "✓ Đã xoá logo → " + r.output : "Xoá logo lỗi: " + (r?.error || "?")); }
    catch (e) { setMsg("Lỗi: " + (e?.message || e)); }
  };
  const renderSec = (t) => (t.finished_at && t.started_at ? t.finished_at - t.started_at : 0);
  const [dling, setDling] = useState("");   // id đang tải lại
  const redown = async (t) => {
    setDling(t.id); setMsg("Đang tải lại về máy…");
    try {
      const r = await redownloadVideo(t.id, t.video_url, t.account || "", t.prompt || "");
      setMsg(r?.already ? "Video này đã có trên máy." : "✓ Đã tải về máy: " + (r?.file || ""));
      await load();
    } catch (e) { setMsg("Tải lại lỗi: " + (e?.message || e)); }
    finally { setDling(""); }
  };
  // Lightbox: chuyển video trước/sau + phím tắt (← → Esc). Kẹp chỉ số trong [0, rows-1].
  const step = useCallback((d) => setLb((i) => (i == null ? i : Math.max(0, Math.min(rows.length - 1, i + d)))), [rows.length]);
  useEffect(() => {
    if (lb == null) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setLb(null); else if (e.key === "ArrowRight") step(1); else if (e.key === "ArrowLeft") step(-1); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lb, step]);

  // Một thẻ video dùng chung cho cả kiểu phẳng lẫn kiểu gộp theo prompt (i = chỉ số trong rows, cho lightbox).
  const videoCard = (t, i) => {
  const stt = sttFromUrl(t.video_url); const f = fnameFromUrl(t.video_url);
  const onDisk = isLocalVideo(t.video_url);
  return (
    <div key={t.id} className="flex flex-col gap-2 rounded-xl bg-surface-low p-2.5">
      <div className="group relative aspect-[9/16] max-h-[220px] w-full cursor-pointer overflow-hidden rounded-lg bg-surface-lowest" onClick={() => setLb(i)} title={f}>
        <video className="h-full w-full object-cover" src={t.video_url + "#t=0.6"} muted preload="metadata" />
        <span className="absolute left-1.5 top-1.5 rounded bg-black/70 px-1.5 py-0.5 font-mono text-[11px] font-bold text-primary">{stt ? `#${stt}` : "—"}</span>
        <span className="absolute right-1.5 top-1.5 rounded bg-black/70 px-1.5 py-0.5 font-mono text-[10px] text-white">{t.duration ? `${t.duration}s` : ""}</span>
        <span className={"absolute bottom-1.5 left-1.5 rounded px-1.5 py-0.5 font-mono text-[10px] " + (onDisk ? "bg-tertiary/85 text-white" : "bg-warn/85 text-black")}>{onDisk ? "✓ đã về máy" : "☁ chỉ trên Dola"}</span>
        <span className="absolute inset-0 flex items-center justify-center opacity-0 transition group-hover:bg-black/30 group-hover:opacity-100"><span className="rounded-full bg-primary p-2"><Play className="h-4 w-4 fill-primary-foreground text-primary-foreground" /></span></span>
      </div>
      <div className="flex items-center gap-1 font-mono text-[11.5px]"><span className="truncate font-semibold" title={t.account}>{t.account || "—"}</span><span className="ml-auto flex-none text-[10.5px] text-muted-foreground">{perNick.get(t.account)} video</span></div>
      <div className="line-clamp-3 text-[12px] leading-snug text-on-variant" title={t.prompt}>{t.prompt}</div>
      <div className="flex items-center gap-1 font-mono text-[10.5px] text-muted-foreground"><span className="truncate">{t.model || "—"} · {t.ratio || "—"}</span><span className="ml-auto flex-none">{timeAgo(t.finished_at)}{renderSec(t) ? ` · dựng ${fmtSec(renderSec(t))}` : ""}</span></div>
      <div className="flex items-center gap-0.5 border-t border-surface pt-1.5">
        <button type="button" className="font-mono text-[10.5px] text-muted-foreground hover:text-primary" onClick={() => copy(t.prompt || "", "prompt")}>copy prompt</button>
        <span className="ml-auto" />
        {!onDisk && <Button variant="ghost" size="icon" className="h-7 w-7 text-warn" title="Tải lại về máy" disabled={dling === t.id} onClick={() => redown(t)}><HardDriveDownload className={"h-3.5 w-3.5 " + (dling === t.id ? "animate-pulse" : "")} /></Button>}
        <Button variant="ghost" size="icon" className="h-7 w-7" title="Xem" onClick={() => setLb(i)}><Play className="h-3.5 w-3.5" /></Button>
        <Button variant="ghost" size="icon" className="h-7 w-7" title="Mở thư mục" onClick={() => api.openDownloads?.()}><FolderOpen className="h-3.5 w-3.5" /></Button>
        <Button variant="ghost" size="icon" className="h-7 w-7" title="Copy tên file" onClick={() => copy(f, "tên file")}><Copy className="h-3.5 w-3.5" /></Button>
        <Button variant="ghost" size="icon" className="h-7 w-7" title="Xoá logo Dola" onClick={() => removeWm(t.video_url)}><Eraser className="h-3.5 w-3.5" /></Button>
      </div>
    </div>
  );
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2">
        {[["Video đã tạo", items.length, "text-foreground", scanned.length ? `${scanned.length} thấy trên Dola` : `${todayCount} hôm nay`],
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
          <Button variant={byPrompt ? "default" : "outline"} size="sm" onClick={() => setByPrompt((v) => !v)} title="Xếp các video cùng prompt vào một nhóm (kiểu lưới)"><Layers className="h-3.5 w-3.5" />Gộp theo prompt</Button>
          <Button variant="outline" size="sm" onClick={scanDola} disabled={!!scanning} title="Đọc lịch sử hội thoại của MỌI nick để lấy video đã dựng xong trên Dola — không tốn lượt"><RadioTower className={"h-3.5 w-3.5 " + (scanning ? "animate-pulse" : "")} />{scanning || "Quét trên Dola"}</Button>
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

      {view === "grid" && (groups ? (
        <div className="space-y-3">
          {groups.map((g) => (
            <section key={g.label} className="rounded-xl bg-surface p-3">
              <div className="mb-2 flex items-start gap-2">
                <Layers className="mt-0.5 h-4 w-4 flex-none text-primary" />
                <div className="min-w-0 flex-1">
                  <div className="line-clamp-2 text-[13px] font-medium leading-snug" title={g.label}>{g.label}</div>
                  <div className="font-mono text-[10.5px] text-muted-foreground">
                    {new Set(g.items.map((x) => x.t.account)).size} nick · {g.items.filter((x) => !isLocalVideo(x.t.video_url)).length} chưa về máy
                  </div>
                </div>
                <span className="flex-none rounded-full bg-primary/15 px-2 py-0.5 font-mono text-[11px] text-primary">{g.items.length} video</span>
                <button type="button" className="flex-none font-mono text-[10.5px] text-muted-foreground hover:text-primary" onClick={() => copy(g.label, "prompt")}>copy prompt</button>
              </div>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
                {g.items.map(({ t, i }) => videoCard(t, i))}
              </div>
            </section>
          ))}
          {!rows.length && <div className="col-span-full rounded-xl bg-surface-low px-3 py-8 text-center text-sm text-muted-foreground">{items.length ? "Không có video nào khớp bộ lọc." : "Chưa có video nào — bấm Quét trên Dola hoặc sang tab Studio để tạo."}</div>}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
          {rows.map((t, i) => videoCard(t, i))}
          {!rows.length && <div className="col-span-full rounded-xl bg-surface-low px-3 py-8 text-center text-sm text-muted-foreground">{items.length ? "Không có video nào khớp bộ lọc." : "Chưa có video nào — bấm Quét trên Dola hoặc sang tab Studio để tạo."}</div>}
        </div>
      ))}

      {view === "table" && <div className="overflow-x-auto rounded-xl bg-surface-low">
        <table className="w-full border-collapse text-[12.5px]">
          <thead><tr className="bg-surface-lowest">
            <th className={th + " w-[72px]"}>Video</th><th className={th + " w-[64px]"}>Số</th><th className={th}>Nick</th><th className={th}>Prompt</th>
            <th className={th + " w-[150px]"}>Cấu hình</th><th className={th + " w-[120px]"}>Thời gian</th><th className={th + " w-[132px] text-right"}>Hành động</th>
          </tr></thead>
          <tbody>
            {rows.map((t, i) => {
              const stt = sttFromUrl(t.video_url); const f = fnameFromUrl(t.video_url);
              return (
                <tr key={t.id} className="border-t border-surface hover:bg-surface/60">
                  <td className="px-3 py-2"><video className="h-[46px] w-9 cursor-pointer rounded object-cover" src={t.video_url + "#t=0.6"} muted preload="metadata" onClick={() => setLb(i)} title={f} /></td>
                  <td className="px-3 py-2 font-mono text-[12px] font-bold text-primary">{stt ? `#${stt}` : "—"}</td>
                  <td className="px-3 py-2 font-mono text-[12px]">{t.account || "—"}<div className="text-[10.5px] text-muted-foreground">{perNick.get(t.account)} video</div></td>
                  <td className="max-w-[420px] px-3 py-2">
                    <div className="line-clamp-2 leading-snug" title={t.prompt}>{t.prompt}</div>
                    <button type="button" className="mt-0.5 font-mono text-[10.5px] text-muted-foreground hover:text-primary" onClick={() => copy(t.prompt || "", "prompt")}>copy prompt</button>
                  </td>
                  <td className="px-3 py-2 font-mono text-[11px] text-muted-foreground">{t.model || "—"}<div>{t.ratio || "—"} · {t.duration ? `${t.duration}s` : "—"}</div></td>
                  <td className="px-3 py-2 font-mono text-[11px] text-muted-foreground">{timeAgo(t.finished_at)}<div>dựng {renderSec(t) ? fmtSec(renderSec(t)) : "—"}</div></td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Xem" onClick={() => setLb(i)}><Play className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Mở thư mục" onClick={() => api.openDownloads?.()}><FolderOpen className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Copy tên file" onClick={() => copy(f, "tên file")}><Copy className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Xoá logo Dola" onClick={() => removeWm(t.video_url)}><Eraser className="h-3.5 w-3.5" /></Button>
                  </td>
                </tr>
              );
            })}
            {!rows.length && <tr><td colSpan={7} className="px-3 py-8 text-center text-sm text-muted-foreground">{items.length ? "Không có video nào khớp bộ lọc." : "Chưa có video nào — sang tab Studio để tạo."}</td></tr>}
          </tbody>
        </table>
      </div>}
      {msg && <div className="text-xs text-muted-foreground">{msg}</div>}
      {nick && <Badge variant="info" className="cursor-pointer" onClick={() => setNick("")}>Đang lọc nick {nick} ✕</Badge>}

      {lb != null && rows[lb] && (() => {
        const t = rows[lb]; const stt = sttFromUrl(t.video_url); const f = fnameFromUrl(t.video_url);
        return (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4" onClick={() => setLb(null)}>
            <button type="button" title="Đóng (Esc)" className="absolute right-4 top-4 rounded-full bg-white/10 p-2 text-white hover:bg-white/20" onClick={() => setLb(null)}><X className="h-5 w-5" /></button>
            <button type="button" title="Trước (←)" disabled={lb <= 0} className="absolute left-3 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2.5 text-white hover:bg-white/20 disabled:opacity-30" onClick={(e) => { e.stopPropagation(); step(-1); }}><ChevronLeft className="h-6 w-6" /></button>
            <button type="button" title="Sau (→)" disabled={lb >= rows.length - 1} className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2.5 text-white hover:bg-white/20 disabled:opacity-30" onClick={(e) => { e.stopPropagation(); step(1); }}><ChevronRight className="h-6 w-6" /></button>
            <div className="flex max-h-[88vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-surface md:flex-row" onClick={(e) => e.stopPropagation()}>
              <video key={t.id} className="max-h-[88vh] w-full flex-1 bg-black object-contain md:max-w-[62%]" src={t.video_url} controls autoPlay />
              <aside className="flex w-full flex-col gap-3 overflow-y-auto p-4 md:w-[38%]">
                <div className="flex items-center gap-2">
                  <span className="rounded bg-primary/15 px-2 py-0.5 font-mono text-[12px] font-bold text-primary">{stt ? `#${stt}` : "—"}</span>
                  <span className="rounded bg-surface-high px-2 py-0.5 font-mono text-[11px] text-muted-foreground">{t.duration ? `${t.duration}s` : ""}</span>
                  <span className={"rounded px-2 py-0.5 font-mono text-[11px] " + (isLocalVideo(t.video_url) ? "bg-tertiary/15 text-tertiary" : "bg-warn/15 text-warn")}>{isLocalVideo(t.video_url) ? "✓ đã về máy" : "☁ chỉ trên Dola"}</span>
                  <span className="ml-auto font-mono text-[11px] text-muted-foreground">{lb + 1}/{rows.length}</span>
                </div>
                <div className="font-mono text-[13px] font-semibold">{t.account || "—"}</div>
                <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-on-variant">{t.prompt || "—"}</div>
                <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 font-mono text-[11px] text-muted-foreground">
                  <div><dt className="opacity-60">Model</dt><dd className="text-foreground">{t.model || "—"}</dd></div>
                  <div><dt className="opacity-60">Tỉ lệ</dt><dd className="text-foreground">{t.ratio || "—"}</dd></div>
                  <div><dt className="opacity-60">Tạo lúc</dt><dd className="text-foreground">{timeAgo(t.finished_at)}</dd></div>
                  <div><dt className="opacity-60">Thời gian dựng</dt><dd className="text-foreground">{renderSec(t) ? fmtSec(renderSec(t)) : "—"}</dd></div>
                  <div className="col-span-2 min-w-0"><dt className="opacity-60">Tệp</dt><dd className="truncate text-foreground" title={f}>{f}</dd></div>
                </dl>
                <div className="mt-auto flex flex-wrap gap-2 border-t border-surface-high pt-3">
                  {!isLocalVideo(t.video_url) && <Button variant="default" size="sm" disabled={dling === t.id} onClick={() => redown(t)}><CloudDownload className={"h-3.5 w-3.5 " + (dling === t.id ? "animate-pulse" : "")} />Tải lại về máy</Button>}
                  <Button variant="secondary" size="sm" onClick={() => copy(t.prompt || "", "prompt")}><Copy className="h-3.5 w-3.5" />Copy prompt</Button>
                  <Button variant="outline" size="sm" onClick={() => api.openDownloads?.()}><FolderOpen className="h-3.5 w-3.5" />Thư mục</Button>
                  <Button variant="outline" size="sm" onClick={() => removeWm(t.video_url)}><Eraser className="h-3.5 w-3.5" />Xoá logo</Button>
                  <Button variant="ghost" size="sm" onClick={() => onPlay?.(t.video_url)}><ExternalLink className="h-3.5 w-3.5" />Mở ngoài</Button>
                </div>
              </aside>
            </div>
          </div>
        );
      })()}
    </div>
  );
}
