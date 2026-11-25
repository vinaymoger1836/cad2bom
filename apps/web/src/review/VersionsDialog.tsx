import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import type { BomVersion } from "@c2b/ts-types";
import {
  ApiError,
  downloadExport,
  getVersion,
  type ExportFormat,
  type VersionSummary,
} from "../lib/api";
import { G } from "./glossary";
import { dfsOrder, fmtNum, numberOf, text } from "./unwrap";

const LABEL = "Generated BoM — review before release";

/** The parts list as the PDF shows it, drawn with the theme colours so it reads in both themes. */
export function PartsListPreview({ version }: { version: BomVersion }) {
  const rows = dfsOrder(version.ebom ?? [], (l) => l.parent);
  const items = version.open_items ?? [];
  return (
    <article className="rv-sheet" aria-label={`Parts list preview, version ${version.version?.toString() ?? ""}`}>
      <header className="rv-sheet-head">
        <h3>Parts list {"–"} {version.product?.name ?? "Product"}</h3>
        <dl>
          <div>
            <dt>Version</dt>
            <dd>v{version.version ?? 1}</dd>
          </div>
          <div>
            <dt>Date</dt>
            <dd>{(version.created_at ?? "").slice(0, 10)}</dd>
          </div>
          <div>
            <dt>Library</dt>
            <dd>{version.library_version}</dd>
          </div>
        </dl>
      </header>
      <div className="rv-sheet-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Find</th>
              <th scope="col">Part number</th>
              <th scope="col">Rev</th>
              <th scope="col">Description</th>
              <th scope="col">Qty</th>
              <th scope="col">UoM</th>
              <th scope="col">Material</th>
              <th scope="col">Make / buy</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ item: l, depth }) => (
              <tr key={l.id}>
                <td>{l.find_no}</td>
                <td className="rv-mono">{text(l.part_number)}</td>
                <td>{l.revision}</td>
                <td style={{ paddingInlineStart: `${(0.5 + depth * 1).toString()}rem` }}>{text(l.description)}</td>
                <td>{fmtNum(numberOf(l.qty_per_parent))}</td>
                <td>{l.uom}</td>
                <td>{text(l.material)}</td>
                <td>{text(l.make_buy)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h4>Appendix {"–"} open items</h4>
      {items.length > 0 ? (
        <ul>
          {items.map((i) => (
            <li key={i}>{i}</li>
          ))}
        </ul>
      ) : (
        <p className="small muted">No open items.</p>
      )}
      <footer className="rv-sheet-foot">{LABEL}</footer>
    </article>
  );
}

const DOWNLOADS: { fmt: ExportFormat; part?: "ebom" | "mbom"; label: string; hint: string }[] = [
  { fmt: "xlsx", label: "Excel workbook", hint: "EBOM, MBOM, purchased items, open items, excluded, explanations" },
  { fmt: "csv", part: "ebom", label: "CSV (EBOM)", hint: "One row per line, for ERP import" },
  { fmt: "csv", part: "mbom", label: "CSV (MBOM)", hint: "The build view, one row per line" },
  { fmt: "json", label: "JSON", hint: "The full frozen version" },
  { fmt: "schema", label: "JSON schema", hint: "Describes the JSON file" },
  { fmt: "pdf", label: "PDF parts list", hint: "Title block, indented list, open items" },
];

export function VersionsDialog({
  open,
  onOpenChange,
  jobId,
  token,
  versions,
  reopened,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  jobId: string;
  token: string;
  versions: VersionSummary[];
  reopened: boolean;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [preview, setPreview] = useState<number | null>(null);
  const [previewData, setPreviewData] = useState<BomVersion | null>(null);
  const ordered = [...versions].sort((a, b) => b.version - a.version);

  useEffect(() => {
    if (!open || preview === null) return;
    let cancelled = false;
    getVersion(jobId, token, preview)
      .then((v) => {
        if (!cancelled) setPreviewData(v);
      })
      .catch(() => {
        if (!cancelled) setNotice({ kind: "error", text: "The preview could not be loaded." });
      });
    return () => {
      cancelled = true;
    };
  }, [open, preview, jobId, token]);

  async function download(version: number, d: (typeof DOWNLOADS)[number]) {
    const key = `${version.toString()}-${d.fmt}-${d.part ?? ""}`;
    setBusy(key);
    setNotice(null);
    try {
      const name = await downloadExport(jobId, token, version, d.fmt, d.part);
      setNotice({ kind: "ok", text: `Downloaded ${name}` });
    } catch (e) {
      setNotice({
        kind: "error",
        text: e instanceof ApiError ? e.message : "The download failed. Please try again.",
      });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="rv-overlay" />
        <Dialog.Content className="rv-dialog rv-dialog-wide" aria-describedby="rv-versions-desc">
          <Dialog.Title>Versions and exports</Dialog.Title>
          <p id="rv-versions-desc" className="small muted">
            Each approval freezes a <G term="version">version</G>. Exports are made from a frozen version, so they always
            match the JSON.
          </p>
          {reopened ? (
            <p className="alert alert-warning" role="status">
              The BoM has changed since the last approval. The files below are still the approved version; approve again
              to freeze the changes.
            </p>
          ) : null}
          {ordered.length === 0 ? (
            <p className="rv-empty">
              Nothing is approved yet. Resolve the open items, approve the BoM, and the exports appear here.
            </p>
          ) : (
            <ul className="rv-versions">
              {ordered.map((v) => (
                <li key={v.version} className="rv-version">
                  <div className="rv-version-head">
                    <strong>Version {v.version}</strong>
                    <span className="small muted">
                      {new Date(v.created_at).toLocaleString()} {"·"} {v.ebom_lines} EBOM / {v.mbom_lines} MBOM lines
                      {" · "}
                      {v.open_items} open item{v.open_items === 1 ? "" : "s"}
                    </span>
                  </div>
                  <div className="actions">
                    {DOWNLOADS.map((d) => {
                      const key = `${v.version.toString()}-${d.fmt}-${d.part ?? ""}`;
                      return (
                        <button
                          key={key}
                          type="button"
                          className="btn btn-secondary"
                          title={d.hint}
                          disabled={busy !== null}
                          onClick={() => {
                            void download(v.version, d);
                          }}
                        >
                          {busy === key ? "Preparing…" : d.label}
                        </button>
                      );
                    })}
                    <button
                      type="button"
                      className="btn btn-quiet"
                      aria-pressed={preview === v.version}
                      onClick={() => {
                        setPreviewData(null);
                        setPreview(preview === v.version ? null : v.version);
                      }}
                    >
                      {preview === v.version ? "Hide preview" : "Preview parts list"}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div aria-live="polite">
            {notice ? (
              <p className={notice.kind === "error" ? "alert alert-danger" : "small muted"} role={notice.kind === "error" ? "alert" : undefined}>
                {notice.text}
              </p>
            ) : null}
          </div>
          {preview !== null && previewData ? <PartsListPreview version={previewData} /> : null}
          {preview !== null && !previewData ? <p className="small muted">Loading the preview{"…"}</p> : null}
          <div className="actions">
            <Dialog.Close className="btn btn-primary">Close</Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
