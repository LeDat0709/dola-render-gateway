import { useEffect, useMemo, useState } from "react";
import { Network } from "lucide-react";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { SelectNative } from "@/components/ui/select-native";
import { api, maskProxy } from "@/lib/api";

const parseList = (t) => String(t || "").split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));

// Chia proxy theo nick, tối đa N nick một IP (cùng luật với deploy/push_nicks.py): nhiều nick chung
// một IP là lý do broker đối thủ bị Dola khoá. Ghi qua IPC account:setProxy (file accounts/<nick>/proxy.txt).
export default function ProxyAssignDialog({ open, onOpenChange, accounts, selected = [], onDone }) {
  const [text, setText] = useState("");
  const [perIp, setPerIp] = useState(5);
  const [scope, setScope] = useState("noproxy");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { if (open) { setScope(selected.length ? "selected" : "noproxy"); setMsg(""); } }, [open, selected.length]);

  const proxies = useMemo(() => parseList(text), [text]);
  const targets = useMemo(() => scope === "selected" ? accounts.filter((a) => selected.includes(a.name))
    : scope === "all" ? accounts : accounts.filter((a) => !a.proxy), [accounts, scope, selected]);
  const cap = Math.max(1, perIp) * proxies.length;
  const over = proxies.length > 0 && targets.length > cap;
  const plan = useMemo(() => targets.map((a, i) => ({ name: a.name, proxy: proxies.length ? proxies[i % proxies.length] : "" })), [targets, proxies]);

  async function apply() {
    if (!proxies.length) { setMsg("Dán ít nhất 1 proxy."); return; }
    if (!api.setProxy) { setMsg("Chỉ gán được trong app Dola Studio (trình duyệt không có IPC)."); return; }
    setBusy(true); let ok = 0; const bad = [];
    for (const p of plan) {
      setMsg(`Đang gán ${ok + bad.length + 1}/${plan.length}: ${p.name}…`);
      try { const r = await api.setProxy?.(p.name, p.proxy); if (r && r.ok === false) throw new Error(r.error || "?"); ok++; }
      catch (e) { bad.push(`${p.name}: ${String(e.message || e).slice(0, 80)}`); }
    }
    setBusy(false);
    setMsg(`Xong ${ok}/${plan.length} nick.` + (bad.length ? ` Lỗi: ${bad.slice(0, 3).join(" · ")}` : ""));
    onDone?.();
    if (!bad.length) onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl bg-surface-low">
        <div className="flex items-center gap-2 text-[15px] font-semibold"><Network className="h-4 w-4 text-tertiary" />Chia proxy tự động</div>
        <p className="-mt-2 text-xs text-muted-foreground">Mỗi dòng một proxy: <code className="font-mono text-primary">http://user:pass@host:port</code>, <code className="font-mono text-primary">socks5://…</code> hoặc <code className="font-mono text-primary">host:port:user:pass</code>. Chia vòng tròn, không quá {perIp} nick một IP.</p>
        <Textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} className="bg-surface-lowest font-mono text-xs" placeholder={"103.1.2.3:8080:user:pass\nhttp://user:pass@1.2.3.4:8080"} />
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <label className="flex items-center gap-2">Gán cho
            <SelectNative className="h-8 w-auto text-xs" value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="noproxy">nick chưa có proxy riêng</option>
              {selected.length > 0 && <option value="selected">{selected.length} nick đang chọn</option>}
              <option value="all">tất cả nick</option>
            </SelectNative>
          </label>
          <label className="flex items-center gap-2">Tối đa
            <Input type="number" min={1} max={20} className="h-8 w-16 text-xs" value={perIp} onChange={(e) => setPerIp(parseInt(e.target.value, 10) || 1)} /> nick / IP
          </label>
          <span className="ml-auto font-mono text-muted-foreground">{proxies.length} proxy · {targets.length} nick · chứa được {cap}</span>
        </div>
        {over && <div className="rounded-md bg-error-container/30 px-3 py-2 text-xs text-error">{targets.length} nick mà chỉ {proxies.length} proxy × {perIp} nick/IP = {cap} chỗ. Thêm proxy hoặc tăng số nick / IP.</div>}
        {proxies.length > 0 && !over && plan.length > 0 && (
          <div className="max-h-44 overflow-auto rounded-md bg-surface-lowest p-2 font-mono text-[11px]">
            {plan.map((p) => <div key={p.name} className="flex justify-between gap-3 py-0.5"><span>{p.name}</span><span className="text-muted-foreground">→ {maskProxy(p.proxy)}</span></div>)}
          </div>
        )}
        {msg && <div className="text-xs text-muted-foreground">{msg}</div>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)} disabled={busy}>Huỷ</Button>
          <Button size="sm" onClick={apply} disabled={busy || over || !plan.length}>Gán {plan.length} nick</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
