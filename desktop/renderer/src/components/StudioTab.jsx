import { useEffect, useRef, useState } from "react";
import { Play, RotateCw, Settings, Trash2, FolderOpen, Copy, ArrowDown, Repeat, Stethoscope, Square, RefreshCw, Eraser, Power, Clock, ListOrdered } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/ui/toast";
import { SelectNative } from "@/components/ui/select-native";
import { ViewToggle, useView } from "@/components/ui/view-toggle";
import { api, submitJob, pollJob, fmtError, creditCost, firstLine, fnameFromUrl, sttFromUrl, accState, accChip, canRunAccount, deleteAccount, STAGE_TEXT, riskyPrompt, durationMismatch, deadNicks, setConcurrency, patchAccount, wakeAccount, inflightTasks, accState as accStateOf } from "@/lib/api";

const MODELS = ["seedance-2.0", "seedance-2.5"];
const RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4"];
// Dola đã bỏ 30 giây (chỉ còn 4–15). Chọn 30 thì Dola hỏi lại rồi tự hạ 15 → mất thêm
// 1–2 phút giữ nick, nên mặc định 15 và nói rõ trên nhãn.
const DURS = [["10", "10s"], ["15", "15s"], ["30", "30s"]];
// Timeline 5 bước trên thẻ nick; giai đoạn server báo (STAGE_TEXT) ánh xạ về bước đang chạy.
const STEPS = ["Hàng đợi", "Gửi", "Dựng", "Tải về", "Xong"];
const STEP_OF = { checking: 0, queued: 0, opening: 1, submitting: 1, rendering: 2, processing: 2, downloading: 3 };
const H2 = "font-mono text-[10px] font-semibold uppercase tracking-wider text-muted-foreground";
const TH = "h-9 whitespace-nowrap px-2 text-left font-mono text-[10px] font-semibold uppercase tracking-wider text-muted-foreground";

// Chip chọn (thay dropdown cho model/thời lượng/tỉ lệ) — nhanh, dễ nhìn. options: [v,label] hoặc "v".
const Chips = ({ options, value, onChange, label }) => (
  <div className="flex items-center gap-1">
    {label && <span className="mr-0.5 text-[11px] text-muted-foreground">{label}</span>}
    {options.map((o) => {
      const [v, txt] = Array.isArray(o) ? o : [o, o];
      const on = String(value) === String(v);
      return (
        <button key={v} type="button" onClick={() => onChange(v)}
          className={"rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors " +
            (on ? "bg-primary text-primary-foreground" : "bg-surface-high text-muted-foreground hover:text-foreground")}>
          {txt}
        </button>
      );
    })}
  </div>
);
// Chip trạng thái trên thẻ/dòng: job đang chạy/xong/lỗi đè lên trạng thái nick; nick không chạy được thì mờ đi.
const stateChip = (a, s) => s.phase === "running" ? { variant: "default", text: "Đang chạy" } : s.phase === "done" ? { variant: "success", text: "Xong" } : s.phase === "error" ? { variant: "danger", text: "Lỗi" } : accChip(a);
const isDim = (a, s) => s.phase === "idle" && accState(a) !== "ready" && accState(a) !== "busy";

export default function StudioTab({ health, onRefresh, onPlay }) {
  const accounts = health?.accounts || [];
  const [view, setView] = useView("dolaStudioView");
  const [def, setDef] = useState({ model: "seedance-2.5", dur: "15", ratio: "9:16" });
  const [bulk, setBulk] = useState("");
  const [rows, setRows] = useState({});           // nick -> {prompt,model,ratio,dur,phase,stage,status,startedAt,videoUrl,errorRaw}
  const [sel, setSel] = useState({});             // nick -> bool
  const [gen, _setGen] = useState("");
  // Ngoài dòng chữ nhỏ cũ, bắn TOAST cho thông báo KẾT THÚC (✓/Xong/Đã…/Lỗi); bỏ qua thông báo "Đang…" tiến trình.
  const setGen = (msg) => {
    _setGen(msg);
    if (!msg || /^Đang |^Kiểm tra phiên/.test(msg)) return;
    toast(msg, /lỗi|✗/i.test(msg) ? "error" : /^✓|Xong|^Đã /.test(msg) ? "success" : "info");
  };
  const [clock, setClock] = useState(0);
  const inflight = useRef(new Set());
  const [conc, setConc] = useState({ send: "", login: "", gmin: "", gmax: "" });   // gmin/gmax: chờ ngẫu nhiên giữa lần gửi
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
  useEffect(() => {
    if (!health) return;
    setConc((c) => ({ send: c.send || String(health.max_concurrency || 3),
                      login: c.login || String(health.login_concurrency || 3),
                      gmin: c.gmin || String(Math.round(health.submit_gap_min ?? 3)),
                      gmax: c.gmax || String(Math.round(health.submit_gap_max ?? 6)) }));
  }, [health?.max_concurrency, health?.login_concurrency, health?.submit_gap_min, health?.submit_gap_max]);   // eslint-disable-line react-hooks/exhaustive-deps
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

  const row = (n) => rows[n] || { prompt: "", model: def.model, ratio: def.ratio, dur: def.dur, phase: "idle", status: "" };
  const setRow = (n, patch) => setRows((p) => ({ ...p, [n]: { ...(p[n] || row(n)), ...patch } }));
  const elapsed = (ms) => { const s = Math.max(0, Math.floor((Date.now() - ms) / 1000)); return `${Math.floor(s / 60)}p ${String(s % 60).padStart(2, "0")}s`; };

  async function runOne(n) {
    const acc = accounts.find((a) => a.account === n);
    const s = row(n);
    const prompt = (s.prompt || "").trim() || firstLine(bulk);
    if (!prompt) { setRow(n, { phase: "error", errorRaw: "Chưa nhập prompt", status: "warn" }); return false; }
    // Chỉ cảnh báo, không chặn: bị Dola chặn thì không trừ lượt, còn hộp confirm trước đây bấm Huỷ một lần là
    // prompt đó bị nhớ "chưa gửi" mãi (không thuộc "Chạy lại lỗi") → auto tạo đứng im.
    const risky = riskyPrompt(prompt);
    if (risky) setRow(n, { status: `gửi thử — có từ dễ bị chặn: ${risky}` });
    // Server tự co các mốc thời gian trong prompt về đúng thời lượng (fit_prompt_to_duration) — chỉ báo, không chặn.
    const over = durationMismatch(prompt, s.dur);
    if (over) setRow(n, { status: `prompt ~${over}s → tự co về ${s.dur}s` });
    if (acc && acc.remaining != null) {
      const need = creditCost(s.dur);
      if (acc.remaining < need) { setRow(n, { phase: "error", errorRaw: `Không đủ điểm cho ${s.dur}s (cần ${need}, còn ${acc.remaining}). Giảm còn 10–15s hoặc đổi nick.` }); return false; }
    }
    if (inflight.current.has(n)) return true;   // đã chạy ở nơi khác, không tính là lỗi
    inflight.current.add(n);
    setRow(n, { prompt, phase: "running", stage: "queued", startedAt: Date.now(), errorRaw: "", videoUrl: "", ranOn: "" });
    try {
      // Người dùng đã chọn đích danh nick này thì "tạm ngưng" không còn là lý do chặn: mở lại giúp rồi
      // gửi luôn (server từ chối job vào nick tạm ngưng). Trước đây thẻ chỉ báo "bấm Bật lịch tất cả rồi chạy lại".
      if (acc && accStateOf(acc) === "off") {
        setRow(n, { status: "đang mở lại nick tạm ngưng…" });
        await patchAccount(n, { scheduling: true });
        onRefresh();
      }
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
    let stage = "queued", fails = 0, pip = "";
    while (true) {
      await new Promise((r) => setTimeout(r, 3000));
      if (stop.current) { setRow(n, { phase: "idle", status: "đã dừng theo dõi" }); return true; }
      let pj;
      try { pj = await pollJob(id); fails = 0; }
      catch (e) {
        // Server tắt / khởi động lại giữa chừng: job vẫn nằm trong tasks.db, chờ server lên rồi hỏi tiếp.
        // 404 = server không còn job này (đổi server / DB mới) → báo lỗi thay vì quay vòng vô tận.
        if (e?.status === 404 || ++fails >= 20) {
          setRow(n, { phase: "error", errorRaw: e?.status === 404 ? "Server không còn job này (đã đổi server hoặc xoá dữ liệu?)" : "Mất kết nối server quá 1 phút: " + (e?.message || e) });
          return false;
        }
        setRow(n, { status: `chờ server trả lời (${fails})` });
        continue;
      }
      const pk = [pj.proxy_ip, pj.proxy_provider, pj.proxy_used, pj.proxy_per, pj.proxy_fresh, pj.proxy_kind].join("|");   // IP + lượt + NCC + loại → cột Proxy
      if (pk !== pip) { pip = pk; setRow(n, { proxyIp: pj.proxy_ip || "", proxyIsp: pj.proxy_isp || "", proxyProvider: pj.proxy_provider || "", proxyUsed: pj.proxy_used, proxyPer: pj.proxy_per, proxyFresh: pj.proxy_fresh, proxyKind: pj.proxy_kind || "" }); }
      if (pj.account && pj.account !== n) setRow(n, { ranOn: pj.account });   // job đã XOAY sang nick khác → hiện nick thật
      if (pj.status === "completed") { setRow(n, { phase: "done", stage: "done", videoUrl: pj.video_url }); api.saveVideo?.(pj.video_url); return true; }
      if (pj.status === "failed") { setRow(n, { phase: "error", errorRaw: pj.error || "?" }); return false; }
      if (pj.stage && pj.stage !== stage) { stage = pj.stage; setRow(n, { stage }); }
    }
  }

  // "Chạy sẵn sàng" = đúng nick chip xanh, dùng chung accState (đã tính cả busy/cooling/scheduling).
  const canRun = canRunAccount;
  const readyNicks = () => accounts.filter(canRun).map((a) => a.account);
  async function runBatch(ns, empty) {
    if (!ns.length) { setGen(empty); return; }
    // Tránh hiểu lầm "13 video giống nhau": nick nào ô prompt RIÊNG còn trống thì khi chạy sẽ lấy DÒNG ĐẦU của
    // khung Prompt. Nếu khung đang có NHIỀU dòng, người dùng thường định "mỗi dòng 1 nick" nhưng quên bấm →
    // chạy luôn thì mọi nick trống dùng chung dòng đầu. Chặn lại, bảo chia trước cho rõ.
    const promptLines = bulk.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
    const noPrompt = ns.filter((n) => !(row(n).prompt || "").trim());
    if (noPrompt.length && promptLines.length > 1) {
      setGen(`${noPrompt.length} nick chưa có prompt riêng mà khung Prompt đang có ${promptLines.length} dòng — bấm ` +
        `"Mỗi dòng 1 nick" (chia mỗi nick 1 dòng) hoặc "Điền tất cả" (mọi nick chung 1 prompt) rồi chạy lại. ` +
        `Chạy luôn sẽ khiến các nick trống dùng CHUNG dòng đầu.`);
      return;
    }
    stop.current = false;
    // Phản hồi ngay trên từng thẻ: trước đây bấm Chạy là bảng đứng im tới 12s (chờ verify).
    ns.forEach((n) => setRow(n, { phase: "running", stage: "checking", startedAt: Date.now(), errorRaw: "", videoUrl: "" }));
    setGen("Kiểm tra phiên đăng nhập trước khi chạy…");
    const dead = await deadNicks(ns);
    dead.forEach((n) => setRow(n, { phase: "error", errorRaw: "Cookie hết hạn — đăng nhập lại nick này rồi chạy lại." }));
    const blocked = ns.filter((n) => {
      const a = accounts.find((x) => x.account === n);
      const st = a ? accStateOf(a) : "";
      return a && !canRun(a) && st !== "busy" && st !== "off";   // busy = đang chạy; off = runOne tự bật lịch
    });
    blocked.forEach((n) => {
      const a = accounts.find((x) => x.account === n) || {};
      const why = { cooling: "nick đang nghỉ (risk-control) — bấm 'Bỏ nghỉ tất cả'",
                    quota: "hết lượt/điểm hôm nay", dead: "cookie chết — đăng nhập lại nick" }[accStateOf(a)]
                 || "nick chưa chạy được";
      setRow(n, { phase: "error", errorRaw: why });
    });
    const run = ns.filter((n) => !dead.includes(n) && !blocked.includes(n));
    if (!run.length) {
      setGen(`Không nick nào chạy được: ${dead.length} cookie chết · ${blocked.length} nghỉ/hết lượt.`);
      return;
    }
    const skipped = [dead.length ? `${dead.length} cookie chết` : "", blocked.length ? `${blocked.length} nghỉ/hết lượt` : ""].filter(Boolean).join(" · ");
    setGen(`Đang chạy ${run.length} nick…` + (skipped ? ` (bỏ ${skipped})` : ""));
    const res = await Promise.all(run.map(runOne));   // runOne trả true=ok / false=lỗi
    const bad = run.filter((_, i) => res[i] === false);
    const summary = bad.length ? `Xong ${run.length - bad.length}/${run.length} nick. Lỗi: ${bad.join(", ")}.` : `Xong ${run.length} nick.`;
    setGen(summary);
    api.notify?.("Dola Studio — chạy xong", summary);   // thông báo desktop (tiện để máy chạy đêm)
  }
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
    if (!off.length) { setGen("Không có nick nào đang tạm ngưng."); return; }
    setGen(`Đang cho ${off.length} nick tạm ngưng chạy lại…`);
    const res = await Promise.all(off.map((n) => patchAccount(n, { scheduling: true }).then(() => true).catch(() => false)));
    const ok = res.filter(Boolean).length;
    setGen(`✓ Cho chạy lại ${ok}/${off.length} nick.` + (ok < off.length ? " Vài nick lỗi — xem Kho tài khoản." : ""));
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
  async function applyGap() {
    const lo = parseFloat(conc.gmin), hi = parseFloat(conc.gmax);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) { setGen("Nhập số giây hợp lệ cho 'chờ ngẫu nhiên'."); return; }
    const r = await api.setSubmitGap?.(lo, hi);
    if (r?.ok) setGen(`✓ Chờ ngẫu nhiên ${Math.round(r.min_sec)}–${Math.round(r.max_sec)}s giữa mỗi lần gửi (theo từng proxy) — chống 710022002.`);
    else setGen("Lỗi đặt chờ ngẫu nhiên: " + (r?.error || "?"));
  }
  const selected = Object.keys(sel).filter((n) => sel[n] && accounts.some((a) => a.account === n));
  const runSelected = () => runBatch(selected, "Chọn ít nhất 1 nick.");
  const runReady = () => runBatch(readyNicks(), "Không có nick sẵn sàng.");
  const retryFailed = () => runBatch(accounts.map((a) => a.account).filter((n) => rows[n]?.phase === "error"), "Không có nick lỗi.");
  const stopAll = () => { stop.current = true; setGen("Đã dừng theo dõi (video có thể vẫn hoàn tất trên Dola)."); };
  const fillAll = () => { const p = firstLine(bulk); accounts.forEach((a) => setRow(a.account, { prompt: p })); setGen("Đã điền prompt cho tất cả nick."); };
  const fillLines = () => { const ps = bulk.split(/\r?\n/).map((x) => x.trim()).filter(Boolean); accounts.forEach((a, i) => ps[i] && setRow(a.account, { prompt: ps[i] })); setGen(`Đã chia ${Math.min(ps.length, accounts.length)} prompt.`); };
  // Xóa prompt cũ hàng loạt (Hoài Nam xin): trả các nick về trạng thái trắng như per-row "Làm mới" — bỏ prompt +
  // reset lỗi/tiến trình, KHÔNG đụng video đã tạo (video nằm ở thư viện). Nick đang chạy thì bỏ qua cho an toàn.
  const clearPrompts = (nicks, label) => {
    const targets = nicks.filter((n) => rows[n]?.phase !== "running");
    if (!targets.length) { setGen("Không có prompt để xóa (nick đang chạy được giữ nguyên)."); return; }
    targets.forEach((n) => setRow(n, { prompt: "", phase: "idle", stage: undefined, status: "", errorRaw: "", videoUrl: "" }));
    setGen(`Đã xóa prompt ${targets.length} nick ${label}.`);
  };
  const clearAllPrompts = () => { if (!window.confirm("Xóa hết prompt đã điền ở tất cả nick? (video đã tạo vẫn còn trong thư viện)")) return; clearPrompts(accounts.map((a) => a.account), "(tất cả)"); };
  const clearSelectedPrompts = () => clearPrompts(selected, "(đã chọn)");
  const syncDef = () => { accounts.forEach((a) => setRow(a.account, { model: def.model, ratio: def.ratio, dur: def.dur })); setGen("Đã đồng bộ mặc định."); };
  const selectWhere = (pred, label) => { const o = {}; accounts.forEach((a) => { if (pred(a)) o[a.account] = true; }); setSel(o); setGen(`Đã chọn ${Object.keys(o).length} nick ${label}.`); };
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

  // Số liệu dùng ngay trên thanh công cụ: prompt đang gõ + ước tính cho nick đã chọn.
  const lines = bulk.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
  const risky = riskyPrompt(firstLine(bulk));
  const needCredit = selected.reduce((s, n) => s + creditCost(row(n).dur), 0);
  const runningNow = Object.values(rows).filter((r) => r.phase === "running").length;
  const allSel = accounts.length > 0 && accounts.every((a) => sel[a.account]);
  // Nick chạy được lên đầu: đang chạy → sẵn sàng → vừa xong / lỗi (chạy lại được) → nghỉ → hết credit/lượt
  // → tắt lịch → chưa đăng nhập. Người dùng nhìn hàng đầu là biết còn bao nhiêu nick dùng được.
  const rank = (a) => {
    const ph = rows[a.account]?.phase, st = accState(a);
    if (ph === "running" || st === "busy") return 0;
    if (st === "ready") return 1;
    if (ph === "done" || ph === "error") return 2;
    return { cooling: 3, quota: 4, off: 5 }[st] ?? 6;
  };
  const ordered = [...accounts].sort((x, y) => rank(x) - rank(y) || String(x.account).localeCompare(String(y.account)));
  const usable = accounts.filter((a) => rank(a) <= 1).length;
  // Thẻ (lưới) và dòng (bảng) nhận đúng cùng một bộ props — đổi kiểu hiển thị không đổi hành vi.
  // IP xoay đang dùng + lượt k/N (dùng chung khi 1 key cho nhiều nick) — lấy từ /health, không gọi mạng.
  const proxyCell = health?.rotating_ip?.ip
    ? { ip: health.rotating_ip.ip, isp: health.rotating_ip.network || health.rotating_ip.location || "",
        used: health.ip_used || 0, per: health.nicks_per_ip || 0 }
    : null;
  // Nick nào đang là ĐÍCH xoay của thẻ khác → map {nickY: nickX}. Để thẻ Y báo rõ "job xoay từ X",
  // khỏi hiện "Đang chạy trên server" mập mờ (nhìn như Y tự chạy job riêng, dễ tưởng chạy 2 lần).
  const rotatedInto = {};
  Object.entries(rows).forEach(([origin, r]) => { if (r?.ranOn && r.ranOn !== origin) rotatedInto[r.ranOn] = origin; });
  const nickProps = (a) => ({
    a, s: row(a.account), selected: !!sel[a.account], clock, elapsed, proxyCell, rotatedFrom: rotatedInto[a.account] || "",
    onSel: (v) => setSel((p) => ({ ...p, [a.account]: v })), onChange: (patch) => setRow(a.account, patch),
    onRun: () => { stop.current = false; runOne(a.account); }, onRelogin: () => relogin(a.account), onProxy: () => setProxy(a.account), onDelete: () => del(a.account),
    onPlay, onOpen: () => api.openDownloads?.(), onCopy: copyPath, onRemoveWm: removeWm,
    onNew: () => setRow(a.account, { phase: "idle", videoUrl: "", stage: undefined, status: "", errorRaw: "" }),
  });

  return (
    <div className="space-y-4">
      {/* Prompt */}
      <div className="space-y-2">
        <div className={H2}>Prompt</div>
        <Textarea rows={3} value={bulk} onChange={(e) => setBulk(e.target.value)} placeholder={"Nhiều prompt, mỗi dòng 1 cái:\ncon mèo lướt sóng\ncon chó chạy trên biển"} />
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={fillAll} disabled={!lines.length}><ArrowDown className="h-3.5 w-3.5" />Điền tất cả</Button>
          <Button variant="outline" size="sm" onClick={fillLines} disabled={!lines.length}><ListOrdered className="h-3.5 w-3.5" />Mỗi dòng 1 nick</Button>
          <Button variant="outline" size="sm" className="border-error/40 text-error hover:text-error" onClick={clearAllPrompts} title="Xóa hết prompt đã điền ở tất cả nick (video đã tạo vẫn còn)"><Eraser className="h-3.5 w-3.5" />Xóa prompt tất cả</Button>
          <span className="font-mono text-[11px] text-muted-foreground">{lines.length} dòng · {bulk.length} ký tự</span>
          {risky && <Badge variant="warn" title="Dola duyệt nội dung cảnh quay, không duyệt từ khoá">Có từ dễ bị chặn: {risky}</Badge>}
          <span className="flex-1" />
          <span className="font-mono text-[11px] text-muted-foreground">Prompt dài hơn thời lượng sẽ tự co cho khớp</span>
        </div>
      </div>

      {/* Cấu hình mặc định */}
      <div className="flex flex-wrap items-center gap-2">
        <span className={H2 + " w-36"}>Cấu hình mặc định</span>
        <Chips label="Model" options={MODELS} value={def.model} onChange={(v) => setDef({ ...def, model: v })} />
        <Chips label="Dài" options={DURS} value={def.dur} onChange={(v) => setDef({ ...def, dur: v })} />
        <Chips label="Tỉ lệ" options={RATIOS} value={def.ratio} onChange={(v) => setDef({ ...def, ratio: v })} />
        <Button variant="outline" size="sm" onClick={syncDef}><Repeat className="h-3.5 w-3.5" />Đồng bộ mặc định</Button>
        <Button variant="outline" size="sm" onClick={verifyAll}><Stethoscope className="h-3.5 w-3.5" />Kiểm tra tất cả</Button>
        <Button variant="outline" size="sm" onClick={enableAllScheduling} title="Mở lại mọi nick đang tạm ngưng (nick bị tắt ở Kho tài khoản hoặc theo file nhập)"><Power className="h-3.5 w-3.5" />Cho chạy lại tất cả</Button>
        <Button variant="outline" size="sm" onClick={wakeAllCooling}><Clock className="h-3.5 w-3.5" />Bỏ nghỉ tất cả</Button>
        <span className="flex-1" />
        <div className="flex items-center gap-2 rounded-lg bg-surface px-3 py-1.5 font-mono text-[12px] text-muted-foreground">
          <Clock className="h-3.5 w-3.5 text-primary" />
          <span>Đã chọn <b className="text-foreground">{selected.length} nick</b> · cần <b className="text-foreground">{needCredit} credit</b></span>
          <span>·</span>
          <span>tối đa <b className="text-foreground">{health?.max_concurrency || "—"}</b> video cùng lúc</span>
        </div>
      </div>

      {/* Chạy */}
      <div className="flex flex-wrap items-center gap-2 border-t border-surface-high pt-3">
        <span className={H2}>Chọn nhanh</span>
        <button type="button" className="text-xs font-medium text-primary hover:underline" onClick={() => selectWhere(canRun, "sẵn sàng")}>Tất cả sẵn sàng</button>
        <span className="text-outline-variant">·</span>
        <button type="button" className="text-xs font-medium text-primary hover:underline" onClick={() => selectWhere((a) => a.remaining != null && a.remaining >= creditCost(def.dur) && canRun(a), `còn đủ credit cho ${def.dur}s`)}>Còn đủ credit</button>
        <span className="text-outline-variant">·</span>
        <button type="button" className="text-xs font-medium text-primary hover:underline" onClick={() => selectWhere(() => !allSel, allSel ? "" : "tất cả")}>{allSel ? "Bỏ chọn" : "Chọn tất cả"}</button>
        {selected.length > 0 && <><span className="text-outline-variant">·</span><button type="button" className="text-xs font-medium text-error hover:underline" onClick={clearSelectedPrompts}>Xóa prompt đã chọn ({selected.length})</button></>}
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={runReady}><Play className="h-3.5 w-3.5" />Chạy sẵn sàng</Button>
        <Button variant="outline" size="sm" onClick={retryFailed}><RefreshCw className="h-3.5 w-3.5" />Chạy lại lỗi</Button>
        <Button variant="outline" size="sm" className="border-error/40 text-error hover:text-error" onClick={stopAll}><Square className="h-3.5 w-3.5" />Dừng</Button>
        <Button size="sm" onClick={runSelected} disabled={!selected.length}><Play className="h-3.5 w-3.5" />Chạy đã chọn{selected.length ? ` (${selected.length})` : ""}</Button>
      </div>
      <div className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-muted-foreground">
        <ViewToggle value={view} onChange={setView} />
        <span><b className="text-foreground">{usable}</b> nick chạy được (xếp lên đầu) · <b className="text-foreground">{runningNow}</b> đang chạy · tối đa {health?.max_concurrency || "—"} song song
          {health?.pending_tasks ? ` · ${health.pending_tasks} job đang chạy/chờ trên server` : ""}
          {accounts.filter((a) => a.scheduling === false).length ? ` · ${accounts.filter((a) => a.scheduling === false).length} nick tạm ngưng` : ""}
          {accounts.filter((a) => a.cooling).length ? ` · ${accounts.filter((a) => a.cooling).length} nick đang nghỉ` : ""}</span>
        <span className="flex-1" />
        <span>Nick gửi cùng lúc</span>
        <Input className="h-7 w-14 font-mono text-[11px]" type="number" min={1} max={health?.max_browser_slots || 24} value={conc.send} onChange={(e) => setConc({ ...conc, send: e.target.value })} />
        <span>Đăng nhập cùng lúc</span>
        <Input className="h-7 w-14 font-mono text-[11px]" type="number" min={1} max={health?.max_login_slots || 12} value={conc.login} onChange={(e) => setConc({ ...conc, login: e.target.value })} />
        <Button variant="outline" size="sm" className="h-7" onClick={applyConc}>Áp dụng</Button>
        <span className="ml-2" title="Giãn nhịp ngẫu nhiên giữa mỗi lần gửi (theo từng proxy) để tránh Dola chặn 710022002 'gửi quá dày'">Chờ ngẫu nhiên</span>
        <Input className="h-7 w-12 font-mono text-[11px]" type="number" min={0} max={60} value={conc.gmin} onChange={(e) => setConc({ ...conc, gmin: e.target.value })} />
        <span>–</span>
        <Input className="h-7 w-12 font-mono text-[11px]" type="number" min={0} max={120} value={conc.gmax} onChange={(e) => setConc({ ...conc, gmax: e.target.value })} />
        <span>giây</span>
        <Button variant="outline" size="sm" className="h-7" onClick={applyGap}>Áp dụng</Button>
      </div>
      {gen && <div className="text-xs text-muted-foreground">{gen}</div>}

      {/* Thẻ theo nick */}
      {!health && <div className="rounded-lg bg-surface p-6 text-center text-sm text-muted-foreground">Server chưa chạy — bấm "Bật server" ở thanh trên.</div>}
      {health && accounts.length === 0 && <div className="rounded-lg bg-surface p-6 text-center text-sm text-muted-foreground">Chưa có nick — sang Kho tài khoản, bấm "Thêm bằng Facebook" hoặc "Nhập kho".</div>}
      {view === "grid" ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {ordered.map((a) => <NickCard key={a.account} {...nickProps(a)} />)}
        </div>
      ) : accounts.length > 0 && (
        <div className="overflow-x-auto rounded-lg bg-surface">
          <table className="w-full min-w-[1300px] border-collapse text-[12.5px]">
            <thead className="border-b border-surface-high">
              <tr className="sticky top-0 z-10 bg-surface">
                <th className={TH + " w-8"}><input type="checkbox" checked={allSel} title={allSel ? "Bỏ chọn" : "Chọn tất cả"} onChange={() => selectWhere(() => !allSel, allSel ? "" : "tất cả")} /></th>
                <th className={TH + " w-10"}>STT</th>
                <th className={TH}>Nick</th>
                <th className={TH}>Trạng thái</th>
                <th className={TH}>Video</th>
                <th className={TH}>Prompt</th>
                <th className={TH}>Model</th>
                <th className={TH}>Tỷ lệ</th>
                <th className={TH}>Dài</th>
                <th className={TH}>Proxy (IP xoay)</th>
                <th className={TH}>Tệp</th>
                <th className={TH}>Tiến trình</th>
                <th className={TH + " text-right"}>Thao tác</th>
              </tr>
            </thead>
            <tbody>{ordered.map((a, i) => <NickRow key={a.account} idx={i + 1} {...nickProps(a)} />)}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Thanh 5 bước; compact = chỉ vạch màu (dòng trong bảng), tên bước hiện khi rê chuột.
function Timeline({ s, compact = false }) {
  const at = s.phase === "done" ? STEPS.length : s.phase === "idle" ? -1 : (STEP_OF[s.stage] ?? 2);
  const tone = (i) => s.phase === "error" && i === at ? "bg-error" : i < at ? "bg-tertiary" : i === at ? "bg-primary" : "bg-surface-highest";
  const text = (i) => s.phase === "error" && i === at ? "text-error" : i === at && s.phase === "running" ? "text-primary" : s.phase === "done" && i === STEPS.length - 1 ? "text-tertiary" : "text-muted-foreground";
  return (
    <div className="flex gap-1">
      {STEPS.map((label, i) => (
        <div key={label} className="flex flex-1 flex-col gap-1" title={compact ? label : undefined}>
          <div className={"h-[3px] rounded-sm " + tone(i)} />
          {!compact && <span className={"whitespace-nowrap font-mono text-[10px] " + text(i)}>{label}</span>}
        </div>
      ))}
    </div>
  );
}

// Dạng bảng: một dòng một nick, cùng dữ liệu và thao tác với thẻ nhưng nhìn được 15–20 nick không cần cuộn.
function NickRow({ a, s, idx, selected, elapsed, proxyCell, rotatedFrom, onSel, onChange, onRun, onRelogin, onProxy, onDelete, onPlay, onOpen, onCopy, onRemoveWm, onNew }) {
  const n = a.account;
  const chip = stateChip(a, s);
  const tint = s.phase === "done" ? " bg-tertiary/5" : s.phase === "error" ? " bg-error/5" : "";
  const icon = "h-7 w-7 text-muted-foreground hover:text-foreground";
  const td = "px-2 py-1.5 align-middle";
  return (
    <tr className={"border-b border-surface-high/60 last:border-0" + tint + (isDim(a, s) ? " opacity-60" : "")}>
      <td className={td}><input type="checkbox" checked={selected} onChange={(e) => onSel(e.target.checked)} /></td>
      <td className={td + " font-mono text-[11px] text-muted-foreground tabular-nums"}>{idx}</td>
      <td className={td + " whitespace-nowrap"}>
        <div className="font-mono text-[12px] font-semibold">{n}</div>
        {s.ranOn && s.ranOn !== n && <div className="font-mono text-[10px] text-primary" title="Job đã xoay sang nick này">↦ chạy trên {s.ranOn}</div>}
        <div className="font-mono text-[10.5px] text-muted-foreground">{a.used_today}/{a.limit} hôm nay{a.remaining != null ? ` · còn ${a.remaining}` : ""}</div>
      </td>
      <td className={td + " whitespace-nowrap"}><Badge variant={chip.variant}>{chip.text}</Badge></td>
      <td className={td}>
        {s.videoUrl ? (
          <div className="flex flex-col items-start gap-0.5">
            <video className="h-[46px] w-[26px] cursor-pointer rounded bg-surface-lowest object-cover" src={s.videoUrl + "#t=0.6"} muted preload="metadata" onClick={() => onPlay?.(s.videoUrl)} title="Bấm để xem" />
            <div className="flex gap-0.5">
              <Button variant="ghost" size="icon" className="h-6 w-6 text-muted-foreground hover:text-foreground" title="Mở thư mục" onClick={onOpen}><FolderOpen className="h-3.5 w-3.5" /></Button>
              <Button variant="ghost" size="icon" className="h-6 w-6 text-muted-foreground hover:text-foreground" title="Copy đường dẫn" onClick={() => onCopy?.(s.videoUrl)}><Copy className="h-3.5 w-3.5" /></Button>
              <Button variant="ghost" size="icon" className="h-6 w-6 text-muted-foreground hover:text-foreground" title="Xoá logo Dola" onClick={() => onRemoveWm?.(s.videoUrl)}><Eraser className="h-3.5 w-3.5" /></Button>
            </div>
          </div>
        ) : <span className="text-muted-foreground">—</span>}
      </td>
      <td className={td + " min-w-[260px]"}>
        <Input className="h-8 text-[12.5px]" value={s.prompt} placeholder={`prompt cho ${n}…`} onChange={(e) => onChange({ prompt: e.target.value })} />
      </td>
      <td className={td}><SelectNative className="h-8 w-[122px] text-xs" value={s.model} onChange={(e) => onChange({ model: e.target.value })}>{MODELS.map((m) => <option key={m}>{m}</option>)}</SelectNative></td>
      <td className={td}><SelectNative className="h-8 w-[68px] text-xs" value={s.ratio} onChange={(e) => onChange({ ratio: e.target.value })}>{RATIOS.map((m) => <option key={m}>{m}</option>)}</SelectNative></td>
      <td className={td}><SelectNative className="h-8 w-[74px] text-xs" value={s.dur} onChange={(e) => onChange({ dur: e.target.value })}>{DURS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</SelectNative></td>
      <td className={td + " whitespace-nowrap font-mono text-[11px]"}>
        {s.proxyIp ? (
          <div className="inline-block rounded bg-tertiary/10 px-1.5 py-0.5 leading-tight">
            <div className="text-tertiary">{s.proxyIp}</div>
            <div className="text-[10px] text-muted-foreground">{[
              s.proxyFresh === true ? "IP mới" : s.proxyFresh === false ? "IP cũ" : "",
              s.proxyUsed && s.proxyPer ? `lượt ${s.proxyUsed}/${s.proxyPer}` : "",
              s.proxyProvider || "", s.proxyIsp || "",
            ].filter(Boolean).join(" · ") || " "}</div>
          </div>
        ) : s.proxyKind === "direct" ? (
          <span className="text-muted-foreground">nối thẳng</span>
        ) : s.proxyKind === "static" ? (
          <span className="text-tertiary">proxy tĩnh</span>
        ) : s.phase === "running" && s.proxyKind === "rotating" ? (
          <span className="text-primary">đang chờ IP…</span>
        ) : proxyCell ? (
          <div className="leading-tight">
            <div className="text-tertiary">{proxyCell.ip}</div>
            <div className="text-[10px] text-muted-foreground">{proxyCell.isp ? proxyCell.isp + " · " : ""}lượt {Math.min(proxyCell.used, proxyCell.per) || proxyCell.used}/{proxyCell.per}</div>
          </div>
        ) : <span className="text-muted-foreground">—</span>}
      </td>
      <td className={td + " max-w-[180px]"}>
        {s.videoUrl
          ? <span className="block truncate font-mono text-[11px] text-muted-foreground" title={fnameFromUrl(s.videoUrl)}>{fnameFromUrl(s.videoUrl)}</span>
          : <span className="text-muted-foreground">—</span>}
      </td>
      <td className={td + " w-[240px] max-w-[280px]"}>
        <Timeline s={s} compact />
        <div className="mt-1 flex min-h-5 items-center"><Footer s={s} a={a} elapsed={elapsed} onRun={onRun} rotatedFrom={rotatedFrom} /></div>
      </td>
      <td className={td + " whitespace-nowrap text-right"}>
        {s.phase !== "running" && <Button variant="ghost" size="icon" className={icon + " text-primary"} title="Chạy nick này" onClick={onRun}><Play className="h-3.5 w-3.5" /></Button>}
        <Button variant="ghost" size="icon" className={icon} title="Đăng nhập lại" onClick={onRelogin}><RotateCw className="h-3.5 w-3.5" /></Button>
        <Button variant="ghost" size="icon" className={icon} title="Proxy riêng" onClick={onProxy}><Settings className="h-3.5 w-3.5" /></Button>
        <Button variant="ghost" size="icon" className={icon} title="Xoá nick" onClick={onDelete}><Trash2 className="h-3.5 w-3.5" /></Button>
      </td>
    </tr>
  );
}

function NickCard({ a, s, selected, elapsed, rotatedFrom, onSel, onChange, onRun, onRelogin, onProxy, onDelete, onPlay, onOpen, onCopy, onRemoveWm, onNew }) {
  const n = a.account;
  const cardChip = stateChip(a, s);
  const border = s.phase === "done" ? " ring-1 ring-tertiary/25" : s.phase === "error" ? " ring-1 ring-error/30" : "";
  const icon = "h-7 w-7 text-muted-foreground hover:text-foreground";
  return (
    <div className={"flex flex-col gap-2.5 rounded-lg bg-surface p-3" + border + (isDim(a, s) ? " opacity-60" : "")}>
      <div className="flex items-center gap-2">
        <input type="checkbox" checked={selected} onChange={(e) => onSel(e.target.checked)} />
        <span className="font-mono text-[12.5px] font-semibold">{n}</span>
        <Badge variant={cardChip.variant}>{cardChip.text}</Badge>
        <span className="ml-auto font-mono text-[11px] text-muted-foreground">{a.used_today}/{a.limit} hôm nay{a.remaining != null ? ` · còn ${a.remaining}` : ""}</span>
      </div>
      {s.phase === "done" ? (
        <DoneRow s={s} onPlay={onPlay} onOpen={onOpen} onCopy={onCopy} onRemoveWm={onRemoveWm} onNew={onNew} />
      ) : (
        <Input className="h-9 text-[13px]" value={s.prompt} placeholder={`prompt cho ${n}…`} onChange={(e) => onChange({ prompt: e.target.value })} />
      )}
      <div className="flex gap-1.5">
        <SelectNative className="h-8 flex-1 text-xs" value={s.model} onChange={(e) => onChange({ model: e.target.value })}>{MODELS.map((m) => <option key={m}>{m}</option>)}</SelectNative>
        <SelectNative className="h-8 w-[70px] text-xs" value={s.ratio} onChange={(e) => onChange({ ratio: e.target.value })}>{RATIOS.map((m) => <option key={m}>{m}</option>)}</SelectNative>
        <SelectNative className="h-8 w-[74px] text-xs" value={s.dur} onChange={(e) => onChange({ dur: e.target.value })}>{DURS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</SelectNative>
      </div>
      <Timeline s={s} />
      <div className="flex min-h-7 items-center gap-1">
        <Footer s={s} a={a} elapsed={elapsed} onRun={onRun} rotatedFrom={rotatedFrom} />
        <span className="ml-auto" />
        {s.phase !== "running" && <Button variant="ghost" size="icon" className={icon + " text-primary"} title="Chạy nick này" onClick={onRun}><Play className="h-3.5 w-3.5" /></Button>}
        <Button variant="ghost" size="icon" className={icon} title="Đăng nhập lại" onClick={onRelogin}><RotateCw className="h-3.5 w-3.5" /></Button>
        <Button variant="ghost" size="icon" className={icon} title="Proxy riêng" onClick={onProxy}><Settings className="h-3.5 w-3.5" /></Button>
        <Button variant="ghost" size="icon" className={icon} title="Xoá nick" onClick={onDelete}><Trash2 className="h-3.5 w-3.5" /></Button>
      </div>
    </div>
  );
}

function Footer({ s, a, elapsed, onRun, rotatedFrom }) {
  if (s.phase === "running") return <span className="font-mono text-[11px] text-primary tabular-nums">{elapsed(s.startedAt)} <span className="text-muted-foreground">· {STAGE_TEXT[s.stage] || "đang tạo…"}</span></span>;
  if (s.phase === "done") return <span className="font-mono text-[11px] text-tertiary">Xong · {elapsed(s.startedAt)}</span>;
  if (s.phase === "error") {
    const f = fmtError(s.errorRaw || "");
    return (
      <div className="flex min-w-0 items-center gap-2">
        <div className="min-w-0 leading-tight" title={s.errorRaw}>
          <div className={"truncate text-[12px] " + (f.kind === "account" ? "text-warn" : "text-error")}>{f.short}</div>
          {f.hint && <div className="truncate text-[10.5px] text-muted-foreground">{f.hint}</div>}
        </div>
        <Button variant="outline" size="sm" className="h-7 flex-none border-error/40 text-[11px] text-error hover:text-error" onClick={onRun}>Chạy lại</Button>
      </div>
    );
  }
  if (a.cooling && a.cooldown_until > 0) return <span className="font-mono text-[11px] text-info">nghỉ còn {Math.max(1, Math.ceil((a.cooldown_until - Date.now() / 1000) / 60))} phút</span>;
  if (a.busy && rotatedFrom) return <span className="font-mono text-[11px] text-primary" title={"Nick này đang dựng video được xoay từ thẻ " + rotatedFrom}>⏳ đang dựng (job xoay từ {rotatedFrom})</span>;
  if (a.busy) return <span className="font-mono text-[11px] text-primary">Đang chạy trên server…</span>;   // #2: khớp badge, khỏi mâu thuẫn "Chưa chạy"
  return <span className="font-mono text-[11px] text-muted-foreground">{s.status || "Chưa chạy"}</span>;
}

function DoneRow({ s, onPlay, onOpen, onCopy, onRemoveWm, onNew }) {
  const stt = sttFromUrl(s.videoUrl);
  return (
    <div className="flex items-center gap-2 rounded-md bg-surface-lowest p-2">
      <video className="h-[52px] w-10 flex-none cursor-pointer rounded object-cover" src={s.videoUrl + "#t=0.6"} muted preload="metadata" onClick={() => onPlay(s.videoUrl)} />
      <div className="min-w-0 flex-1 leading-tight">
        <div className="font-mono text-[12px] font-semibold">{stt ? `#${stt}` : "video"}</div>
        <div className="truncate font-mono text-[10.5px] text-muted-foreground" title={fnameFromUrl(s.videoUrl)}>{fnameFromUrl(s.videoUrl)}</div>
      </div>
      <Button variant="ghost" size="icon" className="h-7 w-7" title="Xem" onClick={() => onPlay(s.videoUrl)}><Play className="h-3.5 w-3.5" /></Button>
      <Button variant="ghost" size="icon" className="h-7 w-7" title="Mở thư mục" onClick={onOpen}><FolderOpen className="h-3.5 w-3.5" /></Button>
      <Button variant="ghost" size="icon" className="h-7 w-7" title="Copy đường dẫn" onClick={() => onCopy(s.videoUrl)}><Copy className="h-3.5 w-3.5" /></Button>
      <Button variant="ghost" size="icon" className="h-7 w-7" title="Xoá logo Dola" onClick={() => onRemoveWm(s.videoUrl)}><Eraser className="h-3.5 w-3.5" /></Button>
      <Button variant="ghost" size="icon" className="h-7 w-7 text-primary" title="Tạo video mới trên nick này (nhập prompt khác)" onClick={onNew}><Repeat className="h-3.5 w-3.5" /></Button>
    </div>
  );
}
