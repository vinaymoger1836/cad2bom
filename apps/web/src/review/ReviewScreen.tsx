import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router";
import * as Dialog from "@radix-ui/react-dialog";
import type { ValidationIssue } from "@c2b/ts-types";
import { Qa, WhatHappens } from "../components/Help";
import { loadToken, saveToken } from "../lib/storage";
import { ApproveDialog } from "./ApproveDialog";
import { BomTable, type Selection } from "./BomTable";
import { ChatDrawer } from "./ChatDrawer";
import { G } from "./glossary";
import { LineDetails } from "./LineDetails";
import { QuestionsPanel } from "./QuestionsPanel";
import { useReview } from "./useReview";
import { bandOf, text, type EditableField } from "./unwrap";
import { VersionsDialog } from "./VersionsDialog";

const Viewer = lazy(() => import("./Viewer").then((m) => ({ default: m.Viewer })));

const SHORTCUTS: [string, string][] = [
  ["Alt + Q", "Go to questions and suggestions"],
  ["Alt + T", "Go to the BoM table"],
  ["Alt + V", "Go to the 3D view"],
  ["Alt + C", "Open or close the chat"],
  ["?", "Show this list"],
  ["Arrow keys", "Move between cells in the table"],
  ["Left / Right on the first cell", "Collapse or expand a group"],
  ["Enter", "Edit the cell. Enter saves, Escape cancels"],
  ["Space", "Select the line (updates the 3D view and details)"],
];

function resolveToken(id: string, hash: string): string | null {
  const fromHash = hash.replace(/^#/, "");
  if (fromHash) {
    saveToken(id, fromHash);
    return fromHash;
  }
  return loadToken(id);
}

export function ReviewScreen() {
  const { id = "" } = useParams();
  const { hash } = useLocation();
  const [token] = useState(() => resolveToken(id, hash));
  const [state, actions] = useReview(id, token);
  const { job, draft, versions, excluded, bands, busy, error, revision } = state;

  const [selection, setSelection] = useState<Selection>({ lineId: null, nodeIds: null });
  const [approveOpen, setApproveOpen] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [keysOpen, setKeysOpen] = useState(false);

  const questionsRef = useRef<HTMLElement>(null);
  const tableRef = useRef<HTMLElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (job) document.title = `Review – ${job.product.name} – CAD to BoM`;
  }, [job]);

  function focusTable() {
    const cell = gridRef.current?.querySelector<HTMLElement>('[role="gridcell"][tabindex="0"]');
    (cell ?? gridRef.current ?? tableRef.current)?.focus();
  }

  // Keyboard shortcuts (docs/07 §3). Ignored while typing in a field.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target;
      const typing =
        t instanceof HTMLElement &&
        (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
      if (e.altKey && !e.ctrlKey && !e.metaKey) {
        if (e.code === "KeyQ") {
          e.preventDefault();
          questionsRef.current?.focus();
        } else if (e.code === "KeyT") {
          e.preventDefault();
          focusTable();
        } else if (e.code === "KeyV") {
          e.preventDefault();
          viewerRef.current?.querySelector<HTMLElement>(".rv-canvas")?.focus();
        } else if (e.code === "KeyC") {
          e.preventDefault();
          setChatOpen((o) => !o);
        }
      } else if (e.key === "?" && !typing) {
        e.preventDefault();
        setKeysOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  const ebom = useMemo(() => draft?.ebom ?? [], [draft]);
  const selectedLine = ebom.find((l) => l.id === selection.lineId) ?? null;
  const lineNodes = useMemo(() => new Map(ebom.map((l) => [l.id, l.cad_nodes ?? []])), [ebom]);
  const highlightNodes = useMemo(
    () => selection.nodeIds ?? selectedLine?.cad_nodes ?? [],
    [selection.nodeIds, selectedLine],
  );
  const excludedIds = useMemo(() => excluded.map((n) => n.id), [excluded]);

  const counts = useMemo(() => {
    const c = { high: 0, medium: 0, low: 0 };
    for (const l of ebom) c[bandOf(l.confidence ?? 1, bands)] += 1;
    return c;
  }, [ebom, bands]);

  if (!token) {
    return (
      <div className="stack">
        <h1>Review</h1>
        <p className="alert alert-warning" role="alert">
          This browser does not have the key for this BoM. Open the private link you were given when you created it.
        </p>
        <Link to="/">Start a new BoM</Link>
      </div>
    );
  }
  if (state.loading) return <p role="status">Loading the BoM{"…"}</p>;
  if (!job || !draft) {
    return (
      <div className="stack">
        <h1>Review</h1>
        <p className="alert alert-danger" role="alert">
          {error ?? "The BoM could not be loaded."}
        </p>
        <Link to={`/j/${id}`}>Back to the upload page</Link>
      </div>
    );
  }
  if (job.status !== "review" && job.status !== "approved") {
    return (
      <div className="stack">
        <h1>{job.product.name}</h1>
        <p role="status">
          The BoM is not ready to review yet (status: {job.status}). Watch its progress on the upload page.
        </p>
        <Link className="btn btn-primary" to={`/j/${id}`}>
          Go to progress
        </Link>
      </div>
    );
  }

  const blocking = draft.validation?.blocking_issues ?? [];
  const latest = versions.at(-1);
  const reopened = job.status === "review" && latest !== undefined;
  const approved = job.status === "approved" && latest !== undefined;
  const openCount = (draft.questions ?? []).filter((q) => q.answer === null || q.answer === undefined).length +
    (draft.suggestions ?? []).filter((s) => s.review === "suggested").length;

  function selectLine(ids: string[]) {
    setSelection({ lineId: ids[0] ?? null, nodeIds: null });
    window.setTimeout(focusTable, 0);
  }

  function goTo(issue: ValidationIssue) {
    if (issue.line_id) selectLine([issue.line_id]);
    else window.setTimeout(() => questionsRef.current?.focus(), 0);
  }

  function edit(lineId: string, field: EditableField, value: string | number) {
    void actions.edit(lineId, field, value);
  }

  return (
    <div className="rv">
      <header className="rv-top">
        <div className="rv-top-main">
          <h1 className="rv-title">{job.product.name}</h1>
          <p className="small muted rv-meta">
            <span className={approved ? "badge badge-done" : "badge badge-running"}>
              {approved ? `Approved · v${latest.version.toString()}` : reopened ? `New draft since v${latest.version.toString()}` : "In review"}
            </span>{" "}
            Library {job.library_version} {"·"} {ebom.length} EBOM lines {"·"}{" "}
            <G term="confidence">Confidence</G>:{" "}
            <span className="rv-conf-sum">
              <span className="rv-conf-high">{counts.high} High</span>,{" "}
              <span className="rv-conf-medium">{counts.medium} Medium</span>,{" "}
              <span className="rv-conf-low">{counts.low} Low</span>
            </span>
          </p>
        </div>
        <div className="rv-top-actions">
          <button
            type="button"
            className="btn btn-primary"
            aria-describedby="rv-approve-state"
            onClick={() => {
              setApproveOpen(true);
            }}
          >
            Approve
          </button>
          <span id="rv-approve-state" className="small muted">
            {blocking.length > 0
              ? `${blocking.length.toString()} thing${blocking.length === 1 ? "" : "s"} to resolve first`
              : openCount > 0
                ? `${openCount.toString()} optional item${openCount === 1 ? "" : "s"} open`
                : "Ready to approve"}
          </span>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => {
              setVersionsOpen(true);
            }}
          >
            Versions and export
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            aria-expanded={chatOpen}
            aria-keyshortcuts="Alt+C"
            onClick={() => {
              setChatOpen((o) => !o);
            }}
          >
            Ask a question
          </button>
          <button
            type="button"
            className="btn btn-quiet"
            aria-label="Keyboard shortcuts"
            aria-keyshortcuts="?"
            onClick={() => {
              setKeysOpen(true);
            }}
          >
            ?
          </button>
        </div>
      </header>

      {error ? (
        <p className="alert alert-danger rv-alert" role="alert">
          {error}{" "}
          <button type="button" className="btn btn-quiet" onClick={actions.clearError}>
            Dismiss
          </button>
        </p>
      ) : null}

      <details className="rv-help" open>
        <summary>What happens here</summary>
        <WhatHappens>
          <p>
            This is the <G term="BoM">bill of materials</G> the system built from your files. Work through the
            questions on the left, check the table in the middle against the 3D model, then approve. Every value shows
            where it came from; anything you type is marked as yours and is never overwritten.
          </p>
        </WhatHappens>
      </details>

      <div className="rv-grid">
        <QuestionsPanel
          draft={draft}
          bands={bands}
          busy={busy}
          onAnswer={actions.answer}
          onDecide={actions.decide}
          onFocusLines={selectLine}
          panelRef={questionsRef}
        />
        <BomTable
          draft={draft}
          volume={job.product.annual_volume ?? null}
          excluded={excluded}
          bands={bands}
          selection={selection}
          onSelect={setSelection}
          onEdit={edit}
          gridRef={gridRef}
          tableRef={tableRef}
        />
        <div className="rv-side" ref={viewerRef}>
          <Suspense fallback={<p className="rv-viewer-note">Loading the 3D view{"…"}</p>}>
            <Viewer
              jobId={id}
              token={token}
              lineNodes={lineNodes}
              highlightNodes={highlightNodes}
              excludedNodes={excludedIds}
              onPickLine={(lineId) => {
                setSelection({ lineId, nodeIds: null });
              }}
            />
          </Suspense>
          <LineDetails jobId={id} token={token} line={selectedLine} bands={bands} revision={revision} />
        </div>
      </div>

      <Qa
        items={[
          {
            q: "Why am I asked questions?",
            a: "When the CAD model and your description disagree, or a value is missing, the system does not guess a number. It asks, and approval waits for your answer.",
          },
          {
            q: "Can I change a value?",
            a: "Yes. Select a cell, press Enter, type, and press Enter again. Your value is marked as yours with 100% confidence and later runs never overwrite it. The History tab shows what it was before.",
          },
          {
            q: "What does accepting a suggestion do?",
            a: "It adds the proposed item (for example a rating label or a shipper carton) to the BoM. Rejecting it removes the proposal for good, so it is not suggested again.",
          },
          {
            q: "What happens when I approve?",
            a: "The BoM is frozen as a numbered version and you can download Excel, CSV, JSON and PDF files that match it exactly. If you change anything afterwards, a new draft starts and the old version stays as it was.",
          },
          {
            q: "How do I use this without a mouse?",
            a: "Press ? for the keyboard shortcuts. Alt+Q, Alt+T and Alt+V jump to the questions, the table and the 3D view.",
          },
        ]}
      />

      <ApproveDialog
        open={approveOpen}
        onOpenChange={setApproveOpen}
        draft={draft}
        versions={versions}
        reopened={reopened}
        busy={busy}
        onApprove={actions.approve}
        onGoTo={goTo}
        onOpenExports={() => {
          setVersionsOpen(true);
        }}
      />
      <VersionsDialog
        open={versionsOpen}
        onOpenChange={setVersionsOpen}
        jobId={id}
        token={token}
        versions={versions}
        reopened={reopened}
      />
      <ChatDrawer
        open={chatOpen}
        onOpenChange={setChatOpen}
        jobId={id}
        token={token}
        draft={draft}
        bands={bands}
        busy={busy}
        onDecide={actions.decide}
        onRefresh={actions.refresh}
        onFocusLines={selectLine}
      />

      <Dialog.Root open={keysOpen} onOpenChange={setKeysOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="rv-overlay" />
          <Dialog.Content className="rv-dialog" aria-describedby={undefined}>
            <Dialog.Title>Keyboard shortcuts</Dialog.Title>
            <dl className="rv-keys">
              {SHORTCUTS.map(([k, d]) => (
                <div key={k}>
                  <dt>
                    <kbd>{k}</kbd>
                  </dt>
                  <dd>{d}</dd>
                </div>
              ))}
            </dl>
            <p className="small muted">
              Selected line: {selectedLine ? `${selectedLine.id} ${text(selectedLine.description)}` : "none"}
            </p>
            <div className="actions">
              <Dialog.Close className="btn btn-primary">Close</Dialog.Close>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
