import type { BomLine } from "@c2b/ts-types";

/** Provenance of one value, when the server sent it (plain values have none). */
export interface ProvInfo {
  value: unknown;
  source: string;
  confidence: number;
  rules: string[];
  note: string | null;
  ref: string | null;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function prov(x: unknown): ProvInfo | null {
  if (!isRecord(x) || !("value" in x) || typeof x.source !== "string") return null;
  return {
    value: x.value,
    source: x.source,
    confidence: typeof x.confidence === "number" ? x.confidence : 1,
    rules: Array.isArray(x.rules) ? x.rules.filter((r): r is string => typeof r === "string") : [],
    note: typeof x.note === "string" ? x.note : null,
    ref: typeof x.ref === "string" ? x.ref : null,
  };
}

/** The plain value of `Prov | plain`. */
export function val(x: unknown): unknown {
  const p = prov(x);
  if (p) return p.value;
  if (isRecord(x) && "value" in x) return x.value;
  return x;
}

export function text(x: unknown): string {
  const v = val(x);
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return "";
}

export function numberOf(x: unknown): number | null {
  const v = val(x);
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function fmtNum(n: number | null, digits = 2): string {
  if (n === null) return "";
  return Number.isInteger(n) ? n.toString() : n.toFixed(digits).replace(/\.?0+$/, "");
}

/** Fields of a line the person can change in the table (what the API accepts). */
export type EditableField =
  | "description"
  | "part_number"
  | "qty"
  | "material"
  | "finish"
  | "make_buy";

export const MAKE_BUY_LABEL: Record<string, string> = {
  make: "Make",
  buy: "Buy",
  supplier_made: "Supplier made",
};

export function fieldProv(line: BomLine, field: EditableField | "class" | "mass_g"): ProvInfo | null {
  switch (field) {
    case "description":
      return prov(line.description);
    case "part_number":
      return prov(line.part_number);
    case "qty":
      return prov(line.qty_per_parent);
    case "material":
      return prov(line.material);
    case "finish":
      return prov(line.finish);
    case "make_buy":
      return prov(line.make_buy);
    case "class":
      return prov(line.class);
    case "mass_g":
      return prov(line.mass_g);
  }
}

export type Band = "high" | "medium" | "low";

export interface Bands {
  high: number;
  medium: number;
}

export const DEFAULT_BANDS: Bands = { high: 0.85, medium: 0.6 };

export function bandOf(confidence: number, bands: Bands): Band {
  if (confidence >= bands.high) return "high";
  if (confidence >= bands.medium) return "medium";
  return "low";
}

/** Who produced the line, in the words used in the table badges. */
export type SourceKind = "you" | "cad" | "file" | "standard" | "rule" | "ai";

export const SOURCE_LABEL: Record<SourceKind, string> = {
  you: "You",
  cad: "CAD",
  file: "File",
  standard: "Standard",
  rule: "Rule",
  ai: "AI",
};

export function sourceKind(source: string): SourceKind {
  switch (source) {
    case "user":
      return "you";
    case "cad_meta":
    case "cad_geom":
      return "cad";
    case "bom_file":
      return "file";
    case "std_table":
      return "standard";
    case "llm":
    case "vision":
      return "ai";
    default:
      return "rule";
  }
}

const EDITED_FIELDS: (EditableField | "class")[] = [
  "description",
  "part_number",
  "qty",
  "material",
  "finish",
  "make_buy",
  "class",
];

/** True when the person changed anything on this line. */
export function isEditedByUser(line: BomLine): boolean {
  return EDITED_FIELDS.some((f) => fieldProv(line, f)?.source === "user");
}

/** The source shown for the whole line: you if you touched it, otherwise how its class was found. */
export function lineSource(line: BomLine): SourceKind {
  if (isEditedByUser(line)) return "you";
  if (line.origin === "user_bom") return "file";
  if (line.origin === "llm") return "ai";
  const cls = prov(line.class);
  if (cls) return sourceKind(cls.source);
  return sourceKind(line.source ?? "cad_meta");
}

export function hasAiValue(line: BomLine): boolean {
  if (line.origin === "llm") return true;
  const fields: (EditableField | "class" | "mass_g")[] = [...EDITED_FIELDS, "mass_g"];
  return fields.some((f) => {
    const s = fieldProv(line, f)?.source;
    return s === "llm" || s === "vision";
  });
}

/** Depth-first order that keeps list order among siblings (the same order the exports use). */
export function dfsOrder<T extends { id: string }>(
  items: T[],
  parentOf: (t: T) => string | null | undefined,
): { item: T; depth: number }[] {
  const known = new Set(items.map((i) => i.id));
  const kids = new Map<string | null, T[]>();
  for (const it of items) {
    const p = parentOf(it);
    const key = p && known.has(p) ? p : null;
    kids.set(key, [...(kids.get(key) ?? []), it]);
  }
  const out: { item: T; depth: number }[] = [];
  const seen = new Set<string>();
  const walk = (it: T, depth: number) => {
    if (seen.has(it.id)) return;
    seen.add(it.id);
    out.push({ item: it, depth });
    for (const c of kids.get(it.id) ?? []) walk(c, depth + 1);
  };
  for (const r of kids.get(null) ?? []) walk(r, 0);
  for (const it of items) walk(it, 0);
  return out;
}
