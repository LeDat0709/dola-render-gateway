import { useEffect, useState } from "react";
import { Film, Download, RefreshCw } from "lucide-react";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { scanNickVideos, redownloadVideo, timeAgo } from "@/lib/api";
import { runPool } from "@/lib/bundle.js";

// CHECK VIDEO NICK (học đối thủ v1.0.88): quét lịch sử hội thoại của nick — CHỈ ĐỌC, không tốn lượt — để cứu video đã
// dựng xong trên Dola mà chưa về máy (job báo lỗi / quá giờ / IP chết lúc tải).
const STATE = {
  local: ["success", "Đã về máy"],
  failed_but_made: ["warn", "Job báo lỗi — video VẪN có"],
  remote: ["secondary", "Chỉ trên Dola"],
};

// `name` nhận MỘT nick (chuỗi) hoặc NHIỀU nick (mảng) — chọn cả loạt rồi quét một lượt, khỏi bấm từng nick.
export default function NickVideosDialog({ name, onOpenChange }) {
  const names = Array.isArray(name) ? name.filter(Boolean) : (name ? [name] : []);
  const khoa = names.join(",");                 // đổi danh sách mới quét lại, không quét lại mỗi lần render
  const [videos, setVideos] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [xong, setXong] = useState(0);          // đã quét xong mấy nick (hiện tiến trình khi chọn nhiều)
  const [done, setDone] = useState({});   // conversation_id -> link local sau khi tải

  const scan = async () => {
    setErr(""); setVideos(null); setXong(0);
    const ra = [], loi = [];
    // 4 nick một lúc: quét là ĐỌC hội thoại (không gửi gì, không tốn lượt) nên không dính giãn nhịp gửi,
    // nhưng vẫn giới hạn để 18 nick không mở 18 kết nối cùng lúc.
    await runPool(names, 4, async (n) => {
      try { ((await scanNickVideos(n)).videos || []).forEach((v) => ra.push({ ...v, account: n })); }
      catch (e) { loi.push(`${n}: ${e?.message || e}`); }
      finally { setXong((x) => x + 1); }
    });
    if (loi.length) setErr(`${loi.length} nick quét lỗi — ${loi.slice(0, 3).join(" · ")}`);
    setVideos(ra.sort((a, b) => (b.created_at || 0) - (a.created_at || 0)));
  };
  useEffect(() => { if (khoa) { setDone({}); scan(); } }, [khoa]);   // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (v) => {
    setBusy(v.conversation_id);
    try {
      const r = await redownloadVideo(v.task_id || "", v.video_url, v.account || names[0] || "", v.prompt || v.name || "");
      setDone((d) => ({ ...d, [v.conversation_id]: r.url }));
    } catch (e) { setErr(`Tải video ${v.conversation_id.slice(-4)} lỗi: ${e?.message || e}`); }
    finally { setBusy(""); }
  };

  const missing = (videos || []).filter((v) => v.state !== "local" && !done[v.conversation_id]);
  return (
    <Dialog open={!!khoa} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <div className="flex flex-wrap items-center gap-2">
          <Film className="h-4 w-4 text-primary" />
          <span className="text-[15px] font-semibold">Video trên Dola của nick</span>
          <span className="font-mono text-[12px] text-muted-foreground">{names.length > 1 ? `${names.length} nick` : names[0]}</span>
          <span className="ml-auto" />
          <Button variant="outline" size="sm" className="mr-8" onClick={scan} disabled={videos === null}><RefreshCw className={"h-3.5 w-3.5 " + (videos === null ? "animate-spin" : "")} />Quét lại</Button>
        </div>
        <p className="text-[11.5px] text-muted-foreground">Đọc lịch sử hội thoại gần đây của nick — không gửi tin, <b>không tốn lượt</b>. Dùng để lấy lại video của job báo lỗi/quá giờ mà Dola vẫn dựng xong.</p>
        {err && <div className="rounded-md border border-error/40 bg-error/10 px-3 py-2 text-[12px] text-error">{err}</div>}
        {videos === null && <div className="py-8 text-center text-sm text-muted-foreground">Đang quét hội thoại… {names.length > 1 ? `(${xong}/${names.length} nick)` : ""}</div>}
        {videos && !videos.length && !err && <div className="py-8 text-center text-sm text-muted-foreground">Không thấy video nào trong các hội thoại gần đây.</div>}
        {!!(videos || []).length && (
          <>
            <div className="text-[12px] text-muted-foreground">{videos.length} video · <b className={missing.length ? "text-warn" : "text-tertiary"}>{missing.length} chưa về máy</b></div>
            <div className="grid max-h-[60vh] gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
              {videos.map((v) => {
                const local = done[v.conversation_id] || v.local_url;
                const [variant, label] = done[v.conversation_id] ? STATE.local : STATE[v.state] || STATE.remote;
                return (
                  <div key={v.conversation_id} className="flex gap-2 rounded-lg bg-surface p-2">
                    <video className="h-24 w-[54px] flex-none cursor-pointer rounded bg-surface-lowest object-cover" src={(local || v.video_url) + "#t=0.6"} muted preload="metadata"
                      onClick={(e) => { const el = e.currentTarget; if (el.paused) el.play(); else el.pause(); }} title="Bấm để xem/dừng" />
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <Badge variant={variant} className="w-fit">{label}</Badge>
                      <div className="truncate text-[12px]" title={v.prompt || v.name}>{v.prompt || v.name || "(không tên)"}</div>
                      <div className="font-mono text-[10.5px] text-muted-foreground">{v.created_at ? `${new Date(v.created_at * 1000).toLocaleString("vi-VN")} · ${timeAgo(v.created_at)} trước` : ""} · #{v.conversation_id.slice(-6)}</div>
                      <div className="mt-auto flex gap-1.5">
                        {!local && <Button size="sm" className="h-7 text-[11px]" disabled={!!busy} onClick={() => save(v)}>
                          <Download className={"h-3.5 w-3.5 " + (busy === v.conversation_id ? "animate-bounce" : "")} />Tải về máy</Button>}
                        <Button variant="outline" size="sm" className="h-7 text-[11px]" onClick={() => window.open(local || v.video_url, "_blank")}>Mở</Button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
