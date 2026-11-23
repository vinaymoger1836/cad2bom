import type {
  JobCreate,
  JobCreated,
  JobOut,
  ProblemDetail,
  UploadOut,
} from "@c2b/ts-types";

export const API = "/api";

export interface AppConfig {
  max_cad_mb: number;
  max_other_mb: number;
  max_files_per_job: number;
  accepted_extensions: string[];
  cad_extensions: string[];
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

export async function getMapping(id: string, token: string): Promise<any> {
  const res = await fetch(`${API}/jobs/${id}/mapping`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw problemFrom(res.status, await res.text());
  return res.json();
}

export async function confirmMapping(
  id: string,
  token: string,
  columns: Record<string, any>,
): Promise<any> {
  const res = await fetch(`${API}/jobs/${id}/mapping/confirm`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ columns }),
  });
  if (!res.ok) throw problemFrom(res.status, await res.text());
  return res.json();
}

export async function getBom(id: string, token: string): Promise<any> {
  const res = await fetch(`${API}/jobs/${id}/bom`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw problemFrom(res.status, await res.text());
  return res.json();
}
