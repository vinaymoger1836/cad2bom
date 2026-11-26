import { useCallback, useEffect, useMemo, useState, type SyntheticEvent } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import { ApiError } from "../lib/api";
import {
  adminApi,
  loadAdminToken,
  saveAdminToken,
  type AdminStatus,
  type DiffResult,
  type DraftInfo,
  type ValidationResult,
  type VersionInfo,
  type VersionsResponse,
} from "../lib/admin";

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
}

export function Admin() {
  const [token, setToken] = useState<string | null>(() => loadAdminToken());

  const signOut = useCallback(() => {
    saveAdminToken(null);
    setToken(null);
  }, []);

  if (!token) {
    return (
      <Login
        onSignedIn={(t) => {
          saveAdminToken(t);
          setToken(t);
        }}
      />
    );
  }
  return <Console token={token} onSignOut={signOut} />;
}

function Login({ onSignedIn }: { onSignedIn: (token: string) => void }) {
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: SyntheticEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await adminApi.login(passcode);
      onSignedIn(r.token);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 404
          ? "The admin page is not turned on for this server."
          : message(err),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack admin-narrow">
      <h1>Library admin</h1>
      <p>
        Edit the rule library without a code release. Changes are made on a draft, checked, and
        published as a new version. Jobs that already exist keep the version they were built with.
      </p>
      <form
        className="card form"
        onSubmit={(e) => {
          void submit(e);
        }}
        aria-labelledby="admin-login-title"
      >
        <h2 id="admin-login-title">Sign in</h2>
        <div className="field">
          <label htmlFor="admin-passcode">Admin passcode</label>
          <input
            id="admin-passcode"
            type="password"
            autoComplete="current-password"
            value={passcode}
            onChange={(e) => {
              setPasscode(e.target.value);
            }}
            required
          />
          <span className="hint">
            You already passed the web login for this page. The passcode is the second check.
          </span>
        </div>
        {error ? (
          <p role="alert" className="alert alert-danger">
            {error}
          </p>
        ) : null}
        <div className="actions">
          <button type="submit" className="btn btn-primary" disabled={busy || passcode === ""}>
            {busy ? "Checking…" : "Sign in"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Console({ token, onSignOut }: { token: string; onSignOut: () => void }) {
  const [tab, setTab] = useState("versions");
  const [data, setData] = useState<VersionsResponse | null>(null);
  const [status, setStatus] = useState<AdminStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [draftId, setDraftId] = useState<string | null>(null);

  const guard = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) {
        onSignOut();
        return;
      }
      setError(message(e));
    },
    [onSignOut],
  );

  const reload = useCallback(async () => {
    try {
      const [v, s] = await Promise.all([adminApi.versions(token), adminApi.status(token)]);
      setData(v);
      setStatus(s);
      setError(null);
    } catch (e) {
      guard(e);
    }
  }, [token, guard]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([adminApi.versions(token), adminApi.status(token)])
      .then(([v, s]) => {
        if (cancelled) return;
        setData(v);
        setStatus(s);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) guard(e);
      });
    return () => {
      cancelled = true;
    };
  }, [token, guard]);

  async function startDraft(from: string) {
    try {
      const d = await adminApi.createDraft(token, from);
      await reload();
      setDraftId(d.id);
      setNotice(`Draft ${d.id} started from ${from}.`);
      setTab("edit");
    } catch (e) {
      guard(e);
    }
  }

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Library admin</h1>
        <button type="button" className="btn btn-secondary" onClick={onSignOut}>
          Sign out
        </button>
      </div>

      {status ? (
        <ul className="admin-chips" aria-label="Server status">
          <li className="badge badge-done">Active library {status.library_active}</li>
          <li className="badge badge-info">
            Autodesk: {status.aps_configured ? `${status.aps_usage.used.toString()} / ${status.aps_usage.cap.toString()} this month` : "not configured"}
          </li>
          <li className="badge badge-info">Jobs kept {status.retention_days.toString()} days</li>
          <li className="badge badge-info">{status.environment}</li>
        </ul>
      ) : null}

      {error ? (
        <p role="alert" className="alert alert-danger">
          {error}
        </p>
      ) : null}
      <p role="status" className="small muted admin-notice">
        {notice ?? ""}
      </p>

      <Tabs.Root value={tab} onValueChange={setTab} className="admin-tabs">
        <Tabs.List aria-label="Library admin sections" className="admin-tablist">
          <Tabs.Trigger value="versions" className="admin-tab">
            Versions
          </Tabs.Trigger>
          <Tabs.Trigger value="edit" className="admin-tab">
            Edit a draft
          </Tabs.Trigger>
          <Tabs.Trigger value="compare" className="admin-tab">
            Compare
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="versions" className="admin-panel">
          {data ? (
            <VersionsTab
              data={data}
              onStartDraft={(v) => {
                void startDraft(v);
              }}
              onOpenDraft={(id) => {
                setDraftId(id);
                setTab("edit");
              }}
              onDiscard={(id) => {
                adminApi
                  .deleteDraft(token, id)
                  .then(() => {
                    if (draftId === id) setDraftId(null);
                    setNotice(`Draft ${id} discarded.`);
                    return reload();
                  })
                  .catch(guard);
              }}
            />
          ) : (
            <p role="status" className="muted">
              Loading…
            </p>
          )}
        </Tabs.Content>

        <Tabs.Content value="edit" className="admin-panel">
          {data && draftId && data.drafts.some((d) => d.id === draftId) ? (
            <DraftEditor
              key={draftId}
              token={token}
              draft={data.drafts.find((d) => d.id === draftId) as DraftInfo}
              nextVersion={data.next_version}
              guard={guard}
              onPublished={(v) => {
                setNotice(`Published ${v}. New jobs use it from now on; existing jobs keep their version.`);
                setDraftId(null);
                setTab("versions");
                void reload();
              }}
              onCompare={() => {
                setTab("compare");
              }}
            />
          ) : (
            <p className="muted">
              No draft is open. Start one from a version on the <strong>Versions</strong> tab.
            </p>
          )}
        </Tabs.Content>

        <Tabs.Content value="compare" className="admin-panel">
          {data ? (
            <CompareTab token={token} data={data} draftId={draftId} guard={guard} />
          ) : null}
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}

function VersionsTab({
  data,
  onStartDraft,
  onOpenDraft,
  onDiscard,
}: {
  data: VersionsResponse;
  onStartDraft: (version: string) => void;
  onOpenDraft: (id: string) => void;
  onDiscard: (id: string) => void;
}) {
  return (
    <div className="stack">
      <section aria-labelledby="versions-title" className="stack">
        <h2 id="versions-title">Published versions</h2>
        <p className="small muted">
          A published version never changes. The active version is the one new jobs are built with;
          each job stays on the version it started with.
        </p>
        <div className="table-wrap">
          <table className="admin-table">
            <caption className="visually-hidden">Published library versions</caption>
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col">Released</th>
                <th scope="col">Files</th>
                <th scope="col">What changed</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.versions.map((v: VersionInfo) => (
                <tr key={v.version}>
                  <th scope="row">
                    {v.version}{" "}
                    {v.active ? <span className="badge badge-done">Active</span> : null}
                  </th>
                  <td>{v.released}</td>
                  <td>{v.file_count}</td>
                  <td>
                    <ul className="admin-changelog">
                      {v.changelog.map((c, i) => (
                        <li key={i.toString()}>{c}</li>
                      ))}
                    </ul>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      onClick={() => {
                        onStartDraft(v.version);
                      }}
                    >
                      Start a draft from {v.version}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="drafts-title" className="stack">
        <h2 id="drafts-title">Drafts</h2>
        {data.drafts.length === 0 ? (
          <p className="muted small">No drafts. A draft is an editable copy; nothing in it affects any job until you publish it.</p>
        ) : (
          <ul className="admin-drafts">
            {data.drafts.map((d) => (
              <li key={d.id}>
                <span>
                  Draft <code>{d.id}</code> from {d.base}
                </span>
                <span className="actions">
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => {
                      onOpenDraft(d.id);
                    }}
                  >
                    Open
                  </button>
                  <button
                    type="button"
                    className="btn btn-quiet"
                    onClick={() => {
                      onDiscard(d.id);
                    }}
                  >
                    Discard
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function DraftEditor({
  token,
  draft,
  nextVersion,
  guard,
  onPublished,
  onCompare,
}: {
  token: string;
  draft: DraftInfo;
  nextVersion: string;
  guard: (e: unknown) => void;
  onPublished: (version: string) => void;
  onCompare: () => void;
}) {
  const [files, setFiles] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [current, setCurrent] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [switchWarn, setSwitchWarn] = useState(false);
  const [result, setResult] = useState<ValidationResult | null>(null);
  const [busy, setBusy] = useState(false);
  const dirty = current !== null && text !== saved;

  useEffect(() => {
    adminApi
      .draftFiles(token, draft.id)
      .then(setFiles)
      .catch(guard);
  }, [token, draft.id, guard]);

  const shown = useMemo(
    () => files.filter((f) => f.toLowerCase().includes(filter.trim().toLowerCase())),
    [files, filter],
  );

  async function open(path: string) {
    if (dirty && path !== current) {
      setSwitchWarn(true);
      return;
    }
    setSwitchWarn(false);
    try {
      const f = await adminApi.draftFile(token, draft.id, path);
      setCurrent(path);
      setText(f.content);
      setSaved(f.content);
      setSaveError(null);
    } catch (e) {
      guard(e);
    }
  }

  async function save() {
    if (!current) return;
    setBusy(true);
    setSaveError(null);
    try {
      await adminApi.saveDraftFile(token, draft.id, current, text);
      setSaved(text);
      setResult(null); // an old check no longer describes the draft
    } catch (e) {
      setSaveError(message(e));
    } finally {
      setBusy(false);
    }
  }

  async function validate() {
    setBusy(true);
    try {
      setResult(await adminApi.validate(token, draft.id));
    } catch (e) {
      guard(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h2>
        Draft <code>{draft.id}</code> <span className="muted small">from {draft.base}</span>
      </h2>
      <div className="admin-editor">
        <nav aria-label="Library files" className="admin-files">
          <label htmlFor="file-filter" className="visually-hidden">
            Filter files
          </label>
          <input
            id="file-filter"
            type="search"
            placeholder="Filter files"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
            }}
          />
          <ul>
            {shown.map((f) => (
              <li key={f}>
                <button
                  type="button"
                  className="admin-file"
                  aria-current={f === current ? "true" : undefined}
                  disabled={f === "VERSION.yaml"}
                  title={f === "VERSION.yaml" ? "Set automatically when you publish" : undefined}
                  onClick={() => {
                    void open(f);
                  }}
                >
                  {f}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="stack admin-edit-pane">
          {switchWarn ? (
            <p role="alert" className="alert alert-warning">
              You have unsaved changes in {current}. Save the file, or press Revert, before opening
              another.
            </p>
          ) : null}
          {current ? (
            <>
              <label htmlFor="file-text" className="admin-file-name">
                {current} {dirty ? <span className="badge badge-info">Unsaved</span> : null}
              </label>
              <textarea
                id="file-text"
                className="admin-textarea"
                spellCheck={false}
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                }}
                onKeyDown={(e) => {
                  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
                    e.preventDefault();
                    void save();
                  }
                }}
              />
              {saveError ? (
                <p role="alert" className="alert alert-danger">
                  {saveError}
                </p>
              ) : null}
              <div className="actions">
                <button type="button" className="btn btn-primary" disabled={!dirty || busy} onClick={() => void save()}>
                  Save file
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={!dirty}
                  onClick={() => {
                    setText(saved);
                    setSwitchWarn(false);
                    setSaveError(null);
                  }}
                >
                  Revert
                </button>
                <span className="small muted">Ctrl+S saves.</span>
              </div>
            </>
          ) : (
            <p className="muted">Choose a file on the left to edit it.</p>
          )}
        </div>
      </div>

      <section className="card" aria-labelledby="publish-title">
        <h3 id="publish-title">Check and publish</h3>
        <p className="small muted">
          Checking runs the same validation as <code>c2b-library validate</code>. Publishing needs a
          clean check, freezes the draft as <strong>{nextVersion}</strong> and makes it the version
          new jobs use.
        </p>
        <div className="actions">
          <button type="button" className="btn btn-secondary" disabled={busy || dirty} onClick={() => void validate()}>
            Check the draft
          </button>
          <button type="button" className="btn btn-secondary" onClick={onCompare}>
            Compare…
          </button>
          <PublishDialog
            token={token}
            draftId={draft.id}
            version={nextVersion}
            disabled={dirty || busy || !(result?.ok ?? false)}
            guard={guard}
            onPublished={onPublished}
          />
        </div>
        {dirty ? <p className="small muted">Save your file first.</p> : null}
        {result ? (
          <div role="status" className="stack">
            <p className={result.ok ? "alert alert-success" : "alert alert-danger"}>
              {result.ok
                ? `The draft is valid (${result.warnings.toString()} warning${result.warnings === 1 ? "" : "s"}).`
                : `${result.errors.toString()} error${result.errors === 1 ? "" : "s"} to fix before publishing.`}
            </p>
            {result.issues.length > 0 ? (
              <ul className="admin-issues">
                {result.issues.map((i, n) => (
                  <li key={`${i.code}-${n.toString()}`} className={`issue-${i.level}`}>
                    <strong>{i.level}</strong> {i.file}: {i.message} <code>{i.code}</code>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}

function PublishDialog({
  token,
  draftId,
  version,
  disabled,
  guard,
  onPublished,
}: {
  token: string;
  draftId: string;
  version: string;
  disabled: boolean;
  guard: (e: unknown) => void;
  onPublished: (version: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function publish() {
    setBusy(true);
    setError(null);
    try {
      const entries = text
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l !== "");
      const r = await adminApi.publish(token, draftId, entries);
      setOpen(false);
      onPublished(r.version);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) guard(e);
      else setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger className="btn btn-primary" disabled={disabled}>
        Publish {version}…
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="rv-overlay" />
        <Dialog.Content className="rv-dialog" aria-describedby="publish-desc">
          <Dialog.Title>Publish {version}?</Dialog.Title>
          <div className="stack" id="publish-desc">
            <p>
              New jobs will use {version} from now on. Jobs that already exist keep their version
              and do not change.
            </p>
            <div className="field">
              <label htmlFor="changelog">What changed (one line per change)</label>
              <textarea
                id="changelog"
                rows={4}
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                }}
              />
            </div>
            {error ? (
              <p role="alert" className="alert alert-danger">
                {error}
              </p>
            ) : null}
            <div className="actions">
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || text.trim() === ""}
                onClick={() => void publish()}
              >
                {busy ? "Publishing…" : `Publish ${version}`}
              </button>
              <Dialog.Close className="btn btn-secondary">Cancel</Dialog.Close>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function CompareTab({
  token,
  data,
  draftId,
  guard,
}: {
  token: string;
  data: VersionsResponse;
  draftId: string | null;
  guard: (e: unknown) => void;
}) {
  const options = [
    ...data.versions.map((v) => ({ value: v.version, label: `Version ${v.version}` })),
    ...data.drafts.map((d) => ({ value: `draft:${d.id}`, label: `Draft ${d.id}` })),
  ];
  const second = data.versions[1]?.version ?? data.versions[0]?.version ?? "";
  const [a, setA] = useState(second);
  const [b, setB] = useState(draftId ? `draft:${draftId}` : (data.versions[0]?.version ?? ""));
  const [result, setResult] = useState<DiffResult | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      setResult(await adminApi.diff(token, a, b));
    } catch (e) {
      guard(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div className="admin-compare-pick">
        <div className="field">
          <label htmlFor="cmp-a">From</label>
          <select id="cmp-a" value={a} onChange={(e) => { setA(e.target.value); }}>
            {options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="cmp-b">To</label>
          <select id="cmp-b" value={b} onChange={(e) => { setB(e.target.value); }}>
            {options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <button type="button" className="btn btn-primary" disabled={busy || a === b} onClick={() => void run()}>
          Compare
        </button>
      </div>
      {result ? (
        <div className="stack" role="status">
          <p>
            {result.files_changed === 0
              ? "No differences."
              : `${result.files_changed.toString()} file${result.files_changed === 1 ? "" : "s"} changed: ${result.added.toString()} lines added, ${result.removed.toString()} removed.`}
          </p>
          {result.changes.map((c) => (
            <details key={c.file} className="admin-diff">
              <summary>
                {c.file} <span className="badge badge-info">{c.status}</span>{" "}
                <span className="small muted">+{c.added} −{c.removed}</span>
              </summary>
              {/* A scrollable region must be reachable by keyboard. */}
              {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
              <pre className="admin-diff-body" tabIndex={0} aria-label={`Changes in ${c.file}`}>
                {c.diff.map((ln, i) => (
                  <span
                    key={i.toString()}
                    className={
                      ln.startsWith("+") && !ln.startsWith("+++")
                        ? "diff-add"
                        : ln.startsWith("-") && !ln.startsWith("---")
                          ? "diff-del"
                          : ln.startsWith("@@")
                            ? "diff-hunk"
                            : undefined
                    }
                  >
                    {ln}
                    {"\n"}
                  </span>
                ))}
              </pre>
            </details>
          ))}
        </div>
      ) : null}
    </div>
  );
}
