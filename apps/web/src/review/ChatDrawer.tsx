import { useEffect, useId, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import type { BomDraft, ChatMessage, Suggestion } from "@c2b/ts-types";
import { ApiError, sendChat } from "../lib/api";
import { SuggestionCard } from "./QuestionsPanel";
import type { Bands } from "./unwrap";

interface Turn {
  role: "user" | "assistant";
  content: string;
  suggestions: Suggestion[];
}

const EXAMPLES = [
  "Why is the motor classed the way it is?",
  "Give me a summary of this BoM",
  "Change the fasteners to stainless steel",
];

export function ChatDrawer({
  open,
  onOpenChange,
  jobId,
  token,
  draft,
  bands,
  busy,
  onDecide,
  onRefresh,
  onFocusLines,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  jobId: string;
  token: string;
  draft: BomDraft;
  bands: Bands;
  busy: boolean;
  onDecide: (id: string, d: "accepted" | "rejected") => Promise<boolean>;
  onRefresh: () => Promise<void>;
  onFocusLines: (ids: string[]) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const inputId = useId();

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [turns]);

  async function send(message: string) {
    const msg = message.trim();
    if (!msg || sending) return;
    const history: ChatMessage[] = turns.map((t) => ({ role: t.role, content: t.content }));
    setTurns((prev) => [...prev, { role: "user", content: msg, suggestions: [] }]);
    setInput("");
    setSending(true);
    setError(null);
    try {
      const res = await sendChat(jobId, token, msg, history);
      setTurns((prev) => [
        ...prev,
        { role: "assistant", content: res.reply, suggestions: res.suggestions ?? [] },
      ]);
      if ((res.suggestions ?? []).length > 0) await onRefresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The assistant could not answer. Please try again.");
    } finally {
      setSending(false);
    }
  }

  // A suggestion's current state lives in the draft, so a decision shows here too.
  const live = new Map((draft.suggestions ?? []).map((s) => [s.id, s]));

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange} modal={false}>
      <Dialog.Portal>
        <Dialog.Content
          className="rv-drawer"
          aria-describedby={undefined}
          onInteractOutside={(e) => {
            e.preventDefault(); // the table stays usable while the chat is open
          }}
        >
          <header className="rv-drawer-head">
            <Dialog.Title>Ask about this BoM</Dialog.Title>
            <Dialog.Close className="btn btn-quiet" aria-label="Close the chat">
              <span aria-hidden="true">{"✕"}</span>
            </Dialog.Close>
          </header>
          <p className="small muted rv-drawer-note">
            Answers come from this job&apos;s data. If I suggest a change it appears as a card you can accept or
            reject. Nothing changes silently.
          </p>

          <div ref={logRef} className="rv-chat-log" role="log" aria-live="polite" aria-label="Conversation">
            {turns.length === 0 ? (
              <ul className="rv-examples" aria-label="Examples">
                {EXAMPLES.map((ex) => (
                  <li key={ex}>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      onClick={() => {
                        void send(ex);
                      }}
                    >
                      {ex}
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            {turns.map((t, i) => (
              <div key={`${t.role}-${i.toString()}`} className={`rv-turn rv-turn-${t.role}`}>
                <span className="visually-hidden">{t.role === "user" ? "You: " : "Assistant: "}</span>
                <p>{t.content}</p>
                {t.suggestions.length > 0 ? (
                  <ul className="rv-list">
                    {t.suggestions.map((s) => {
                      const current = live.get(s.id) ?? s;
                      return current.review === "suggested" ? (
                        <SuggestionCard
                          key={s.id}
                          s={current}
                          bands={bands}
                          busy={busy}
                          onDecide={onDecide}
                          onFocusLines={onFocusLines}
                        />
                      ) : (
                        <li key={s.id} className="small muted">
                          {current.text || s.id}: {current.review}
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </div>
            ))}
            {sending ? <p className="small muted">Thinking{"…"}</p> : null}
          </div>

          {error ? (
            <p className="alert alert-danger" role="alert">
              {error}
            </p>
          ) : null}

          <form
            className="rv-chat-form"
            onSubmit={(e) => {
              e.preventDefault();
              void send(input);
            }}
          >
            <label htmlFor={inputId} className="visually-hidden">
              Your question
            </label>
            <input
              id={inputId}
              value={input}
              placeholder="Ask a question or propose a change…"
              onChange={(e) => {
                setInput(e.target.value);
              }}
            />
            <button type="submit" className="btn btn-primary" disabled={sending || !input.trim()}>
              Send
            </button>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
