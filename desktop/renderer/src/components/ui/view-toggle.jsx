import { useState } from "react";
import { LayoutGrid, Rows3 } from "lucide-react";

// Nút Lưới/Bảng dùng chung cho Studio và Kho video. Lựa chọn nhớ theo key trong localStorage
// (mỗi tab một key) nên mở lại app vẫn giữ kiểu hiển thị cũ.
export function useView(key, initial = "grid") {
  const [view, setView] = useState(() => { try { return localStorage.getItem(key) || initial; } catch { return initial; } });
  const pick = (v) => { setView(v); try { localStorage.setItem(key, v); } catch {} };
  return [view, pick];
}

const OPTIONS = [["grid", "Lưới", LayoutGrid], ["table", "Bảng", Rows3]];

export function ViewToggle({ value, onChange }) {
  return (
    <div className="flex rounded-md bg-surface-lowest p-0.5" role="radiogroup" aria-label="Kiểu hiển thị">
      {OPTIONS.map(([v, label, Icon]) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} onClick={() => onChange(v)}
          className={"flex items-center gap-1 rounded px-2.5 py-1 text-xs font-medium " + (value === v ? "bg-surface text-primary" : "text-muted-foreground hover:text-foreground")}>
          <Icon className="h-3.5 w-3.5" />{label}
        </button>
      ))}
    </div>
  );
}
