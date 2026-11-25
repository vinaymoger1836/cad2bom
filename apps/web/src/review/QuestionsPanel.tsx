import { useId, useMemo, useState } from "react";
import type { BomDraft, Question, Suggestion } from "@c2b/ts-types";
import { Term } from "../components/Help";
import { ConfidenceBadge } from "./Badges";
import type { Bands } from "./unwrap";

function isOpen(q: Question): boolean {
  return q.answer === null || q.answer === undefined;
}

function answerText(a: unknown): string {
  if (typeof a === "string" || typeof a === "number" || typeof a === "boolean") return String(a);
  return JSON.stringify(a);
}

export function suggestionTitle(s: Suggestion): string {
  if (s.text) return s.text;
  const what = s.class ?? s.cad_name ?? s.id;
  return s.action === "modify" && s.field ? `Change ${s.field} to ${answerText(s.value)}` : `Add ${what}`;
}

/** Larger impact first: blocking questions, then the ones touching the most lines. */
function byImpact(a: Question, b: Question): number {
  if (a.blocking !== b.blocking) return a.blocking ? -1 : 1;
  return (b.line_ids?.length ?? 0) - (a.line_ids?.length ?? 0);
}

function QuestionCard({
  q,
  busy,
  onAnswer,
  onFocusLines,
}: {
  q: Question;
  busy: boolean;
  onAnswer: (id: string, answer: unknown) => Promise<boolean>;
  onFocusLines: (ids: string[]) => void;
}) {
  const [value, setValue] = useState("");
  const name = useId();
  const kind = q.kind ?? "choice";
  const options = q.options ?? [];
  const lines = q.line_ids ?? [];

  function submit(answer: unknown) {
    void onAnswer(q.id, answer);
  }

  return (
    <li className="rv-card" data-blocking={q.blocking ? "true" : "false"}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim()) submit(kind === "number" ? Number(value) : value.trim());
        }}
      >
        <fieldset className="rv-fieldset" disabled={busy}>
          <legend className="rv-card-title">
            {q.blocking ? (
              <span className="rv-badge rv-status-flagged">Blocks approval</span>
            ) : (
              <span className="rv-badge">Optional</span>
            )}{" "}
            {q.text}
          </legend>

          {(q.evidence ?? []).length > 0 ? (
            <ul className="rv-chips" aria-label="Evidence">
              {(q.evidence ?? []).map((ev) => (
                <li key={ev} className="rv-chip">
                  {ev}
                </li>
              ))}
            </ul>
          ) : null}

          {kind === "choice" && options.length > 0 ? (
            <div className="rv-choices" role="radiogroup" aria-label="Your answer">
              {options.map((o) => (
                <label key={o} className="rv-choice">
                  <input
                    type="radio"
                    name={name}
                    value={o}
                    checked={value === o}
                    onChange={() => {
                      setValue(o);
                    }}
                  />
                  <span>{o}</span>
                </label>
              ))}
            </div>
          ) : null}
          {kind === "choice" && options.length === 0 ? (
            <input
              aria-label="Your answer"
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
              }}
            />
          ) : null}
          {kind === "number" ? (
            <input
              type="number"
              step="any"
              aria-label="Your answer (a number)"
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
              }}
            />
          ) : null}
          {kind === "text" ? (
            <textarea
              rows={2}
              aria-label="Your answer"
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
              }}
            />
          ) : null}

          <div className="rv-card-actions">
            {kind === "confirm" ? (
              <>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => {
                    submit("yes");
                  }}
                >
                  Yes
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => {
                    submit("no");
                  }}
                >
                  No
                </button>
              </>
            ) : (
              <button type="submit" className="btn btn-primary" disabled={!value.trim()}>
                Use this answer
              </button>
            )}
            {lines.length > 0 ? (
              <button
                type="button"
                className="btn btn-quiet"
                onClick={() => {
                  onFocusLines(lines);
                }}
              >
                Show line{lines.length === 1 ? "" : "s"} {lines.slice(0, 2).join(", ")}
                {lines.length > 2 ? "…" : ""}
              </button>
            ) : null}
            <Term
              word="Why am I asked?"
              meaning={
                q.blocking
                  ? "The data disagree or are missing, and the system will not guess a number. Approval stays blocked until you answer."
                  : "The system has a doubt it cannot settle from your files. Your answer improves the BoM but does not block approval."
              }
            />
          </div>
        </fieldset>
      </form>
    </li>
  );
}

function SuggestionCard({
  s,
  bands,
  busy,
  onDecide,
  onFocusLines,
}: {
  s: Suggestion;
  bands: Bands;
  busy: boolean;
  onDecide: (id: string, d: "accepted" | "rejected") => Promise<boolean>;
  onFocusLines: (ids: string[]) => void;
}) {
  const title = suggestionTitle(s);
  const ruleIds = s.rule_ids ?? (s.rule ? [s.rule] : []);
  return (
    <li className="rv-card">
      <div className="rv-card-title">
        <span className="rv-badge rv-source-ai">{s.level === "mbom" ? "MBOM" : "EBOM"}</span> {title}
      </div>
      {s.reason ? <p className="small muted">{s.reason}</p> : null}
      <ul className="rv-chips" aria-label="Details">
        {ruleIds.map((r) => (
          <li key={r} className="rv-chip rv-mono">
            {r}
          </li>
        ))}
        {s.qty_total !== null && s.qty_total !== undefined ? (
          <li className="rv-chip">Qty {s.qty_total}</li>
        ) : null}
        <li className="rv-chip">
          <ConfidenceBadge value={s.confidence ?? 0.5} bands={bands} />
        </li>
      </ul>
      <div className="rv-card-actions">
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy}
          aria-label={`Accept: ${title}`}
          onClick={() => {
            void onDecide(s.id, "accepted");
          }}
        >
          Accept
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={busy}
          aria-label={`Reject: ${title}`}
          onClick={() => {
            void onDecide(s.id, "rejected");
          }}
        >
          Reject
        </button>
        {s.line_id ? (
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => {
              if (s.line_id) onFocusLines([s.line_id]);
            }}
          >
            Show line {s.line_id}
          </button>
        ) : null}
      </div>
    </li>
  );
}

export { SuggestionCard };

export function QuestionsPanel({
  draft,
  bands,
  busy,
  onAnswer,
  onDecide,
  onFocusLines,
  panelRef,
}: {
  draft: BomDraft;
  bands: Bands;
  busy: boolean;
  onAnswer: (id: string, answer: unknown) => Promise<boolean>;
  onDecide: (id: string, d: "accepted" | "rejected") => Promise<boolean>;
  onFocusLines: (ids: string[]) => void;
  panelRef: React.RefObject<HTMLElement | null>;
}) {
  const questions = useMemo(() => draft.questions ?? [], [draft.questions]);
  const open = useMemo(() => questions.filter(isOpen).sort(byImpact), [questions]);
  const done = questions.filter((q) => !isOpen(q));
  const suggestions = draft.suggestions ?? [];
  const pending = suggestions.filter((s) => s.review === "suggested");
  const decided = suggestions.filter((s) => s.review !== "suggested");
  const total = open.length + pending.length;
  const headId = useId();

  return (
    <section
      ref={panelRef}
      tabIndex={-1}
      className="rv-panel rv-questions"
      aria-labelledby={headId}
    >
      <header className="rv-panel-head">
        <h2 id={headId}>
          Questions and suggestions{" "}
          <span className={total > 0 ? "rv-count rv-count-open" : "rv-count"} aria-live="polite">
            {total > 0 ? `${total.toString()} open` : "all done"}
          </span>
        </h2>
        <p className="small muted">
          Most important first. A <Term word="suggestion" meaning="Something the system proposes. Nothing is added until you accept it." /> never changes the BoM until you accept it.
        </p>
      </header>

      <div className="rv-panel-body">
        {total === 0 ? (
          <p className="rv-empty">
            Nothing is waiting for you. You can approve the BoM when you are ready.
          </p>
        ) : null}

        {open.length > 0 ? (
          <>
            <h3 className="rv-subhead">Questions ({open.length})</h3>
            <ul className="rv-list">
              {open.map((q) => (
                <QuestionCard
                  key={q.id}
                  q={q}
                  busy={busy}
                  onAnswer={onAnswer}
                  onFocusLines={onFocusLines}
                />
              ))}
            </ul>
          </>
        ) : null}

        {pending.length > 0 ? (
          <>
            <h3 className="rv-subhead">Suggestions ({pending.length})</h3>
            <ul className="rv-list">
              {pending.map((s) => (
                <SuggestionCard
                  key={s.id}
                  s={s}
                  bands={bands}
                  busy={busy}
                  onDecide={onDecide}
                  onFocusLines={onFocusLines}
                />
              ))}
            </ul>
          </>
        ) : null}

        {done.length + decided.length > 0 ? (
          <details className="rv-resolved">
            <summary>Resolved ({done.length + decided.length})</summary>
            <ul className="rv-resolved-list">
              {done.map((q) => (
                <li key={q.id}>
                  <span className="rv-badge rv-status-accepted">Answered</span> {q.text}{" "}
                  <strong>{"→"} {answerText(q.answer)}</strong>
                </li>
              ))}
              {decided.map((s) => (
                <li key={s.id}>
                  <span
                    className={
                      s.review === "accepted"
                        ? "rv-badge rv-status-accepted"
                        : "rv-badge rv-status-rejected"
                    }
                  >
                    {s.review === "accepted" ? "Accepted" : "Rejected"}
                  </span>{" "}
                  {suggestionTitle(s)}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </section>
  );
}
