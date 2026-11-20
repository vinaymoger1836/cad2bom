export interface ProgressItem {
  id: number;
  kind: string;
  message: string;
}

interface Props {
  title: string;
  items: ProgressItem[];
  /** 0..1, or null when the amount of work is not known (shows a moving bar). */
  fraction: number | null;
  state: "running" | "done" | "failed";
  note?: string;
}

/**
 * Progress bar plus a live log. The bar moves with CSS only when motion is allowed;
 * with prefers-reduced-motion it is a static striped bar (see app.css).
 */
export function ProgressPanel({ title, items, fraction, state, note }: Props) {
  const pct = fraction === null ? undefined : Math.round(fraction * 100);
  return (
    <section className="progress-panel" aria-label={title}>
      <div className="progress-head">
        <h3>{title}</h3>
        <span className={`badge badge-${state}`}>
          {state === "running" ? "Working" : state === "done" ? "Step finished" : "Stopped"}
        </span>
      </div>
      <div
        className={`bar${fraction === null && state === "running" ? " bar-indeterminate" : ""}${
          state === "failed" ? " bar-failed" : ""
        }`}
        role="progressbar"
        aria-label={title}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={pct === undefined ? "In progress" : `${pct.toString()} percent`}
      >
        <div className="bar-fill" style={pct === undefined ? undefined : { width: `${pct.toString()}%` }} />
      </div>
      {note ? <p className="muted small">{note}</p> : null}
      <ol className="log" aria-live="polite" aria-relevant="additions">
        {items.map((it) => (
          <li key={it.id} className={`log-${it.kind}`}>
            {it.message}
          </li>
        ))}
      </ol>
    </section>
  );
}
