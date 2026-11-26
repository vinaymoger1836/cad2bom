import type {
  BomDraft,
  BomVersion,
  CadNode,
  ChatMessage,
  ChatResponse,
  JobCreate,
  JobCreated,
  JobOut,
  ProblemDetail,
  TableMapping,
  UploadOut,
} from "@c2b/ts-types";

export const API = "/api";

export interface AppConfig {
  max_cad_mb: number;
  max_other_mb: number;
  max_files_per_job: number;
  accepted_extensions: string[];
  cad_extensions: string[];
  /** How long the CAD reader may spend on one file, in seconds. */
  cad_timeout_s: number;
  /** Confidence cut-offs from the library: High >= high, Medium >= medium, else Low. */
  confidence_bands?: { high: number; medium: number };
  /** True when this server can convert SolidWorks and Fusion files through Autodesk. */
  native_enabled?: boolean;
  native_extensions?: string[];
  /** The exact words the person agrees to before a file is sent to Autodesk. */
  native_consent_text?: string;
  native_consent_text_version?: string;
  /** This month's Autodesk conversions against the server's cap (null when not enabled). */
  native_usage?: { used: number; cap: number } | null;
  retention_days?: number;
  admin_enabled?: boolean;
}

/** An error whose message is already written for the person using the app. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

function isProblem(v: unknown): v is ProblemDetail {
  return (
    typeof v === "object" &&
    v !== null &&
    "detail" in v &&
    typeof v.detail === "string"
  );
}

function problemFrom(status: number, text: string): ApiError {
  try {
    const body: unknown = JSON.parse(text);
    if (isProblem(body)) return new ApiError(status, body.code, body.detail);
  } catch {
    /* not JSON */
  }
  if (status === 413) {
    return new ApiError(
      status,
      "file_too_large",
      "The file is larger than the server accepts.",
    );
  }
  return new ApiError(
    status,
    "unknown",
    `Something went wrong (error ${status.toString()}). Please try again.`,
  );
}

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, init);
  } catch {
    throw new ApiError(
      0,
      "network",
      "Could not reach the server. Check your connection and try again.",
    );
  }
  const text = await res.text();
  if (!res.ok) throw problemFrom(res.status, text);
  return JSON.parse(text) as T;
}

export function getConfig(): Promise<AppConfig> {
  return request<AppConfig>("/config");
}

export function createJob(body: JobCreate): Promise<JobCreated> {
  return request<JobCreated>("/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getJob(id: string, token: string): Promise<JobOut> {
  return request<JobOut>(`/jobs/${id}`, { headers: authHeaders(token) });
}

export function startJob(id: string, token: string): Promise<JobOut> {
  return request<JobOut>(`/jobs/${id}/start`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

/** Record (or withdraw) consent to send native CAD files to Autodesk. Before any upload. */
export function setNativeConsent(id: string, token: string, consent: boolean): Promise<JobOut> {
  return request<JobOut>(`/jobs/${id}`, {
    method: "PATCH",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ consent_native_conversion: consent }),
  });
}

/** Delete the job and every file made for it, now. Cannot be undone. */
export function deleteJob(id: string, token: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/jobs/${id}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}

export interface UploadHandle {
  promise: Promise<UploadOut>;
  abort: () => void;
}

/** Raw-body upload with progress. The server's refusal message is passed through verbatim. */
export function uploadFile(
  id: string,
  token: string,
  file: File,
  onProgress: (fraction: number) => void,
): UploadHandle {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<UploadOut>((resolve, reject) => {
    xhr.open(
      "POST",
      `${API}/jobs/${id}/files?filename=${encodeURIComponent(file.name)}`,
    );
    xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(1);
        resolve(JSON.parse(xhr.responseText) as UploadOut);
      } else {
        reject(problemFrom(xhr.status, xhr.responseText));
      }
    };
    xhr.onerror = () => {
      reject(
        new ApiError(
          0,
          "network",
          `The upload of ${file.name} was interrupted. Check your connection and try again.`,
        ),
      );
    };
    xhr.onabort = () => {
      reject(new ApiError(0, "aborted", `The upload of ${file.name} was cancelled.`));
    };
    xhr.send(file);
  });
  return {
    promise,
    abort: () => {
      xhr.abort();
    },
  };
}

export interface MappingConfirmResult {
  status: string;
  message: string;
  /** True when a choice differs from what the draft BoM was built with. */
  rebuild_needed: boolean;
}

async function authedJson<T>(
  url: string,
  token: string,
  init?: { method: string; body: string },
): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init ? { "Content-Type": "application/json" } : {}),
    },
  });
  if (!res.ok) throw problemFrom(res.status, await res.text());
  return (await res.json()) as T;
}

export function getMapping(id: string, token: string): Promise<TableMapping> {
  return authedJson<TableMapping>(`${API}/jobs/${id}/mapping`, token);
}

export function confirmMapping(
  id: string,
  token: string,
  columns: Record<string, string>,
): Promise<MappingConfirmResult> {
  return authedJson<MappingConfirmResult>(`${API}/jobs/${id}/mapping/confirm`, token, {
    method: "POST",
    body: JSON.stringify({ columns }),
  });
}

export function getBom(id: string, token: string): Promise<BomDraft> {
  return authedJson<BomDraft>(`${API}/jobs/${id}/bom`, token);
}

/* ---- Review screen (P7) ------------------------------------------------------------------ */

export interface HistoryEntry {
  id: number;
  line_id: string | null;
  field: string;
  before: unknown;
  after: unknown;
  actor: string;
  kind: "edit" | "answer" | "suggestion";
  ref: string | null;
  at: string;
}

export interface ApprovalResult {
  status: string;
  version: number;
  job_id: string;
  /** True when nothing changed since the last version, so no new version was made. */
  unchanged: boolean;
}

export interface VersionSummary {
  version: number;
  created_at: string;
  ebom_lines: number;
  mbom_lines: number;
  open_items: number;
  library_version: string | null;
}

export type ExportFormat = "xlsx" | "csv" | "json" | "schema" | "pdf";

export function answerQuestion(
  id: string,
  token: string,
  questionId: string,
  answer: unknown,
): Promise<unknown> {
  return authedJson(`${API}/jobs/${id}/questions/${encodeURIComponent(questionId)}/answer`, token, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
}

export function reviewSuggestion(
  id: string,
  token: string,
  suggestionId: string,
  review: "accepted" | "rejected",
): Promise<unknown> {
  return authedJson(`${API}/jobs/${id}/suggestions/${encodeURIComponent(suggestionId)}`, token, {
    method: "POST",
    body: JSON.stringify({ review }),
  });
}

export function editLine(
  id: string,
  token: string,
  lineId: string,
  field: string,
  value: unknown,
): Promise<unknown> {
  return authedJson(`${API}/jobs/${id}/lines/${encodeURIComponent(lineId)}`, token, {
    method: "PATCH",
    body: JSON.stringify({ field, value }),
  });
}

export function approveBom(id: string, token: string): Promise<ApprovalResult> {
  return authedJson<ApprovalResult>(`${API}/jobs/${id}/approve`, token, {
    method: "POST",
    body: "{}",
  });
}

export function listVersions(id: string, token: string): Promise<VersionSummary[]> {
  return authedJson<VersionSummary[]>(`${API}/jobs/${id}/versions`, token);
}

export function getVersion(id: string, token: string, version: number): Promise<BomVersion> {
  return authedJson<BomVersion>(`${API}/jobs/${id}/versions/${version.toString()}`, token);
}

export function getLineHistory(id: string, token: string, lineId: string): Promise<HistoryEntry[]> {
  return authedJson<HistoryEntry[]>(
    `${API}/jobs/${id}/lines/${encodeURIComponent(lineId)}/history`,
    token,
  );
}

export function getExcluded(id: string, token: string): Promise<CadNode[]> {
  return authedJson<CadNode[]>(`${API}/jobs/${id}/excluded`, token);
}

export function sendChat(
  id: string,
  token: string,
  message: string,
  history: ChatMessage[],
): Promise<ChatResponse> {
  return authedJson<ChatResponse>(`${API}/jobs/${id}/chat`, token, {
    method: "POST",
    body: JSON.stringify({ message, history }),
  });
}

/** The GLB needs the Authorization header, so it is fetched here and parsed in memory. */
export async function getModelGlb(id: string, token: string): Promise<ArrayBuffer> {
  let res: Response;
  try {
    res = await fetch(`${API}/jobs/${id}/model.glb`, { headers: authHeaders(token) });
  } catch {
    throw new ApiError(0, "network", "Could not reach the server to load the 3D model.");
  }
  if (!res.ok) throw problemFrom(res.status, await res.text());
  return res.arrayBuffer();
}

/** Downloads an export through the Authorization header, then hands the file to the browser. */
export async function downloadExport(
  id: string,
  token: string,
  version: number,
  fmt: ExportFormat,
  part: "ebom" | "mbom" = "ebom",
): Promise<string> {
  let res: Response;
  try {
    res = await fetch(
      `${API}/jobs/${id}/versions/${version.toString()}/export?fmt=${fmt}&part=${part}`,
      { headers: authHeaders(token) },
    );
  } catch {
    throw new ApiError(0, "network", "Could not reach the server. Check your connection and try again.");
  }
  if (!res.ok) throw problemFrom(res.status, await res.text());
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `bom_v${version.toString()}.${fmt}`;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 10_000);
  return name;
}
