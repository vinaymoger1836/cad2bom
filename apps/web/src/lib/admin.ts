/** Admin API client (ADR-020). The session token goes in X-Admin-Token: the browser already
 * uses the Authorization header for the reverse proxy's basic-auth credentials. */

import { API, ApiError } from "./api";

export interface VersionInfo {
  version: string;
  released: string;
  changelog: string[];
  checksum: string;
  file_count: number;
  active: boolean;
}

export interface DraftInfo {
  id: string;
  base: string;
}

export interface VersionsResponse {
  active: string;
  versions: VersionInfo[];
  drafts: DraftInfo[];
  next_version: string;
}

export interface ValidationIssue {
  level: "error" | "warning" | "info";
  code: string;
  file: string;
  message: string;
}

export interface ValidationResult {
  ok: boolean;
  errors: number;
  warnings: number;
  issues: ValidationIssue[];
}

export interface FileChange {
  file: string;
  status: "added" | "removed" | "changed";
  added: number;
  removed: number;
  diff: string[];
}

export interface DiffResult {
  a: string;
  b: string;
  files_changed: number;
  added: number;
  removed: number;
  changes: FileChange[];
}

export interface AdminStatus {
  library_active: string;
  aps_configured: boolean;
  aps_mode: string;
  aps_usage: { used: number; cap: number };
  retention_days: number;
  environment: string;
}

const TOKEN_KEY = "c2b.admin.token";

export function loadAdminToken(): string | null {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function saveAdminToken(token: string | null): void {
  try {
    if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
    else window.sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* the person signs in again after a reload */
  }
}

async function call<T>(
  path: string,
  token: string | null,
  init?: { method: string; body?: unknown },
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API}/admin${path}`, {
      method: init?.method ?? "GET",
      headers: {
        ...(token ? { "X-Admin-Token": token } : {}),
        ...(init?.body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      ...(init?.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
    });
  } catch {
    throw new ApiError(0, "network", "Could not reach the server. Check your connection and try again.");
  }
  const text = await res.text();
  if (!res.ok) {
    try {
      const body = JSON.parse(text) as { detail?: string; code?: string };
      if (typeof body.detail === "string") {
        throw new ApiError(res.status, body.code ?? "unknown", body.detail);
      }
    } catch (e) {
      if (e instanceof ApiError) throw e;
    }
    throw new ApiError(res.status, "unknown", `Something went wrong (error ${res.status.toString()}).`);
  }
  return JSON.parse(text) as T;
}

export const adminApi = {
  login: (passcode: string) =>
    call<{ token: string; expires_in: number }>("/login", null, { method: "POST", body: { passcode } }),
  status: (t: string) => call<AdminStatus>("/status", t),
  versions: (t: string) => call<VersionsResponse>("/library/versions", t),
  versionFiles: (t: string, v: string) =>
    call<string[]>(`/library/versions/${encodeURIComponent(v)}/files`, t),
  versionFile: (t: string, v: string, path: string) =>
    call<{ path: string; content: string }>(
      `/library/versions/${encodeURIComponent(v)}/file?path=${encodeURIComponent(path)}`,
      t,
    ),
  createDraft: (t: string, from: string) =>
    call<DraftInfo>("/library/drafts", t, { method: "POST", body: { from_version: from } }),
  draftFiles: (t: string, id: string) => call<string[]>(`/library/drafts/${id}/files`, t),
  draftFile: (t: string, id: string, path: string) =>
    call<{ path: string; content: string }>(
      `/library/drafts/${id}/file?path=${encodeURIComponent(path)}`,
      t,
    ),
  saveDraftFile: (t: string, id: string, path: string, content: string) =>
    call<{ status: string }>(`/library/drafts/${id}/file?path=${encodeURIComponent(path)}`, t, {
      method: "PUT",
      body: { content },
    }),
  validate: (t: string, id: string) =>
    call<ValidationResult>(`/library/drafts/${id}/validate`, t, { method: "POST" }),
  publish: (t: string, id: string, changelog: string[]) =>
    call<{ version: string }>(`/library/drafts/${id}/publish`, t, {
      method: "POST",
      body: { changelog },
    }),
  deleteDraft: (t: string, id: string) =>
    call<{ status: string }>(`/library/drafts/${id}`, t, { method: "DELETE" }),
  diff: (t: string, a: string, b: string) =>
    call<DiffResult>(`/library/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`, t),
};
