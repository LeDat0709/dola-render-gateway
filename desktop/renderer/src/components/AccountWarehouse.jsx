import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RotateCw, Settings, Trash2, FolderOpen, Stethoscope, RefreshCw, Eraser, Search, Cookie, Network, Facebook, Copy, Download, Upload, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { SelectNative } from "@/components/ui/select-native";
import {
  api, cfg, adminAccounts, patchAccount, verifyAccount, openProfile,
  deleteAccount, clearCookies, timeAgo, accState, accChip, maskProxy, proxyHost,
  exportAccounts, importAccounts,
} from "@/lib/api";
import { normalizeBundle } from "@/lib/bundle";
import ProxyAssignDialog from "@/components/ProxyAssignDialog";

const POLL_MS = 4000;
const ORDER = { ready: 0, busy: 1, cooling: 2, quota: 3, off: 4, dead: 5 };
const FILTERS = [["", "Tất cả trạng thái"], ["ready", "Sẵn sàng"], ["busy", "Đang chạy"], ["quota", "Hết credit / hết lượt"], ["cooling", "Đang nghỉ"], ["off", "Tạm ngưng"], ["dead", "Chưa đăng nhập"]];
const SORTS = [["status", "Xếp: Trạng thái"], ["name", "Xếp: Tên"], ["remaining", "Xếp: Credit còn"], ["last", "Xếp: Dùng gần nhất"]];
const msgOf = (e) => String((e && e.message) || e).slice(0, 140);

function Switch({ on, onClick, title, disabled }) {
  return (
    <button type="button" title={title} onClick={onClick} disabled={disabled}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-40 ${on ? "bg-tertiary" : "bg-surface-highest"}`}>
      <span className={`absolute top-0.5 block h-4 w-4 rounded-full bg-surface-lowest transition-transform ${on ? "translate-x-4" : "translate-x-0.5"}`} />
    </button>
  );
}

function Metric({ label, value, tone = "text-foreground", sub }) {
  return (
    <div className="rounded-lg bg-surface-low p-3">
      <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={"mt-0.5 text-2xl font-semibold tabular-nums " + tone}>{value}</div>
      {sub && <div className="font-mono text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  );
}

export default function AccountWarehouse({ onRefresh, onAdd, active = true }) {
  const [list, setList] = useState(null);
  const [loadErr, setLoadErr] = useState(null); // "auth" | "net" | "http"
  const [sel, setSel] = useState({});
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState("status");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [dlg, setDlg] = useState(false);
  const fileRef = useRef(null);
  const [imp, setImp] = useState(null);   // tiến độ nhập kho: {i,total,name,ok,unverified,bad,done,stopped}
  const stopImp = useRef(false);
  // Đếm chứ không dùng cờ: one() lồng trong each() từng tắt cờ mà each() đang giữ,
  // mở cửa cho người dùng bấm tiếp giữa chừng một vòng xoá hàng loạt.
  const busyRef = useRef(0);
  const mark = (d) => { busyRef.current = Math.max(0, busyRef.current + d); setBusy(busyRef.current > 0); };

  const load = useCallback(async () => {
    const res = await adminAccounts();
    if (!res.ok) { setLoadErr(res.kind); setList(null); return; }
    setLoadErr(null);
    const accs = res.accounts;
    // Server mới trả sẵn `proxy` trong danh sách; server cũ thì hỏi IPC từng nick.
    const proxies = await Promise.all(accs.map((a) => a.proxy !== undefined ? a.proxy
      : Promise.resolve(api.getProxy?.(a.name)).then((r) => r?.proxy || "").catch(() => "")));
    setList(accs.map((a, i) => ({ ...a, proxy: proxies[i] || "" })));
    // Cắt bỏ mọi khoá không còn trong kho (xoá 1 nick, xoá hàng loạt, hoặc xoá bằng
    // đường khác) -> sel không thể giữ tên mà pool không còn nữa.
    setSel((prev) => Object.fromEntries(accs.filter((a) => prev[a.name]).map((a) => [a.name, true])));
  }, []);

  // Mọi tab đều giữ mount (forceMount) để không mất state → chỉ tab đang mở mới poll, tab ẩn im.
  useEffect(() => {
    if (!active) return;
    load();
    const t = setInterval(() => {
      // Chỉ ngừng khi đang gõ GHI CHÚ; con trỏ ở ô tìm kiếm/bộ lọc không được làm bảng đứng hình.
      if (document.activeElement?.hasAttribute?.("data-note")) return;
      load();
    }, POLL_MS);
    return () => clearInterval(t);
  }, [load, active]);

  const all = useMemo(() => list || [], [list]);
  const rows = useMemo(() => {
    const kw = q.trim().toLowerCase();
    const out = all
      .map((a) => ({ ...a, st: accState(a) }))
      .filter((a) => !filter || a.st === filter)
      .filter((a) => !kw || [a.name, a.email, a.note, a.proxy].some((v) => String(v || "").toLowerCase().includes(kw)));
    return [...out].sort((x, y) =>
      sort === "remaining" ? (y.remaining ?? 0) - (x.remaining ?? 0)
        : sort === "last" ? (y.last_used_at || 0) - (x.last_used_at || 0)
          : sort === "name" ? String(x.name).localeCompare(String(y.name))
            : ORDER[x.st] - ORDER[y.st] || String(x.name).localeCompare(String(y.name)));
  }, [all, q, filter, sort]);

  // Suy từ HÀNG ĐANG HIỆN (rows): nick bị bộ lọc giấu đi không nằm trong tầm bắn của nút xoá hàng loạt.
  const selNames = useMemo(() => rows.filter((a) => sel[a.name]).map((a) => a.name), [rows, sel]);
  const allSel = rows.length > 0 && rows.every((a) => sel[a.name]);
  const counts = useMemo(() => {
    const m = { ready: 0, busy: 0, credit: 0, day: 0, dead: 0, cooling: 0, off: 0 };
    for (const a of all) { const c = accChip(a); if (c.st === "quota") m[c.text === "Hết credit" ? "credit" : "day"]++; else m[c.st]++; }
    return m;
  }, [all]);
  const credits = all.reduce((s, a) => s + (a.remaining || 0), 0);
  const noProxy = all.filter((a) => !a.proxy).length;

  const done = async () => { await load(); onRefresh?.(); };
  async function one(n, fn, label) {
    mark(1); setMsg(`${label} ${n}…`);
    try {
      const r = await fn(n);
      if (r && r.ok === false) throw new Error(r.error || "?");
      setMsg(r && r.alive === false ? `✗ ${n}: cookie đã chết — cần đăng nhập lại` : `✓ ${label} ${n}`);
    } catch (e) { setMsg(`Lỗi ${label} ${n}: ${msgOf(e)}`); }
    finally { mark(-1); await done(); }
  }
  async function each(names, fn, label, clearSelAfter = false) {
    if (!names.length) { setMsg("Chọn ít nhất 1 nick."); return; }
    mark(1);
    let ok = 0; const bad = [];
    for (const n of names) {
      setMsg(`${label}: ${ok + bad.length + 1}/${names.length} (${n})…`);
      try {
        const r = await fn(n);
        if (r && r.ok === false) throw new Error(r.error || "?");
        if (r && r.alive === false) { bad.push(`${n}: cookie chết`); continue; }
        ok++;
      } catch (e) { bad.push(`${n}: ${msgOf(e)}`); }
    }
    mark(-1);
    setMsg(`${label}: xong ${ok}/${names.length}.`
      + (bad.length ? ` Lỗi — ${bad.slice(0, 3).join(" · ")}${bad.length > 3 ? ` (+${bad.length - 3} nick nữa)` : ""}` : ""));
    if (clearSelAfter) setSel({});
    await done();
  }

  const toggleSched = (a) => one(a.name, (n) => patchAccount(n, { scheduling: !a.scheduling }), a.scheduling ? "tạm ngưng" : "cho chạy lại");
  const saveNote = async (n, v, el) => {
    try { await patchAccount(n, { note: v }); if (el) el.classList.remove("border-error"); setMsg(`✓ ghi chú ${n}`); await done(); }
    catch (e) { if (el) el.classList.add("border-error"); setMsg(`Lỗi lưu ghi chú ${n}: ${msgOf(e)} — chưa lưu, thử lại: "${v}"`); }
  };
  const relogin = (n) => one(n, (x) => api.loginElectron?.(x, "ja"), "đăng nhập lại");
  const setProxy = async (n) => {
    const cur = (await api.getProxy?.(n))?.proxy || "";
    const v = window.prompt(`Proxy riêng cho "${n}" (để trống = dùng proxy chung).\nhttp://host:port · socks5://host:port · user:pass@host:port · host:port:user:pass`, cur);
    if (v === null) return;
    one(n, (x) => api.setProxy?.(x, v), "đổi proxy");
  };
  // Mang nick sang máy khác: xuất một file JSON (cookie + proxy + ghi chú), máy kia bấm Nhập kho.
  const exportAll = async () => {
    mark(1); setMsg("Đang gói nick…");
    try {
      const b = await exportAccounts();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([JSON.stringify(b, null, 1)], { type: "application/json" }));
      a.download = `dola-nicks-${new Date().toISOString().slice(0, 10)}.json`;
      a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      setMsg(`✓ Đã xuất ${b.accounts.length} nick${b.skipped?.length ? ` (bỏ qua ${b.skipped.length} nick chưa có cookie)` : ""}. File chứa phiên đăng nhập — giữ như mật khẩu.`);
    } catch (e) { setMsg("Lỗi xuất kho: " + msgOf(e)); }
    finally { mark(-1); }
  };
  // Bảng tiến độ (imp) hiện ngay trên bảng nick khi nhập file lớn: 100+ nick × 5–30s mỗi nick, người dùng
  // phải thấy đang ở đâu, dừng được, và cuối cùng biết bao nhiêu vào / chưa rõ / lỗi.
  const importFile = async (file) => {
    if (!file) return;
    mark(1); stopImp.current = false;
    try {
      const b = normalizeBundle(JSON.parse(await file.text()));
      const src = b.source === "seedance"
        ? ` (file của tool khác: tên nick đổi về dạng hợp lệ${b.dupes ? `, bỏ ${b.dupes} bản ghi trùng tài khoản Dola` : ""})` : "";
      if (!window.confirm(`Nhập ${b.accounts.length} nick từ file${src} vào ${cfg.remote ? "server từ xa" : "máy này"}?\nNick trùng tên sẽ được nạp lại cookie mới.`)) return;
      setImp({ i: 0, total: b.accounts.length, name: "", ok: 0, unverified: 0, bad: 0, src: b.source });
      const r = await importAccounts(b, (text, p) => { setMsg(text); if (p) setImp((s) => ({ ...s, ...p })); }, () => stopImp.current);
      setImp((s) => ({ ...s, i: r.done, ok: r.ok, unverified: r.unverified, bad: r.bad.length, badList: r.bad, why: r.why, paused: r.paused, done: true, stopped: r.stopped }));
      setMsg(`${r.stopped ? "Đã dừng" : "Nhập xong"} ${r.ok}/${r.total} nick.`
        + (r.unverified ? ` ${r.unverified} nick chưa kiểm tra được phiên: ${r.why}` : "")
        + (r.bad.length ? ` Lỗi — ${r.bad.slice(0, 3).join(" · ")}${r.bad.length > 3 ? ` (+${r.bad.length - 3} nick nữa)` : ""}` : ""));
    } catch (e) { setMsg("Lỗi nhập kho: " + msgOf(e)); setImp(null); }
    finally { mark(-1); await done(); }
  };
  const del = (n) => { if (window.confirm(`XOÁ HẲN nick ${n}?\nToàn bộ profile và phiên đăng nhập sẽ mất (không hoàn tác).`)) one(n, deleteAccount, "xoá"); };
  const bulkDel = () => { if (window.confirm(`XOÁ HẲN ${selNames.length} nick?\n${selNames.join(", ")}\nKhông hoàn tác.`)) each(selNames, deleteAccount, "Xoá nick", true); };
  const bulkClear = () => { if (window.confirm(`Xoá cookie của ${selNames.length} nick?\n${selNames.join(", ")}\nCác nick này sẽ phải đăng nhập lại.`)) each(selNames, clearCookies, "Xoá cookie", true); };

  const emptyMsg = loadErr === "auth" ? "Sai admin key — kiểm tra DOLA_ADMIN_KEY (Cài đặt → Server từ xa, hoặc .env.local) rồi mở lại app."
    : loadErr === "http" ? "Gateway trả lỗi — xem nút Log ở thanh trên."
      : "Chưa nối được server — bấm “Bật server” ở thanh trên.";
  const pushCmd = `.venv/bin/python deploy/push_nicks.py ${cfg.base} ADMIN_KEY`;

  const th = "h-9 px-3 text-left font-mono text-[10px] font-semibold uppercase tracking-wider text-muted-foreground";
  const td = "px-3 py-2 align-middle";
  return (
    <div className="mb-5 space-y-3">
      <div className="grid grid-cols-3 gap-2 lg:grid-cols-6">
        <Metric label="Tổng số nick" value={all.length} sub={`${noProxy} chưa có proxy riêng`} />
        <Metric label="Sẵn sàng" value={counts.ready} tone="text-tertiary" sub={all.length ? `${Math.round((counts.ready / all.length) * 100)}%` : "—"} />
        <Metric label="Đang render" value={counts.busy} tone="text-primary" />
        <Metric label="Tổng credit còn" value={credits} tone="text-info" sub={`~${Math.floor(credits / 4)} video 10s`} />
        <Metric label="Hết credit / lượt" value={counts.credit + counts.day} tone="text-warn" sub={`${counts.credit} credit · ${counts.day} lượt ngày`} />
        <Metric label="Chưa đăng nhập" value={counts.dead} tone={counts.dead ? "text-error" : "text-foreground"} sub="cần cookie mới" />
      </div>

      <div className="space-y-3 rounded-xl bg-surface p-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold tracking-tight">Kho tài khoản</h2>
          <span className="rounded-full bg-primary/15 px-2 py-0.5 font-mono text-[11px] text-primary">{all.length} nick ({counts.ready} sẵn sàng)</span>
          <span className="ml-auto" />
          <Button variant="secondary" size="sm" onClick={() => onAdd?.("cookie")}><Cookie className="h-3.5 w-3.5 text-primary" />Nhập cookie</Button>
          <Button variant="secondary" size="sm" onClick={() => setDlg(true)} disabled={!all.length}><Network className="h-3.5 w-3.5 text-tertiary" />Chia proxy tự động</Button>
          <Button variant="secondary" size="sm" disabled={busy || !all.length} onClick={() => each(all.map((a) => a.name), verifyAccount, "Kiểm tra phiên")}><Stethoscope className="h-3.5 w-3.5 text-info" />Kiểm tra phiên</Button>
          <Button variant="secondary" size="sm" onClick={exportAll} disabled={busy || !all.length} title="Xuất cookie + proxy + ghi chú của mọi nick ra một file để nhập ở máy khác"><Download className="h-3.5 w-3.5" />Xuất kho</Button>
          <Button variant="secondary" size="sm" onClick={() => fileRef.current?.click()} disabled={busy} title="Nhập file đã xuất từ máy khác"><Upload className="h-3.5 w-3.5" />Nhập kho</Button>
          <input ref={fileRef} type="file" accept=".json,application/json" className="hidden" onChange={(e) => { importFile(e.target.files?.[0]); e.target.value = ""; }} />
          <Button size="sm" onClick={() => onAdd?.("fb")}><Facebook className="h-3.5 w-3.5" />Thêm bằng Facebook</Button>
          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={load} disabled={busy} title="Làm mới"><RefreshCw className={"h-3.5 w-3.5 " + (busy ? "animate-spin" : "")} /></Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[240px] flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-muted-foreground" />
            <Input className="h-8 bg-surface-lowest pl-8 font-mono text-xs" placeholder="Tìm theo tên nick, email, ghi chú, IP proxy…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <SelectNative className="h-8 w-auto text-xs" value={filter} onChange={(e) => setFilter(e.target.value)}>{FILTERS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</SelectNative>
          <SelectNative className="h-8 w-auto text-xs" value={sort} onChange={(e) => setSort(e.target.value)}>{SORTS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</SelectNative>
        </div>
      </div>

      {imp && (
        <div className="space-y-2 rounded-lg border border-primary/30 bg-surface px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Upload className="h-4 w-4 text-primary" />
            <span className="text-[13.5px] font-semibold">{imp.done ? (imp.stopped ? "Đã dừng nhập kho" : "Nhập kho xong") : "Đang nhập kho"} — <span className="font-mono">{imp.i} / {imp.total}</span> nick</span>
            {imp.src === "seedance" && <span className="font-mono text-[11px] text-muted-foreground">file của tool khác</span>}
            <span className="ml-auto" />
            {!imp.done && <Button variant="outline" size="sm" className="h-7 border-error/40 text-error hover:text-error" onClick={() => { stopImp.current = true; setMsg("Sẽ dừng sau nick đang nhập…"); }}><Square className="h-3.5 w-3.5" />Dừng</Button>}
            {imp.done && <Button variant="ghost" size="sm" className="h-7" onClick={() => setImp(null)}>Đóng</Button>}
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-high"><div className="h-full rounded-full bg-primary transition-all" style={{ width: (imp.total ? (imp.i / imp.total) * 100 : 0) + "%" }} /></div>
          <div className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-muted-foreground">
            {!imp.done && imp.name && <span>Đang: <span className="text-foreground">{imp.name}</span> — nạp cookie, kiểm tra phiên…</span>}
            <span className="ml-auto" />
            <Badge variant="success">{imp.ok} đã vào</Badge>
            {imp.paused > 0 && <Badge variant="secondary" title="File đánh dấu các nick này đang tắt bên tool cũ. Bấm chạy nick là tự mở lại, hoặc chọn rồi bấm 'Cho chạy lại'.">{imp.paused} tạm ngưng theo file</Badge>}
            {imp.unverified > 0 && <Badge variant="info" title={imp.why}>{imp.unverified} chưa kiểm tra được phiên</Badge>}
            {imp.bad > 0 && <Badge variant="danger" title={(imp.badList || []).join("\n")}>{imp.bad} lỗi</Badge>}
            {imp.done && imp.unverified > 0 && <Button variant="outline" size="sm" className="h-7" disabled={busy}
              onClick={() => each(all.filter((a) => a.login_ok == null).map((a) => a.name), verifyAccount, "Kiểm tra phiên")}>Kiểm tra phiên nick chưa rõ</Button>}
          </div>
        </div>
      )}

      {selNames.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg bg-surface-high/95 px-3 py-2 shadow-xl">
          <span className="flex items-center gap-1.5 font-mono text-xs"><span className="h-2 w-2 animate-ping rounded-full bg-primary" />Đã chọn {selNames.length} nick</span>
          <span className="text-outline-variant">|</span>
          <Button variant="outline" size="sm" className="h-7" disabled={busy} onClick={() => setDlg(true)}><Network className="h-3.5 w-3.5 text-tertiary" />Gán proxy</Button>
          <Button variant="outline" size="sm" className="h-7" disabled={busy} onClick={() => each(selNames, verifyAccount, "Kiểm tra phiên")}><Stethoscope className="h-3.5 w-3.5" />Kiểm tra</Button>
          <Button variant="outline" size="sm" className="h-7" disabled={busy} title="Nick tạm ngưng không được xếp chạy tự động; bấm chạy đích danh vẫn tự mở lại" onClick={() => each(selNames, (n) => patchAccount(n, { scheduling: true }), "Cho chạy lại")}>Cho chạy lại</Button>
          <Button variant="outline" size="sm" className="h-7" disabled={busy} title="Tạm ngưng: giữ nick trong kho nhưng không xếp chạy tự động" onClick={() => each(selNames, (n) => patchAccount(n, { scheduling: false }), "Tạm ngưng")}>Tạm ngưng</Button>
          <Button variant="outline" size="sm" className="h-7" disabled={busy} onClick={bulkClear}><Eraser className="h-3.5 w-3.5" />Xoá cookie</Button>
          <Button variant="outline" size="sm" className="h-7 text-error hover:text-error" disabled={busy} onClick={bulkDel}><Trash2 className="h-3.5 w-3.5" />Xoá nick</Button>
          <Button variant="ghost" size="sm" className="h-7" disabled={busy} onClick={() => setSel({})}>Bỏ chọn</Button>
        </div>
      )}

      <div className="overflow-x-auto rounded-xl bg-surface-low">
        <table className="w-full border-collapse text-[12.5px]">
          <thead>
            <tr className="bg-surface-lowest">
              <th className={th + " w-8 text-center"}><input type="checkbox" checked={allSel} disabled={busy} onChange={(e) => setSel(e.target.checked ? Object.fromEntries(rows.map((a) => [a.name, true])) : {})} /></th>
              <th className={th}>Tên nick & email</th>
              <th className={th + " w-[150px]"}>Trạng thái</th>
              <th className={th + " w-[70px] text-right"}>Credit</th>
              <th className={th + " w-[90px] text-center"}>Lượt hôm nay</th>
              <th className={th + " w-[170px]"}>Proxy</th>
              <th className={th + " w-[76px]"}>Lần cuối</th>
              <th className={th + " w-[160px]"}>Ghi chú</th>
              <th className={th + " w-[52px]"}>Lịch</th>
              <th className={th + " w-[170px] text-right"}>Hành động</th>
            </tr>
          </thead>
          <tbody>
            {list === null && <tr><td colSpan={10} className="px-3 py-8 text-center text-sm text-muted-foreground">{emptyMsg}</td></tr>}
            {list !== null && !rows.length && (
              <tr><td colSpan={10} className="px-3 py-8 text-center text-sm text-muted-foreground">
                {all.length ? "Không có nick khớp bộ lọc." : <div className="space-y-2">
                  <div>Chưa có nick trên {cfg.remote ? "server này" : "máy này"} — bấm <b className="text-foreground">Nhập kho</b> (file xuất từ máy cũ), <b className="text-foreground">Nhập cookie</b> hoặc <b className="text-foreground">Thêm bằng Facebook</b>{cfg.remote ? ", hoặc đẩy nick từ máy đang giữ nick:" : "."}</div>
                  {cfg.remote && <div className="mx-auto flex w-fit items-center gap-2 rounded-lg bg-surface-lowest px-3 py-1.5 font-mono text-[11px] text-tertiary">{pushCmd}<button className="text-muted-foreground hover:text-foreground" onClick={() => navigator.clipboard?.writeText(pushCmd)} title="Sao chép"><Copy className="h-3.5 w-3.5" /></button></div>}
                </div>}
              </td></tr>
            )}
            {rows.map((a) => {
              const c = accChip(a);
              return (
                <tr key={a.name} className={`border-t border-surface hover:bg-surface/60 ${sel[a.name] ? "bg-surface" : ""} ${a.scheduling ? "" : "opacity-60"}`}>
                  <td className={td + " text-center"}><input type="checkbox" checked={!!sel[a.name]} disabled={busy} onChange={(e) => setSel({ ...sel, [a.name]: e.target.checked })} /></td>
                  <td className={td}>
                    <div className="flex items-center gap-2">
                      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-highest font-mono text-[10px] font-bold text-muted-foreground">{String(a.name).replace(/^fb/, "").slice(-2).toUpperCase()}</span>
                      <div className="min-w-0"><div className="truncate font-medium">{a.name}</div>
                        <div className="truncate font-mono text-[10.5px] text-muted-foreground">{a.email || "—"}</div></div>
                    </div>
                  </td>
                  <td className={td} title={a.limit_reason || a.quota_reason || ""}>
                    <Badge variant={c.variant}>{c.st === "busy" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />}{c.text}</Badge>
                  </td>
                  <td className={td + " text-right font-mono text-[13px] font-bold " + (a.remaining == null ? "text-muted-foreground" : a.remaining <= 1 ? "text-error" : "text-foreground")}>{a.remaining ?? "—"}</td>
                  <td className={td + " text-center"}><span className={"rounded px-1.5 py-0.5 font-mono text-[11px] " + (a.limit && a.used_today >= a.limit ? "bg-warn/20 text-warn" : "bg-surface-high")}>{a.used_today}/{a.limit}</span></td>
                  <td className={td + " font-mono text-[11px]"} title={a.proxy ? maskProxy(a.proxy) : "dùng proxy chung"}>
                    {a.proxy ? <span className="text-foreground">{proxyHost(a.proxy)}</span> : <span className="text-muted-foreground">proxy chung</span>}
                  </td>
                  <td className={td + " font-mono text-[11px] text-muted-foreground"}>{timeAgo(a.last_used_at)}</td>
                  <td className={td}>
                    <Input data-note className="h-7 border-transparent bg-transparent px-2 text-[11.5px] hover:border-input focus:border-input"
                      defaultValue={a.note || ""} placeholder="ghi chú…"
                      onBlur={(e) => { if (e.target.value !== (a.note || "")) saveNote(a.name, e.target.value, e.target); }} />
                  </td>
                  <td className={td}><Switch on={!!a.scheduling} disabled={busy} onClick={() => toggleSched(a)} title={a.scheduling ? "Đang cho chạy — bấm để tạm ngưng" : "Đang tạm ngưng — bấm để cho chạy lại"} /></td>
                  <td className={td + " whitespace-nowrap text-right"}>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Kiểm tra cookie còn sống" disabled={busy} onClick={() => one(a.name, verifyAccount, "kiểm tra")}><Stethoscope className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Đăng nhập lại (lấy cookie mới)" disabled={busy} onClick={() => relogin(a.name)}><RotateCw className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Proxy riêng cho nick" disabled={busy} onClick={() => setProxy(a.name)}><Settings className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Mở cửa sổ Chrome của nick" disabled={busy} onClick={() => one(a.name, openProfile, "mở profile")}><FolderOpen className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 text-error hover:text-error" title="Xoá hẳn nick" disabled={busy} onClick={() => del(a.name)}><Trash2 className="h-3.5 w-3.5" /></Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="flex items-center justify-between bg-surface-lowest px-3 py-2 font-mono text-[11px] text-muted-foreground">
          <span>Hiển thị {rows.length} / {all.length} nick</span>
          {msg && <span className="truncate text-foreground">{msg}</span>}
        </div>
      </div>
      <ProxyAssignDialog open={dlg} onOpenChange={setDlg} accounts={all} selected={selNames} onDone={done} />
    </div>
  );
}
