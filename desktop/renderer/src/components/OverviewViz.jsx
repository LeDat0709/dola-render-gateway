// Các khối trực quan của tab Tổng quan: chẩn đoán nhanh, dòng chảy job theo giai đoạn, bản đồ nick, biểu đồ theo giờ.
// Chỉ vẽ từ dữ liệu sẵn có (/api/admin/tasks + /api/admin/accounts) — không gọi thêm API.
import { Fragment } from "react";
import { AlertTriangle, CheckCircle2, ChevronRight, Info, Workflow, Grid3x3, BarChart3 } from "lucide-react";
import { accState, cookieInfo, fmtError, fmtSec } from "@/lib/api";

const nowSec = () => Date.now() / 1000;

// Server cũ (chưa có field stage / opened_at) → suy ra như trước: chưa gửi = "đang mở nick".
export const stageOf = (t) => t.stage || (t.conversation_id ? "rendering" : t.submitted_at ? "submitting" : "opening");

// Ngưỡng KẸT từng giai đoạn (giây) — khớp trần phía server (đồng hồ chống treo 300s, gửi 180s, dựng 900/2400s).
const STAGES = [
  { key: "waiting", label: "Chờ slot Chrome", since: (t) => t.started_at, stuckAt: () => 900, dot: "bg-outline", text: "text-foreground" },
  { key: "opening", label: "Đang mở nick", since: (t) => t.opened_at || t.started_at, stuckAt: () => 300, dot: "bg-info", text: "text-info" },
  { key: "submitting", label: "Gửi prompt", since: (t) => t.submitted_at, stuckAt: () => 240, dot: "bg-warn", text: "text-warn" },
  { key: "rendering", label: "Dola dựng video", since: (t) => t.submitted_at || t.started_at, stuckAt: (t) => (t.duration >= 30 ? 2400 : 900), dot: "bg-primary", text: "text-primary" },
];
const STUCK_HINT = {
  waiting: "Slot Chrome bị job khác giữ — xem giai đoạn 'Đang mở nick' có job kẹt không.",
  opening: "Chrome/proxy/trang treo. Tool tự cắt sau 5 phút; máy yếu thì giảm 'Nick gửi cùng lúc'.",
  submitting: "Gửi lệnh không phản hồi — thường do proxy chập chờn.",
  rendering: "Dola dựng lâu bất thường — mở dola.com kiểm tra, ĐỪNG chạy lại (dễ trừ lượt 2 lần).",
};
export const shortNick = (n) => (n && n.length > 10 ? "…" + n.slice(-6) : n || "—");

export function pipelineGroups(running) {
  const now = nowSec();
  return STAGES.map((s) => {
    const items = running.filter((t) => stageOf(t) === s.key)
      .map((t) => ({ t, age: Math.max(0, now - (s.since(t) || t.created_at || now)) }))
      .sort((a, b) => b.age - a.age);
    return { ...s, items, stuck: items.filter((x) => x.age > s.stuckAt(x.t)) };
  });
}

// Lỗi có thể ĐÃ trừ lượt → không bấm chạy lại ngay.
export const MAYBE_CHARGED = /Đã gửi, chưa xác nhận|Quá giờ chưa ra video/;

export function Insights({ today, failed, groups, accs, queued, resetAt, onGo }) {
  const out = [];
  for (const g of groups) {
    if (g.stuck.length) out.push({ tone: "error", title: `${g.stuck.length} job kẹt ở "${g.label}"`, hint: STUCK_HINT[g.key],
      detail: g.stuck.slice(0, 3).map((x) => `${shortNick(x.t.account)} ${fmtSec(x.age)}`).join(" · ") });
  }
  if (today.length >= 5 && failed.length / today.length >= 0.4) {
    const m = new Map();
    for (const t of failed) { const e = fmtError(t.error); const k = e.icon + " " + e.short; m.set(k, { n: (m.get(k)?.n || 0) + 1, hint: e.hint }); }
    const [k, v] = [...m.entries()].sort((a, b) => b[1].n - a[1].n)[0];
    out.push({ tone: "error", title: `${Math.round((failed.length / today.length) * 100)}% job lỗi hôm nay`, detail: `nhiều nhất: ${k} (${v.n} lần)`, hint: v.hint });
  }
  const charged = failed.filter((t) => MAYBE_CHARGED.test(fmtError(t.error).short)).length;
  if (charged) out.push({ tone: "warn", title: `${charged} job có thể đã trừ lượt mà chưa có video`, hint: "Mở dola.com kiểm tra trước khi bấm chạy lại — tránh trừ lượt 2 lần." });
  const dead = accs.filter((a) => cookieInfo(a).st === "dead").length;
  if (dead) out.push({ tone: "warn", title: `${dead} nick cookie chết`, hint: "Đăng nhập lại ở Kho tài khoản — tool đã tự bỏ qua các nick này.", go: "acct" });
  const usable = accs.filter((a) => ["ready", "busy"].includes(accState(a)));
  const credit = usable.reduce((s, a) => s + Math.max(0, a.remaining || 0), 0);
  if (accs.length && !usable.length) out.push({ tone: "error", title: "Không còn nick nào chạy được", hint: "Đăng nhập lại nick chết, bỏ nghỉ, hoặc chờ reset 0h giờ Nhật.", go: "acct" });
  else if (queued.length && credit < queued.length) out.push({ tone: "warn", title: `Hàng chờ ${queued.length} job nhưng chỉ còn ~${credit} điểm`, hint: "Một phần job sẽ lỗi 'hết lượt' — thêm nick hoặc để mai.", go: "acct" });
  const quota = accs.filter((a) => accState(a) === "quota").length;
  if (quota) out.push({ tone: "info", title: `${quota} nick hết lượt/điểm`, hint: `Tự mở lại lúc ${resetAt || "0h giờ Nhật (22h VN)"}.` });
  if (!out.some((x) => x.tone !== "info")) out.unshift({ tone: "ok", title: "Hệ thống ổn", hint: `Không job kẹt · còn ~${credit} điểm trên ${usable.length} nick chạy được.` });

  const STYLE = { error: ["bg-error/10 ring-error/35", "text-error", AlertTriangle], warn: ["bg-warn/10 ring-warn/35", "text-warn", AlertTriangle],
    info: ["bg-info/10 ring-info/25", "text-info", Info], ok: ["bg-tertiary/10 ring-tertiary/30", "text-tertiary", CheckCircle2] };
  return (
    <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
      {out.slice(0, 4).map((x, i) => { const [box, tone, Icon] = STYLE[x.tone]; return (
        <button type="button" key={i} onClick={() => x.go && onGo?.(x.go)} className={"flex gap-2.5 rounded-xl p-3 text-left ring-1 " + box + (x.go ? " hover:brightness-110" : " cursor-default")}>
          <Icon className={"mt-0.5 h-4 w-4 flex-none " + tone} />
          <div className="min-w-0 leading-snug">
            <div className={"text-[13px] font-semibold " + tone}>{x.title}</div>
            {x.detail && <div className="truncate font-mono text-[11px] text-foreground/80" title={x.detail}>{x.detail}</div>}
            <div className="text-[11.5px] text-muted-foreground">{x.hint}</div>
          </div>
        </button>); })}
    </div>
  );
}

export function StagePipeline({ groups, done, failed, conc, stats }) {
  const busy = groups.reduce((s, g) => s + (g.key === "waiting" ? 0 : g.items.length), 0);
  return (
    <div className="flex flex-col gap-3 rounded-xl bg-surface-low p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-2 text-[15px] font-medium"><Workflow className="h-4 w-4 text-info" />Dòng chảy job</span>
        <span className="font-mono text-[11px] text-muted-foreground">đỏ = kẹt quá ngưỡng</span>
        <span className="ml-auto rounded bg-surface px-1.5 py-0.5 font-mono text-[11px] text-info" title="Slot Chrome đang dùng / 'Nick gửi cùng lúc'">{busy}/{conc} slot</span>
      </div>
      <div className="flex items-stretch gap-1 overflow-x-auto pb-1">
        {groups.map((g) => (
          <Fragment key={g.key}>
            <div className={"flex min-w-[112px] flex-1 flex-col rounded-lg p-2.5 " + (g.stuck.length ? "bg-error/10 ring-1 ring-error/45" : "bg-surface")}>
              <span className="flex items-center gap-1.5 whitespace-nowrap text-[11px] text-muted-foreground"><span className={"h-2 w-2 rounded-full " + g.dot} />{g.label}</span>
              <span className={"mt-1 text-2xl font-semibold tabular-nums " + (g.stuck.length ? "text-error" : g.items.length ? g.text : "text-muted-foreground/40")}>{g.items.length}</span>
              <span className={"whitespace-nowrap font-mono text-[10.5px] " + (g.stuck.length ? "text-error" : "text-muted-foreground")}>
                {g.stuck.length ? `${g.stuck.length} kẹt · ${fmtSec(g.items[0].age)}` : g.items.length ? `lâu nhất ${fmtSec(g.items[0].age)}` : "—"}
              </span>
              {/* mỗi job một vạch: dài theo thời gian đã ở giai đoạn này so với ngưỡng kẹt */}
              <div className="mt-2 flex flex-col gap-0.5">
                {g.items.slice(0, 5).map((x) => (
                  <div key={x.t.id} className="h-1 overflow-hidden rounded-full bg-surface-high" title={`${x.t.account || "chưa có nick"} · ${fmtSec(x.age)}`}>
                    <div className={"h-full rounded-full " + (x.age > g.stuckAt(x.t) ? "bg-error" : g.dot)} style={{ width: Math.min(100, (x.age / g.stuckAt(x.t)) * 100) + "%" }} />
                  </div>
                ))}
              </div>
            </div>
            <ChevronRight className="h-4 w-4 flex-none self-center text-muted-foreground/40" />
          </Fragment>
        ))}
        <div className="flex min-w-[112px] flex-1 flex-col rounded-lg bg-surface p-2.5">
          <span className="whitespace-nowrap text-[11px] text-muted-foreground">Kết quả hôm nay</span>
          <span className="mt-1 flex items-baseline gap-2 tabular-nums"><span className="text-2xl font-semibold text-tertiary">{done}</span><span className="text-sm font-semibold text-error">✗ {failed}</span></span>
          <div className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-surface-high">
            <div className="h-full bg-tertiary" style={{ width: (done / Math.max(1, done + failed)) * 100 + "%" }} />
            <div className="h-full bg-error" style={{ width: (failed / Math.max(1, done + failed)) * 100 + "%" }} />
          </div>
          <span className="mt-1 whitespace-nowrap font-mono text-[10.5px] text-muted-foreground">{done + failed ? `${Math.round((done / (done + failed)) * 100)}% thành công` : "chưa có job"}</span>
        </div>
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-[11px] text-muted-foreground">
        {stats.map(([label, value, tone]) => <span key={label}>{label} <b className={"font-semibold " + (tone || "text-foreground")}>{value}</b></span>)}
      </div>
    </div>
  );
}

// Màu theo trạng thái nick — dùng chung cho ô nick và chú thích.
const NICK_TONE = {
  busy: ["bg-primary", "ring-primary/60", "Đang chạy"], ready: ["bg-tertiary", "ring-tertiary/35", "Sẵn sàng"],
  credit: ["bg-error", "ring-error/40", "Hết điểm"], day: ["bg-warn", "ring-warn/40", "Hết lượt ngày"],
  cooling: ["bg-info", "ring-info/40", "Đang nghỉ"], dead: ["bg-outline", "ring-outline/40", "Cookie chết"], off: ["bg-outline", "ring-outline/25", "Tạm ngưng"],
};
const nickKey = (a) => { const st = accState(a); return st !== "quota" ? st : (a.rate_limited || (a.limit != null && a.used_today >= a.limit) ? "day" : "credit"); };
const ORDER = { busy: 0, ready: 1, cooling: 2, day: 3, credit: 4, off: 5, dead: 6 };
const STAGE_SHORT = { waiting: "chờ slot", opening: "mở nick", submitting: "gửi", rendering: "dựng" };

export function NickGrid({ accs, today, running, onGo }) {
  const stat = new Map();
  for (const t of today) {
    if (!t.account || (t.status !== "completed" && t.status !== "failed")) continue;
    const s = stat.get(t.account) || { ok: 0, fail: 0 };
    if (t.status === "completed") s.ok++; else s.fail++;
    stat.set(t.account, s);
  }
  const job = new Map(running.filter((t) => t.account).map((t) => [t.account, t]));
  const rows = accs.map((a) => ({ a, k: nickKey(a) }))
    .sort((x, y) => ORDER[x.k] - ORDER[y.k] || (y.a.remaining || 0) - (x.a.remaining || 0));
  const now = nowSec();
  return (
    <div className="rounded-xl bg-surface-low p-3">
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5 text-[13px] font-medium text-foreground"><Grid3x3 className="h-3.5 w-3.5 text-primary" />Bản đồ nick</span>
        {Object.entries(NICK_TONE).map(([k, [dot, , label]]) => <span key={k} className="flex items-center gap-1"><span className={"h-2 w-2 rounded-sm " + dot} />{label}</span>)}
        <span className="ml-auto font-mono">vạch = điểm còn · ✓/✗ hôm nay</span>
      </div>
      <div className="grid gap-1.5" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))" }}>
        {rows.map(({ a, k }) => {
          const [dot, ring, label] = NICK_TONE[k];
          const rem = Math.max(0, a.remaining || 0), cap = Math.min(8, Math.max(a.limit || 4, rem));
          const s = stat.get(a.name) || { ok: 0, fail: 0 };
          const t = job.get(a.name);
          return (
            <button type="button" key={a.name} onClick={() => onGo?.("acct")} title={`${a.name} · ${label} · còn ${rem} điểm · hôm nay ✓${s.ok} ✗${s.fail} · ${cookieInfo(a).text}`}
              className={"flex flex-col gap-1 rounded-md bg-surface p-2 text-left ring-1 transition hover:bg-surface-high " + ring}>
              <span className="flex items-center gap-1.5"><span className={"h-2 w-2 flex-none rounded-full " + dot + (k === "busy" ? " animate-pulse" : "")} /><span className="truncate font-mono text-[11px]">{shortNick(a.name)}</span>
                <span className="ml-auto font-mono text-[10px] tabular-nums text-muted-foreground">{rem > 8 ? rem : ""}</span></span>
              <span className="flex gap-0.5">{Array.from({ length: cap }, (_, i) => <span key={i} className={"h-1.5 flex-1 rounded-sm " + (i < rem ? "bg-tertiary" : "bg-surface-high")} />)}</span>
              <span className="truncate font-mono text-[10px] text-muted-foreground">
                {t ? <span className="text-primary">{STAGE_SHORT[stageOf(t)] || "chạy"} {fmtSec(now - (t.started_at || t.created_at))}</span>
                  : <><span className="text-tertiary">✓{s.ok}</span> <span className={s.fail ? "text-error" : ""}>✗{s.fail}</span></>}
              </span>
            </button>
          );
        })}
        {!accs.length && <div className="col-span-full py-4 text-center text-xs text-muted-foreground">Chưa có nick — thêm ở Kho tài khoản.</div>}
      </div>
    </div>
  );
}

// Job xong/lỗi theo GIỜ hôm nay: thấy lúc nào bắt đầu lỗi dồn dập (proxy chết, Dola chặn…).
export function HourlyBars({ today }) {
  const hours = Array.from({ length: 24 }, () => ({ ok: 0, fail: 0 }));
  for (const t of today) {
    if (t.status !== "completed" && t.status !== "failed") continue;
    const h = new Date((t.finished_at || t.created_at) * 1000).getHours();
    if (t.status === "completed") hours[h].ok++; else hours[h].fail++;
  }
  const cur = new Date().getHours();
  const firstData = hours.findIndex((x) => x.ok + x.fail);
  const first = Math.min(Math.max(0, cur - 11), firstData === -1 ? cur : firstData);
  const shown = hours.slice(first, cur + 1).map((x, i) => ({ ...x, h: first + i }));
  const max = Math.max(1, ...shown.map((x) => x.ok + x.fail));
  const H = 72;
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-medium"><BarChart3 className="h-3.5 w-3.5 text-tertiary" />Theo giờ hôm nay
        <span className="ml-auto font-mono text-[10px] font-normal text-muted-foreground">xanh xong · đỏ lỗi</span></div>
      <div className="flex items-end gap-[3px]" style={{ height: H + 14 }}>
        {shown.map((x) => (
          <div key={x.h} className="flex min-w-0 flex-1 flex-col items-center" title={`${x.h}h: ✓${x.ok} ✗${x.fail}`}>
            <div className={"flex w-full max-w-[22px] flex-col justify-end overflow-hidden rounded-sm bg-surface/40 " + (x.h === cur ? "ring-1 ring-primary/50" : "")} style={{ height: H }}>
              <div className="w-full bg-error/75" style={{ height: (x.fail / max) * H }} />
              <div className="w-full bg-tertiary" style={{ height: (x.ok / max) * H }} />
            </div>
            <span className={"mt-0.5 font-mono text-[9px] " + (x.h === cur ? "text-primary" : "text-muted-foreground")}>{(cur - x.h) % 2 === 0 ? x.h : ""}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
