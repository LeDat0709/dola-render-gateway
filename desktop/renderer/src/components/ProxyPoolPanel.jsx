import { useCallback, useEffect, useState } from "react";
import { Boxes, RefreshCw, Activity, Trash2, Shuffle, Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { SelectNative } from "@/components/ui/select-native";
import { proxyPoolList, proxyPoolAdd, proxyPoolCheck, proxyPoolPrune, proxyPoolAssign, proxyPoolDelete } from "@/lib/api";

// Kho proxy tập trung: dán proxy vào kho, kiểm tra sống/chết (8 luồng/15s trên server), lọc chết, rồi
// chia cho nick chưa có proxy — tất cả qua API admin nên chạy cả khi nối server từ xa (VPS). Mật khẩu
// proxy do server che, giao diện không bao giờ thấy pass gốc.
export default function ProxyPoolPanel({ onAssigned }) {
  const [data, setData] = useState(null);
  const [text, setText] = useState("");
  const [perIp, setPerIp] = useState(5);
  const [scope, setScope] = useState("noproxy");
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    try { setData(await proxyPoolList()); } catch (e) { setMsg("Lỗi đọc kho: " + (e?.message || e)); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const run = async (key, fn, done) => {
    setBusy(key); setMsg("");
    try { const r = await fn(); await load(); done?.(r); }
    catch (e) { setMsg("Lỗi: " + (e?.message || e)); }
    finally { setBusy(""); }
  };
  const add = () => run("add", () => proxyPoolAdd(text), (r) => { setMsg(`Đã thêm ${r.added} proxy (kho có ${r.total}).`); if (r.added) setText(""); });
  const check = () => run("check", proxyPoolCheck, (r) => setMsg(`Kiểm xong: ${r.alive} sống · ${r.dead} chết (${r.threads} luồng, ${r.timeout}s/proxy).`));
  const prune = () => run("prune", proxyPoolPrune, (r) => setMsg(`Đã xoá ${r.removed} proxy chết (còn ${r.total}).`));
  const assign = () => run("assign", () => proxyPoolAssign(perIp, scope), (r) => { setMsg(`Đã chia proxy cho ${r.assigned} nick (${r.proxies_used} IP, tối đa ${r.per_ip}/IP).`); onAssigned?.(); });
  const del = (id) => run("del" + id, () => proxyPoolDelete(id));

  const s = data?.stats || { total: 0, alive: 0, dead: 0, unchecked: 0 };
  const dot = (a) => a === true ? <Badge variant="success">sống</Badge> : a === false ? <Badge variant="danger">chết</Badge> : <Badge variant="secondary">chưa kiểm</Badge>;

  return (
    <div className="space-y-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2 text-[15px] font-semibold"><Boxes className="h-4 w-4 text-tertiary" />Kho proxy tập trung</div>
        <span className="rounded bg-tertiary/10 px-1.5 py-0.5 font-mono text-[10px] text-tertiary">{s.total} proxy · {s.alive} sống · {s.dead} chết</span>
        <span className="ml-auto" />
        <Button variant="outline" size="sm" onClick={check} disabled={!!busy || !s.total}><Activity className={"h-3.5 w-3.5 " + (busy === "check" ? "animate-spin" : "text-tertiary")} />Kiểm tra kho</Button>
        <Button variant="outline" size="sm" onClick={prune} disabled={!!busy || !s.dead}><Trash2 className="h-3.5 w-3.5" />Xoá proxy chết</Button>
        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={load}><RefreshCw className="h-3.5 w-3.5" /></Button>
      </div>

      <Textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} className="bg-background font-mono text-xs"
        placeholder={"Mỗi dòng một proxy:\n103.1.2.3:8080:user:pass\nhttp://user:pass@1.2.3.4:8080\nsocks5://1.2.3.4:1080"} />
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={add} disabled={!!busy || !text.trim()}><Plus className="h-4 w-4" />Thêm vào kho</Button>
        <span className="mx-1 h-4 w-px bg-surface-high" />
        <span className="text-xs text-muted-foreground">Chia proxy sống cho</span>
        <SelectNative className="h-8 w-auto text-xs" value={scope} onChange={(e) => setScope(e.target.value)}>
          <option value="noproxy">nick chưa có proxy</option>
          <option value="all">tất cả nick</option>
        </SelectNative>
        <span className="text-xs text-muted-foreground">tối đa</span>
        <Input type="number" min={1} max={50} className="h-8 w-16 text-xs" value={perIp} onChange={(e) => setPerIp(parseInt(e.target.value, 10) || 1)} />
        <span className="text-xs text-muted-foreground">nick/IP</span>
        <Button size="sm" variant="outline" onClick={assign} disabled={!!busy || !s.alive}><Shuffle className="h-3.5 w-3.5" />Chia cho nick</Button>
      </div>
      {msg && <div className="text-xs text-muted-foreground">{msg}</div>}

      {!!(data?.proxies || []).length && (
        <Table wrapperClassName="max-h-56 bg-background" className="text-[12px]">
          <TableHeader className="bg-background">
            <TableRow><TableHead>Proxy (đã che mật khẩu)</TableHead><TableHead>Giao thức</TableHead><TableHead>Trạng thái</TableHead><TableHead>Nick đang gán</TableHead><TableHead /></TableRow>
          </TableHeader>
          <TableBody>
            {data.proxies.map((p) => (
              <TableRow key={p.id}>
                <TableCell className="py-1.5 font-mono">{p.proxy}</TableCell>
                <TableCell className="py-1.5"><span className="rounded bg-surface-high px-1.5 py-0.5 font-mono text-[10px] uppercase">{p.scheme}</span></TableCell>
                <TableCell className="py-1.5">{dot(p.alive)}</TableCell>
                <TableCell className="py-1.5 font-mono text-muted-foreground">{p.nicks ?? 0}</TableCell>
                <TableCell className="py-1.5 text-right"><Button variant="ghost" size="icon" className="h-7 w-7 text-error hover:text-error" onClick={() => del(p.id)} disabled={!!busy}><Trash2 className="h-3.5 w-3.5" /></Button></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
