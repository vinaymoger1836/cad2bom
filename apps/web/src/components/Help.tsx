import { useId, useRef, useState, type ReactNode } from "react";

/** "What happens here" box shown at the top of each step (docs/07 §3). */
export function WhatHappens({ children }: { children: ReactNode }) {
  const titleId = useId();
  return (
    <aside className="callout" aria-labelledby={titleId}>
      <h2 id={titleId} className="callout-title">
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
  const titleId = useId();
  return (
    <section aria-labelledby={titleId} className="qa">
      <h2 id={titleId}>Questions you might have</h2>
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
  const button = useRef<HTMLButtonElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number; above: boolean } | null>(null);

  // The tip is fixed-positioned so scrolling panels and the table cannot clip it.
  function place() {
    const r = button.current?.getBoundingClientRect();
    if (!r) return;
    const width = Math.min(288, window.innerWidth - 16);
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
    const above = r.top > 140;
    setPos({ left, top: above ? r.top - 6 : r.bottom + 6, above });
  }

  return (
    <span className="term-wrap" onMouseEnter={place}>
      <button
        ref={button}
        type="button"
        className="term"
        aria-describedby={id}
        onFocus={place}
        onKeyDown={(e) => {
          if (e.key === "Escape") e.currentTarget.blur(); // lets keyboard users dismiss the tip
        }}
      >
        {word}
      </button>
      <span
        role="tooltip"
        id={id}
        className={pos?.above ? "term-tip term-tip-above" : "term-tip"}
        style={pos ? { left: pos.left, top: pos.top } : undefined}
      >
        {meaning}
      </span>
    </span>
  );
}
