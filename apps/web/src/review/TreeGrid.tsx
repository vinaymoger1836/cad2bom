import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import {
  getCoreRowModel,
  getExpandedRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";

const ROW_HEIGHT = 40;

export interface CellEdit<T> {
  kind: "text" | "number" | "select";
  options?: string[];
  /** Name of the field for the screen reader, e.g. "Description". */
  label: string;
  value: (row: T) => string;
  onCommit: (row: T, value: string) => void;
}

export interface GridColumn<T> {
  id: string;
  header: ReactNode;
  /** CSS grid track, e.g. "minmax(16rem, 3fr)". */
  width: string;
  cell: (row: T) => ReactNode;
  /** The first column carries the indent and the expand button. */
  tree?: boolean;
  edit?: CellEdit<T>;
  align?: "end";
}

interface Props<T> {
  label: string;
  columns: GridColumn<T>[];
  roots: T[];
  getId: (row: T) => string;
  getChildren: (row: T) => T[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  empty: ReactNode;
  /** Lets the screen move focus into the grid (Alt+T). */
  gridRef?: React.RefObject<HTMLDivElement | null>;
}

interface Active {
  r: number;
  c: number;
}

/**
 * A virtualised indented grid with the WAI-ARIA treegrid keyboard model:
 * arrows move between cells, Left/Right on the first cell collapse and expand, Enter edits,
 * Escape cancels, Space selects, Home/End and Ctrl+Home/End jump.
 */
export function TreeGrid<T>({
  label,
  columns,
  roots,
  getId,
  getChildren,
  selectedId,
  onSelect,
  empty,
  gridRef,
}: Props<T>) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [active, setActive] = useState<Active>({ r: 0, c: 0 });
  const [editing, setEditing] = useState<{ rowId: string; colId: string } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pendingFocus = useRef(false);
  const helpId = useId();

  const expanded = useMemo(() => {
    const out: Record<string, boolean> = {};
    const walk = (nodes: T[]) => {
      for (const n of nodes) {
        const kids = getChildren(n);
        if (kids.length > 0) {
          if (!collapsed.has(getId(n))) out[getId(n)] = true;
          walk(kids);
        }
      }
    };
    walk(roots);
    return out;
  }, [roots, collapsed, getChildren, getId]);

  const defs = useMemo<ColumnDef<T>[]>(() => columns.map((c) => ({ id: c.id })), [columns]);

  // TanStack Table owns the row model (tree flattening and expansion); this grid owns the keys.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable<T>({
    data: roots,
    columns: defs,
    getSubRows: (r) => getChildren(r),
    getRowId: (r) => getId(r),
    getRowCanExpand: (row) => getChildren(row.original).length > 0,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    state: { expanded },
    onExpandedChange: () => {
      /* expansion is derived from `collapsed` above */
    },
  });
  const rows = table.getRowModel().rows;

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10,
  });

  const template = columns.map((c) => c.width).join(" ");
  const rowCount = rows.length;
  const clamp = useCallback(
    (a: Active): Active => ({
      r: Math.max(0, Math.min(rowCount - 1, a.r)),
      c: Math.max(0, Math.min(columns.length - 1, a.c)),
    }),
    [rowCount, columns.length],
  );

  const activeRow = Math.min(active.r, Math.max(0, rowCount - 1));
  const activeCol = Math.min(active.c, Math.max(0, columns.length - 1));

  function move(next: Active) {
    setActive(clamp(next));
    pendingFocus.current = true;
    virtualizer.scrollToIndex(clamp(next).r, { align: "auto" });
  }

  // After a keyboard move, focus the target cell once it has been rendered.
  useEffect(() => {
    if (!pendingFocus.current || editing) return;
    const el = scrollRef.current?.querySelector<HTMLElement>(
      `[data-r="${activeRow.toString()}"][data-c="${activeCol.toString()}"]`,
    );
    if (el) {
      el.focus({ preventScroll: true });
      pendingFocus.current = false;
    }
  });

  function toggle(id: string, open: boolean) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (open) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (editing || rowCount === 0) return;
    const row = rows[activeRow];
    if (!row) return;
    const id = row.id;
    const canExpand = row.getCanExpand();
    const isOpen = canExpand && !collapsed.has(id);
    const col = columns[activeCol];
    let handled = true;
    switch (e.key) {
      case "ArrowDown":
        move({ r: activeRow + 1, c: activeCol });
        break;
      case "ArrowUp":
        move({ r: activeRow - 1, c: activeCol });
        break;
      case "ArrowRight":
        if (activeCol === 0 && canExpand && !isOpen) toggle(id, true);
        else move({ r: activeRow, c: activeCol + 1 });
        break;
      case "ArrowLeft":
        if (activeCol === 0) {
          if (isOpen) toggle(id, false);
          else {
            const parent = row.getParentRow();
            const at = parent ? rows.findIndex((r) => r.id === parent.id) : -1;
            if (at >= 0) move({ r: at, c: 0 });
          }
        } else move({ r: activeRow, c: activeCol - 1 });
        break;
      case "Home":
        move(e.ctrlKey ? { r: 0, c: 0 } : { r: activeRow, c: 0 });
        break;
      case "End":
        move(e.ctrlKey ? { r: rowCount - 1, c: columns.length - 1 } : { r: activeRow, c: columns.length - 1 });
        break;
      case "PageDown":
        move({ r: activeRow + 10, c: activeCol });
        break;
      case "PageUp":
        move({ r: activeRow - 10, c: activeCol });
        break;
      case "Enter":
        if (col?.edit) setEditing({ rowId: id, colId: col.id });
        else onSelect(id);
        break;
      case " ":
        onSelect(id);
        break;
      default:
        handled = false;
    }
    if (handled) e.preventDefault();
  }

  function stopEditing() {
    setEditing(null);
    pendingFocus.current = true;
  }

  return (
    <div className="rv-grid-wrap">
      <p id={helpId} className="visually-hidden">
        Use the arrow keys to move between cells. Left and Right on the first cell collapse and
        expand a group. Press Enter to edit a cell, Escape to cancel, Space to select a line.
      </p>
      <div
        ref={(el) => {
          scrollRef.current = el;
          if (gridRef) gridRef.current = el;
        }}
        className="rv-grid-scroll"
        role="treegrid"
        aria-label={label}
        aria-describedby={helpId}
        aria-rowcount={rowCount + 1}
        aria-colcount={columns.length}
        onKeyDown={onKeyDown}
      >
        <div className="rv-grid-inner" style={{ minWidth: "max-content" }}>
          <div role="row" aria-rowindex={1} className="rv-grid-head" style={{ gridTemplateColumns: template }}>
            {columns.map((c, ci) => (
              <div
                key={c.id}
                role="columnheader"
                aria-colindex={ci + 1}
                className={c.align === "end" ? "rv-cell rv-end" : "rv-cell"}
              >
                {c.header}
              </div>
            ))}
          </div>

          {rowCount === 0 ? (
            <div className="rv-grid-empty">{empty}</div>
          ) : (
            <div className="rv-grid-body" style={{ height: virtualizer.getTotalSize() }}>
              {virtualizer.getVirtualItems().map((vi) => {
                const row = rows[vi.index];
                if (!row) return null;
                const id = row.id;
                const canExpand = row.getCanExpand();
                const isOpen = canExpand && !collapsed.has(id);
                const selected = id === selectedId;
                return (
                  <div
                    key={id}
                    role="row"
                    aria-rowindex={vi.index + 2}
                    aria-level={row.depth + 1}
                    aria-selected={selected}
                    aria-expanded={canExpand ? isOpen : undefined}
                    className={selected ? "rv-row rv-row-selected" : "rv-row"}
                    style={{
                      gridTemplateColumns: template,
                      height: ROW_HEIGHT,
                      transform: `translateY(${vi.start.toString()}px)`,
                    }}
                    onClick={() => {
                      onSelect(id);
                    }}
                  >
                    {columns.map((c, ci) => {
                      const isActive = vi.index === activeRow && ci === activeCol;
                      const isEditing = editing?.rowId === id && editing.colId === c.id;
                      return (
                        <div
                          key={c.id}
                          role="gridcell"
                          aria-colindex={ci + 1}
                          data-r={vi.index}
                          data-c={ci}
                          tabIndex={isActive && !isEditing ? 0 : -1}
                          className={c.align === "end" ? "rv-cell rv-end" : "rv-cell"}
                          onFocus={() => {
                            if (!isActive) setActive({ r: vi.index, c: ci });
                          }}
                          onClick={() => {
                            setActive({ r: vi.index, c: ci });
                          }}
                          onDoubleClick={() => {
                            if (c.edit) setEditing({ rowId: id, colId: c.id });
                          }}
                        >
                          {c.tree ? (
                            <span className="rv-tree" style={{ paddingInlineStart: `${(row.depth * 1.1).toString()}rem` }}>
                              {canExpand ? (
                                <button
                                  type="button"
                                  tabIndex={-1}
                                  className="rv-toggle"
                                  aria-label={`${isOpen ? "Collapse" : "Expand"} group`}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    toggle(id, !isOpen);
                                  }}
                                >
                                  <span aria-hidden="true" className={isOpen ? "rv-caret rv-caret-open" : "rv-caret"}>
                                    {"▸"}
                                  </span>
                                </button>
                              ) : (
                                <span className="rv-toggle-spacer" aria-hidden="true" />
                              )}
                              {isEditing && c.edit ? (
                                <CellEditor edit={c.edit} row={row.original} onDone={stopEditing} />
                              ) : (
                                c.cell(row.original)
                              )}
                            </span>
                          ) : isEditing && c.edit ? (
                            <CellEditor edit={c.edit} row={row.original} onDone={stopEditing} />
                          ) : (
                            c.cell(row.original)
                          )}
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function CellEditor<T>({
  edit,
  row,
  onDone,
}: {
  edit: CellEdit<T>;
  row: T;
  onDone: () => void;
}) {
  const initial = edit.value(row);
  const [draft, setDraft] = useState(initial);
  const ref = useRef<HTMLInputElement | HTMLSelectElement>(null);
  useEffect(() => {
    const el = ref.current;
    el?.focus();
    if (el instanceof HTMLInputElement) el.select();
  }, []);

  function commit() {
    if (draft.trim() !== initial.trim() && draft.trim() !== "") edit.onCommit(row, draft.trim());
    onDone();
  }

  function keys(e: KeyboardEvent) {
    e.stopPropagation(); // the grid's own keys must not fire while typing
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onDone();
    }
  }

  if (edit.kind === "select") {
    return (
      <select
        ref={ref}
        className="rv-edit"
        aria-label={edit.label}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
        }}
        onKeyDown={keys}
        onBlur={commit}
      >
        {(edit.options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      ref={ref}
      className="rv-edit"
      type={edit.kind === "number" ? "number" : "text"}
      step={edit.kind === "number" ? "any" : undefined}
      aria-label={edit.label}
      value={draft}
      onChange={(e) => {
        setDraft(e.target.value);
      }}
      onKeyDown={keys}
      onBlur={commit}
    />
  );
}
