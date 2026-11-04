// The ptrade terminal's data table on TanStack Table: sortable columns, a text filter, column visibility and CSV
// export of the raw values. Long tables render the first `pageSize` rows with a "show more" control.
import {
  flexRender, getCoreRowModel, getFilteredRowModel, getSortedRowModel, useReactTable,
  type ColumnDef, type SortingState, type VisibilityState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, Columns3, Download } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuTrigger, Input, Table, TableBody,
  TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/primitives";
import { download, toCsv } from "@/lib/csv";
import { cn } from "@/lib/utils";

export interface Col<T> {
  id: string;
  header: string;
  value: (row: T) => unknown;
  cell?: (row: T) => ReactNode;
  align?: "left" | "right";
  hidden?: boolean;
}

export function DataTable<T>({ rows, cols, name, toolbar, onRowClick, rowKey, maxHeight = 520, filter = true, empty, pageSize = 500 }: {
  rows: T[];
  cols: Col<T>[];
  name: string;
  toolbar?: ReactNode;
  onRowClick?: (row: T) => void;
  rowKey?: (row: T, i: number) => string;
  maxHeight?: number;
  filter?: boolean;
  empty?: ReactNode;
  pageSize?: number;
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");
  const [limit, setLimit] = useState(pageSize);
  const [visibility, setVisibility] = useState<VisibilityState>(Object.fromEntries(cols.filter((c) => c.hidden).map((c) => [c.id, false])));
  const columns: ColumnDef<T>[] = cols.map((c) => ({
    id: c.id, header: c.header, accessorFn: (r) => c.value(r),
    cell: (info) => (c.cell ? c.cell(info.row.original) : String(info.getValue() ?? "")),
    sortingFn: "basic", meta: { align: c.align },
  }));
  const table = useReactTable({
    data: rows, columns, state: { sorting, globalFilter, columnVisibility: visibility },
    onSortingChange: setSorting, onGlobalFilterChange: setGlobalFilter, onColumnVisibilityChange: setVisibility,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(), getFilteredRowModel: getFilteredRowModel(),
    globalFilterFn: "includesString",
  });
  const all = table.getRowModel().rows;
  const shown = all.slice(0, limit);
  const exportCsv = () => {
    const visible = cols.filter((c) => visibility[c.id] !== false);
    download(`${name}.csv`, toCsv(visible.map((c) => c.header), all.map((r) => visible.map((c) => c.value(r.original)))));
  };
  return (
    <div className="space-y-2" data-table={name}>
      <div className="flex flex-wrap items-center gap-2">
        {filter && <Input placeholder="Filter…" value={globalFilter} onChange={(e) => setGlobalFilter(e.target.value)} className="h-7 w-48 text-xs" aria-label={`filter ${name}`} />}
        {toolbar}
        <span className="ml-auto text-2xs text-muted-foreground"><span className="num">{all.length}</span> of <span className="num">{rows.length}</span></span>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-7 gap-1 text-xs"><Columns3 className="size-3.5" />Columns</Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {table.getAllLeafColumns().map((c) => (
              <DropdownMenuCheckboxItem key={c.id} checked={c.getIsVisible()} onCheckedChange={(v) => c.toggleVisibility(!!v)} onSelect={(e) => e.preventDefault()}>
                {String(c.columnDef.header)}
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <Button variant="outline" size="sm" className="h-7 gap-1 text-xs" onClick={exportCsv} data-action="csv"><Download className="size-3.5" />CSV</Button>
      </div>
      <div className="overflow-auto rounded-md border" style={{ maxHeight }}>
        <Table className="text-xs">
          <TableHeader className="sticky top-0 z-[1] bg-card">
            {table.getHeaderGroups().map((hg) => (
              <TableRow key={hg.id} className="hover:bg-transparent">
                {hg.headers.map((h) => {
                  const align = (h.column.columnDef.meta as { align?: string } | undefined)?.align;
                  const dir = h.column.getIsSorted();
                  return (
                    <TableHead key={h.id} className={cn("h-7 cursor-pointer px-2 text-2xs font-medium whitespace-nowrap text-muted-foreground select-none", align === "right" && "text-right")}
                      onClick={h.column.getToggleSortingHandler()} data-sort={dir || undefined}>
                      <span className="inline-flex items-center gap-0.5">
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {dir === "asc" ? <ArrowUp className="size-3" /> : dir === "desc" ? <ArrowDown className="size-3" /> : null}
                      </span>
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {shown.length === 0 ? (
              <TableRow><TableCell colSpan={cols.length} className="py-6 text-center text-muted-foreground">{empty ?? "No rows"}</TableCell></TableRow>
            ) : shown.map((r, i) => (
              <TableRow key={rowKey ? rowKey(r.original, i) : r.id} className={cn(onRowClick && "cursor-pointer")} onClick={() => onRowClick?.(r.original)}>
                {r.getVisibleCells().map((cell) => {
                  const align = (cell.column.columnDef.meta as { align?: string } | undefined)?.align;
                  return (
                    <TableCell key={cell.id} className={cn("px-2 py-1 whitespace-nowrap", align === "right" && "num text-right")}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {all.length > limit && (
        <div className="text-center">
          <Button variant="ghost" size="xs" onClick={() => setLimit((n) => n + pageSize * 4)}>Show more ({all.length - limit} hidden)</Button>
        </div>
      )}
    </div>
  );
}
