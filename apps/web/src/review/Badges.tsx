import { bandOf, SOURCE_LABEL, type Bands, type SourceKind } from "./unwrap";

const BAND_TEXT = { high: "High", medium: "Medium", low: "Low" } as const;

/** Confidence as colour plus text (never colour alone). */
export function ConfidenceBadge({
  value,
  bands,
}: {
  value: number;
  bands: Bands;
}) {
  const band = bandOf(value, bands);
  const pct = Math.round(value * 100).toString();
  return (
    <span className={`rv-badge rv-conf-${band}`} title={`Confidence ${pct}%`}>
      <span className="rv-badge-dot" aria-hidden="true" />
      {BAND_TEXT[band]}
      <span className="visually-hidden"> confidence, {pct} percent</span>
    </span>
  );
}

const SOURCE_ICON: Record<SourceKind, string> = {
  you: "\u{1F464}",
  cad: "▦",
  file: "▤",
  standard: "⚙",
  rule: "☰",
  ai: "✦",
};

export function SourceBadge({ kind }: { kind: SourceKind }) {
  return (
    <span className={`rv-badge rv-source-${kind}`}>
      <span aria-hidden="true">{SOURCE_ICON[kind]}</span> {SOURCE_LABEL[kind]}
    </span>
  );
}

export function StatusBadge({ review }: { review: string | undefined }) {
  if (!review || review === "derived") return null;
  const label: Record<string, string> = {
    given: "Given",
    accepted: "Accepted",
    edited: "Edited",
    flagged: "Flagged",
    suggested: "Suggested",
  };
  return <span className={`rv-badge rv-status-${review}`}>{label[review] ?? review}</span>;
}
