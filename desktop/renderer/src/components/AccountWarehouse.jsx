import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RotateCw, Settings, Trash2, FolderOpen, Stethoscope, RefreshCw, Eraser, Users } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { SelectNative } from "@/components/ui/select-native";
import {
  api, adminAccounts, patchAccount, verifyAccount, openProfile,
  deleteAccount, clearCookies, timeAgo, accState,
} from "@/lib/api";

const POLL_MS = 4000;

// accState + ACC_BADGE nay o lib/api.js (dung chung cho ca 3 tab).
const ST = {
  ready: { v: "success", t: "sẵn sàng" },
  busy: { v: "default", t: "đang chạy" },
  cooling: { v: "warn", t: "đang nghỉ" },
  quota: { v: "warn", t: "hết lượt" },
  off: { v: "secondary", t: "tắt lịch" },
  dead: { v: "danger", t: "cookie chết" },
};
const ORDER = { ready: 0, busy: 1, cooling: 2, quota: 3, off: 4, dead: 5 };
const FILTERS = [["", "Tất cả trạng thái"], ["ready", "Sẵn sàng"], ["busy", "Đang chạy"], ["quota", "Hết lượt / hết điểm"], ["cooling", "Đang nghỉ"], ["off", "Tắt lịch"], ["dead", "Cookie chết"]];
const SORTS = [["status", "Xếp: Trạng thái"], ["name", "Xếp: Tên"], ["remaining", "Xếp: Lượt còn"], ["last", "Xếp: Dùng gần nhất"]];
const msgOf = (e) => String((e && e.message) || e).slice(0, 140);

function Switch({ on, onClick, title, disabled }) {
  return (
    <button type="button" title={title} onClick={onClick} disabled={disabled}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-40 ${on ? "bg-emerald-500" : "bg-muted-foreground/40"}`}>
      <span className={`absolute top-0.5 block h-4 w-4 rounded-full bg-white transition-transform ${on ? "translate-x-4" : "translate-x-0.5"}`} />
    </button>
  );
}

export default function AccountWarehouse({ onRefresh }) {
  const [list, setList] = useState(null);
  const [loadErr, setLoadErr] = useState(null); // "auth" | "net" | "http"
  const [sel, setSel] = useState({});
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState("status");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  // Đếm chứ không dùng cờ: one() lồng trong each() từng tắt cờ mà each() đang giữ,
  // mở cửa cho người dùng bấm tiếp giữa chừng một vòng xoá hàng loạt.
  const busyRef = useRef(0);
  const mark = (d) => { busyRef.current = Math.max(0, busyRef.current + d); setBusy(busyRef.current > 0); };

  const load = useCallback(async () => {
    const res = await adminAccounts();
    if (!res.ok) { setLoadErr(res.kind); setList(null); return; }
    setLoadErr(null);
    const accs = res.accounts;
    const proxies = await Promise.all(accs.map((a) =>
      Promise.resolve(api.getProxy?.(a.name)).then((r) => r?.proxy || "").catch(() => "")));
    setList(accs.map((a, i) => ({ ...a, proxy: proxies[i] })));
    // Cắt bỏ mọi khoá không còn trong kho (xoá 1 nick, xoá hàng loạt, hoặc xoá bằng
    // đường khác) -> sel không thể giữ tên mà pool không còn nữa.
    setSel((prev) => Object.fromEntries(accs.filter((a) => prev[a.name]).map((a) => [a.name, true])));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(() => {
      // Chỉ ngừng khi đang gõ GHI CHÚ. Trước đây chặn theo cả thẻ Card nên chỉ cần
      // con trỏ nằm ở ô tìm kiếm/bộ lọc/checkbox là bảng đứng hình vô thời hạn.
      if (document.activeElement?.hasAttribute?.("data-note")) return;
      load();
    }, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const all = useMemo(() => list || [], [list]);
  const rows = useMemo(() => {
    const kw = q.trim().toLowerCase();
    const out = all
      .map((a) => ({ ...a, st: accState(a) }))
      .filter((a) => !filter || a.st === filter)
      .filter((a) => !kw || [a.name, a.email, a.note].some((v) => String(v || "").toLowerCase().includes(kw)));
    return [...out].sort((x, y) =>
      sort === "remaining" ? (y.remaining ?? 0) - (x.remaining ?? 0)
        : sort === "last" ? (y.last_used_at || 0) - (x.last_used_at || 0)
          : sort === "name" ? String(x.name).localeCompare(String(y.name))
            : ORDER[x.st] - ORDER[y.st] || String(x.name).localeCompare(String(y.name)));
  }, [all, q, filter, sort]);

  // Suy từ HÀNG ĐANG HIỆN (rows), không phải toàn kho: nick bị bộ lọc/ô tìm kiếm giấu đi
  // thì không được nằm trong tầm bắn của nút xoá hàng loạt. Cũng khớp với ngữ nghĩa ô
  // "chọn tất cả" ở đầu bảng, vốn chỉ tick các hàng đang hiện.
  const selNames = useMemo(() => rows.filter((a) => sel[a.name]).map((a) => a.name), [rows, sel]);
  const allSel = rows.length > 0 && rows.every((a) => sel[a.name]);
  const counts = useMemo(() => {
    const m = {};
    for (const a of all) { const k = accState(a); m[k] = (m[k] || 0) + 1; }
    return m;
  }, [all]);
  const credits = all.reduce((s, a) => s + (a.remaining || 0), 0);

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
      // Giữ NGUYÊN VĂN lý do lỗi: "xong 3/5" mà không nói vì sao thì không sửa được gì.
      try {
        const r = await fn(n);
        if (r && r.ok === false) throw new Error(r.error || "?");
        if (r && r.alive === false) { bad.push(`${n}: cookie chết`); continue; }  // verify trả alive:false
        ok++;
      } catch (e) { bad.push(`${n}: ${msgOf(e)}`); }
    }
    mark(-1);
    setMsg(`${label}: xong ${ok}/${names.length}.`
      + (bad.length ? ` Lỗi — ${bad.slice(0, 3).join(" · ")}${bad.length > 3 ? ` (+${bad.length - 3} nick nữa)` : ""}` : ""));
    if (clearSelAfter) setSel({});
    await done();
  }

  const toggleSched = (a) => one(a.name, (n) => patchAccount(n, { scheduling: !a.scheduling }), a.scheduling ? "tắt lịch" : "bật lịch");
  const saveNote = async (n, v, el) => {
    try { await patchAccount(n, { note: v }); if (el) el.classList.remove("border-red-500"); setMsg(`✓ ghi chú ${n}`); await done(); }
    catch (e) {
      // GIỮ text vừa gõ (nó chỉ tồn tại ở đây) và đánh dấu chưa lưu; đừng revert về giá trị cũ.
      if (el) el.classList.add("border-red-500");
      setMsg(`Lỗi lưu ghi chú ${n}: ${msgOf(e)} — chưa lưu, thử lại: "${v}"`);
    }
  };
  const relogin = (n) => one(n, (x) => api.loginElectron?.(x, "ja"), "đăng nhập lại");
  const setProxy = async (n) => {
    const cur = (await api.getProxy?.(n))?.proxy || "";
    const v = window.prompt(`Proxy riêng cho "${n}" (để trống = dùng proxy chung).\nhttp://host:port · socks5://host:port · user:pass@host:port · host:port:user:pass`, cur);
    if (v === null) return;
    one(n, (x) => api.setProxy?.(x, v), "đổi proxy");
  };
  const del = (n) => { if (window.confirm(`XOÁ HẲN nick ${n}?\nToàn bộ profile và phiên đăng nhập sẽ mất (không hoàn tác).`)) one(n, deleteAccount, "xoá"); };
  const bulkDel = () => { if (window.confirm(`XOÁ HẲN ${selNames.length} nick?\n${selNames.join(", ")}\nKhông hoàn tác.`)) each(selNames, deleteAccount, "Xoá nick", true); };
  const bulkClear = () => { if (window.confirm(`Xoá cookie của ${selNames.length} nick?\n${selNames.join(", ")}\nCác nick này sẽ phải đăng nhập lại.`)) each(selNames, clearCookies, "Xoá cookie", true); };

  const emptyMsg = loadErr === "auth"
    ? "Sai admin key — kiểm tra DOLA_ADMIN_KEY trong .env.local rồi mở lại app."
    : loadErr === "http" ? "Gateway trả lỗi — xem nút Log ở thanh trên."
      : "Chưa nối được server — bấm “Bật server” ở thanh trên.";

  const th = "h-11 px-3 text-left font-medium";
  const td = "px-3 py-2.5 align-middle";
  return (
    <Card className="mb-5">
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-4">
        <CardTitle className="flex items-center gap-2"><Users className="h-4 w-4" />Kho tài khoản</CardTitle>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={load} disabled={busy}><RefreshCw className="h-3.5 w-3.5" />Làm mới</Button>
          <Button variant="outline" size="sm" disabled={busy || !all.length}
            onClick={() => each(all.map((a) => a.name), verifyAccount, "Kiểm tra cookie")}>
            <Stethoscope className="h-3.5 w-3.5" />Kiểm tra tất cả
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11.5px] text-muted-foreground">
          <span>Tổng <b className="text-foreground">{all.length}</b></span>
          <span>Sẵn sàng <b className="text-emerald-400">{counts.ready || 0}</b></span>
          <span>Đang chạy <b className="text-foreground">{counts.busy || 0}</b></span>
          <span>Đang nghỉ <b className="text-amber-400">{counts.cooling || 0}</b></span>
          <span>Hết lượt <b className="text-amber-400">{counts.quota || 0}</b></span>
          <span>Cookie chết <b className="text-red-400">{counts.dead || 0}</b></span>
          <span>Tắt lịch <b className="text-foreground">{counts.off || 0}</b></span>
          <span>Tổng lượt còn <b className="text-foreground">{credits}</b></span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Input className="h-8 min-w-[180px] flex-1 text-xs" placeholder="Tìm nick, email, ghi chú…" value={q} onChange={(e) => setQ(e.target.value)} />
          <SelectNative className="h-8 w-auto text-xs" value={filter} onChange={(e) => setFilter(e.target.value)}>
            {FILTERS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </SelectNative>
          <SelectNative className="h-8 w-auto text-xs" value={sort} onChange={(e) => setSort(e.target.value)}>
            {SORTS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </SelectNative>
        </div>

        {selNames.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 p-2">
            <span className="px-1 text-xs text-muted-foreground">Đã chọn {selNames.length} nick:</span>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => each(selNames, (n) => patchAccount(n, { scheduling: true }), "Bật lịch")}>Bật lịch</Button>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => each(selNames, (n) => patchAccount(n, { scheduling: false }), "Tắt lịch")}>Tắt lịch</Button>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => each(selNames, verifyAccount, "Kiểm tra cookie")}><Stethoscope className="h-3.5 w-3.5" />Kiểm tra</Button>
            <Button variant="outline" size="sm" disabled={busy} onClick={bulkClear}><Eraser className="h-3.5 w-3.5" />Xoá cookie</Button>
            <Button variant="outline" size="sm" disabled={busy} className="text-red-400 hover:text-red-300" onClick={bulkDel}><Trash2 className="h-3.5 w-3.5" />Xoá nick</Button>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => setSel({})}>Bỏ chọn</Button>
          </div>
        )}

        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full border-collapse text-[12.5px]">
            <thead>
              <tr className="border-b bg-muted/40 text-muted-foreground">
                <th className={th + " w-8"}>
                  <input type="checkbox" checked={allSel} disabled={busy}
                    onChange={(e) => setSel(e.target.checked ? Object.fromEntries(rows.map((a) => [a.name, true])) : {})} />
                </th>
                <th className={th}>Nick</th>
                <th className={th + " w-[120px]"}>Trạng thái</th>
                <th className={th + " w-[92px]"}>Lượt</th>
                <th className={th + " w-[120px]"}>Proxy</th>
                <th className={th + " w-[84px]"}>Lần cuối</th>
                <th className={th + " w-[170px]"}>Ghi chú</th>
                <th className={th + " w-[56px]"}>Lịch</th>
                <th className={th + " w-[160px]"} />
              </tr>
            </thead>
            <tbody>
              {list === null && <tr><td colSpan={9} className="px-3 py-6 text-center text-muted-foreground">{emptyMsg}</td></tr>}
              {list !== null && !rows.length && <tr><td colSpan={9} className="px-3 py-6 text-center text-muted-foreground">{all.length ? "Không có nick khớp bộ lọc." : "Chưa có nick — thêm ở phần dưới."}</td></tr>}
              {rows.map((a) => (
                <tr key={a.name} className={`border-b last:border-0 hover:bg-white/[.025] ${a.scheduling ? "" : "opacity-60"}`}>
                  <td className={td}><input type="checkbox" checked={!!sel[a.name]} disabled={busy} onChange={(e) => setSel({ ...sel, [a.name]: e.target.checked })} /></td>
                  <td className={td}>
                    <b>{a.name}</b>
                    {a.email && <div className="text-[10.5px] text-muted-foreground">{a.email}</div>}
                  </td>
                  <td className={td} title={a.limit_reason || a.quota_reason || ""}>
                    <Badge variant={ST[a.st].v}>{ST[a.st].t}</Badge>
                    {a.cooling && a.cooldown_until > 0 && (
                      <div className="mt-0.5 text-[10.5px] text-muted-foreground">còn {Math.max(0, Math.ceil((a.cooldown_until - Date.now() / 1000) / 60))} phút</div>
                    )}
                  </td>
                  <td className={td}>
                    {a.used_today}/{a.limit}
                    <div className="text-[10.5px] text-muted-foreground">còn {a.remaining ?? "?"}</div>
                  </td>
                  <td className={td + " text-[11px] text-muted-foreground"} title={a.proxy || "dùng proxy chung"}>
                    {a.proxy ? a.proxy.replace(/^https?:\/\//, "").slice(0, 20) : "chung"}
                  </td>
                  <td className={td + " text-[11px] text-muted-foreground"}>{timeAgo(a.last_used_at)}</td>
                  <td className={td}>
                    <Input data-note className="h-7 border-transparent bg-transparent px-2 text-[11.5px] hover:border-input focus:border-input"
                      defaultValue={a.note || ""} placeholder="ghi chú…"
                      onBlur={(e) => { if (e.target.value !== (a.note || "")) saveNote(a.name, e.target.value, e.target); }} />
                  </td>
                  <td className={td}><Switch on={!!a.scheduling} disabled={busy} onClick={() => toggleSched(a)} title={a.scheduling ? "Đang xếp lịch — bấm để tạm ngưng" : "Đang tắt — bấm để cho chạy lại"} /></td>
                  <td className={td + " whitespace-nowrap text-right"}>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Kiểm tra cookie còn sống" disabled={busy} onClick={() => one(a.name, verifyAccount, "kiểm tra")}><Stethoscope className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Đăng nhập lại (lấy cookie mới)" disabled={busy} onClick={() => relogin(a.name)}><RotateCw className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Proxy riêng cho nick" disabled={busy} onClick={() => setProxy(a.name)}><Settings className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="Mở cửa sổ Chrome của nick" disabled={busy} onClick={() => one(a.name, openProfile, "mở profile")}><FolderOpen className="h-3.5 w-3.5" /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 text-red-400 hover:text-red-300" title="Xoá hẳn nick" disabled={busy} onClick={() => del(a.name)}><Trash2 className="h-3.5 w-3.5" /></Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {msg && <div className="text-xs text-muted-foreground">{msg}</div>}
      </CardContent>
    </Card>
  );
}
