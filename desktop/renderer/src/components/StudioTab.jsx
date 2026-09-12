import { useEffect, useMemo, useRef, useState } from "react";
import { Play, RotateCw, Settings, Trash2, FolderOpen, Copy, ArrowDown, Repeat, Stethoscope, Square, RefreshCw, Eraser, Power, Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { SelectNative } from "@/components/ui/select-native";
import { api, cfg, submitJob, pollJob, fmtError, creditCost, firstLine, fnameFromUrl, sttFromUrl, accState, canRunAccount, ACC_BADGE, deleteAccount, STAGE_TEXT, riskyPrompt, durationMismatch, deadNicks, setConcurrency, patchAccount, wakeAccount, inflightTasks, accState as accStateOf } from "@/lib/api";

const MODELS = ["seedance-2.0", "seedance-2.5"];
const RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4"];
// Dola đã bỏ 30 giây (chỉ còn 4–15). Chọn 30 thì Dola hỏi lại rồi tự hạ 15 → mất thêm
// 1–2 phút giữ nick, nên mặc định 15 và nói rõ trên nhãn.
const DURS = [["10", "10s"], ["15", "15s"], ["30", "30s → Dola hạ 15s"]];

export default function StudioTab({ health, onRefresh, onPlay }) {
  const accounts = health?.accounts || [];
  const [def, setDef] = useState({ model: "seedance-2.5", dur: "15", ratio: "9:16" });
  const [bulk, setBulk] = useState("");
  const [rows, setRows] = useState({});           // nick -> {prompt,model,ratio,dur,phase,status,startedAt,videoUrl,errorRaw}
  const [sel, setSel] = useState({});             // nick -> bool
  const [gen, setGen] = useState("");
  const [clock, setClock] = useState(0);
  const inflight = useRef(new Set());
  const riskAns = useRef(new Map());              // prompt -> đã đồng ý gửi thử chưa (hỏi 1 lần)
  const [conc, setConc] = useState({ send: "", login: "" });
  const stop = useRef(false);
  const vidDir = useRef("");

  useEffect(() => { api.getVideoDir?.().then((r) => (vidDir.current = r?.abs || "")).catch(() => {}); }, []);
  // Mở app lên mà server vẫn đang render dở: bám lại job đó, đừng để bảng trống trông như treo.
  useEffect(() => {
    (async () => {
      for (const t of await inflightTasks()) {
        const n = t.account;
        if (!n || inflight.current.has(n)) continue;
        inflight.current.add(n);
        setRow(n, { prompt: t.prompt || "", phase: "running", stage: t.status === "queued" ? "queued" : "rendering",
                    startedAt: (t.started_at || t.created_at || Date.now() / 1000) * 1000, errorRaw: "", videoUrl: "" });
        watchJob(n, t.id).catch((e) => setRow(n, { phase: "error", errorRaw: "Không theo dõi được job: " + (e?.message || e) }))
          .finally(() => inflight.current.delete(n));
      }
    })();
  }, []);
  // Ô số luồng: chỉ điền khi đang trống, không giật giá trị lúc người dùng đang gõ.
  // Phụ thuộc vào 2 số, KHÔNG phải cả object health (App thay object mới mỗi 1.5s → effect chạy mỗi
  // lần poll, người dùng vừa xoá ô để gõ số mới là bị điền lại mặc định).
  useEffect(() => {
    if (!health) return;
    setConc((c) => ({ send: c.send || String(health.max_concurrency || 3),
                      login: c.login || String(health.login_concurrency || 3) }));
  }, [health?.max_concurrency, health?.login_concurrency]);   // eslint-disable-line react-hooks/exhaustive-deps
  // đồng hồ 1s khi có job chạy
  useEffect(() => {
    const any = Object.values(rows).some((r) => r.phase === "running");
    if (!any) return;
    const t = setInterval(() => setClock((c) => c + 1), 1000);
    return () => clearInterval(t);
  }, [rows]);
  // khôi phục mặc định đã lưu
  useEffect(() => { try { const d = JSON.parse(localStorage.getItem("dolaDef") || "{}"); if (d.model) setDef(d); if (d.bulk != null) setBulk(d.bulk); } catch {} }, []);
  useEffect(() => { try { localStorage.setItem("dolaDef", JSON.stringify({ ...def, bulk })); } catch {} }, [def, bulk]);

  const row = (n) => rows[n] || { prompt: "", model: def.model, ratio: def.ratio, dur: def.dur, phase: "idle", status: "—" };
  const setRow = (n, patch) => setRows((p) => ({ ...p, [n]: { ...(p[n] || row(n)), ...patch } }));
  const elapsed = (ms) => { const s = Math.max(0, Math.floor((Date.now() - ms) / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

  async function runOne(n) {
    const acc = accounts.find((a) => a.account === n);
    const s = row(n);
    const prompt = (s.prompt || "").trim() || firstLine(bulk);
    if (!prompt) { setRow(n, { phase: "error", errorRaw: "Chưa nhập prompt", status: "warn" }); return false; }
    const risky = riskyPrompt(prompt);
    if (risky) {
      if (!riskAns.current.has(prompt)) riskAns.current.set(prompt, window.confirm(
        `Prompt này Dola nhiều khả năng CHẶN (${risky}).\n\n`
        + "Bị chặn thì không trừ lượt, nhưng cũng không ra video — phải đổi NỘI DUNG cảnh quay. "
        + "Viết lại cho nhẹ chữ không giúp: Dola duyệt nội dung, không duyệt từ khoá.\n\nVẫn gửi thử?"));
      if (!riskAns.current.get(prompt)) { setRow(n, { phase: "idle", status: "⏸ chưa gửi (prompt dễ bị chặn)" }); return false; }
    }
    // Server tự co các mốc thời gian trong prompt về đúng thời lượng (fit_prompt_to_duration) — chỉ báo, không chặn.
    const over = durationMismatch(prompt, s.dur);
    if (over) setRow(n, { status: `⏱ prompt ~${over}s → tự co về ${s.dur}s` });
    if (acc && acc.remaining != null) {
      const need = creditCost(s.dur);
      if (acc.remaining < need) { setRow(n, { phase: "error", errorRaw: `Không đủ điểm cho ${s.dur}s (cần ${need}, còn ${acc.remaining}). Giảm còn 10–15s hoặc đổi nick.` }); return false; }
    }
    if (inflight.current.has(n)) return true;   // đã chạy ở nơi khác, không tính là lỗi
    inflight.current.add(n);
    setRow(n, { prompt, phase: "running", stage: "queued", startedAt: Date.now(), errorRaw: "", videoUrl: "" });
    try {
      const id = await submitJob(prompt, { model: s.model, duration: parseInt(s.dur, 10), ratio: s.ratio, account: n });
      return await watchJob(n, id);
    } catch (e) {
      const msg = e?.message || String(e);
      setRow(n, { phase: "error", errorRaw: msg });
      if (/không tồn tại/i.test(msg)) onRefresh();   // bảng đang cũ → nạp lại danh sách nick
      return false;
    }
    finally { inflight.current.delete(n); }
  }
  // Theo dõi một job đã có id — dùng cho cả job vừa gửi và job đang chạy dở từ lần mở app trước.
  async function watchJob(n, id) {
    let stage = "queued", fails = 0;
    while (true) {
      await new Promise((r) => setTimeout(r, 3000));
      if (stop.current) { setRow(n, { phase: "idle", status: "⏸ đã dừng theo dõi" }); return true; }
      let pj;
      try { pj = await pollJob(id); fails = 0; }
      catch (e) {
        // Server tắt / khởi động lại giữa chừng: job vẫn nằm trong tasks.db, chờ server lên rồi hỏi tiếp.
        // 404 = server không còn job này (đổi server / DB mới) → báo lỗi thay vì quay vòng vô tận.
        if (e?.status === 404 || ++fails >= 20) {
          setRow(n, { phase: "error", errorRaw: e?.status === 404 ? "Server không còn job này (đã đổi server hoặc xoá dữ liệu?)" : "Mất kết nối server quá 1 phút: " + (e?.message || e) });
          return false;
        }
        setRow(n, { status: `⏳ chờ server trả lời (${fails})` });
        continue;
      }
      if (pj.status === "completed") { setRow(n, { phase: "done", videoUrl: pj.video_url }); api.saveVideo?.(pj.video_url); return true; }
      if (pj.status === "failed") { setRow(n, { phase: "error", errorRaw: pj.error || "?" }); return false; }
      if (pj.stage && pj.stage !== stage) { stage = pj.stage; setRow(n, { stage }); }
    }
  }

  // "Chạy sẵn sàng" = đúng nick badge xanh, dùng chung accState (đã tính cả busy/cooling/scheduling).
  const canRun = canRunAccount;
  const readyNicks = () => accounts.filter(canRun).map((a) => a.account);
  async function runBatch(ns, empty) {
    if (!ns.length) { setGen(empty); return; }
    stop.current = false;
    // Phản hồi ngay trên từng dòng: trước đây bấm Chạy là bảng đứng im tới 12s (chờ verify)
    // nên trông như tool không nhận lệnh.
    ns.forEach((n) => setRow(n, { phase: "running", stage: "checking", startedAt: Date.now(), errorRaw: "", videoUrl: "" }));
    setGen("Kiểm tra phiên đăng nhập trước khi chạy…");
    const dead = await deadNicks(ns);
    dead.forEach((n) => setRow(n, { phase: "error", errorRaw: "Cookie hết hạn — đăng nhập lại nick này rồi chạy lại." }));
    const blocked = ns.filter((n) => {
      const a = accounts.find((x) => x.account === n);
      return a && !canRun(a) && accStateOf(a) !== "busy";   // busy = đang chạy, không tính là chặn
    });
    // Nick bị chặn cũng phải hiện lý do NGAY TRÊN DÒNG, không chỉ một dòng chữ nhỏ ở cuối bảng.
    blocked.forEach((n) => {
      const a = accounts.find((x) => x.account === n) || {};
      const why = { off: "nick đang tắt lịch — bấm 'Bật lịch tất cả'",
                    cooling: "nick đang nghỉ (risk-control) — bấm 'Bỏ nghỉ tất cả'",
                    quota: "hết lượt/điểm hôm nay", dead: "cookie chết — đăng nhập lại nick" }[accStateOf(a)]
                 || "nick chưa chạy được";
      setRow(n, { phase: "error", errorRaw: why });
    });
    const run = ns.filter((n) => !dead.includes(n) && !blocked.includes(n));
    if (!run.length) {
      setGen(`Không nick nào chạy được: ${dead.length} cookie chết · ${blocked.length} tắt lịch/nghỉ/hết lượt.`);
      return;
    }
    const skipped = [dead.length ? `${dead.length} cookie chết` : "", blocked.length ? `${blocked.length} tắt lịch/hết lượt` : ""].filter(Boolean).join(" · ");
    setGen(`Đang chạy ${run.length} nick…` + (skipped ? ` (bỏ ${skipped})` : ""));
    const res = await Promise.all(run.map(runOne));   // runOne trả true=ok / false=lỗi
    const bad = run.filter((_, i) => res[i] === false);
    setGen(bad.length ? `Xong ${run.length - bad.length}/${run.length} nick. Lỗi: ${bad.join(", ")}.` : `Xong ${run.length} nick.`);
  }
  // Cooldown chống risk-control giữ nick 30 phút; không có nút này thì chỉ còn cách ngồi chờ.
  async function wakeAllCooling() {
    const cooling = accounts.filter((a) => a.cooling).map((a) => a.account);
    if (!cooling.length) { setGen("Không có nick nào đang nghỉ."); return; }
    setGen(`Đang bỏ nghỉ ${cooling.length} nick…`);
    const res = await Promise.all(cooling.map((n) => wakeAccount(n).then(() => true).catch(() => false)));
    const ok = res.filter(Boolean).length;
    setGen(`✓ Bỏ nghỉ ${ok}/${cooling.length} nick. Lưu ý: nick nghỉ vì Dola bắt captcha — chạy lại ngay có thể bị bắt tiếp.`);
    onRefresh();
  }

  async function enableAllScheduling() {
    const off = accounts.filter((a) => a.scheduling === false).map((a) => a.account);
    if (!off.length) { setGen("Không có nick nào đang tắt lịch."); return; }
    setGen(`Đang bật lịch cho ${off.length} nick…`);
    const res = await Promise.all(off.map((n) => patchAccount(n, { scheduling: true }).then(() => true).catch(() => false)));
    const ok = res.filter(Boolean).length;
    setGen(`✓ Bật lịch ${ok}/${off.length} nick.` + (ok < off.length ? " Vài nick lỗi — xem tab Tài khoản." : ""));
    onRefresh();
  }

  // Đổi số luồng ngay lúc đang chạy: server nhả thêm slot, job đang chờ chạy liền.
  async function applyConc() {
    const r = await setConcurrency(parseInt(conc.send, 10), parseInt(conc.login, 10));
    if (r?.ok) {
      setGen(`✓ ${r.max_concurrency} nick gửi cùng lúc · ${r.login_concurrency} đăng nhập cùng lúc`
             + (r.max_concurrency >= 8 ? " — mỗi slot là 1 Chrome (~0.4GB), coi RAM." : ""));
      onRefresh();
    } else setGen("Lỗi đổi luồng: " + (r?.error || "?"));
  }
  const runSelected = () => runBatch(Object.keys(sel).filter((n) => sel[n]), "Chọn ít nhất 1 nick.");
  const runReady = () => runBatch(readyNicks(), "Không có nick sẵn sàng.");
  const retryFailed = () => runBatch(accounts.map((a) => a.account).filter((n) => rows[n]?.phase === "error"), "Không có nick lỗi.");
  const stopAll = () => { stop.current = true; setGen("Đã dừng theo dõi (video có thể vẫn hoàn tất trên Dola)."); };
  const fillAll = () => { const p = firstLine(bulk); accounts.forEach((a) => setRow(a.account, { prompt: p })); setGen("Đã điền prompt cho tất cả nick."); };
  const fillLines = () => { const ps = bulk.split(/\r?\n/).map((x) => x.trim()).filter(Boolean); accounts.forEach((a, i) => ps[i] && setRow(a.account, { prompt: ps[i] })); setGen(`Đã chia ${Math.min(ps.length, accounts.length)} prompt.`); };
  const syncDef = () => { accounts.forEach((a) => setRow(a.account, { model: def.model, ratio: def.ratio, dur: def.dur })); setGen("Đã đồng bộ mặc định."); };
  async function verifyAll() { setGen("Đang kiểm tra phiên các nick…"); try { const r = await api.verifyAll?.(); if (r?.ok) { const dead = (r.results || []).filter((x) => !x.ok).map((x) => x.name); onRefresh(); setGen(dead.length ? `Cookie chết: ${dead.join(", ")}` : "Tất cả nick còn đăng nhập tốt."); } else setGen("Lỗi kiểm tra: " + (r?.error || "?")); } catch (e) { setGen("Lỗi: " + e.message); } }
  const removeWm = async (u) => {
    setGen("Đang xoá logo… (dò watermark trôi)");
    try {
      const r = await api.removeWatermark?.(fnameFromUrl(u));
      if (r?.ok) { setGen("✓ Đã xoá logo → " + r.output); api.openDownloads?.(); }
      else setGen("Xoá logo lỗi: " + (r?.error || "?"));
    } catch (e) { setGen("Lỗi: " + e.message); }
  };
  const copyPath = (u) => { const p = vidDir.current ? vidDir.current + "/" + fnameFromUrl(u) : u; navigator.clipboard.writeText(p).then(() => setGen("Đã copy: " + p)).catch(() => {}); };
  const relogin = async (n) => { setGen(`Đăng nhập lại ${n}…`); try { const r = await api.loginElectron?.(n, "ja"); setGen(r?.ok ? "✓ đăng nhập lại xong" : "Lỗi: " + (r?.error || "?")); onRefresh(); } catch (e) { setGen("Lỗi: " + e.message); } };
  const setProxy = async (n) => { const cur = (await api.getProxy?.(n))?.proxy || ""; const v = window.prompt(`Proxy cho ${n} (trống = proxy chung):`, cur); if (v === null) return; const r = await api.setProxy?.(n, v); setGen(r?.ok ? `✓ proxy ${n}: ${r.proxy}` : "Lỗi: " + r?.error); };
  const del = async (n) => { if (!window.confirm(`Xoá hẳn nick ${n}? Không hoàn tác.`)) return; try { await deleteAccount(n); setGen(`Đã xoá ${n}`); } catch (e) { setGen("Lỗi xoá " + n + ": " + (e?.message || e)); } finally { onRefresh(); } };

  const allSel = accounts.length > 0 && accounts.every((a) => sel[a.account]);
  // pill trung thực: xanh CHỈ khi nick thật sự chạy được (còn lượt ngày + còn điểm + cookie sống)
  const pill = (a) => { const [v, t] = ACC_BADGE[accState(a)]; return <Badge variant={v}>{t}</Badge>; };

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="rounded-lg border bg-muted/30 p-4">
        <Textarea rows={4} value={bulk} onChange={(e) => setBulk(e.target.value)} placeholder={"Nhiều prompt, mỗi dòng 1 cái:\ncon mèo lướt sóng\ncon chó chạy trên biển"} />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Mặc định:</span>
          <SelectNative className="w-auto" value={def.model} onChange={(e) => setDef({ ...def, model: e.target.value })}>{MODELS.map((m) => <option key={m}>{m}</option>)}</SelectNative>
          <SelectNative className="w-auto" value={def.dur} onChange={(e) => setDef({ ...def, dur: e.target.value })}>{DURS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</SelectNative>
          <SelectNative className="w-auto" value={def.ratio} onChange={(e) => setDef({ ...def, ratio: e.target.value })}>{RATIOS.map((m) => <option key={m}>{m}</option>)}</SelectNative>
          <Button variant="outline" size="sm" onClick={fillAll}><ArrowDown className="h-3.5 w-3.5" />Điền tất cả</Button>
          <Button variant="outline" size="sm" onClick={fillLines}><ArrowDown className="h-3.5 w-3.5" />Mỗi dòng 1 nick</Button>
          <Button variant="outline" size="sm" onClick={syncDef}><Repeat className="h-3.5 w-3.5" />Đồng bộ mặc định</Button>
          <Button variant="outline" size="sm" onClick={verifyAll}><Stethoscope className="h-3.5 w-3.5" />Kiểm tra tất cả</Button>
          <Button variant="outline" size="sm" onClick={enableAllScheduling}><Power className="h-3.5 w-3.5" />Bật lịch tất cả</Button>
          <Button variant="outline" size="sm" onClick={wakeAllCooling}><Clock className="h-3.5 w-3.5" />Bỏ nghỉ tất cả</Button>
          <span className="flex-1" />
          <Button variant="outline" size="sm" onClick={runReady}><Play className="h-3.5 w-3.5" />Chạy sẵn sàng</Button>
          <Button variant="outline" size="sm" onClick={retryFailed}><RefreshCw className="h-3.5 w-3.5" />Chạy lại lỗi</Button>
          <Button variant="outline" size="sm" onClick={stopAll}><Square className="h-3.5 w-3.5" />Dừng</Button>
          <Button size="sm" onClick={runSelected}><Play className="h-3.5 w-3.5" />Chạy đã chọn</Button>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3 text-xs text-muted-foreground">
          <span>Luồng: tối đa <b className="text-foreground">{accounts.filter(canRun).length}</b> video song song (1 video/nick)
            {health?.pending_tasks ? ` · ${health.pending_tasks} job đang chạy/chờ` : ""}
            {accounts.filter((a) => a.scheduling === false).length
              ? ` · ${accounts.filter((a) => a.scheduling === false).length} nick tắt lịch` : ""}
            {accounts.filter((a) => a.cooling).length
              ? ` · ${accounts.filter((a) => a.cooling).length} nick đang nghỉ (risk-control)` : ""}</span>
          <span className="flex-1" />
          <span>Nick gửi cùng lúc</span>
          <Input className="h-8 w-16" type="number" min={1} max={health?.max_browser_slots || 24}
                 value={conc.send} onChange={(e) => setConc({ ...conc, send: e.target.value })} />
          <span>Đăng nhập cùng lúc</span>
          <Input className="h-8 w-16" type="number" min={1} max={health?.max_login_slots || 12}
                 value={conc.login} onChange={(e) => setConc({ ...conc, login: e.target.value })} />
          <Button variant="outline" size="sm" onClick={applyConc}>Áp dụng</Button>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-xs font-medium text-muted-foreground">
              <th className="h-11 w-10 px-3"><input type="checkbox" checked={allSel} onChange={(e) => { const v = e.target.checked; const o = {}; accounts.forEach((a) => (o[a.account] = v)); setSel(o); }} /></th>
              <th className="px-3">Nick</th><th className="px-3">Prompt</th><th className="w-40 px-3">Model</th><th className="w-24 px-3">Tỉ lệ</th><th className="w-[84px] px-3">Giây</th><th className="w-44 px-3">Trạng thái</th><th className="w-28 px-3" />
            </tr>
          </thead>
          <tbody>
            {!health && <tr><td colSpan={8} className="p-4 text-sm text-muted-foreground">Server chưa chạy — bấm "Bật server".</td></tr>}
            {health && accounts.length === 0 && <tr><td colSpan={8} className="p-4 text-sm text-muted-foreground">Chưa có nick — thêm ở tab Tài khoản.</td></tr>}
            {accounts.map((a) => {
              const n = a.account; const s = row(n);
              return (
                <tr key={n} className="border-b transition-colors hover:bg-white/[.025]">
                  <td className="px-3"><input type="checkbox" checked={!!sel[n]} onChange={(e) => setSel((p) => ({ ...p, [n]: e.target.checked }))} /></td>
                  <td className="px-3 py-2.5">
                    <div className="font-semibold">{n}</div>
                    <div className="mt-1">{pill(a)}</div>
                    {a.cooling && a.cooldown_until > 0 && (
                      <div className="mt-0.5 text-[10.5px] text-amber-400">
                        nghỉ còn {Math.max(1, Math.ceil((a.cooldown_until - Date.now() / 1000) / 60))} phút</div>
                    )}
                    <div className="mt-1 text-[10.5px] text-muted-foreground">{a.used_today}/{a.limit} hôm nay{a.remaining != null ? ` · còn ${a.remaining}` : ""}</div>
                  </td>
                  <td className="px-3"><Input value={s.prompt} placeholder={`prompt cho ${n}…`} onChange={(e) => setRow(n, { prompt: e.target.value })} /></td>
                  <td className="px-3"><SelectNative value={s.model} onChange={(e) => setRow(n, { model: e.target.value })}>{MODELS.map((m) => <option key={m}>{m}</option>)}</SelectNative></td>
                  <td className="px-3"><SelectNative value={s.ratio} onChange={(e) => setRow(n, { ratio: e.target.value })}>{RATIOS.map((m) => <option key={m}>{m}</option>)}</SelectNative></td>
                  <td className="px-3"><SelectNative value={s.dur} onChange={(e) => setRow(n, { dur: e.target.value })}>{DURS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</SelectNative></td>
                  <td className="px-3 py-2.5"><StatusCell s={s} clock={clock} elapsed={elapsed} onPlay={onPlay} onOpen={() => api.openDownloads?.()} onCopy={copyPath} onRemoveWm={removeWm} /></td>
                  <td className="whitespace-nowrap px-3 text-right">
                    <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-foreground" title="Chạy" onClick={() => { stop.current = false; runOne(n); }}><Play className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-foreground" title="Đăng nhập lại" onClick={() => relogin(n)}><RotateCw className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-foreground" title="Proxy riêng" onClick={() => setProxy(n)}><Settings className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-foreground" title="Xoá" onClick={() => del(n)}><Trash2 className="h-3.5 w-3.5" /></Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {gen && <div className="text-xs text-muted-foreground">{gen}</div>}
    </div>
  );
}

function StatusCell({ s, elapsed, onPlay, onOpen, onCopy, onRemoveWm }) {
  if (s.phase === "running") return (
    <div>
      <div className="flex items-center gap-1.5 text-indigo-300 tabular-nums">⏳ {elapsed(s.startedAt)}<span className="text-[10.5px] text-muted-foreground">{STAGE_TEXT[s.stage] || "đang tạo…"}</span></div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted"><div className="h-full w-2/5 animate-[slide_1.15s_linear_infinite] rounded-full bg-gradient-to-r from-indigo-400 to-indigo-600" /></div>
    </div>
  );
  if (s.phase === "done") { const stt = sttFromUrl(s.videoUrl); return (
    <div className="flex items-center gap-1.5">
      <video className="h-[30px] w-12 flex-none cursor-pointer rounded border object-cover" src={s.videoUrl + "#t=0.6"} muted preload="metadata" onClick={() => onPlay(s.videoUrl)} />
      {stt && <span title={fnameFromUrl(s.videoUrl)} className="cursor-help rounded bg-indigo-500/15 px-1.5 py-0.5 text-[11px] font-bold tabular-nums text-indigo-300">#{stt}</span>}
      <Button variant="ghost" size="icon" className="h-6 w-6" title="Xem" onClick={() => onPlay(s.videoUrl)}><Play className="h-3 w-3" /></Button>
      <Button variant="ghost" size="icon" className="h-6 w-6" title="Mở thư mục" onClick={onOpen}><FolderOpen className="h-3 w-3" /></Button>
      <Button variant="ghost" size="icon" className="h-6 w-6" title="Copy đường dẫn" onClick={() => onCopy(s.videoUrl)}><Copy className="h-3 w-3" /></Button>
      <Button variant="ghost" size="icon" className="h-6 w-6" title="Xoá logo Dola" onClick={() => onRemoveWm(s.videoUrl)}><Eraser className="h-3 w-3" /></Button>
    </div>
  ); }
  if (s.phase === "error") { const f = fmtError(s.errorRaw || ""); return (
    <div className="leading-tight">
      <div title={s.errorRaw} className={"cursor-help " + (f.kind === "account" ? "text-amber-400" : "text-red-400")}>{f.icon} {f.short}</div>
      <div className="mt-0.5 text-[10.5px] text-muted-foreground">{f.hint}</div>
    </div>
  ); }
  return <span className="text-muted-foreground">{s.status || "—"}</span>;
}
