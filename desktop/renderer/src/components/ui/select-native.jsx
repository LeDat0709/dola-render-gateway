import * as React from "react";
import { cn } from "@/lib/utils";
const SelectNative = React.forwardRef(({ className, children, ...props }, ref) => (
  <select ref={ref} className={cn(
    "flex h-9 w-full items-center rounded-md border border-input bg-transparent px-2.5 py-1 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50",
    className)} {...props}>{children}</select>
));
SelectNative.displayName = "SelectNative";
export { SelectNative };
