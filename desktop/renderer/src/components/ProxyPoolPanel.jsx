import { useCallback, useEffect, useState } from "react";
import { Boxes, RefreshCw, Activity, Trash2, Shuffle, Plus, RotateCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { SelectNative } from "@/components/ui/select-native";
import { proxyPoolList, proxyPoolAdd, proxyPoolCheck, proxyPoolPrune, proxyPoolAssign, proxyPoolDelete, proxyPoolRotate } from "@/lib/api";

// Kho proxy tập trung: dán proxy vào kho, kiểm tra sống/chết (8 luồng/15s trên server), lọc chết, rồi
// chia cho nick chưa có proxy — tất cả qua API admin nên chạy cả khi nối server từ xa (VPS). Mật khẩu
// proxy do server che, giao diện không bao giờ thấy pass gốc.
// "3p", "1g05" — tuổi IP / thời gian chờ đổi cho gọn.
const ago = (x) => (x == null ? "" : x < 60 ? `${x}s` : x < 3600 ? `${Math.floor(x / 60)}p` : `${Math.floor(x / 3600)}g${String(Math.floor((x % 3600) / 60)).padStart(2, "0")}`);

export default function ProxyPoolPanel({ onAssigned }) {
  const [data, setData] = useState(null);
  const [text, setText] = useState("");
  const [provider, setProvider] = useState("");   // "" auto | tmproxy | topproxy — dán key trần thì chọn loại
  const [perIp, setPerIp] = useState(5);
  const [scope, setScope] = useState("noproxy");
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");

  const [loadFailed, setLoadFailed] = useState(false);
  const load = useCallback(async () => {
    try {
      setData(await proxyPoolList());
      setLoadFailed(false);
      setMsg((m) => (m.startsWith("Lỗi đọc kho") ? "" : m));
    } catch (e) {
      setLoadFailed(true);
      setMsg("Lỗi đọc kho: " + (e?.message || e) + " — gateway đang khởi động? Tự thử lại mỗi 5 giây, hoặc bấm ↻.");
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  // Tuổi IP / chờ đổi chạy theo thời gian: đọc lại kho mỗi 15s (API chỉ đọc cache, không gọi nhà bán).
  useEffect(() => { const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);
  // Mở tab đúng lúc gateway đang restart → lần tải đầu hỏng; trước đây kẹt "Failed to fetch" mãi dù header đã online.
  useEffect(() => {
    if (!loadFailed) return undefined;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [loadFailed, load]);

  const run = async (key, fn, done) => {
    setBusy(key); setMsg("");
    try { const r = await fn(); await load(); done?.(r); }
    catch (e) { setMsg("Lỗi: " + (e?.message || e)); }
    finally { setBusy(""); }
  };
  const add = () => run("add", () => proxyPoolAdd(text, provider), (r) => { setMsg(`Đã thêm ${r.added} proxy (kho có ${r.total}).`); if (r.added) setText(""); });
  const check = () => run("check", proxyPoolCheck, (r) => setMsg(`Kiểm xong: ${r.alive} sống · ${r.dead} chết (${r.threads} luồng, ${r.timeout}s/proxy).`));
  const prune = () => run("prune", proxyPoolPrune, (r) => setMsg(`Đã xoá ${r.removed} proxy chết (còn ${r.total}).`));
  const assign = () => run("assign", () => proxyPoolAssign(perIp, scope), (r) => { setMsg(`Đã chia proxy cho ${r.assigned} nick (${r.proxies_used} IP, tối đa ${r.per_ip}/IP).`); onAssigned?.(); });
  const del = (id) => run("del" + id, () => proxyPoolDelete(id));
  const rotate = (p) => {
    if (p.nicks > 0 && !window.confirm(`Đổi IP proxy này? ${p.nicks} nick đang dùng nó — job ĐANG CHẠY trên các nick đó có thể lỗi giữa chừng.`)) return;
    run("rot" + p.id, () => proxyPoolRotate(p.id), (r) => {
      const n = r.proxy || {};
      setMsg(n.rot?.rotate_in > 0 && n.endpoint === p.endpoint
        ? `Nhà bán chưa cho đổi — còn ${ago(n.rot.rotate_in)} nữa (đang giữ IP ${n.endpoint}).`
        : `Đã đổi IP: ${n.endpoint || "?"}${n.exit_ip ? ` · IP ra ${n.exit_ip}` : ""}${n.alive === false ? ` · CHẾT: ${n.error || "?"}` : ""}.`);
    });
  };

  const s = data?.stats || { total: 0, alive: 0, dead: 0, unchecked: 0 };
  const dot = (a) => a === true ? <Badge variant="success">sống</Badge> : a === false ? <Badge variant="danger">chết</Badge> : <Badge variant="secondary">chưa kiểm</Badge>;

  // Cảnh báo loại proxy xoay theo whitelist IP (proxy.vn/topproxy/link get.php của proxyxoay). Máy IP động →
  // whitelist xong đổi IP là "chết" ngay. tmproxy (key-auth) và proxy tĩnh user:pass thì KHÔNG bị.
  const needsWhitelist = provider === "topproxy" || provider === "proxyvn"
    || /get\.php|proxyxoay|(?:^|[\s\n])(?:topproxy|proxyvn|proxyxoay):\/\//i.test(text);

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
        placeholder={provider === "tmproxy" ? "Mỗi dòng một KEY TMProxy trần:\nabc123...   (tool tự lấy IP + tự đổi IP)"
          : provider === "topproxy" ? "Mỗi dòng một KEY TopProxy trần:\nWvsxrBXBy...   (nhớ whitelist IP máy trên topproxy.vn)"
          : provider === "proxyvn" ? "Mỗi dòng một KEY Proxy.vn trần:\nabc123...   (cùng backend proxyxoay.shop — NHỚ whitelist IP máy trên proxy.vn)"
          : "Mỗi dòng một proxy:\n103.1.2.3:8080:user:pass\nhttp://user:pass@1.2.3.4:8080\nsocks5://1.2.3.4:1080\ntmproxy://API_KEY\nhttps://proxyxoay.shop/api/get.php?key=...   (link topproxy/proxyxoay)"} />
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Loại</span>
        <SelectNative className="h-8 w-auto text-xs" value={provider} onChange={(e) => setProvider(e.target.value)} title="Chọn loại để dán KEY TRẦN; 'Tự động' cho link đầy đủ / host:port">
          <option value="">Tự động (link / host:port)</option>
          <option value="tmproxy">TMProxy — dán key trần</option>
          <option value="topproxy">TopProxy — dán key trần</option>
          <option value="proxyvn">Proxy.vn — dán key trần</option>
        </SelectNative>
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
      {needsWhitelist && (
        <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-[11px] leading-relaxed text-warn">
          ⚠ Loại này (proxy.vn / topproxy / link <code className="font-mono">get.php</code>) xác thực theo <b>whitelist IP máy</b>. Tool <b>tự khai whitelist IP máy</b> mỗi khi IP máy đổi (cột "IP proxy" hiện IP đã khai). Vẫn chết thì: mạng máy chỉ có IPv6, IP máy đổi quá nhanh, hoặc key hết hạn — khi đó dùng <b>tmproxy</b> (xác thực bằng key) hoặc <b>proxy tĩnh <code className="font-mono">ip:port:user:pass</code></b>.
        </div>
      )}
      {msg && <div className="text-xs text-muted-foreground">{msg}</div>}

      {!!(data?.proxies || []).length && (
        <Table wrapperClassName="max-h-96 bg-background" className="text-[12px]">
          <TableHeader className="bg-background">
            <TableRow><TableHead>Proxy</TableHead><TableHead>IP ra</TableHead><TableHead>IP proxy</TableHead><TableHead>Trạng thái</TableHead><TableHead>Nick</TableHead><TableHead /></TableRow>
          </TableHeader>
          <TableBody>
            {data.proxies.map((p) => {
              const rot = p.rot;
              const life = rot?.expires_in;
              return (
                <TableRow key={p.id} className="align-top">
                  <TableCell className="py-2">
                    <div className="flex items-center gap-1.5">
                      <span className="rounded bg-primary/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase text-primary">{rot?.provider || p.scheme}</span>
                      <span className="font-mono text-[12px] font-semibold" title={p.proxy}>{rot ? (rot.key_tail ? `…${rot.key_tail}` : "key") : p.proxy}</span>
                    </div>
                    <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">{p.endpoint || (rot ? "chưa lấy IP — bấm Kiểm tra kho" : "")}</div>
                  </TableCell>
                  <TableCell className="whitespace-nowrap py-2">
                    {p.exit_ip
                      ? <><div className="font-mono text-[12px] font-semibold text-tertiary">{p.exit_ip}</div>
                          <div className="text-[11px] text-muted-foreground">{[p.isp, p.city].filter(Boolean).join(" · ") || "—"}</div></>
                      : <span className="text-[11px] text-muted-foreground">{p.alive === false ? "—" : "bấm Kiểm tra kho"}</span>}
                  </TableCell>
                  <TableCell className="min-w-[250px] whitespace-nowrap py-2 text-[11px] leading-snug">
                    {rot ? (
                      <>
                        <div className={life == null ? "text-muted-foreground" : life <= 0 ? "text-warn" : life < 120 ? "text-warn" : "text-tertiary"}>
                          {life == null ? "chưa lấy IP" : life <= 0 ? "IP đã hết tuổi" : `IP còn sống ${ago(life)}`}{rot.age != null ? <span className="text-muted-foreground"> · lấy {ago(rot.age)} trước</span> : null}
                        </div>
                        <div className="text-muted-foreground">đổi {rot.changes ?? 0} lần hôm nay · {rot.rotate_in > 0 ? `đổi được sau ${ago(rot.rotate_in)}` : "đổi được ngay"}</div>
                        {rot.whitelist_ip && <div className="font-mono text-[10.5px] text-info">whitelist {rot.whitelist_ip} (tự khai)</div>}
                      </>
                    ) : <span className="text-muted-foreground">IP tĩnh</span>}
                  </TableCell>
                  <TableCell className="max-w-[240px] py-2">
                    <div className="flex items-center gap-1.5">{dot(p.alive)}{p.alive && p.latency_ms != null && <span className="font-mono text-[10.5px] text-muted-foreground">{p.latency_ms}ms</span>}</div>
                    {p.error && p.alive !== true && <div className="mt-0.5 truncate text-[11px] text-error" title={p.error}>{p.error}</div>}
                  </TableCell>
                  <TableCell className="py-2 font-mono text-muted-foreground">{p.nicks ?? 0}</TableCell>
                  <TableCell className="whitespace-nowrap py-2 text-right">
                    {rot && <Button variant="outline" size="sm" className="h-7 text-[11px]" onClick={() => rotate(p)} disabled={!!busy} title="Xin IP mới từ nhà bán rồi kiểm lại ngay">
                      <RotateCw className={"h-3.5 w-3.5 " + (busy === "rot" + p.id ? "animate-spin" : "")} />Đổi IP</Button>}
                    <Button variant="ghost" size="icon" className="h-7 w-7 text-error hover:text-error" onClick={() => del(p.id)} disabled={!!busy}><Trash2 className="h-3.5 w-3.5" /></Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
