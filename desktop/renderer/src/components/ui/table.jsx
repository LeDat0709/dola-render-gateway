import * as React from "react";
import { cn } from "@/lib/utils";
// Bảng kiểu shadcn-admin: khung bo góc có viền, header nền mờ dính trên cùng, hàng đổi màu khi rê chuột.
const Table = React.forwardRef(({ className, wrapperClassName, ...props }, ref) => (
  <div className={cn("relative w-full overflow-auto rounded-lg border bg-card", wrapperClassName)}>
    <table ref={ref} className={cn("w-full caption-bottom text-[12.5px]", className)} {...props} />
  </div>
));
Table.displayName = "Table";
const TableHeader = React.forwardRef(({ className, ...props }, ref) => (
  <thead ref={ref} className={cn("sticky top-0 z-10 bg-card [&_tr]:border-b [&_tr]:bg-muted/40", className)} {...props} />
));
TableHeader.displayName = "TableHeader";
const TableBody = React.forwardRef(({ className, ...props }, ref) => (
  <tbody ref={ref} className={cn("[&_tr:last-child]:border-0", className)} {...props} />
));
TableBody.displayName = "TableBody";
const TableFooter = React.forwardRef(({ className, ...props }, ref) => (
  <tfoot ref={ref} className={cn("border-t bg-muted/40 font-medium", className)} {...props} />
));
TableFooter.displayName = "TableFooter";
const TableRow = React.forwardRef(({ className, ...props }, ref) => (
  <tr ref={ref} className={cn("border-b border-border/60 transition-colors hover:bg-muted/40 data-[state=selected]:bg-muted/60", className)} {...props} />
));
TableRow.displayName = "TableRow";
const TableHead = React.forwardRef(({ className, ...props }, ref) => (
  <th ref={ref} className={cn("h-9 px-3 text-left align-middle font-mono text-[10px] font-semibold uppercase tracking-wider text-muted-foreground [&:has([type=checkbox])]:pr-0", className)} {...props} />
));
TableHead.displayName = "TableHead";
const TableCell = React.forwardRef(({ className, ...props }, ref) => (
  <td ref={ref} className={cn("px-3 py-2 align-middle [&:has([type=checkbox])]:pr-0", className)} {...props} />
));
TableCell.displayName = "TableCell";
export { Table, TableHeader, TableBody, TableFooter, TableRow, TableHead, TableCell };
