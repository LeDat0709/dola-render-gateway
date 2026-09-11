import { cva } from "class-variance-authority";
import { cn } from "@/lib/utils";
// Chip trạng thái theo bản Stitch: xanh lá = sẵn sàng, tím = đang chạy, đỏ = hết credit/cookie chết,
// hổ phách = hết lượt ngày, xanh dương = đang nghỉ, xám = tắt lịch.
const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11.5px] font-semibold transition-colors focus:outline-none",
  { variants: { variant: {
      default: "border-transparent bg-primary/15 text-primary",
      secondary: "border-transparent bg-surface-highest text-muted-foreground",
      destructive: "border-transparent bg-destructive text-destructive-foreground",
      outline: "text-foreground",
      success: "border-tertiary/25 bg-tertiary/15 text-tertiary",
      warn: "border-warn/30 bg-warn/15 text-warn",
      danger: "border-error/25 bg-error-container/30 text-error",
      info: "border-info/25 bg-info/15 text-info",
  } }, defaultVariants: { variant: "default" } }
);
export function Badge({ className, variant, ...props }) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}
