import { useEffect, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import type { BomLine } from "@c2b/ts-types";
import { getLineHistory, type HistoryEntry } from "../lib/api";
import { ConfidenceBadge, SourceBadge, StatusBadge } from "./Badges";
import { G } from "./glossary";
import {
  fieldProv,
  fmtNum,
  lineSource,
  MAKE_BUY_LABEL,
  numberOf,
  prov,
  sourceKind,
  text,
  type Bands,
  type ProvInfo,
} from "./unwrap";

interface FieldRow {
  label: string;
  value: string;
  info: ProvInfo | null;
}

function fieldRows(line: BomLine): FieldRow[] {
  const mb = text(line.make_buy);
  const d = line.designation;
  return [
    { label: "Description", value: text(line.description), info: prov(line.description) },
    { label: "Part number", value: text(line.part_number), info: prov(line.part_number) },
    { label: "Class", value: text(line.class), info: prov(line.class) },
    {
      label: "Quantity per parent",
      value: `${fmtNum(numberOf(line.qty_per_parent))} ${line.uom ?? "EA"}`,
      info: fieldProv(line, "qty"),
    },
    { label: "Quantity in product", value: fmtNum(line.qty_total ?? null), info: null },
    { label: "Material", value: text(line.material), info: prov(line.material) },
    { label: "Finish", value: text(line.finish), info: prov(line.finish) },
    {
      label: "Mass",
      value: numberOf(line.mass_g) === null ? "" : `${fmtNum(numberOf(line.mass_g), 1)} g`,
      info: prov(line.mass_g),
    },
    { label: "Make / buy", value: MAKE_BUY_LABEL[mb] ?? mb, info: prov(line.make_buy) },
    {
      label: "Standard",
      value: d ? (d.key ?? `${d.standard} ${d.size}`.trim()) : "",
      info: null,
    },
    { label: "Manufacturer", value: line.manufacturer ?? "", info: null },
    { label: "Manufacturer part no", value: line.mpn ?? "", info: null },
    { label: "Ref designators", value: (line.refdes ?? []).join(", "), info: null },
    { label: "CAD nodes", value: (line.cad_nodes ?? []).join(", "), info: null },
  ];
}

function show(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

function fieldName(field: string): string {
  return field.replace(/_/g, " ");
}

function History({ jobId, token, lineId, revision }: { jobId: string; token: string; lineId: string; revision: number }) {
  const [rows, setRows] = useState<HistoryEntry[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    getLineHistory(jobId, token, lineId)
      .then((r) => {
        if (!cancelled) {
          setRows(r);
          setFailed(false);
        }
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, token, lineId, revision]);

  if (failed) return <p className="rv-empty">The history could not be loaded.</p>;
  if (rows === null) return <p className="rv-empty">Loading{"…"}</p>;
  if (rows.length === 0) {
    return <p className="rv-empty">No one has changed this line yet. Your edits and answers appear here.</p>;
  }
  return (
    <ol className="rv-history">
      {rows.map((h) => (
        <li key={h.id}>
          <div className="small muted">
            <time dateTime={h.at}>{new Date(h.at).toLocaleString()}</time> {"·"} {h.actor}
          </div>
          <div>
            <strong>{fieldName(h.field)}</strong>: <del>{show(h.before)}</del> {"→"} <ins>{show(h.after)}</ins>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function LineDetails({
  jobId,
  token,
  line,
  bands,
  revision,
}: {
  jobId: string;
  token: string;
  line: BomLine | null;
  bands: Bands;
  revision: number;
}) {
  if (!line) {
    return (
      <section className="rv-panel rv-details" aria-label="Line details">
        <div className="rv-panel-body">
          <p className="rv-empty">
            Select a line in the table to see where every value came from, which rules produced it, and its history.
          </p>
        </div>
      </section>
    );
  }
  const rows = fieldRows(line);
  const rules = [...new Set([...(line.rule_ids ?? []), ...rows.flatMap((r) => r.info?.rules ?? [])])];
  const explanation = line.explanation ?? [];
  return (
    <section className="rv-panel rv-details" aria-label={`Details of line ${line.id}`}>
      <header className="rv-panel-head">
        <h2 className="rv-details-title">
          <span className="rv-mono">{line.id}</span> {text(line.description)}
        </h2>
        <div className="rv-flags">
          <SourceBadge kind={lineSource(line)} />
          <ConfidenceBadge value={line.confidence ?? 1} bands={bands} />
          <StatusBadge review={line.review} />
        </div>
      </header>
      <Tabs.Root defaultValue="fields" className="rv-details-tabs" key={line.id}>
        <Tabs.List className="rv-tabs" aria-label="Line details">
          <Tabs.Trigger value="fields" className="rv-tab">
            Fields
          </Tabs.Trigger>
          <Tabs.Trigger value="explain" className="rv-tab">
            Explain this line
          </Tabs.Trigger>
          <Tabs.Trigger value="history" className="rv-tab">
            History
          </Tabs.Trigger>
        </Tabs.List>
        <div className="rv-panel-body">
          <Tabs.Content value="fields">
            <table className="rv-fields">
              <caption className="visually-hidden">Every field of the line with where it came from</caption>
              <thead>
                <tr>
                  <th scope="col">Field</th>
                  <th scope="col">Value</th>
                  <th scope="col">
                    <G term="provenance">Source</G>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows
                  .filter((r) => r.value !== "" || r.info)
                  .map((r) => (
                    <tr key={r.label}>
                      <th scope="row">{r.label}</th>
                      <td>{r.value || "—"}</td>
                      <td>
                        {r.info ? (
                          <span className="rv-source-cell">
                            <SourceBadge kind={sourceKind(r.info.source)} />{" "}
                            <span className="small muted">{Math.round(r.info.confidence * 100)}%</span>
                            {r.info.rules.length > 0 ? (
                              <span className="rv-mono small"> {r.info.rules.join(", ")}</span>
                            ) : null}
                            {r.info.note ? <span className="small muted"> {r.info.note}</span> : null}
                          </span>
                        ) : (
                          <span className="muted small">from the line</span>
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </Tabs.Content>
          <Tabs.Content value="explain">
            {explanation.length > 0 ? (
              <ul className="rv-explain">
                {explanation.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            ) : (
              <p className="rv-empty">No written explanation was recorded for this line.</p>
            )}
            <h3 className="rv-subhead">
              <G term="rule id">Rule ids</G>
            </h3>
            {rules.length > 0 ? (
              <ul className="rv-chips">
                {rules.map((r) => (
                  <li key={r} className="rv-chip rv-mono">
                    {r}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="small muted">None recorded.</p>
            )}
            {(line.open_items ?? []).length > 0 ? (
              <>
                <h3 className="rv-subhead">Open items</h3>
                <ul className="rv-explain">
                  {(line.open_items ?? []).map((o) => (
                    <li key={o}>{o}</li>
                  ))}
                </ul>
              </>
            ) : null}
            {line.raw ? (
              <details className="rv-raw">
                <summary>Your original row (never changed)</summary>
                <pre>{JSON.stringify(line.raw, null, 2)}</pre>
              </details>
            ) : null}
          </Tabs.Content>
          <Tabs.Content value="history">
            <History jobId={jobId} token={token} lineId={line.id} revision={revision} />
          </Tabs.Content>
        </div>
      </Tabs.Root>
    </section>
  );
}
