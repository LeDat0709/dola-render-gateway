import { useCallback, useEffect, useMemo, useState } from "react";
import { Network, RefreshCw, Activity, Users, Unlink, ChevronDown, ChevronUp, Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, cfg, adminAccounts, adminConfig, accState, accChip, maskProxy, proxyHost } from "@/lib/api";
import ProxyAssignDialog from "@/components/ProxyAssignDialog";

const PER_IP = 5;

// Gom nick theo proxy đang gán (file accounts/<nick>/proxy.txt). Không có bảng proxy riêng trên server:
// một proxy "tồn tại" khi có nick dùng nó. Trạng thái Dola của proxy suy từ nick: nick nào trên IP
// đó đang nghỉ risk-control thì cả IP coi như bị Dola soi.
export default function ProxyTab({ active = true }) {
  const [list, setList] = useState(null);
  const [gproxy, setGproxy] = useState("");
  const [tests, setTests] = useState({});
  const [openRow, setOpenRow] = useState("");
  const [dlg, setDlg] = useState(false);
  const [msg, setMsg] = useState("");
  const load = useCallback(async () => {
    const res = await adminAccounts();
    if (!res.ok) { setList(null); return; }
    const proxies = await Promise.all(res.accounts.map((a) => a.proxy !== undefined ? a.proxy
      : Promise.resolve(api.getProxy?.(a.name)).then((r) => r?.proxy || "").catch(() => "")));
    setList(res.accounts.map((a, i) => ({ ...a, proxy: proxies[i] || "" })));
    const c = await adminConfig();   // proxy chung ĐANG chạy trên server (đã che mật khẩu); server cũ → hỏi IPC
    if (c) setGproxy(c.proxy || "");
    else { try { setGproxy((await api.getGlobalProxy?.())?.proxy || ""); } catch { /* không có IPC (trình duyệt) */ } }
  }, []);
  useEffect(() => { if (!active) return; load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load, active]);

  const groups = useMemo(() => {
    const m = new Map();
    for (const a of list || []) { const k = a.proxy || ""; if (!m.has(k)) m.set(k, []); m.get(k).push(a); }
    return [...m.entries()].map(([proxy, nicks]) => ({
      proxy, nicks,
      cooling: nicks.filter((a) => a.cooling), busy: nicks.filter((a) => a.busy).length,
      ready: nicks.filter((a) => accState(a) === "ready").length,
      until: Math.max(0, ...nicks.map((a) => (a.cooling && a.cooldown_until) || 0)),
    })).sort((x, y) => (x.proxy === "" ? -1 : y.proxy === "" ? 1 : y.nicks.length - x.nicks.length));
  }, [list]);
  const priv = groups.filter((g) => g.proxy);
  const onGlobal = groups.find((g) => !g.proxy)?.nicks.length || 0;
  const flagged = groups.filter((g) => g.cooling.length).length;

  async function test(proxy) {
    const key = proxy || "__global";
    setTests((t) => ({ ...t, [key]: { busy: true } }));
    const t0 = performance.now();
    let r;
    try { r = await api.testProxy?.(proxy || gproxy); } catch (e) { r = { ok: false, error: String(e) }; }
    if (!r) r = { ok: false, error: "Chỉ kiểm tra được trong app (không có IPC)" };
    setTests((t) => ({ ...t, [key]: { ...r, ms: Math.round(performance.now() - t0) } }));
  }
  async function testAll() { for (const g of groups) await test(g.proxy); }
  async function detach(g) {
    if (!window.confirm(`Gỡ proxy riêng khỏi ${g.nicks.length} nick (${g.nicks.map((a) => a.name).join(", ")})?\nCác nick này sẽ dùng proxy chung.`)) return;
    let ok = 0;
    for (const a of g.nicks) { try { await api.setProxy?.(a.name, ""); ok++; } catch { /* báo ở cuối */ } }
    setMsg(`Đã gỡ proxy khỏi ${ok}/${g.nicks.length} nick.`); load();
  }

  const statusOf = (g) => {
    if (g.cooling.length) {
      const t = g.until ? new Date(g.until * 1000).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" }) : "";
      return <Badge variant="danger"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-error" />Dola soi IP · {g.cooling.length} nick nghỉ{t ? ` đến ${t}` : ""}</Badge>;
    }
    const r = tests[g.proxy || "__global"];
    if (!r) return <Badge variant="secondary">Chưa kiểm tra</Badge>;
    if (r.busy) return <Badge variant="info"><RefreshCw className="h-3 w-3 animate-spin" />Đang kiểm tra…</Badge>;
    if (r.ok) return <Badge variant="success">Vào được Dola · {r.ms}ms</Badge>;
    return <Badge variant="danger" title={r.error}>{String(r.error || "lỗi").slice(0, 60)}</Badge>;
  };
  const loadBadge = (n) => n > PER_IP ? <Badge variant="danger">Quá tải</Badge> : n === PER_IP ? <Badge variant="warn">Đầy</Badge> : n === 0 ? <Badge variant="secondary">Trống</Badge> : <Badge variant="success">Ổn</Badge>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 rounded-xl bg-surface-low p-4">
        <div>
          <div className="flex items-center gap-2 text-xl font-semibold tracking-tight">Quản lý Proxy<span className="rounded bg-tertiary/10 px-1.5 py-0.5 font-mono text-[10px] text-tertiary">tối đa {PER_IP} nick / IP</span></div>
          <p className="text-xs text-muted-foreground">Dola đánh giá rủi ro theo cặp IP + nick. Nhiều nick chung một IP là lý do tool đối thủ bị khoá cả loạt. Mỗi nick một proxy cố định, mỗi IP không quá {PER_IP} nick.</p>
        </div>
        <span className="ml-auto" />
        <Button size="sm" onClick={() => setDlg(true)} disabled={!list}><Plus className="h-4 w-4" />Thêm proxy & chia nick</Button>
        <Button variant="outline" size="sm" onClick={testAll} disabled={!groups.length}><Activity className="h-3.5 w-3.5 text-tertiary" />Kiểm tra toàn bộ</Button>
        <Button variant="outline" size="sm" onClick={load}><RefreshCw className="h-3.5 w-3.5" />Làm mới</Button>
      </div>

      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {[["Proxy riêng đang dùng", priv.length, "text-foreground", "IP có nick gán"],
          ["Nick dùng proxy chung", onGlobal, onGlobal > PER_IP ? "text-error" : "text-foreground", onGlobal > PER_IP ? `quá ${PER_IP} nick một IP — nên chia proxy` : "chung IP với gateway"],
          ["IP đang bị Dola soi", flagged, flagged ? "text-error" : "text-tertiary", "có nick nghỉ risk-control"],
          ["Tổng nick", (list || []).length, "text-foreground", `${groups.reduce((s, g) => s + g.busy, 0)} đang render`]].map(([l, v, tone, sub]) => (
          <div key={l} className="rounded-xl bg-surface-low p-3"><div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{l}</div>
            <div className={"mt-1 text-2xl font-semibold tabular-nums " + tone}>{v}</div><div className="font-mono text-[11px] text-muted-foreground">{sub}</div></div>
        ))}
      </div>

      {cfg.remote && <div className="rounded-lg bg-info/10 px-3 py-2 text-xs text-info">Đang nối server từ xa: nút "Kiểm tra" chạy từ máy này, không phải từ server. Proxy của nick nằm trên server.</div>}

      <div className="overflow-x-auto rounded-xl bg-surface-low">
        <table className="w-full text-left text-[13px]">
          <thead><tr className="bg-surface-lowest font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            <th className="px-3 py-2.5">Proxy</th><th className="px-3 py-2.5">Giao thức</th><th className="px-3 py-2.5">Tải nick</th><th className="px-3 py-2.5">Nick</th><th className="px-3 py-2.5">Trạng thái Dola</th><th className="px-3 py-2.5 text-right">Tác vụ</th>
          </tr></thead>
          <tbody>
            {list === null && <tr><td colSpan={6} className="px-3 py-6 text-center text-sm text-muted-foreground">Chưa nối được server hoặc sai admin key.</td></tr>}
            {groups.map((g) => {
              const key = g.proxy || "__global"; const open = openRow === key;
              const scheme = g.proxy ? (g.proxy.match(/^(\w+):\/\//)?.[1] || "http") : "";
              return [
                <tr key={key} className={"border-t border-surface hover:bg-surface/60 " + (g.cooling.length ? "bg-error-container/10" : "")}>
                  <td className="px-3 py-2.5 font-mono text-[12px]">
                    {g.proxy ? <span title={maskProxy(g.proxy)}>{maskProxy(g.proxy)}</span>
                      : <span><span className="text-info">Proxy chung của gateway</span><div className="text-[11px] text-muted-foreground">{gproxy ? maskProxy(gproxy) : "nối thẳng (không proxy) — đặt ở Cài đặt"}</div></span>}
                  </td>
                  <td className="px-3 py-2.5"><span className="rounded bg-surface-high px-1.5 py-0.5 font-mono text-[11px] uppercase">{scheme || "—"}</span></td>
                  <td className="px-3 py-2.5"><span className="mr-2 font-mono">{g.nicks.length} / {PER_IP}</span>{loadBadge(g.nicks.length)}</td>
                  <td className="px-3 py-2.5 font-mono text-[11px] text-muted-foreground"><span className="text-tertiary">{g.ready} sẵn sàng</span> · <span className="text-primary">{g.busy} chạy</span> · {g.nicks.length - g.ready - g.busy} khác</td>
                  <td className="px-3 py-2.5">{statusOf(g)}</td>
                  <td className="px-3 py-2.5 text-right">
                    <Button variant="ghost" size="sm" className="h-7" onClick={() => test(g.proxy)} disabled={!g.proxy && !gproxy}><Activity className="h-3.5 w-3.5" />Kiểm tra</Button>
                    <Button variant="ghost" size="sm" className="h-7" onClick={() => setOpenRow(open ? "" : key)}><Users className="h-3.5 w-3.5" />{open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}</Button>
                    {g.proxy && <Button variant="ghost" size="sm" className="h-7 text-error hover:text-error" onClick={() => detach(g)}><Unlink className="h-3.5 w-3.5" />Gỡ</Button>}
                  </td>
                </tr>,
                open && <tr key={key + "-nicks"} className="bg-surface-lowest"><td colSpan={6} className="px-3 py-2">
                  <div className="flex flex-wrap gap-1.5">{g.nicks.map((a) => { const c = accChip(a); return <Badge key={a.name} variant={c.variant} title={c.text}>{a.name}</Badge>; })}</div>
                  {g.proxy && <div className="mt-1.5 font-mono text-[11px] text-muted-foreground">{proxyHost(g.proxy)}</div>}
                </td></tr>,
              ];
            })}
          </tbody>
        </table>
      </div>
      {msg && <div className="text-xs text-muted-foreground">{msg}</div>}
      <div className="rounded-xl bg-surface-low p-4 text-xs text-muted-foreground">
        <div className="mb-1 flex items-center gap-2 text-sm font-medium text-foreground"><Network className="h-4 w-4 text-primary" />Đẩy nick kèm proxy từ máy khác</div>
        Có danh sách proxy trong file <code className="font-mono text-primary">proxies.txt</code> thì chạy trên máy đang giữ nick:
        <code className="mt-1 block rounded bg-surface-lowest px-2 py-1.5 font-mono text-[11px] text-tertiary">.venv/bin/python deploy/push_nicks.py {cfg.base} ADMIN_KEY --proxies proxies.txt --per-ip {PER_IP}</code>
      </div>
      <ProxyAssignDialog open={dlg} onOpenChange={setDlg} accounts={list || []} onDone={load} />
    </div>
  );
}
