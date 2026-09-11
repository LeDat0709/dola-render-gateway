import { useEffect, useRef, useState } from "react";
import { Facebook, Cookie, LogIn, FileText, Rocket } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { SelectNative } from "@/components/ui/select-native";
import { api } from "@/lib/api";
import AccountWarehouse from "@/components/AccountWarehouse";

const fbUid = (line) => { const m = String(line || "").match(/c_user=(\d{5,})/); if (m) return m[1]; const f = String(line || "").split("|")[0].trim(); return /^\d{5,}$/.test(f) ? f : ""; };

export default function AccountsTab({ onRefresh }) {
  const [name, setName] = useState(""); const [lang, setLang] = useState("ja");
  const [open, setOpen] = useState(""); // "fb" | "cookie" | "bulk" | ""
  const [fbText, setFbText] = useState(""); const [cookie, setCookie] = useState(""); const [bulk, setBulk] = useState(""); const [verify, setVerify] = useState(false);
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState("");
  useEffect(() => { const off = api.onFbStep?.(({ line }) => setMsg(line)); const off2 = api.onBulkStep?.(({ line }) => setMsg(line)); return () => {}; }, []);

  async function submitFb() {
    const lines = fbText.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (!lines.length) { setMsg("Dán ít nhất 1 cookie/dòng Facebook."); return; }
    setBusy(true);
    try {
      if (lines.length === 1) {
        const nm = name.trim() || (fbUid(lines[0]) ? "fb" + fbUid(lines[0]) : "");
        if (!nm) { setMsg("Không lấy được UID (thiếu c_user) — nhập tên nick."); return; }
        const r = await api.importFacebookElectron?.(nm, lines[0], lang);
        setMsg(r?.ok ? "✓ " + (r.output || "Đã nạp phiên Dola vào " + nm) : "Lỗi: " + (r?.error || r?.output || "?"));
        if (r?.ok) { setFbText(""); setName(""); setOpen(""); onRefresh(); }
      } else {
        let ok = 0; const fail = []; const q = lines.map((line, i) => ({ line, i, uid: fbUid(line) }));
        const worker = async () => { while (q.length) { const j = q.shift(); if (!j.uid) { fail.push(`dòng ${j.i + 1}`); continue; } const nm = ("fb" + j.uid).slice(0, 32); setMsg(`Đăng nhập ${nm}…`); try { const r = await api.importFacebookElectron?.(nm, j.line, lang); if (r?.ok) ok++; else fail.push(nm); } catch { fail.push(nm); } onRefresh(); } };
        await Promise.all([worker(), worker(), worker()]);
        setMsg(`Xong ${ok}/${lines.length} nick.` + (fail.length ? ` Lỗi: ${fail.join(", ")}` : "")); if (ok) setFbText("");
      }
    } finally { setBusy(false); }
  }
  async function submitCookie() {
    const nm = name.trim(); if (!nm) { setMsg("Nhập tên nick."); return; } if (!cookie.trim()) { setMsg("Dán cookie."); return; }
    setBusy(true); try { const r = await api.importAccountText?.(nm, cookie.trim(), lang); setMsg(r?.ok ? "✓ Đã nạp cookie vào " + nm : "Lỗi: " + (r?.error || "?")); if (r?.ok) { setCookie(""); setName(""); setOpen(""); onRefresh(); } } finally { setBusy(false); }
  }
  async function submitBulk() { if (!bulk.trim()) { setMsg("Dán JSON export."); return; } setBusy(true); setMsg("Đang nạp hàng loạt…"); try { const r = await api.bulkImport?.(bulk.trim(), verify); const last = (r?.output || "").split(/\r?\n/).filter((l) => l.trim()).pop() || ""; setMsg(r?.ok ? "✓ " + (last || "Xong") : "Lỗi: " + (r?.error || last)); if (r?.ok) { setBulk(""); setOpen(""); onRefresh(); } } finally { setBusy(false); } }
  async function login() { const nm = name.trim(); if (!nm) { setMsg("Nhập tên nick."); return; } setBusy(true); setMsg("Đang mở cửa sổ đăng nhập…"); try { const r = await api.loginElectron?.(nm, lang); setMsg(r?.ok ? "✓ đăng nhập xong" : "Lỗi: " + (r?.error || r?.output || "?")); if (r?.ok) { setName(""); onRefresh(); } } finally { setBusy(false); } }
  async function fromFile() { const nm = name.trim(); if (!nm) { setMsg("Nhập tên nick."); return; } setBusy(true); try { const r = await api.importAccount?.(nm, lang); setMsg(r?.ok ? "✓ Đã nạp từ file" : "Lỗi: " + (r?.error || "?")); if (r?.ok) { setName(""); onRefresh(); } } finally { setBusy(false); } }

  const box = "mt-3 rounded-lg border p-3 space-y-2";
  const addRef = useRef(null);
  // Nút "Nhập cookie" / "Thêm bằng Facebook" trên thanh công cụ của kho: mở đúng ô nhập rồi cuộn tới.
  const onAdd = (kind) => { setOpen(kind); setTimeout(() => addRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50); };
  return (
    <div>
      <AccountWarehouse onRefresh={onRefresh} onAdd={onAdd} />
      <div ref={addRef} className="mx-auto max-w-2xl">
      <Card>
        <CardHeader><CardTitle>Thêm / đăng nhập nick</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div><Label>Tên nick</Label><Input className="mt-1.5" value={name} onChange={(e) => setName(e.target.value)} placeholder="acc3" /></div>
          <div><Label>Ngôn ngữ giao diện Dola</Label>
            <SelectNative className="mt-1.5" value={lang} onChange={(e) => setLang(e.target.value)}>
              <option value="ja">ja (khuyên dùng — worker cần UI tiếng Nhật)</option><option value="vi">vi</option><option value="en">en</option>
            </SelectNative>
          </div>
          <div className="grid grid-cols-2 gap-2 pt-1">
            <Button className="bg-[#1877f2] text-white hover:bg-[#1877f2]/90" onClick={() => setOpen(open === "fb" ? "" : "fb")} disabled={busy}><Facebook className="h-4 w-4" />Thêm bằng Facebook</Button>
            <Button variant="secondary" onClick={() => setOpen(open === "cookie" ? "" : "cookie")} disabled={busy}><Cookie className="h-4 w-4" />Dán Cookie Dola</Button>
            <Button variant="outline" onClick={login} disabled={busy}><LogIn className="h-4 w-4" />Đăng nhập (cửa sổ app)</Button>
            <Button variant="outline" onClick={fromFile} disabled={busy}><FileText className="h-4 w-4" />Nạp từ file (.txt)</Button>
          </div>
          <Button variant="outline" className="w-full" onClick={() => setOpen(open === "bulk" ? "" : "bulk")} disabled={busy}><Rocket className="h-4 w-4" />Nạp hàng loạt (JSON nhiều nick)</Button>

          {open === "fb" && <div className={box}>
            <Label className="text-[#93c5fd]">Cookie/dòng Facebook — dán NHIỀU DÒNG để nạp nhiều nick (mỗi dòng 1 nick, tên fb&lt;UID&gt;)</Label>
            <Textarea rows={6} value={fbText} onChange={(e) => setFbText(e.target.value)} placeholder={"datr=...;c_user=100000...;xs=...\n(hoặc UID|PASS|2FA|COOKIE|UA)"} />
            <div className="flex gap-2"><Button className="flex-1 bg-[#1877f2] text-white hover:bg-[#1877f2]/90" onClick={submitFb} disabled={busy}>Đăng nhập trong cửa sổ app</Button><Button variant="outline" onClick={() => setOpen("")}>Hủy</Button></div>
          </div>}
          {open === "cookie" && <div className={box}>
            <Label>Dán chuỗi Cookie Dola</Label>
            <Textarea rows={4} value={cookie} onChange={(e) => setCookie(e.target.value)} placeholder="sessionid=... hoặc datr=...;c_user=...;xs=..." />
            <div className="flex gap-2"><Button className="flex-1" onClick={submitCookie} disabled={busy}>Xác nhận nạp</Button><Button variant="outline" onClick={() => setOpen("")}>Hủy</Button></div>
          </div>}
          {open === "bulk" && <div className={box}>
            <Label>Dán JSON export (dạng {'{"tai_khoan":[...]}'}) — nạp tất cả một lần</Label>
            <Textarea rows={5} value={bulk} onChange={(e) => setBulk(e.target.value)} placeholder='{"tai_khoan":[{"name":"...","cookies":{...}}, ...]}' />
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={verify} onChange={(e) => setVerify(e.target.checked)} />Verify từng nick (chậm) — bỏ chọn để nhanh</label>
            <div className="flex gap-2"><Button className="flex-1" onClick={submitBulk} disabled={busy}>Nạp tất cả</Button><Button variant="outline" onClick={() => setOpen("")}>Đóng</Button></div>
          </div>}

          {msg && <div className="text-xs text-muted-foreground">{msg}</div>}
          <p className="pt-1 text-[11px] text-muted-foreground">"Thêm bằng Facebook": tự kết nối Dola qua OAuth bằng Cookie Facebook (c_user + xs).</p>
        </CardContent>
      </Card>
      </div>
    </div>
  );
}
