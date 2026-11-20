import { API } from "./api";

export interface SseMessage {
  id: string | null;
  event: string;
  data: string;
}

/** Split an SSE text buffer into complete messages; returns the unparsed remainder. */
function drain(buffer: string): { messages: SseMessage[]; rest: string } {
  const messages: SseMessage[] = [];
  let rest = buffer.replace(/\r\n/g, "\n");
  for (;;) {
    const end = rest.indexOf("\n\n");
    if (end < 0) break;
    const block = rest.slice(0, end);
    rest = rest.slice(end + 2);
    let id: string | null = null;
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue; // comment / keepalive
      const idx = line.indexOf(":");
      const field = idx < 0 ? line : line.slice(0, idx);
      const value = idx < 0 ? "" : line.slice(idx + 1).replace(/^ /, "");
      if (field === "id") id = value;
      else if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length > 0) messages.push({ id, event, data: data.join("\n") });
  }
  return { messages, rest };
}

/**
 * Read one SSE connection with fetch (EventSource cannot send an Authorization header, and the
 * access key must never appear in a URL). Resolves when the server closes the stream.
 */
export async function readSse(
  path: string,
  opts: {
    token?: string;
    lastEventId?: string | null;
    signal: AbortSignal;
    onMessage: (m: SseMessage) => void;
  },
): Promise<void> {
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (opts.token) headers["Authorization"] = `Bearer ${opts.token}`;
  if (opts.lastEventId) headers["Last-Event-ID"] = opts.lastEventId;
  const res = await fetch(`${API}${path}`, {
    headers,
    signal: opts.signal,
    cache: "no-store",
  });
  if (!res.ok || !res.body) {
    throw new Error(`stream failed with status ${res.status.toString()}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { messages, rest } = drain(buffer);
    buffer = rest;
    messages.forEach(opts.onMessage);
  }
}

/** Follow a job's events, reconnecting with Last-Event-ID until aborted. */
export async function followJob(
  jobId: string,
  token: string,
  signal: AbortSignal,
  onMessage: (m: SseMessage) => void,
  onState: (connected: boolean) => void,
): Promise<void> {
  let last = null as string | null;
  let delay = 500;
  const aborted = (): boolean => signal.aborted; // not narrowed by the loop condition
  while (!aborted()) {
    try {
      onState(true);
      await readSse(`/jobs/${jobId}/events`, {
        token,
        lastEventId: last,
        signal,
        onMessage: (m) => {
          if (m.id) last = m.id;
          delay = 500;
          onMessage(m);
        },
      });
    } catch {
      if (aborted()) return;
    }
    onState(false);
    await new Promise<void>((resolve) => {
      const t = window.setTimeout(resolve, delay);
      signal.addEventListener(
        "abort",
        () => {
          window.clearTimeout(t);
          resolve();
        },
        { once: true },
      );
    });
    delay = Math.min(delay * 2, 8000);
  }
}
