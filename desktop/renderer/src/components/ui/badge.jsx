import { cva } from "class-variance-authority";
import { cn } from "@/lib/utils";
const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-semibold transition-colors focus:outline-none",
  { variants: { variant: {
      default: "border-transparent bg-primary text-primary-foreground",
      secondary: "border-transparent bg-secondary text-secondary-foreground",
      destructive: "border-transparent bg-destructive text-destructive-foreground",
      outline: "text-foreground",
      success: "border-emerald-500/30 bg-emerald-500/15 text-emerald-400",
      warn: "border-amber-500/30 bg-amber-500/15 text-amber-400",
      danger: "border-red-500/30 bg-red-500/15 text-red-400",
  } }, defaultVariants: { variant: "default" } }
);
export function Badge({ className, variant, ...props }) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}
