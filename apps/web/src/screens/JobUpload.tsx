import { useCallback, useEffect, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import type { BomDraft, JobEvent, JobOut } from "@c2b/ts-types";
import { DeleteJobDialog } from "../components/DeleteJobDialog";
import { Qa, Term, WhatHappens } from "../components/Help";
import { NativeConsentDialog } from "../components/NativeConsentDialog";
import { ProgressPanel, type ProgressItem } from "../components/ProgressPanel";
import {
  ApiError,
  getBom,
  getConfig,
  getJob,
  setNativeConsent,
  startJob,
  uploadFile,
  type AppConfig,
} from "../lib/api";
import { followJob, readSse } from "../lib/sse";
import { forgetToken, loadToken, saveToken } from "../lib/storage";

type ItemState = "queued" | "uploading" | "done" | "error";

interface UploadItem {
  key: number;
  name: string;
  size: number;
  state: ItemState;
  progress: number;
  message?: string;
}

const NATIVE = [".sldprt", ".sldasm", ".f3d"];
const SOLIDWORKS = [".sldprt", ".sldasm"];

function fmtSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024).toString()} KB`;
  return `${bytes.toString()} bytes`;
}

function fmtDuration(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const sec = totalSeconds % 60;
  return m > 0 ? `${m.toString()} min ${sec.toString()} s` : `${sec.toString()} s`;
}

function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i < 0 ? "" : name.slice(i).toLowerCase();
}

/** Quick pre-check so people get an answer before a big upload. The server stays authoritative. */
function precheck(file: File, cfg: AppConfig): string | null {
  const ext = extOf(file.name);
  if (NATIVE.includes(ext) && !cfg.native_enabled) {
    return `${file.name} is a native CAD file, and this server is not set up to convert those. Please export it as STEP (AP242 if you can) and upload that instead. Your other files are not affected.`;
  }
  if (!cfg.accepted_extensions.includes(ext)) {
    return `${file.name} has type ${ext || "(no extension)"}, which is not accepted. Accepted types: ${cfg.accepted_extensions.join(", ")}.`;
  }
  const isCad = cfg.cad_extensions.includes(ext);
  const limit = (isCad ? cfg.max_cad_mb : cfg.max_other_mb) * 1024 * 1024;
  if (file.size > limit) {
    return `${file.name} is ${fmtSize(file.size)}; the limit for ${isCad ? "CAD files" : "this kind of file"} is ${(isCad ? cfg.max_cad_mb : cfg.max_other_mb).toString()} MB.`;
  }
  if (file.size === 0) return `${file.name} is empty (0 bytes).`;
  return null;
}

function resolveToken(id: string, hash: string): string | null {
  const fromHash = hash.replace(/^#/, "");
  if (fromHash) {
    saveToken(id, fromHash);
    return fromHash;
  }
  return loadToken(id);
}

export function JobUpload() {
  const { id = "" } = useParams();
  const { hash } = useLocation();
  const [token] = useState(() => resolveToken(id, hash));
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [job, setJob] = useState<JobOut | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [events, setEvents] = useState<ProgressItem[]>([]);
  const [firstAt, setFirstAt] = useState<number | null>(null);
  const [lastAt, setLastAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [copied, setCopied] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [pendingNative, setPendingNative] = useState<File[] | null>(null);
  const [consentBusy, setConsentBusy] = useState(false);
  const nextKey = useRef(1);
  const navigate = useNavigate();

  // Load config and the job.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    getConfig()
      .then((c) => {
        if (!cancelled) setCfg(c);
      })
      .catch((e: unknown) => {
        if (!cancelled) setLoadError(e instanceof ApiError ? e.message : "Could not load settings.");
      });
    getJob(id, token)
      .then((j) => {
        if (!cancelled) setJob(j);
      })
      .catch((e: unknown) => {
        if (!cancelled) setLoadError(e instanceof ApiError ? e.message : "Could not load this job.");
      });
    return () => {
      cancelled = true;
    };
  }, [id, token]);

  const started = job !== null && job.status !== "uploading";
  const running =
    job !== null &&
    (job.status === "extracting" || job.status === "building" || job.status === "converting");

  // The live stream can drop or miss an event. While a build is running, also ask for the job's
  // state every few seconds (the server also stops builds that went silent), and tick a clock.
  useEffect(() => {
    if (!token || !running) return;
    const poll = window.setInterval(() => {
      getJob(id, token)
        .then((j) => {
          setJob((cur) => (cur && cur.status !== j.status ? { ...cur, status: j.status } : cur));
        })
        .catch(() => {
          /* the next tick tries again */
        });
    }, 5000);
    const tick = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(poll);
      window.clearInterval(tick);
    };
  }, [id, token, running]);

  const [draft, setDraft] = useState<BomDraft | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const finished = job?.status === "review" || job?.status === "approved";

  // When the build is done, fetch the draft so this page can say what came out of it.
  useEffect(() => {
    if (!token || !finished) return;
    let cancelled = false;
    getBom(id, token)
      .then((d) => {
        if (!cancelled) setDraft(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setDraftError(e instanceof ApiError ? e.message : "Could not load the draft BoM.");
      });
    return () => {
      cancelled = true;
    };
  }, [id, token, finished]);

  function downloadDraft() {
    if (!draft) return;
    const blob = new Blob([JSON.stringify(draft, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `draft-bom-${id.slice(0, 8)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // Follow progress once the build has started (also replays history when you come back).
  useEffect(() => {
    if (!token || !started) return;
    const ctrl = new AbortController();
    void followJob(
      id,
      token,
      ctrl.signal,
      (m) => {
        try {
          const ev = JSON.parse(m.data) as JobEvent;
          setEvents((prev) =>
            prev.some((p) => p.id === ev.id) ? prev : [...prev, { id: ev.id, kind: ev.kind, message: ev.message }],
          );
          const at = Date.parse(ev.created_at);
          if (!Number.isNaN(at)) {
            setFirstAt((f) => (f === null || at < f ? at : f));
            setLastAt((l) => (l === null || at > l ? at : l));
          }
          const next = (ev.data as { status?: string }).status;
          if (ev.kind === "status" && typeof next === "string") {
            setJob((j) => (j ? { ...j, status: next as JobOut["status"] } : j));
          }
        } catch {
          /* ignore a malformed frame */
        }
      },
      () => {
        /* connection state is not shown in P0 */
      },
    );
    return () => {
      ctrl.abort();
    };
  }, [id, token, started]);

  const patch = useCallback((key: number, p: Partial<UploadItem>) => {
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, ...p } : it)));
  }, []);

  /** Native files need the person's consent first (they leave the server). Others go straight. */
  async function addFiles(files: File[], consented = false) {
    if (!token || !cfg || files.length === 0) return;
    const natives = files.filter((f) => cfg.native_enabled && NATIVE.includes(extOf(f.name)));
    if (natives.length > 0 && !consented && !job?.consent_native_conversion) {
      setPendingNative(files);
      return;
    }
    const fresh: { item: UploadItem; file: File }[] = files.map((file) => {
      const problem = precheck(file, cfg);
      return {
        file,
        item: {
          key: nextKey.current++,
          name: file.name,
          size: file.size,
          state: problem ? "error" : "queued",
          progress: 0,
          ...(problem ? { message: problem } : {}),
        },
      };
    });
    setItems((prev) => [...prev, ...fresh.map((f) => f.item)]);
    setUploading(true);
    for (const { item, file } of fresh) {
      if (item.state === "error") continue;
      patch(item.key, { state: "uploading" });
      try {
        await uploadFile(id, token, file, (f) => {
          patch(item.key, { progress: f });
        }).promise;
        patch(item.key, { state: "done", progress: 1 });
      } catch (e) {
        patch(item.key, {
          state: "error",
          message: e instanceof ApiError ? e.message : `Uploading ${file.name} failed.`,
        });
      }
    }
    setUploading(false);
    try {
      setJob(await getJob(id, token));
    } catch {
      /* the list refreshes on the next action */
    }
  }

  async function onConsent() {
    if (!token || !pendingNative) return;
    setConsentBusy(true);
    try {
      setJob(await setNativeConsent(id, token, true));
      const files = pendingNative;
      setPendingNative(null);
      await addFiles(files, true);
    } catch (e) {
      setPendingNative(null);
      setStartError(e instanceof ApiError ? e.message : "Could not save your consent. Please try again.");
    } finally {
      setConsentBusy(false);
    }
  }

  function onPick(e: ChangeEvent<HTMLInputElement>) {
    const list = Array.from(e.target.files ?? []);
    e.target.value = "";
    void addFiles(list);
  }

  function onDrop(e: DragEvent<HTMLElement>) {
    e.preventDefault();
    setDragging(false);
    void addFiles(Array.from(e.dataTransfer.files));
  }

  async function onStart() {
    if (!token) return;
    setStartError(null);
    try {
      setJob(await startJob(id, token));
    } catch (e) {
      setStartError(e instanceof ApiError ? e.message : "Could not start. Please try again.");
    }
  }

  async function copyLink() {
    const url = `${window.location.origin}/j/${id}#${token ?? ""}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => {
        setCopied(false);
      }, 3000);
    } catch {
      setStartError("Could not copy automatically. Select the link and copy it by hand.");
    }
  }

  if (!token) {
    return (
      <div className="stack">
        <h1>This job needs its full link</h1>
        <p role="alert" className="alert alert-warning">
          This page was opened without its access key. Use the complete link you got when you created
          the job (it ends with <code>#</code> and a long code), or{" "}
          <Link to="/">start a new BoM</Link>.
        </p>
      </div>
    );
  }

  const status = job?.status ?? "uploading";
  const failed = status === "failed";
  const fraction = failed
    ? 0.5
    : finished
      ? 1
      : status === "building"
        ? 0.65
        : status === "extracting"
          ? 0.4
          : status === "converting"
            ? 0.2
            : started
              ? 0.1
              : 0;
  const hasUploads = (job?.uploads.length ?? 0) > 0;
  const failureReason =
    [...events].reverse().find((e) => e.kind === "notice")?.message ??
    "The build stopped, but no reason was reported.";
  const elapsedS = firstAt === null ? 0 : Math.max(0, Math.round((now - firstAt) / 1000));
  const quietS = lastAt === null ? 0 : Math.max(0, Math.round((now - lastAt) / 1000));
  const limitMin = cfg ? Math.ceil(cfg.cad_timeout_s / 60) : null;
  const hasSolidWorks = (job?.uploads ?? []).some((u) =>
    SOLIDWORKS.some((x) => u.filename.toLowerCase().endsWith(x)),
  );
  const hasStep = (job?.uploads ?? []).some((u) => u.detected_type === "step" && u.origin === "user");
  const expires = job?.expires_at ? new Date(job.expires_at).toLocaleDateString() : null;

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{job?.product.name ?? "Loading…"}</h1>
        {job ? <span className={`badge badge-${failed ? "failed" : finished ? "done" : "info"}`}>Status: {status}</span> : null}
      </div>

      <WhatHappens>
        <p>
          Add the files you have. Each file is checked by its content (not just its name) and stored
          privately for this job. When you press <strong>Start building</strong>, we read the CAD file
          and report progress here.
        </p>
        <p className="small muted">
          The draft is built by fixed rules from your files. When it is ready, the review screen lets
          you edit it, accept or reject suggestions, approve it and download it.
        </p>
      </WhatHappens>

      {loadError ? (
        <p role="alert" className="alert alert-danger">
          {loadError}
        </p>
      ) : null}

      <section className="card" aria-labelledby="link-title">
        <h2 id="link-title">Your private link</h2>
        <p className="small">
          Keep this link. It is the only way back into this job, and anyone who has it can open it.
        </p>
        <div className="link-row">
          <input
            readOnly
            aria-label="Private job link"
            value={`${window.location.origin}/j/${id}#${token}`}
            onFocus={(e) => {
              e.currentTarget.select();
            }}
          />
          <button type="button" className="btn btn-secondary" onClick={() => void copyLink()}>
            Copy link
          </button>
        </div>
        <p role="status" className="small muted">
          {copied ? "Link copied." : ""}
        </p>
      </section>

      <section className="card" aria-labelledby="files-title">
        <h2 id="files-title">Add your files</h2>
        <label
          className={`dropzone${dragging ? " dropzone-over" : ""}${status !== "uploading" ? " dropzone-disabled" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => {
            setDragging(false);
          }}
          onDrop={onDrop}
        >
          <input
            type="file"
            multiple
            className="visually-hidden"
            disabled={status !== "uploading" || !cfg}
            accept={cfg?.accepted_extensions.join(",")}
            onChange={onPick}
          />
          <span className="dropzone-title">Drop files here, or press to choose</span>
          <span className="small muted">
            <Term word="STEP" meaning="A neutral 3D CAD file format (.step or .stp) that most CAD programs can export." />{" "}
            files up to {cfg?.max_cad_mb ?? "…"} MB; photos, parts lists and PDFs up to{" "}
            {cfg?.max_other_mb ?? "…"} MB.
            {cfg?.native_enabled
              ? " SolidWorks (.sldprt, .sldasm) and Fusion (.f3d) files are converted by Autodesk, only after you agree."
              : ""}
          </span>
        </label>

        {items.length > 0 ? (
          <ul className="file-list" aria-label="Files you added in this session">
            {items.map((it) => (
              <li key={it.key} className={`file file-${it.state}`}>
                <div className="file-row">
                  <span className="file-name">{it.name}</span>
                  <span className="muted small">{fmtSize(it.size)}</span>
                  <span className={`badge badge-${it.state === "error" ? "failed" : it.state === "done" ? "done" : "info"}`}>
                    {it.state === "queued"
                      ? "Waiting"
                      : it.state === "uploading"
                        ? "Uploading"
                        : it.state === "done"
                          ? "Added"
                          : "Not added"}
                  </span>
                </div>
                {it.state === "uploading" ? (
                  <progress max={1} value={it.progress} aria-label={`Uploading ${it.name}`} />
                ) : null}
                {it.state === "error" && it.message ? (
                  <p role="alert" className="alert alert-danger small">
                    {it.message}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}

        {job && job.uploads.length > 0 ? (
          <>
            <h3>Stored for this job</h3>
            <ul className="stored">
              {job.uploads.map((u) => (
                <li key={u.id}>
                  <span className="file-name">{u.filename}</span>{" "}
                  <span className="muted small">
                    {u.detected_type.toUpperCase()}, {fmtSize(u.size_bytes)}
                    {u.origin === "converted" ? ", converted by Autodesk" : ""}
                  </span>
                </li>
              ))}
            </ul>
            {hasSolidWorks && !hasStep && !started ? (
              <div className="callout" role="note">
                <strong className="callout-title">Also add a STEP AP242 export if you can</strong>
                <p className="small">
                  SolidWorks files are read from meshes through Autodesk, which gives part names,
                  part numbers and materials but less exact shapes. A STEP AP242 export of the same
                  model gives the best result; the properties from the SolidWorks file are then
                  matched to it by part name.
                </p>
              </div>
            ) : null}
            {job.uploads.some((u) =>
              [".xlsx", ".xls", ".csv"].some((ext) => u.filename.toLowerCase().endsWith(ext))
            ) ? (
              <div
                style={{
                  marginTop: "1rem",
                  padding: "0.75rem",
                  border: "1px solid var(--c-accent)",
                  borderRadius: "var(--radius, 6px)",
                  background: "var(--c-accent-soft)",
                }}
              >
                <p className="small" style={{ margin: 0 }}>
                  <strong>BoM file detected:</strong> You can review and confirm column mappings for your parts list.{" "}
                  <Link to={`/j/${id}/mapping`}>Review Column Mapping &rarr;</Link>
                </p>
              </div>
            ) : null}
          </>
        ) : null}
      </section>

      <section className="card" aria-labelledby="build-title">
        <h2 id="build-title">Build</h2>
        {!started ? (
          <>
            <p className="small">
              {hasUploads
                ? "Ready when you are. You cannot add more files after this."
                : "Add at least one file to continue."}
            </p>
            <div className="actions">
              <button
                type="button"
                className="btn btn-primary"
                disabled={!hasUploads || uploading}
                onClick={() => void onStart()}
              >
                Start building
              </button>
              <DemoPreview />
            </div>
          </>
        ) : (
          <ProgressPanel
            title={finished ? "Draft BoM ready" : failed ? "Building stopped" : "Building your BoM"}
            items={events}
            fraction={fraction}
            state={failed ? "failed" : finished ? "done" : "running"}
          />
        )}
        {started && running ? (
          <p className="small muted" role="status">
            Working for {fmtDuration(elapsedS)}. Last update {fmtDuration(quietS)} ago.
            {quietS > 45
              ? ` Large CAD files can take several minutes${limitMin ? ` (up to ${limitMin.toString()} min)` : ""}. If it takes longer, it will stop and tell you why.`
              : ""}
          </p>
        ) : null}
        {failed ? (
          <div role="alert" className="alert alert-danger">
            <strong>The build stopped.</strong>
            <p>{failureReason}</p>
            <p className="small">
              Your files are safe on the server. You can <Link to="/">start a new BoM</Link> and try again
              {" "}with a different export of the file.
            </p>
          </div>
        ) : null}
        {startError ? (
          <p role="alert" className="alert alert-danger">
            {startError}
          </p>
        ) : null}
      </section>

      {finished ? (
        <section className="card" aria-labelledby="result-title">
          <h2 id="result-title">What was built</h2>
          {draftError ? (
            <p role="alert" className="alert alert-danger">
              {draftError}
            </p>
          ) : draft ? (
            <DraftSummary draft={draft} onDownload={downloadDraft} reviewHref={`/j/${id}/review`} />
          ) : (
            <p role="status" className="small muted">
              Loading the draft…
            </p>
          )}
        </section>
      ) : null}

      <section className="card" aria-labelledby="data-title">
        <h2 id="data-title">Your data</h2>
        <p className="small">
          {expires
            ? `This job and its files are deleted automatically on ${expires}${cfg?.retention_days ? ` (${cfg.retention_days.toString()} days after the last change)` : ""}.`
            : "This job and its files are deleted automatically after a while."}{" "}
          You can delete everything now.
        </p>
        <div className="actions">
          <DeleteJobDialog
            jobId={id}
            token={token}
            disabled={running}
            onDeleted={() => {
              forgetToken(id);
              void navigate("/", { replace: true });
            }}
          />
        </div>
      </section>

      <NativeConsentDialog
        open={pendingNative !== null}
        fileNames={(pendingNative ?? []).filter((f) => NATIVE.includes(extOf(f.name))).map((f) => f.name)}
        consentText={cfg?.native_consent_text ?? "This file will be sent to Autodesk for conversion. Continue?"}
        usage={cfg?.native_usage ?? null}
        busy={consentBusy}
        onConfirm={() => {
          void onConsent();
        }}
        onCancel={() => {
          setPendingNative(null);
        }}
      />

      <Qa
        items={[
          {
            q: "Why was my file refused?",
            a: "We check what is really inside the file. A program renamed to .step, a file over the size limit, or a type we do not read yet are refused with a message that says which.",
          },
          {
            q: "Where are my files stored?",
            a: "On the server running this app, in a private folder for this job, outside the website’s public files. CAD files are only opened by a separate sandboxed reader with no internet access.",
          },
          {
            q: "Can I use SolidWorks or Fusion files?",
            a: cfg?.native_enabled
              ? "Yes. They are sent to Autodesk for conversion, only after you agree for this job. Fusion files become STEP; SolidWorks files are read from meshes plus their part properties. A STEP AP242 export from SolidWorks still gives the best shapes, so add one if you can."
              : "Not on this server: it has no conversion set up. Export a STEP file (AP242 gives the best result) and upload that.",
          },
        ]}
      />
    </div>
  );
}

function DraftSummary({
  draft,
  onDownload,
  reviewHref,
}: {
  draft: BomDraft;
  onDownload: () => void;
  reviewHref: string;
}) {
  const ebom = draft.ebom ?? [];
  const mbom = draft.mbom ?? [];
  const questions = draft.questions ?? [];
  const openQuestions = questions.filter((q) => q.answer === null || q.answer === undefined);
  const suggestions = draft.suggestions ?? [];
  const openSuggestions = suggestions.filter((x) => x.review === "suggested");
  const blocking = draft.validation?.blocking_issues ?? [];
  return (
    <>
      <ul className="small">
        <li>
          <strong>{ebom.length}</strong> EBOM lines and <strong>{mbom.length}</strong> MBOM lines
        </li>
        <li>
          <strong>{openQuestions.length}</strong> open question{openQuestions.length === 1 ? "" : "s"} for you
        </li>
        <li>
          <strong>{openSuggestions.length}</strong> suggestion{openSuggestions.length === 1 ? "" : "s"} waiting for
          your accept or reject (nothing is added until you accept)
        </li>
      </ul>
      {openQuestions.length > 0 ? (
        <>
          <h3>Questions</h3>
          <ul className="small">
            {openQuestions.map((q) => (
              <li key={q.id}>{q.text}</li>
            ))}
          </ul>
        </>
      ) : null}
      {openSuggestions.length > 0 ? (
        <>
          <h3>Suggestions</h3>
          <ul className="small">
            {openSuggestions.map((x) => (
              <li key={x.id}>{typeof x.description === "string" ? x.description : (x.text ?? x.id)}</li>
            ))}
          </ul>
        </>
      ) : null}
      <p className="small muted">
        {blocking.length > 0
          ? `Approval is blocked by ${blocking.length.toString()} open item${blocking.length === 1 ? "" : "s"}. Open the review screen to resolve them.`
          : "Nothing blocks approval."}
      </p>
      <div className="actions">
        <Link to={reviewHref} className="btn btn-primary">
          Open the review screen
        </Link>
        <button type="button" className="btn btn-secondary" onClick={onDownload}>
          Download draft (JSON)
        </button>
      </div>
    </>
  );
}

/** Shows the live progress pattern using the public demo stream (no job data involved). */
function DemoPreview() {
  const [items, setItems] = useState<ProgressItem[] | null>(null);
  const [done, setDone] = useState(false);
  const ctrl = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      ctrl.current?.abort();
    },
    [],
  );

  async function run() {
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setItems([]);
    setDone(false);
    try {
      await readSse("/demo/stream", {
        signal: c.signal,
        onMessage: (m) => {
          if (m.event === "done") {
            setDone(true);
            return;
          }
          const d = JSON.parse(m.data) as { step: number; message: string };
          setItems((prev) => [...(prev ?? []), { id: d.step, kind: "demo", message: d.message }]);
        },
      });
    } catch {
      /* aborted or offline: nothing to show */
    }
  }

  return (
    <div className="demo">
      <button type="button" className="btn btn-quiet" onClick={() => void run()}>
        Preview how progress looks
      </button>
      {items ? (
        <ProgressPanel
          title="Demo progress"
          items={items}
          fraction={items.length / 10}
          state={done ? "done" : "running"}
          note="This is only a preview. No files are used."
        />
      ) : null}
    </div>
  );
}
