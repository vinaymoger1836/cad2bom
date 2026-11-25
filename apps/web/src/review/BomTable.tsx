import { useId, useMemo, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import type { BomDraft, BomLine, CadNode, MbomLine } from "@c2b/ts-types";
import { ConfidenceBadge, SourceBadge, StatusBadge } from "./Badges";
import { G } from "./glossary";
import { TreeGrid, type GridColumn } from "./TreeGrid";
import {
  fieldProv,
  fmtNum,
  hasAiValue,
  lineSource,
  MAKE_BUY_LABEL,
  numberOf,
  text,
  type Bands,
  type EditableField,
} from "./unwrap";

export type View = "ebom" | "mbom" | "purchased" | "excluded";

export interface Selection {
  lineId: string | null;
  /** CAD node ids to highlight when they differ from the line's own (excluded bodies). */
  nodeIds: string[] | null;
}

interface TreeNode<T> {
  id: string;
  item: T;
  children: TreeNode<T>[];
}

/** Builds the indented forest. Sibling order is the order of the list; orphans become roots. */
function forest<T extends { id: string }>(
  items: T[],
  parentOf: (t: T) => string | null | undefined,
  keep: (t: T) => boolean,
): TreeNode<T>[] {
  const nodes = new Map<string, TreeNode<T>>(items.map((t) => [t.id, { id: t.id, item: t, children: [] }]));
  const roots: TreeNode<T>[] = [];
  for (const t of items) {
    const node = nodes.get(t.id);
    if (!node) continue;
    const pid = parentOf(t);
    const parent = pid ? nodes.get(pid) : undefined;
    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  }
  const prune = (list: TreeNode<T>[], seen: Set<string>): TreeNode<T>[] => {
    const out: TreeNode<T>[] = [];
    for (const n of list) {
      if (seen.has(n.id)) continue; // a cycle must not loop the screen
      seen.add(n.id);
      const kids = prune(n.children, seen);
      if (keep(n.item) || kids.length > 0) out.push({ ...n, children: kids });
    }
    return out;
  };
  return prune(roots, new Set());
}

function UserMark({ line, field }: { line: BomLine; field: EditableField | "class" }) {
  if (fieldProv(line, field)?.source !== "user") return null;
  return (
    <span className="rv-you" title="You edited this value" role="img" aria-label="edited by you">
      {"\u{1F464}"}
    </span>
  );
}

interface Filters {
  q: string;
  low: boolean;
  ai: boolean;
  suggested: boolean;
  cls: string;
  makeBuy: string;
}

const NO_FILTERS: Filters = { q: "", low: false, ai: false, suggested: false, cls: "", makeBuy: "" };

function includesText(haystack: string[], q: string): boolean {
  const needle = q.trim().toLowerCase();
  return needle === "" || haystack.some((h) => h.toLowerCase().includes(needle));
}

export function BomTable({
  draft,
  volume,
  excluded,
  bands,
  selection,
  onSelect,
  onEdit,
  gridRef,
  tableRef,
}: {
  draft: BomDraft;
  volume: number | null;
  excluded: CadNode[];
  bands: Bands;
  selection: Selection;
  onSelect: (s: Selection) => void;
  onEdit: (lineId: string, field: EditableField, value: string | number) => void;
  gridRef: React.RefObject<HTMLDivElement | null>;
  tableRef: React.RefObject<HTMLElement | null>;
}) {
  const [view, setView] = useState<View>("ebom");
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const headId = useId();
  const ebom = draft.ebom ?? [];
  const mbom = draft.mbom ?? [];

  const suggestedLines = useMemo(
    () =>
      new Set(
        (draft.suggestions ?? [])
          .filter((s) => s.review === "suggested" && s.line_id)
          .map((s) => s.line_id ?? ""),
      ),
    [draft.suggestions],
  );
  const classes = useMemo(
    () => [...new Set(ebom.map((l) => text(l.class)).filter(Boolean))].sort(),
    [ebom],
  );
  const filtering =
    filters.q !== "" || filters.low || filters.ai || filters.suggested || filters.cls !== "" || filters.makeBuy !== "";

  const ebomRoots = useMemo(() => {
    const keep = (l: BomLine): boolean => {
      if (filters.low && (l.confidence ?? 1) >= bands.medium) return false;
      if (filters.ai && !hasAiValue(l)) return false;
      if (filters.suggested && !(l.review === "suggested" || suggestedLines.has(l.id))) return false;
      if (filters.cls && text(l.class) !== filters.cls) return false;
      if (filters.makeBuy && text(l.make_buy) !== filters.makeBuy) return false;
      return includesText([text(l.description), text(l.part_number), l.id], filters.q);
    };
    return forest(ebom, (l) => l.parent, filtering ? keep : () => true);
  }, [ebom, filters, filtering, bands.medium, suggestedLines]);

  const mbomRoots = useMemo(() => {
    const keep = (m: MbomLine): boolean => {
      if (filters.cls && m.class !== filters.cls) return false;
      if (filters.makeBuy && m.make_buy !== filters.makeBuy) return false;
      return includesText([m.description, m.part_number ?? "", m.id], filters.q);
    };
    const active = filters.q !== "" || filters.cls !== "" || filters.makeBuy !== "";
    return forest(mbom, (m) => m.parent, active ? keep : () => true);
  }, [mbom, filters.q, filters.cls, filters.makeBuy]);

  const purchased = useMemo(() => {
    const groups = new Map<
      string,
      { id: string; part: string; desc: string; cls: string; uom: string; qty: number; lines: string[]; refs: string[] }
    >();
    for (const m of mbom) {
      if (m.make_buy !== "buy") continue;
      const key = `${m.part_number ?? m.description}|${m.uom ?? "EA"}`;
      const g = groups.get(key) ?? {
        id: key,
        part: m.part_number ?? "",
        desc: m.description,
        cls: m.class,
        uom: m.uom ?? "EA",
        qty: 0,
        lines: [],
        refs: [],
      };
      g.qty += m.qty_total ?? 0;
      g.lines.push(m.id);
      g.refs.push(...(m.ebom_refs ?? []));
      groups.set(key, g);
    }
    return [...groups.values()]
      .filter((g) => includesText([g.part, g.desc], filters.q))
      .map((g) => ({ id: g.id, item: g, children: [] as never[] }));
  }, [mbom, filters.q]);

  const excludedRows = useMemo(
    () =>
      excluded
        .filter((n) => includesText([n.name, n.excluded ?? ""], filters.q))
        .map((n) => ({ id: n.id, item: n, children: [] as never[] })),
    [excluded, filters.q],
  );

  const selectLine = (id: string | null) => {
    onSelect({ lineId: id, nodeIds: null });
  };

  /* ---- columns ---- */
  const ebomColumns: GridColumn<TreeNode<BomLine>>[] = [
    {
      id: "description",
      header: "Description",
      width: "minmax(17rem, 3fr)",
      tree: true,
      cell: ({ item: l }) => (
        <span className="rv-ellipsis" title={text(l.description)}>
          <UserMark line={l} field="description" />
          {text(l.description) || l.id}
          {l.phantom ? <span className="rv-tag"><G term="phantom">phantom</G></span> : null}
          {l.buy_as_assembly ? <span className="rv-tag">bought as one</span> : null}
        </span>
      ),
      edit: {
        kind: "text",
        label: "Description",
        value: ({ item }) => text(item.description),
        onCommit: ({ item }, v) => {
          onEdit(item.id, "description", v);
        },
      },
    },
    {
      id: "part_number",
      header: "Part number",
      width: "minmax(9rem, 1.2fr)",
      cell: ({ item: l }) => (
        <span className="rv-ellipsis rv-mono">
          <UserMark line={l} field="part_number" />
          {text(l.part_number) || <span className="muted">{"—"}</span>}
        </span>
      ),
      edit: {
        kind: "text",
        label: "Part number",
        value: ({ item }) => text(item.part_number),
        onCommit: ({ item }, v) => {
          onEdit(item.id, "part_number", v);
        },
      },
    },
    {
      id: "qty",
      header: "Qty",
      width: "5.5rem",
      align: "end",
      cell: ({ item: l }) => (
        <span title={`${fmtNum(l.qty_total ?? null)} in the whole product`}>
          <UserMark line={l} field="qty" />
          {fmtNum(numberOf(l.qty_per_parent))} <span className="muted small">{l.uom ?? "EA"}</span>
        </span>
      ),
      edit: {
        kind: "number",
        label: "Quantity per parent",
        value: ({ item }) => fmtNum(numberOf(item.qty_per_parent)),
        onCommit: ({ item }, v) => {
          const n = Number(v);
          if (Number.isFinite(n) && n > 0) onEdit(item.id, "qty", n);
        },
      },
    },
    {
      id: "material",
      header: "Material",
      width: "minmax(8rem, 1fr)",
      cell: ({ item: l }) => (
        <span className="rv-ellipsis" title={text(l.material)}>
          <UserMark line={l} field="material" />
          {text(l.material) || <span className="muted">{"—"}</span>}
        </span>
      ),
      edit: {
        kind: "text",
        label: "Material code",
        value: ({ item }) => text(item.material),
        onCommit: ({ item }, v) => {
          onEdit(item.id, "material", v);
        },
      },
    },
    {
      id: "make_buy",
      header: <G term="make / buy">Make / buy</G>,
      width: "7.5rem",
      cell: ({ item: l }) => {
        const mb = text(l.make_buy);
        return (
          <span>
            <UserMark line={l} field="make_buy" />
            {MAKE_BUY_LABEL[mb] ?? (mb || <span className="muted">{"—"}</span>)}
          </span>
        );
      },
      edit: {
        kind: "select",
        options: ["make", "buy", "supplier_made"],
        label: "Make or buy",
        value: ({ item }) => text(item.make_buy) || "make",
        onCommit: ({ item }, v) => {
          onEdit(item.id, "make_buy", v);
        },
      },
    },
    {
      id: "class",
      header: "Class",
      width: "minmax(9rem, 1.2fr)",
      cell: ({ item: l }) => (
        <span className="rv-ellipsis rv-mono small" title={text(l.class)}>
          {text(l.class)}
        </span>
      ),
    },
    {
      id: "confidence",
      header: <G term="confidence">Confidence</G>,
      width: "6.5rem",
      cell: ({ item: l }) => <ConfidenceBadge value={l.confidence ?? 1} bands={bands} />,
    },
    {
      id: "source",
      header: <G term="provenance">Source</G>,
      width: "6.5rem",
      cell: ({ item: l }) => <SourceBadge kind={lineSource(l)} />,
    },
    {
      id: "flags",
      header: "Status",
      width: "8.5rem",
      cell: ({ item: l }) => {
        const open = (l.open_items ?? []).length;
        const pending = suggestedLines.has(l.id);
        return (
          <span className="rv-flags">
            <StatusBadge review={l.review} />
            {pending ? <span className="rv-badge rv-status-suggested">Suggestion</span> : null}
            {open > 0 ? (
              <span className="rv-open" title={(l.open_items ?? []).join("\n")}>
                {"⚑"} {open}
                <span className="visually-hidden"> open item{open === 1 ? "" : "s"}</span>
              </span>
            ) : null}
          </span>
        );
      },
    },
  ];

  const mbomColumns: GridColumn<TreeNode<MbomLine>>[] = [
    {
      id: "description",
      header: "Description",
      width: "minmax(17rem, 3fr)",
      tree: true,
      cell: ({ item: m }) => (
        <span className="rv-ellipsis" title={m.description}>
          {m.description}
        </span>
      ),
    },
    {
      id: "find_no",
      header: <G term="find number">Find no</G>,
      width: "5.5rem",
      align: "end",
      cell: ({ item: m }) => <span>{m.find_no ?? ""}</span>,
    },
    {
      id: "part_number",
      header: "Part number",
      width: "minmax(9rem, 1.2fr)",
      cell: ({ item: m }) => (
        <span className="rv-ellipsis rv-mono">{m.part_number ?? <span className="muted">{"—"}</span>}</span>
      ),
    },
    {
      id: "qty",
      header: "Qty",
      width: "5.5rem",
      align: "end",
      cell: ({ item: m }) => (
        <span title={`${fmtNum(m.qty_total ?? null)} in the whole product`}>
          {fmtNum(m.qty_per_parent ?? null)} <span className="muted small">{m.uom ?? "EA"}</span>
        </span>
      ),
    },
    {
      id: "make_buy",
      header: <G term="make / buy">Make / buy</G>,
      width: "7.5rem",
      cell: ({ item: m }) => <span>{MAKE_BUY_LABEL[m.make_buy ?? ""] ?? m.make_buy}</span>,
    },
    {
      id: "class",
      header: "Class",
      width: "minmax(9rem, 1.2fr)",
      cell: ({ item: m }) => <span className="rv-ellipsis rv-mono small">{m.class}</span>,
    },
    {
      id: "refs",
      header: "From EBOM",
      width: "7rem",
      cell: ({ item: m }) => <span className="rv-mono small">{(m.ebom_refs ?? []).join(", ")}</span>,
    },
  ];

  type PurchasedRow = (typeof purchased)[number];
  const purchasedColumns: GridColumn<PurchasedRow>[] = [
    {
      id: "description",
      header: "Description",
      width: "minmax(17rem, 3fr)",
      cell: ({ item: g }) => <span className="rv-ellipsis">{g.desc}</span>,
    },
    {
      id: "part_number",
      header: "Part number",
      width: "minmax(9rem, 1.2fr)",
      cell: ({ item: g }) => (
        <span className="rv-mono">{g.part || <span className="muted">{"—"}</span>}</span>
      ),
    },
    {
      id: "class",
      header: "Class",
      width: "minmax(9rem, 1.2fr)",
      cell: ({ item: g }) => <span className="rv-ellipsis rv-mono small">{g.cls}</span>,
    },
    {
      id: "per_build",
      header: "Per product",
      width: "7rem",
      align: "end",
      cell: ({ item: g }) => (
        <span>
          {fmtNum(g.qty)} <span className="muted small">{g.uom}</span>
        </span>
      ),
    },
    {
      id: "per_year",
      header: "Per year",
      width: "7rem",
      align: "end",
      cell: ({ item: g }) =>
        volume ? (
          <span>{fmtNum(g.qty * volume)}</span>
        ) : (
          <span className="muted" title="Enter an annual volume when you create the BoM to see this">
            {"—"}
          </span>
        ),
    },
  ];

  type ExcludedRow = (typeof excludedRows)[number];
  const excludedColumns: GridColumn<ExcludedRow>[] = [
    {
      id: "name",
      header: "CAD body",
      width: "minmax(14rem, 2fr)",
      cell: ({ item: n }) => <span className="rv-ellipsis">{n.name}</span>,
    },
    {
      id: "id",
      header: "Node",
      width: "6rem",
      cell: ({ item: n }) => <span className="rv-mono small">{n.id}</span>,
    },
    {
      id: "reason",
      header: "Why left out",
      width: "minmax(14rem, 3fr)",
      cell: ({ item: n }) => <span className="rv-ellipsis">{n.excluded}</span>,
    },
    {
      id: "instances",
      header: "Instances",
      width: "6rem",
      align: "end",
      cell: ({ item: n }) => <span>{n.instance_count ?? 1}</span>,
    },
  ];

  const counts = {
    ebom: ebom.length,
    mbom: mbom.length,
    purchased: purchased.length,
    excluded: excluded.length,
  };

  const mbomSelected =
    mbom.find((m) => selection.lineId !== null && (m.ebom_refs ?? []).includes(selection.lineId))?.id ?? null;
  const purchasedSelected =
    purchased.find((g) => selection.lineId !== null && g.item.refs.includes(selection.lineId))?.id ?? null;

  return (
    <section ref={tableRef} tabIndex={-1} className="rv-panel rv-table" aria-labelledby={headId}>
      <header className="rv-panel-head">
        <h2 id={headId} className="visually-hidden">
          Bill of materials
        </h2>
        <Tabs.Root
          value={view}
          onValueChange={(v) => {
            setView(v as View);
          }}
        >
          <Tabs.List className="rv-tabs" aria-label="BoM view">
            <Tabs.Trigger value="ebom" className="rv-tab">
              <G term="EBOM">EBOM</G> <span className="rv-count">{counts.ebom}</span>
            </Tabs.Trigger>
            <Tabs.Trigger value="mbom" className="rv-tab">
              <G term="MBOM">MBOM</G> <span className="rv-count">{counts.mbom}</span>
            </Tabs.Trigger>
            <Tabs.Trigger value="purchased" className="rv-tab">
              <G term="purchased items">Purchased items</G> <span className="rv-count">{counts.purchased}</span>
            </Tabs.Trigger>
            <Tabs.Trigger value="excluded" className="rv-tab">
              <G term="excluded">Excluded</G> <span className="rv-count">{counts.excluded}</span>
            </Tabs.Trigger>
          </Tabs.List>
        </Tabs.Root>

        <div className="rv-filters" role="group" aria-label="Filters">
          <label className="rv-search">
            <span className="visually-hidden">Search description, part number or line</span>
            <input
              type="search"
              placeholder="Search…"
              value={filters.q}
              onChange={(e) => {
                setFilters({ ...filters, q: e.target.value });
              }}
            />
          </label>
          {view === "ebom" ? (
            <>
              <label className="rv-check">
                <input
                  type="checkbox"
                  checked={filters.low}
                  onChange={(e) => {
                    setFilters({ ...filters, low: e.target.checked });
                  }}
                />
                Low confidence
              </label>
              <label className="rv-check">
                <input
                  type="checkbox"
                  checked={filters.ai}
                  onChange={(e) => {
                    setFilters({ ...filters, ai: e.target.checked });
                  }}
                />
                AI-sourced
              </label>
              <label className="rv-check">
                <input
                  type="checkbox"
                  checked={filters.suggested}
                  onChange={(e) => {
                    setFilters({ ...filters, suggested: e.target.checked });
                  }}
                />
                Suggested
              </label>
            </>
          ) : null}
          {view === "ebom" || view === "mbom" ? (
            <>
              <label className="rv-select">
                <span className="visually-hidden">Filter by class</span>
                <select
                  value={filters.cls}
                  onChange={(e) => {
                    setFilters({ ...filters, cls: e.target.value });
                  }}
                >
                  <option value="">All classes</option>
                  {classes.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </label>
              <label className="rv-select">
                <span className="visually-hidden">Filter by make or buy</span>
                <select
                  value={filters.makeBuy}
                  onChange={(e) => {
                    setFilters({ ...filters, makeBuy: e.target.value });
                  }}
                >
                  <option value="">Make and buy</option>
                  <option value="make">Make</option>
                  <option value="buy">Buy</option>
                  <option value="supplier_made">Supplier made</option>
                </select>
              </label>
            </>
          ) : null}
          {filtering ? (
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => {
                setFilters(NO_FILTERS);
              }}
            >
              Clear filters
            </button>
          ) : null}
        </div>
      </header>

      <div className="rv-panel-body rv-table-body">
        {view === "ebom" ? (
          <TreeGrid
            key="ebom"
            label="Engineering BoM, indented"
            columns={ebomColumns}
            roots={ebomRoots}
            getId={(n) => n.id}
            getChildren={(n) => n.children}
            selectedId={selection.lineId}
            onSelect={selectLine}
            empty={<p className="rv-empty">No lines match these filters.</p>}
            gridRef={gridRef}
          />
        ) : null}
        {view === "mbom" ? (
          <TreeGrid
            key="mbom"
            label="Manufacturing BoM, indented"
            columns={mbomColumns}
            roots={mbomRoots}
            getId={(n) => n.id}
            getChildren={(n) => n.children}
            selectedId={mbomSelected}
            onSelect={(id) => {
              selectLine(mbom.find((m) => m.id === id)?.ebom_refs?.[0] ?? null);
            }}
            empty={<p className="rv-empty">No manufacturing lines yet.</p>}
            gridRef={gridRef}
          />
        ) : null}
        {view === "purchased" ? (
          <TreeGrid
            key="purchased"
            label="Purchased items rolled up by part"
            columns={purchasedColumns}
            roots={purchased}
            getId={(n) => n.id}
            getChildren={(n) => n.children}
            selectedId={purchasedSelected}
            onSelect={(id) => {
              selectLine(purchased.find((g) => g.id === id)?.item.refs[0] ?? null);
            }}
            empty={<p className="rv-empty">Nothing is marked as bought yet.</p>}
            gridRef={gridRef}
          />
        ) : null}
        {view === "excluded" ? (
          <TreeGrid
            key="excluded"
            label="CAD bodies left out of the BoM"
            columns={excludedColumns}
            roots={excludedRows}
            getId={(n) => n.id}
            getChildren={(n) => n.children}
            selectedId={selection.nodeIds?.[0] ?? null}
            onSelect={(id) => {
              onSelect({ lineId: null, nodeIds: [id] });
            }}
            empty={<p className="rv-empty">No CAD bodies were left out.</p>}
            gridRef={gridRef}
          />
        ) : null}
      </div>
    </section>
  );
}
