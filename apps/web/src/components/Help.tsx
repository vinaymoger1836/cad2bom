import { useId, type ReactNode } from "react";

/** "What happens here" box shown at the top of each step (docs/07 §3). */
export function WhatHappens({ children }: { children: ReactNode }) {
  return (
    <aside className="callout" aria-labelledby="what-happens-title">
      <h2 id="what-happens-title" className="callout-title">
        What happens here
      </h2>
      {children}
    </aside>
  );
}

export interface QaItem {
  q: string;
  a: ReactNode;
}

/** Short Q&A at the bottom of each step. Native <details> keeps it keyboard accessible. */
export function Qa({ items }: { items: QaItem[] }) {
  return (
    <section aria-labelledby="qa-title" className="qa">
      <h2 id="qa-title">Questions you might have</h2>
      {items.map((item) => (
        <details key={item.q}>
          <summary>{item.q}</summary>
          <div className="qa-answer">{item.a}</div>
        </details>
      ))}
    </section>
  );
}

/** Plain-language tooltip for a technical term. Works on hover, focus and touch. */
export function Term({ word, meaning }: { word: string; meaning: string }) {
  const id = useId();
  return (
    <span className="term-wrap">
      <button
        type="button"
        className="term"
        aria-describedby={id}
        onKeyDown={(e) => {
          if (e.key === "Escape") e.currentTarget.blur(); // lets keyboard users dismiss the tip
        }}
      >
        {word}
      </button>
      <span role="tooltip" id={id} className="term-tip">
        {meaning}
      </span>
    </span>
  );
}
