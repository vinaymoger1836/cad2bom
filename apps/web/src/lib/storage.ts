/** Storage can throw (private windows, blocked site data). Every access is wrapped. */

export function readSession(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeSession(key: string, value: string): void {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    /* the token then lives only in the URL fragment, which is enough */
  }
}

export function readLocal(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeLocal(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

const tokenKey = (jobId: string): string => `c2b.token.${jobId}`;

export function saveToken(jobId: string, token: string): void {
  writeSession(tokenKey(jobId), token);
  writeSession("c2b.lastJob", jobId);
}

/** Forget a job's token (after the job was deleted). */
export function forgetToken(jobId: string): void {
  try {
    window.sessionStorage.removeItem(tokenKey(jobId));
    if (window.sessionStorage.getItem("c2b.lastJob") === jobId) {
      window.sessionStorage.removeItem("c2b.lastJob");
    }
  } catch {
    /* nothing to forget */
  }
}

export function loadToken(jobId: string): string | null {
  return readSession(tokenKey(jobId));
}

export function lastJobId(): string | null {
  return readSession("c2b.lastJob");
}
