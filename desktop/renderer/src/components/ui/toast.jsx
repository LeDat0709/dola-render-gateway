// Toast tối giản, KHÔNG thêm thư viện: gọi toast("...", "success"|"error"|"warn"|"info") ở bất kỳ đâu;
// <Toaster/> gắn 1 lần ở App. Tự tắt sau vài giây, xếp chồng góc dưới-phải, không chặn thao tác (pointer-events).
import { useEffect, useState } from "react";

let _id = 0;
let _items = [];
const _subs = new Set();
const _emit = () => _subs.forEach((fn) => fn(_items));

export function toast(text, type = "info", ms = 3200) {
  if (!text) return;
  const id = ++_id;
  _items = [..._items, { id, text: String(text), type }];
  _emit();
  setTimeout(() => { _items = _items.filter((t) => t.id !== id); _emit(); }, ms);
  return id;
}

const ACCENT = { success: "border-l-tertiary", error: "border-l-error", warn: "border-l-warn", info: "border-l-info" };

export function Toaster() {
  const [items, setItems] = useState(_items);
  useEffect(() => { _subs.add(setItems); return () => { _subs.delete(setItems); }; }, []);
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[200] flex max-w-[360px] flex-col gap-2">
      {items.map((t) => (
        <div key={t.id}
             className={"pointer-events-auto animate-in slide-in-from-bottom-2 rounded-lg border border-input border-l-4 bg-surface-low px-3.5 py-2.5 text-[13px] leading-snug text-foreground shadow-lg " + (ACCENT[t.type] || ACCENT.info)}>
          {t.text}
        </div>
      ))}
    </div>
  );
}
