import { useEffect, useRef, useState } from "react";
import { Play, RotateCw, Settings, Trash2, FolderOpen, Copy, ArrowDown, Repeat, Stethoscope, Square, RefreshCw, Eraser, Power, Clock, ListOrdered, Zap, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/ui/toast";
import { SelectNative } from "@/components/ui/select-native";
import { ViewToggle, useView } from "@/components/ui/view-toggle";
import { api, submitJob, pollJob, fmtError, creditCost, cheaperHint, firstLine, fnameFromUrl, sttFromUrl, accState, accChip, canRunAccount, deleteAccount, STAGE_TEXT, riskyPrompt, durationMismatch, deadNicks, setConcurrency, patchAccount, wakeAccount, inflightTasks, cookieInfo, accState as accStateOf } from "@/lib/api";

const MODELS = ["seedance-2.0", "seedance-2.5"];
const RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4"];
// Dola đã bỏ 30 giây (chỉ còn 4–15). Chọn 30 thì Dola hỏi lại rồi tự hạ 15 → mất thêm
// 1–2 phút giữ nick, nên mặc định 15 và nói rõ trên nhãn.
const DURS = [["10", "10s"], ["15", "15s"], ["30", "30s"]];
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
  // nick → { ctl: AbortController, id: job id | null }. Trước là Set: nick kẹt trong fetch treo (gateway bật lại) bị coi
  // "đang chạy" mãi → mọi lệnh Chạy sau bị nuốt trong im lặng, thẻ đứng "chờ server nhận job" hàng phút (15/09).
  const inflight = useRef(new Map());
  // Khóa idempotency mỗi nick, giữ trong localStorage tới khi job KẾT THÚC (kể cả khi Dừng / đóng app): gửi lại cùng
  // khóa → server trả job cũ còn sống, không tạo trùng. Chốt chặn trừ lượt 2 lần nằm ở server (live_by_client_id).
  const KEY = (n) => "dolaJobKey:" + n;
  const keyMem = useRef(new Map());   // dự phòng khi localStorage bị chặn: khóa vẫn ổn định trong phiên
  const jobKey = (n) => {
    let k = keyMem.current.get(n) || null;
    if (!k) { try { k = localStorage.getItem(KEY(n)); } catch {} }
    if (!k) { k = globalThis.crypto?.randomUUID?.() || (Math.random().toString(36).slice(2) + Date.now().toString(36)); try { localStorage.setItem(KEY(n), k); } catch {} }
    keyMem.current.set(n, k);
    return k;
  };
  const clearJobKey = (n) => { keyMem.current.delete(n); try { localStorage.removeItem(KEY(n)); } catch {} };
  // ponytail: quét tuần tự cả localStorage mỗi job lúc mở app — vài chục nick thì không đáng kể.
  // Hàng nghìn nick mới cần bảng ngược (khóa → nick) lưu kèm.
  const nickOfKey = (k) => {   // thẻ chủ của một job theo khóa — đúng cả khi server đã XOAY job sang nick khác
    try { for (let i = 0; i < localStorage.length; i++) { const kk = localStorage.key(i); if (kk?.startsWith("dolaJobKey:") && localStorage.getItem(kk) === k) return kk.slice(11); } } catch {}
    return null;
  };
  const [conc, setConc] = useState({ send: "", login: "", gmin: "", gmax: "" });   // gmin/gmax: chờ ngẫu nhiên giữa lần gửi
  // Bỏ qua bước "kiểm tra nick" trước khi chạy (deadNicks): chạy thẳng, nick cookie chết sẽ lỗi lúc gửi rồi tự xoay.
  const [skipVerify, setSkipVerify] = useState(() => { try { return localStorage.getItem("dolaSkipVerify") === "1"; } catch { return false; } });
  useEffect(() => { try { localStorage.setItem("dolaSkipVerify", skipVerify ? "1" : "0"); } catch {} }, [skipVerify]);
  const stop = useRef(false);
  const vidDir = useRef("");

  useEffect(() => { api.getVideoDir?.().then((r) => (vidDir.current = r?.abs || "")).catch(() => {}); }, []);
  // Mở app lên mà server vẫn đang render dở: bám lại job đó, đừng để bảng trống trông như treo.
  useEffect(() => {
    (async () => {
      for (const t of await inflightTasks()) {
        const n = (t.client_id && nickOfKey(t.client_id)) || t.account;   // theo khóa trước (đúng cả khi đã xoay nick), rồi mới theo nick
        if (!n || inflight.current.has(n)) continue;
        const me = { ctl: new AbortController(), id: t.id };
        inflight.current.set(n, me);
        setRow(n, { prompt: t.prompt || "", phase: "running", stage: t.status === "queued" ? "queued" : "rendering",
                    startedAt: (t.started_at || t.created_at || Date.now() / 1000) * 1000, errorRaw: "", videoUrl: "" });
        watchJob(n, t.id, me).catch((e) => { if (inflight.current.get(n) === me) setRow(n, { phase: "error", errorRaw: "Không theo dõi được job: " + (e?.message || e) }); })
          .finally(() => { if (inflight.current.get(n) === me) inflight.current.delete(n); });
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
  // Nick còn điểm NHƯNG không đủ cho thời lượng đang chọn (vd còn 1 mà 30s cần 2) = coi như HẾT ĐIỂM hôm nay:
  // cho "nghỉ" (không đưa vào Chạy sẵn sàng), đẩy xuống cuối + làm mờ. Điểm tự reset 0h JST nên không đụng lịch.
  const cost = (model, dur) => creditCost(dur, model, health?.credit_cost);   // giá THẬT theo model (2.5 · 15s = 4 điểm)
  const needForDur = cost(def.model, def.dur);
  const lowCredit = (a) => a.remaining != null && a.remaining < needForDur;

  async function runOne(n, promptOverride) {
    const acc = accounts.find((a) => a.account === n);
    const s = row(n);
    // Ô prompt riêng trống thì LẤY TẠM dòng đầu khung Prompt — CHỈ khi khung đó đúng 1 kịch bản. Trước đây lấy
    // vô điều kiện: khung có nhiều kịch bản (hoặc 1 khối dài không xuống dòng) thì nick nhận nguyên khối của
    // người khác — đúng cái "tràn prompt". Nút ▶ từng nick không đi qua rào chắn của runBatch nên lọt ở đây.
    let prompt = ((promptOverride ?? s.prompt) || "").trim();
    if (!prompt) {
      const first = firstLine(bulk);
      if (first && bulk.trim() !== first) {
        setRow(n, { phase: "error", status: "warn",
                    errorRaw: `Nick này chưa có prompt riêng, mà khung Prompt đang có nhiều nội dung — bấm "Mỗi dòng 1 nick" / "Mỗi khối 1 nick" để chia, hoặc gõ prompt riêng cho ${n}.` });
        return false;
      }
      prompt = first;
    }
    if (!prompt) { setRow(n, { phase: "error", errorRaw: "Chưa nhập prompt", status: "warn" }); return false; }
    // Chỉ cảnh báo, không chặn: bị Dola chặn thì không trừ lượt, còn hộp confirm trước đây bấm Huỷ một lần là
    // prompt đó bị nhớ "chưa gửi" mãi (không thuộc "Chạy lại lỗi") → auto tạo đứng im.
    const risky = riskyPrompt(prompt);
    if (risky) setRow(n, { status: `gửi thử — có từ dễ bị chặn: ${risky}` });
    // Server tự co các mốc thời gian trong prompt về đúng thời lượng (fit_prompt_to_duration) — chỉ báo, không chặn.
    const over = durationMismatch(prompt, s.dur);
    if (over) setRow(n, { status: `prompt ~${over}s → tự co về ${s.dur}s` });
    if (acc && acc.remaining != null) {
      const need = cost(s.model, s.dur);
      if (acc.remaining < need) { setRow(n, { phase: "error", errorRaw: `Không đủ điểm cho ${s.model} · ${s.dur}s (cần ${need}, còn ${acc.remaining}) — ${cheaperHint(acc.remaining, health?.credit_cost)}.` }); return false; }
    }
    const cur = inflight.current.get(n);
    if (cur) {   // nick đang có lượt chạy chưa xong → KHÔNG tạo lượt thứ hai (tránh trừ lượt 2 lần). Mọi fetch đều có hạn
                 // (fetchT) nên lượt cũ không thể kẹt vô hạn; chỉ nút Dừng mới hủy. Khóa client_id là lưới thứ hai ở server.
      setRow(n, { phase: "running" });
      setGen(`${n}: ${cur.id ? "đang chạy dở từ trước — theo dõi tiếp" : "đang gửi — chờ server nhận"}, không gửi lại.`);   // thẻ đang chạy không hiện status → báo ở dòng chung
      return true;
    }
    const me = { ctl: new AbortController(), id: null };
    inflight.current.set(n, me);
    const mine = () => inflight.current.get(n) === me;   // bị Dừng / chạy lại đè lên → lần này im lặng rút lui
    setRow(n, { prompt, phase: "running", stage: "queued", startedAt: Date.now(), stageAt: Date.now(), endedAt: 0, errorRaw: "", videoUrl: "", ranOn: "" });
    try {
      // Người dùng đã chọn đích danh nick này thì "tạm ngưng" không còn là lý do chặn: mở lại giúp rồi
      // gửi luôn (server từ chối job vào nick tạm ngưng). Trước đây thẻ chỉ báo "bấm Bật lịch tất cả rồi chạy lại".
      if (acc && accStateOf(acc) === "off") {
        setRow(n, { status: "đang mở lại nick tạm ngưng…" });
        await patchAccount(n, { scheduling: true }, me.ctl.signal);
        onRefresh();
      }
      // client_id: server thấy job CHƯA xong cùng khóa (Dừng→Chạy, POST treo rồi gửi lại, mở lại app) → trả job cũ.
      const j = await submitJob(prompt, { model: s.model, duration: parseInt(s.dur, 10), ratio: s.ratio, account: n, client_id: jobKey(n) }, me.ctl.signal);
      me.id = j.id;
      if (j.prompt && j.prompt !== prompt) {   // server trả job CŨ cùng khóa (Dừng → sửa prompt → Chạy): nói rõ, đừng để tưởng prompt mới đã đi
        setRow(n, { prompt: j.prompt });
        setGen(`${n}: bám lại job đang dở (prompt cũ) — prompt mới CHƯA gửi; chờ job xong rồi chạy lại.`);
      }
      return await watchJob(n, j.id, me);
    } catch (e) {
      if (e?.name === "AbortError" && stop.current) return true;   // người dùng Dừng (stopAll đã sơn thẻ + gỡ Map) ≠ lỗi
      if (!mine()) return false;                 // lần chạy mới đã sơn thẻ, đừng đè lỗi cũ lên
      if (e?.name === "AbortError") {            // quá hạn (fetchT) → nói rõ thay vì chữ thô của Chromium
        setRow(n, { phase: "error", errorRaw: "Server không trả lời kịp (quá hạn) — chờ vài giây rồi chạy lại." }); return false;
      }
      const msg = e?.message || String(e);
      setRow(n, { phase: "error", errorRaw: msg });
      if (/không tồn tại/i.test(msg)) onRefresh();   // bảng đang cũ → nạp lại danh sách nick
      return false;
    }
    finally { if (mine()) inflight.current.delete(n); }
  }
  // Theo dõi một job đã có id — dùng cho cả job vừa gửi và job đang chạy dở từ lần mở app trước.
  async function watchJob(n, id, me) {
    let stage = "queued", fails = 0, pip = "";
    const mine = () => !me || inflight.current.get(n) === me;   // bị Dừng / chạy lại đè lên → rút lui, không sơn thẻ nữa
    while (true) {
      await new Promise((r) => setTimeout(r, 3000));
      if (!mine()) return true;
      if (stop.current) { setRow(n, { phase: "idle", status: "đã dừng theo dõi" }); return true; }
      let pj;
      try { pj = await pollJob(id, me?.ctl.signal); fails = 0; }
      catch (e) {
        if (!mine()) return true;
        // Server tắt / khởi động lại giữa chừng: job vẫn nằm trong tasks.db, chờ server lên rồi hỏi tiếp.
        // 404 = server không còn job này (đổi server / DB mới) → báo lỗi thay vì quay vòng vô tận.
        if (e?.status === 404 || ++fails >= 20) {
          if (e?.status === 404) clearJobKey(n);   // job không còn → khóa cũ vô nghĩa, lần sau tạo mới
          setRow(n, { phase: "error", errorRaw: e?.status === 404 ? "Server không còn job này (đã đổi server hoặc xoá dữ liệu?)" : "Mất kết nối server (nhiều lần liên tiếp): " + (e?.message || e) });
          return false;
        }
        setRow(n, { status: `chờ server trả lời (${fails})` });
        continue;
      }
      if (!mine()) return true;
      const pk = [pj.proxy_ip, pj.proxy_provider, pj.proxy_used, pj.proxy_per, pj.proxy_fresh, pj.proxy_kind].join("|");   // IP + lượt + NCC + loại → cột Proxy
      if (pk !== pip) { pip = pk; setRow(n, { proxyIp: pj.proxy_ip || "", proxyIsp: pj.proxy_isp || "", proxyProvider: pj.proxy_provider || "", proxyUsed: pj.proxy_used, proxyPer: pj.proxy_per, proxyFresh: pj.proxy_fresh, proxyKind: pj.proxy_kind || "" }); }
      if (pj.account && pj.account !== n) setRow(n, { ranOn: pj.account });   // job đã XOAY sang nick khác → hiện nick thật
      // note = cảnh báo kèm job ĐÃ xong (vd Dola trả clip ngắn hơn số giây đã đặt mà vẫn trừ đủ lượt).
      if (pj.status === "completed") { clearJobKey(n); setRow(n, { phase: "done", stage: "done", videoUrl: pj.video_url, endedAt: Date.now(), note: pj.error || "" }); api.saveVideo?.(pj.video_url); return true; }
      if (pj.status === "failed") { clearJobKey(n); setRow(n, { phase: "error", errorRaw: pj.error || "?", endedAt: Date.now(), charged: !!pj.charged }); return false; }   // charged: lệnh đã tới Dola → chạy lại là trừ lượt lần 2
      if (pj.stage && pj.stage !== stage) { stage = pj.stage; setRow(n, { stage, stageAt: Date.now() }); }
    }
  }

  // "Chạy sẵn sàng" = đúng nick chip xanh, dùng chung accState (đã tính cả busy/cooling/scheduling).
  const canRun = canRunAccount;
  const readyNicks = () => accounts.filter((a) => canRun(a) && !lowCredit(a)).map((a) => a.account);
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
    // Chỉ kiểm tra nick CHƯA được xác nhận gần đây: nick Dola vừa nhận lệnh (< 60 phút) chắc chắn cookie sống → chạy
    // luôn. Bật "Bỏ qua kiểm tra nick" thì không kiểm nick nào (cookie chết sẽ lỗi lúc gửi rồi tự xoay).
    // Nick đang có lượt chạy chưa xong: giữ nguyên thẻ, không kiểm cookie, không đưa vào lượt này (runOne cũng từ chối tạo lượt 2).
    const running = ns.filter((n) => inflight.current.has(n));
    // Nick lỗi mà lệnh ĐÃ tới Dola (charged = đã trừ lượt): không tự chạy lại trong lô — muốn thật thì bấm ▶ từng thẻ (có hỏi).
    const chargedErr = ns.filter((n) => !running.includes(n) && rows[n]?.phase === "error" && rows[n]?.charged);
    const hold = (n) => running.includes(n) || chargedErr.includes(n);
    const toCheck = skipVerify ? [] : ns.filter((n) => !hold(n) && cookieInfo(accounts.find((x) => x.account === n)).st !== "fresh");
    ns.forEach((n) => { if (hold(n)) return; setRow(n, { phase: "running", stage: toCheck.includes(n) ? "checking" : "queued", startedAt: Date.now(), stageAt: Date.now(), endedAt: 0, errorRaw: "", videoUrl: "" }); });
    if (toCheck.length) setGen(`Kiểm tra cookie ${toCheck.length} nick` + (ns.length > toCheck.length ? ` (bỏ qua ${ns.length - toCheck.length} nick vừa chạy OK)` : "") + "…");
    const dead = toCheck.length ? await deadNicks(toCheck) : [];
    if (stop.current) { ns.forEach((n) => setRow(n, { phase: "idle", status: "đã dừng" })); setGen("Đã dừng trước khi gửi."); return; }   // Dừng lúc đang kiểm cookie
    dead.forEach((n) => setRow(n, { phase: "error", errorRaw: "Cookie hết hạn — đăng nhập lại nick này rồi chạy lại." }));
    const blocked = ns.filter((n) => {
      if (hold(n)) return false;   // đang chạy dở / đã trừ lượt: giữ nguyên thẻ, không sơn "Lỗi" đè lên
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
    const run = ns.filter((n) => !dead.includes(n) && !blocked.includes(n) && !hold(n));
    const skipped = [dead.length ? `${dead.length} cookie chết` : "", blocked.length ? `${blocked.length} nghỉ/hết lượt` : "",
                     running.length ? `${running.length} đang chạy dở` : "", chargedErr.length ? `${chargedErr.length} đã trừ lượt (bấm ▶ từng thẻ nếu muốn chạy lại)` : ""].filter(Boolean).join(" · ");
    if (!run.length) {
      setGen(`Không nick nào chạy được: ${skipped || "0 nick được chọn"}.`);
      return;
    }
    setGen(`Đang chạy ${run.length} nick…` + (skipped ? ` (bỏ ${skipped})` : ""));
    // KHÔNG `run.map(runOne)`: map truyền (nick, CHỈ SỐ, mảng) → chỉ số thành promptOverride → nick[0] chạy nhầm dòng đầu
    // khung Prompt, nick[1..] ném TypeError trước khi gửi → chỉ 1 job đi, các thẻ còn lại đứng "Xếp hàng" mãi (fa85bf5, 15/09).
    const res = await Promise.all(run.map((n) => runOne(n)));   // runOne trả true=ok / false=lỗi
    const bad = run.filter((_, i) => res[i] === false);
    if (stop.current) { setGen("Đã dừng."); return; }   // người dùng Dừng giữa chừng: không tổng kết "Lỗi", không thông báo desktop
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
  // Không nick nào đủ điểm cho model/giây đang chọn: NÓI RÕ thay vì "Không có nick sẵn sàng" (chip vẫn xanh → tưởng tool
  // không gửi job, 15/09: 9 nick còn 1 điểm mà đang chọn 2.5 · 30s cần 2).
  const noReadyMsg = () => {
    const live = accounts.filter(canRun);
    if (!live.length) return "Không có nick sẵn sàng (hết lượt / nghỉ / cookie chết) — xem Kho tài khoản.";
    const best = Math.max(0, ...live.map((a) => a.remaining ?? 0));
    return `Không nick nào đủ điểm cho ${def.model} · ${def.dur}s (cần ${needForDur}). ${live.length} nick sẵn sàng còn tối đa ${best} điểm → ${cheaperHint(best, health?.credit_cost)}.`;
  };
  const runReady = () => runBatch(readyNicks(), noReadyMsg());
  // "Chạy lại lỗi" bỏ qua nick mà lệnh ĐÃ tới Dola (charged): chạy lại = trừ lượt lần 2 — runBatch giữ các nick đó lại và nói rõ.
  const retryFailed = () => runBatch(accounts.map((a) => a.account).filter((n) => rows[n]?.phase === "error"), "Không có nick lỗi.");
  // Dừng = hủy CẢ fetch đang treo (POST tạo job / PATCH mở nick), không chỉ vòng theo dõi — rồi xoá dấu để Chạy lại
  // luôn được (runOne tự hỏi server còn job dở không → bám lại, không tạo trùng).
  const stopAll = () => {
    stop.current = true;
    for (const [n, e] of inflight.current) { e.ctl.abort(); setRow(n, { phase: "idle", status: "đã dừng theo dõi" }); }
    inflight.current.clear();   // vòng theo dõi cũ thấy mình bị gỡ → rút lui im lặng; thẻ đã sơn ở trên
    setGen("Đã dừng theo dõi (video có thể vẫn hoàn tất trên Dola).");
  };
  // AUTO-DRAIN "Chạy hết lượt hôm nay": mỗi nick chạy hết SỐ VIDEO còn làm được hôm nay (còn điểm ÷ điểm/video),
  // lấy prompt round-robin từ khung Prompt. Mỗi nick chạy TUẦN TỰ (xong video này mới video kế), các nick CHẠY SONG
  // SONG; server tự giãn nhịp + giới hạn luồng + xoay nick. Mục tiêu: không để lượt/điểm ngày hết hạn oan.
  async function autoDrain() {
    const ps = bulk.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
    if (!ps.length) { setGen("Nhập prompt (mỗi dòng 1 cái) trước khi 'Chạy hết lượt'."); return; }
    const need = needForDur;
    const plan = accounts
      .filter((a) => canRun(a) && rows[a.account]?.phase !== "running")
      .map((a) => ({ n: a.account, cap: Math.floor((a.remaining ?? 0) / need) }))
      .filter((p) => p.cap > 0);
    if (!plan.length) { setGen(noReadyMsg()); return; }
    const total = plan.reduce((s, p) => s + p.cap, 0);
    if (!window.confirm(`Chạy hết lượt hôm nay: ${plan.length} nick × tối đa lượt còn lại = ${total} video ${def.dur}s (mỗi video ${need} điểm), chia ${ps.length} prompt vòng tròn. Bắt đầu?`)) return;
    stop.current = false;
    setGen(`Auto-drain: ${total} video trên ${plan.length} nick (${def.dur}s · ${need} điểm/video) — server tự giãn nhịp + xoay nick.`);
    let pi = 0;   // chỉ số prompt round-robin, dùng chung các nick
    await Promise.all(plan.map(async ({ n, cap }) => {
      for (let k = 0; k < cap; k++) {
        if (stop.current) break;
        const p = ps[pi++ % ps.length];
        setRow(n, { model: def.model, ratio: def.ratio, dur: def.dur });
        const ok = await runOne(n, p);   // đưa prompt trực tiếp, khỏi lệ thuộc state trễ
        if (!ok || stop.current) break;  // nick lỗi/hết điểm giữa chừng → dừng nick đó (đã tự xoay nếu bật AUTO_RETRY)
      }
    }));
    setGen(`Auto-drain xong. Xem tiến trình từng nick ở bảng dưới.`);
    api.notify?.("Dola Studio — auto-drain xong", `Đã chạy hết lượt ${plan.length} nick.`);
  }
  const fillAll = () => { const p = firstLine(bulk); accounts.forEach((a) => setRow(a.account, { prompt: p })); setGen("Đã điền prompt cho tất cả nick."); };
  // Chia xong phải NÓI RÕ phần lệch: nick không được gán vẫn giữ prompt CŨ (chạy tiếp là tạo lại video cũ,
  // tốn lượt), còn kịch bản dư thì không ai nhận. Trước đây im lặng cả hai → prompt cũ "tràn" sang mẻ mới.
  const chiaMsg = (dat, co, donVi) => {
    const thua = co - dat, thieu = accounts.length - dat;
    return `Đã chia ${dat} ${donVi}.`
      + (thieu > 0 ? ` ⚠ ${thieu} nick KHÔNG được gán — vẫn giữ prompt cũ, chạy sẽ tạo lại video cũ (bấm "Xóa prompt" nếu không muốn).` : "")
      + (thua > 0 ? ` ⚠ ${thua} ${donVi} dư chưa nick nào nhận (thiếu nick).` : "");
  };
  const fillLines = () => { const ps = bulk.split(/\r?\n/).map((x) => x.trim()).filter(Boolean); accounts.forEach((a, i) => ps[i] && setRow(a.account, { prompt: ps[i] })); setGen(chiaMsg(Math.min(ps.length, accounts.length), ps.length, "prompt")); };
  // MỖI KHỐI 1 NICK: kịch bản nhiều dòng (镜头1…mô tả…音频…) là 1 prompt, các kịch bản CÁCH NHAU DÒNG TRỐNG.
  // Giữ NGUYÊN cả khối (không ngắt từng dòng) → khối[i] cho nick[i]. Dùng khi prompt là kịch bản nhiều dòng.
  const fillBlocks = () => {
    // Kịch bản hay có dòng trống ngăn TỪNG CẢNH (镜头1 / 音频 / 镜头2) chứ không chỉ ngăn giữa các kịch bản.
    // Tách theo 1 dòng trống sẽ chẻ vụn 1 kịch bản thành nhiều mảnh, mỗi nick nhận một mảnh cụt. Nên nếu văn bản
    // có DÒNG TRỐNG ĐÔI thì coi đó mới là ranh giới kịch bản (cảnh vẫn cách nhau 1 dòng trống).
    const doi = bulk.split(/\r?\n\s*\r?\n\s*\r?\n/).map((b) => b.trim()).filter(Boolean);
    const blocks = doi.length > 1 ? doi : bulk.split(/\r?\n\s*\r?\n/).map((b) => b.trim()).filter(Boolean);
    accounts.forEach((a, i) => blocks[i] && setRow(a.account, { prompt: blocks[i] }));
    setGen(chiaMsg(Math.min(blocks.length, accounts.length), blocks.length,
                   doi.length > 1 ? "kịch bản (cách nhau DÒNG TRỐNG ĐÔI)" : "kịch bản"));
  };
  const fillAllWhole = () => { const p = bulk.trim(); if (!p) return; accounts.forEach((a) => setRow(a.account, { prompt: p })); setGen("Đã điền CẢ khối prompt cho tất cả nick (không ngắt)."); };
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
  const needCredit = selected.reduce((s, n) => s + cost(row(n).model, row(n).dur), 0);
  const runningNow = Object.values(rows).filter((r) => r.phase === "running").length;
  const allSel = accounts.length > 0 && accounts.every((a) => sel[a.account]);
  // Nick chạy được lên đầu: đang chạy → sẵn sàng → vừa xong / lỗi (chạy lại được) → nghỉ → hết credit/lượt
  // → tắt lịch → chưa đăng nhập. Người dùng nhìn hàng đầu là biết còn bao nhiêu nick dùng được.
  const rank = (a) => {
    const ph = rows[a.account]?.phase, st = accState(a);
    if (ph === "running" || st === "busy") return 0;
    if (st === "ready" && !lowCredit(a)) return 1;   // còn ĐỦ điểm cho thời lượng này → lên đầu
    if (ph === "done" || ph === "error") return 2;
    if (st === "ready" && lowCredit(a)) return 4;    // còn điểm nhưng KHÔNG đủ → xuống cùng nhóm hết điểm
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
    lowCredit: lowCredit(a),   // hết điểm cho thời lượng này → làm mờ + (đã) đẩy xuống cuối
    // prompt THẬT đang dựng trên nick này (job xoay từ nick gốc) → cột Prompt hiện đúng, không còn placeholder.
    rotatedPrompt: rotatedInto[a.account] ? (row(rotatedInto[a.account]).prompt || "") : "",
    onSel: (v) => setSel((p) => ({ ...p, [a.account]: v })), onChange: (patch) => setRow(a.account, patch),
    onRun: () => {
      if (row(a.account).charged && !window.confirm(`${a.account}: lệnh trước ĐÃ tới Dola (đã trừ lượt) — xem dola.com có video chưa. Chạy lại sẽ trừ lượt lần 2, vẫn chạy?`)) return;
      stop.current = false; runOne(a.account);
    }, onRelogin: () => relogin(a.account), onProxy: () => setProxy(a.account), onDelete: () => del(a.account),
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
          <Button variant="outline" size="sm" onClick={fillBlocks} disabled={!lines.length} title="Kịch bản nhiều dòng = 1 prompt, các kịch bản cách nhau DÒNG TRỐNG. Giữ nguyên cả khối, không ngắt từng dòng."><ListOrdered className="h-3.5 w-3.5" />Mỗi khối 1 nick</Button>
          <Button variant="outline" size="sm" onClick={fillAllWhole} disabled={!lines.length} title="Điền NGUYÊN cả khối prompt (nhiều dòng, không ngắt) cho MỌI nick">Cả khối cho tất cả</Button>
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
        <button type="button" className="text-xs font-medium text-primary hover:underline" onClick={() => selectWhere((a) => a.remaining != null && a.remaining >= needForDur && canRun(a), `còn đủ credit cho ${def.model} · ${def.dur}s`)}>Còn đủ credit</button>
        <span className="text-outline-variant">·</span>
        <button type="button" className="text-xs font-medium text-primary hover:underline" onClick={() => selectWhere(() => !allSel, allSel ? "" : "tất cả")}>{allSel ? "Bỏ chọn" : "Chọn tất cả"}</button>
        {selected.length > 0 && <><span className="text-outline-variant">·</span><button type="button" className="text-xs font-medium text-error hover:underline" onClick={clearSelectedPrompts}>Xóa prompt đã chọn ({selected.length})</button></>}
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={runReady}><Play className="h-3.5 w-3.5" />Chạy sẵn sàng</Button>
        <Button variant="outline" size="sm" className="border-tertiary/50 text-tertiary hover:text-tertiary" onClick={autoDrain} title="Mỗi nick chạy hết số video còn làm được hôm nay (còn điểm ÷ điểm/video), lấy prompt vòng tròn — không để lượt ngày hết hạn oan"><Zap className="h-3.5 w-3.5" />Chạy hết lượt</Button>
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
        <label className="ml-2 flex cursor-pointer items-center gap-1.5" title="Bỏ bước 'đang kiểm tra nick' trước khi chạy: chạy thẳng cho nhanh. Nick cookie chết sẽ lỗi lúc gửi rồi tự xoay.">
          <input type="checkbox" checked={skipVerify} onChange={(e) => setSkipVerify(e.target.checked)} />
          <span>Bỏ qua kiểm tra nick</span>
        </label>
        <span className="ml-2" title="Giãn nhịp ngẫu nhiên giữa mỗi lần gửi (theo từng proxy) để tránh Dola chặn 710022002 'gửi quá dày'">Chờ ngẫu nhiên</span>
        <Input className="h-7 w-12 font-mono text-[11px]" type="number" min={0} max={60} value={conc.gmin} onChange={(e) => setConc({ ...conc, gmin: e.target.value })} />
        <span>–</span>
        <Input className="h-7 w-12 font-mono text-[11px]" type="number" min={0} max={120} value={conc.gmax} onChange={(e) => setConc({ ...conc, gmax: e.target.value })} />
        <span>giây</span>
        <Button variant="outline" size="sm" className="h-7" onClick={applyGap}>Áp dụng</Button>
      </div>
      {parseInt(conc.send, 10) > 5 && (
        <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-1.5 text-[11px] leading-relaxed text-warn">
          ⚠ Đang để <b>{conc.send} nick gửi cùng lúc</b> — mở nhiều Chrome một lúc dễ làm máy nghẽn (nick kẹt lâu ở "đang kiểm tra nick"). <b>3–4 là tối ưu</b>: gửi xong là trả trình duyệt ngay, render chạy nền nên không chậm hơn.
        </div>
      )}
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

// TIẾN TRÌNH: MỘT thanh liên tục (%) + tên bước + thời gian, thay 5 vạch rời khó đọc. Bước "Dola dựng" tăng dần theo
// thời gian dựng thường lệ (30s ~15p, 10/15s ~8p) để thấy còn lâu không; quá 1.5× thì chuyển vàng "đừng chạy lại".
// 3 chặng: Gửi → Dựng → Tải. Mỗi stage server báo ánh xạ về chặng nào; chặng "Dựng" tự đầy theo thời gian dựng
// thường lệ (30s ~15p, 10/15s ~8p) để biết còn lâu không.
const STEPS = ["Gửi", "Dựng", "Tải"];
const STAGE_STEP = { checking: 0, queued: 0, waiting: 0, opening: 0, submitting: 0, rendering: 1, processing: 1, downloading: 2 };
const STAGE_LABEL = {
  checking: "Kiểm tra nick", queued: "Xếp hàng", waiting: "Chờ slot Chrome", opening: "Mở nick",
  submitting: "Gửi prompt tới Dola", rendering: "Dola đang dựng video", processing: "Đang tạo", downloading: "Tải video về",
};
const STAGE_HINT = {
  checking: "xem cookie còn sống không", queued: "gửi tới server → chờ tới lượt gửi (giãn nhịp theo IP)",
  waiting: "slot Chrome đang bận — máy khỏe thì tăng 'Nick gửi cùng lúc'",
  opening: "mở Chrome + vào Dola (treo quá 5 phút tự cắt, xoay nick)", submitting: "chờ Dola nhận lệnh",
};
const renderSec = (dur) => (parseInt(dur, 10) >= 30 ? 900 : 480);
const fmtMs = (ms) => { const x = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(x / 60)}p ${String(x % 60).padStart(2, "0")}s`; };

function progressOf(s) {
  if (s.phase === "done") return { step: 3, label: "Hoàn tất", sub: 1 };
  const step = STAGE_STEP[s.stage] ?? 1;
  const label = STAGE_LABEL[s.stage] || "Đang tạo";
  if (s.phase === "running" && step === 1) {   // chặng Dựng: đầy dần theo thời gian
    const t = (Date.now() - (s.stageAt || s.startedAt)) / 1000, exp = renderSec(s.dur);
    return { step, label, sub: Math.min(0.97, t / exp), left: exp - t, slow: t > exp * 1.5 };
  }
  return { step, label, sub: null };   // sub=null → chặng đang chạy dạng "đang xử lý" (sọc chạy), không đo %
}

// Một chặng trong thanh: xong = đầy màu; đang chạy = đầy theo sub (hoặc sọc chạy khi sub=null); chưa tới = rỗng.
function Seg({ state, sub, tone, err }) {
  const base = "relative h-1.5 flex-1 overflow-hidden rounded-full bg-surface-highest";
  if (state === "done") return <div className={base}><div className={"absolute inset-0 rounded-full " + tone} /></div>;
  if (state !== "cur") return <div className={base} />;
  const fill = err ? "bg-error" : tone;
  if (sub == null) return (   // đang xử lý, không đo được % → sọc chạy
    <div className={base}><div className={"absolute inset-y-0 w-2/5 animate-[pulse_1.2s_ease-in-out_infinite] rounded-full " + fill} /></div>
  );
  return <div className={base}><div className={"absolute inset-y-0 left-0 rounded-full transition-[width] duration-700 " + fill}
    style={{ width: Math.max(6, sub * 100) + "%" }} /></div>;
}

function Progress({ s, a, compact = false, onRun, rotatedFrom }) {
  const p = progressOf(s);
  const run = s.phase === "running", done = s.phase === "done", err = s.phase === "error";
  const idle = !run && !done && !err;
  const took = s.startedAt ? fmtMs((s.endedAt || Date.now()) - s.startedAt) : "";
  const tone = done ? "bg-tertiary" : p.slow ? "bg-warn" : "bg-primary";
  const toneText = done ? "text-tertiary" : p.slow ? "text-warn" : err ? "text-error" : run ? "text-primary" : "text-muted-foreground";
  // err: tô đỏ đúng chặng đang dở; idle: 3 chặng rỗng.
  const curStep = err ? (STAGE_STEP[s.stage] ?? 1) : p.step;
  return (
    <div className="flex w-full min-w-0 flex-col gap-1.5">
      {(run || done || err) && (
        <div className="flex items-center gap-1.5 text-[12px] leading-none">
          {run ? <span className="relative flex h-2 w-2 flex-none"><span className="absolute h-full w-full animate-ping rounded-full bg-primary/60" /><span className="relative h-2 w-2 rounded-full bg-primary" /></span>
            : done ? <CheckCircle2 className="h-3.5 w-3.5 flex-none text-tertiary" />
              : <span className="h-2 w-2 flex-none rounded-full bg-error" />}
          <span className={"truncate font-medium " + toneText}>{err ? fmtError(s.errorRaw || "").short : p.label}</span>
          <span className="ml-auto flex-none font-mono text-[10.5px] tabular-nums text-muted-foreground">
            {done ? took : run ? (p.sub != null ? `${Math.round(p.sub * 100)}% · ${took}` : took) : ""}
          </span>
        </div>
      )}
      <div className="flex items-center gap-1" title={run ? `${p.label}${p.sub != null ? " · " + Math.round(p.sub * 100) + "%" : ""}` : undefined}>
        {STEPS.map((_, i) => (
          <Seg key={i} tone={tone} err={err && i === curStep}
            sub={i === curStep ? p.sub : null}
            state={idle ? "todo" : done || i < curStep ? "done" : i === curStep ? "cur" : "todo"} />
        ))}
      </div>
      {!compact && (
        <div className="flex justify-between font-mono text-[9.5px] text-muted-foreground">
          {STEPS.map((n, i) => <span key={n} className={!idle && (done || i < curStep) ? "text-foreground/70" : i === curStep && !idle ? toneText : ""}>{n}</span>)}
        </div>
      )}
      {run && (
        <span className={"truncate font-mono text-[10.5px] " + (p.slow ? "text-warn" : "text-muted-foreground")}>
          {p.left != null ? (p.slow ? "lâu hơn thường lệ — Dola vẫn dựng, ĐỪNG chạy lại" : p.left > 0 ? `~còn ${fmtMs(p.left * 1000)}` : "sắp xong…") : STAGE_HINT[s.stage] || ""}
        </span>
      )}
      {(err || idle) && <div className="flex min-h-5 items-center"><Footer s={s} a={a} onRun={onRun} rotatedFrom={rotatedFrom} /></div>}
    </div>
  );
}

// Nhãn cookie: xanh = Dola vừa nhận lệnh/vừa kiểm (< 60 phút) → bấm Chạy khỏi kiểm tra lại.
function CookieTag({ a }) {
  const c = cookieInfo(a);
  const tone = { fresh: "text-tertiary", stale: "text-muted-foreground", unknown: "text-muted-foreground/70", dead: "text-error" }[c.st];
  return (
    <span className={"font-mono text-[10px] " + tone} title="Cookie được xác nhận MỖI KHI Dola nhận lệnh của nick (hoặc khi kiểm tra). Xác nhận trong 60 phút thì bấm Chạy bỏ qua bước kiểm tra nick.">
      {c.st === "fresh" ? "● " : c.st === "dead" ? "✗ " : "○ "}{c.text}
    </span>
  );
}

// Dạng bảng: một dòng một nick, cùng dữ liệu và thao tác với thẻ nhưng nhìn được 15–20 nick không cần cuộn.
function NickRow({ a, s, idx, selected, elapsed, proxyCell, rotatedFrom, rotatedPrompt, lowCredit, onSel, onChange, onRun, onRelogin, onProxy, onDelete, onPlay, onOpen, onCopy, onRemoveWm, onNew }) {
  const n = a.account;
  const chip = stateChip(a, s);
  const tint = s.phase === "done" ? " bg-tertiary/5" : s.phase === "error" ? " bg-error/5" : "";
  const icon = "h-7 w-7 text-muted-foreground hover:text-foreground";
  const td = "px-2 py-1.5 align-middle";
  return (
    <tr className={"border-b border-surface-high/60 last:border-0" + tint + (isDim(a, s) || (lowCredit && s.phase === "idle") ? " opacity-60" : "")}>
      <td className={td}><input type="checkbox" checked={selected} onChange={(e) => onSel(e.target.checked)} /></td>
      <td className={td + " font-mono text-[11px] text-muted-foreground tabular-nums"}>{idx}</td>
      <td className={td + " whitespace-nowrap"}>
        <div className="font-mono text-[12px] font-semibold">{n}</div>
        {s.ranOn && s.ranOn !== n && <div className="font-mono text-[10px] text-primary" title="Job đã xoay sang nick này">↦ chạy trên {s.ranOn}</div>}
        <div className="font-mono text-[10.5px] text-muted-foreground">{a.used_today}/{a.limit} hôm nay{a.remaining != null ? ` · còn ${a.remaining}` : ""}{lowCredit && s.phase === "idle" ? <span className="text-warn"> · nghỉ (thiếu điểm)</span> : ""}</div>
        <CookieTag a={a} />
      </td>
      <td className={td + " whitespace-nowrap"}><Badge variant={chip.variant} title={chip.title || undefined}>{chip.text}</Badge></td>
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
      {/* PHẢI có max-w: truncate = white-space:nowrap, mà ô bảng tự co giãn thì cột nở bằng ĐÚNG chiều dài
          chuỗi không ngắt (prompt 9000 ký tự) → tràn cả bảng ra ngoài màn hình. max-w cho truncate chỗ bấu. */}
      <td className={td + " w-[300px] min-w-[260px] max-w-[300px]"}>
        {rotatedPrompt
          ? <div className="rounded-md border border-primary/30 bg-primary/5 px-2 py-1 text-[12px] leading-tight" title={rotatedPrompt.slice(0, 300)}>
              <span className="block min-w-0 truncate">{rotatedPrompt.slice(0, 300)}</span>
              <span className="font-mono text-[10px] text-primary">↳ prompt của job xoay từ {rotatedFrom}</span>
            </div>
          : <Input className="h-8 text-[12.5px]" value={s.prompt} placeholder={`prompt cho ${n}…`} onChange={(e) => onChange({ prompt: e.target.value })} />}
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
        <Progress s={s} a={a} compact onRun={onRun} rotatedFrom={rotatedFrom} />
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

function NickCard({ a, s, selected, elapsed, rotatedFrom, rotatedPrompt, lowCredit, onSel, onChange, onRun, onRelogin, onProxy, onDelete, onPlay, onOpen, onCopy, onRemoveWm, onNew }) {
  const n = a.account;
  const cardChip = stateChip(a, s);
  const border = s.phase === "done" ? " ring-1 ring-tertiary/25" : s.phase === "error" ? " ring-1 ring-error/30" : "";
  const icon = "h-7 w-7 text-muted-foreground hover:text-foreground";
  return (
    <div className={"flex flex-col gap-2.5 rounded-lg bg-surface p-3" + border + (isDim(a, s) || (lowCredit && s.phase === "idle") ? " opacity-60" : "")}>
      <div className="flex items-center gap-2">
        <input type="checkbox" checked={selected} onChange={(e) => onSel(e.target.checked)} />
        <span className="font-mono text-[12.5px] font-semibold">{n}</span>
        <Badge variant={cardChip.variant}>{cardChip.text}</Badge>
        <span className="ml-auto font-mono text-[11px] text-muted-foreground">{a.used_today}/{a.limit} hôm nay{a.remaining != null ? ` · còn ${a.remaining}` : ""}</span>
      </div>
      {s.phase === "done" ? (
        <DoneRow s={s} onPlay={onPlay} onOpen={onOpen} onCopy={onCopy} onRemoveWm={onRemoveWm} onNew={onNew} />
      ) : rotatedPrompt ? (
        <div className="rounded-md border border-primary/30 bg-primary/5 px-2 py-1.5 text-[12.5px] leading-tight" title={rotatedPrompt.slice(0, 300)}>
          <span className="block line-clamp-2">{rotatedPrompt}</span>
          <span className="font-mono text-[10px] text-primary">↳ prompt của job xoay từ {rotatedFrom}</span>
        </div>
      ) : (
        <Input className="h-9 text-[13px]" value={s.prompt} placeholder={`prompt cho ${n}…`} onChange={(e) => onChange({ prompt: e.target.value })} />
      )}
      <div className="flex gap-1.5">
        <SelectNative className="h-8 flex-1 text-xs" value={s.model} onChange={(e) => onChange({ model: e.target.value })}>{MODELS.map((m) => <option key={m}>{m}</option>)}</SelectNative>
        <SelectNative className="h-8 w-[70px] text-xs" value={s.ratio} onChange={(e) => onChange({ ratio: e.target.value })}>{RATIOS.map((m) => <option key={m}>{m}</option>)}</SelectNative>
        <SelectNative className="h-8 w-[74px] text-xs" value={s.dur} onChange={(e) => onChange({ dur: e.target.value })}>{DURS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</SelectNative>
      </div>
      <Progress s={s} a={a} onRun={onRun} rotatedFrom={rotatedFrom} />
      <div className="flex min-h-7 items-center gap-1">
        <CookieTag a={a} />
        <span className="ml-auto" />
        {s.phase !== "running" && <Button variant="ghost" size="icon" className={icon + " text-primary"} title="Chạy nick này" onClick={onRun}><Play className="h-3.5 w-3.5" /></Button>}
        <Button variant="ghost" size="icon" className={icon} title="Đăng nhập lại" onClick={onRelogin}><RotateCw className="h-3.5 w-3.5" /></Button>
        <Button variant="ghost" size="icon" className={icon} title="Proxy riêng" onClick={onProxy}><Settings className="h-3.5 w-3.5" /></Button>
        <Button variant="ghost" size="icon" className={icon} title="Xoá nick" onClick={onDelete}><Trash2 className="h-3.5 w-3.5" /></Button>
      </div>
    </div>
  );
}

function Footer({ s, a, onRun, rotatedFrom }) {
  if (s.phase === "error") {
    const f = fmtError(s.errorRaw || "");
    return (
      <div className="flex min-w-0 items-center gap-2">
        <div className="min-w-0 leading-tight" title={(s.errorRaw || "").slice(0, 300)}>
          <div className={"truncate text-[12px] " + (f.kind === "account" ? "text-warn" : "text-error")}>{f.short}</div>
          {f.hint && <div className="truncate text-[10.5px] text-muted-foreground">{f.hint}</div>}
        </div>
        <Button variant="outline" size="sm" className="h-7 flex-none border-error/40 text-[11px] text-error hover:text-error" onClick={onRun}>Chạy lại</Button>
      </div>
    );
  }
  if (a.cooling && a.cooldown_until > 0) return <span className={"font-mono text-[11px] " + (a.quarantine ? "text-warn" : "text-info")} title={a.quarantine || undefined}>{a.quarantine ? "cách ly (lỗi trên nhiều IP)" : "nghỉ"} còn {Math.max(1, Math.ceil((a.cooldown_until - Date.now() / 1000) / 60))} phút</span>;
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
        {/* Video xong nhưng có điều cần biết (vd Dola trả clip ngắn hơn số giây đã đặt mà vẫn trừ đủ lượt). */}
        {s.note
          ? <div className="truncate text-[10.5px] text-warn" title={s.note}>{s.note}</div>
          : <div className="truncate font-mono text-[10.5px] text-muted-foreground" title={fnameFromUrl(s.videoUrl)}>{fnameFromUrl(s.videoUrl)}</div>}
      </div>
      <Button variant="ghost" size="icon" className="h-7 w-7" title="Xem" onClick={() => onPlay(s.videoUrl)}><Play className="h-3.5 w-3.5" /></Button>
      <Button variant="ghost" size="icon" className="h-7 w-7" title="Mở thư mục" onClick={onOpen}><FolderOpen className="h-3.5 w-3.5" /></Button>
      <Button variant="ghost" size="icon" className="h-7 w-7" title="Copy đường dẫn" onClick={() => onCopy(s.videoUrl)}><Copy className="h-3.5 w-3.5" /></Button>
      <Button variant="ghost" size="icon" className="h-7 w-7" title="Xoá logo Dola" onClick={() => onRemoveWm(s.videoUrl)}><Eraser className="h-3.5 w-3.5" /></Button>
      <Button variant="ghost" size="icon" className="h-7 w-7 text-primary" title="Tạo video mới trên nick này (nhập prompt khác)" onClick={onNew}><Repeat className="h-3.5 w-3.5" /></Button>
    </div>
  );
}
