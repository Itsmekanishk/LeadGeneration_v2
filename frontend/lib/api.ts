"use client";

const API_BASE = process.env.NEXT_PUBLIC_API_URL
  ? `${process.env.NEXT_PUBLIC_API_URL}/api`
  : "/backend/api";

const ACCESS_KEY = "ll.access";
const REFRESH_KEY = "ll.refresh";
const USER_KEY = "ll.user";

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACCESS_KEY);
}

export function getStoredUser(): import("@/types").User | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  return raw ? (JSON.parse(raw) as import("@/types").User) : null;
}

export function setAuth(tokens: import("@/types").TokenResponse) {
  localStorage.setItem(ACCESS_KEY, tokens.access_token);
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
  localStorage.setItem(USER_KEY, JSON.stringify(tokens.user));
}

export function clearAuth() {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
}

async function refreshTokens(): Promise<boolean> {
  const refresh = typeof window !== "undefined" ? localStorage.getItem(REFRESH_KEY) : null;
  if (!refresh) return false;
  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!res.ok) return false;
    const data = (await res.json()) as import("@/types").TokenResponse;
    setAuth(data);
    return true;
  } catch {
    return false;
  }
}

/**
 * Map HTTP status codes to user-friendly messages.
 * The backend never leaks internals; this adds the "what can I do about it"
 * layer so end users always get actionable guidance.
 */
const FRIENDLY_STATUS_MESSAGES: Record<number, string> = {
  400: "The request was invalid. Please adjust your search and try again.",
  401: "Your session has expired. Please sign in again.",
  403: "You don't have permission to perform this action.",
  404: "This item could not be found. It may belong to another account.",
  409: "This item was already created. Please refresh.",
  413: "The requested export is too large. Narrow your search first.",
  422: "Some fields need attention. Please review your input.",
  429: "You're going too fast — please wait a moment and try again.",
  500: "Something went wrong on our side. Please try again shortly.",
  502: "The service is temporarily unreachable. Please try again shortly.",
  503: "The service is under maintenance or at capacity. Please retry soon.",
  504: "The service took too long to respond. Please try again.",
};

export class ApiError extends Error {
  status: number;
  /** True when the failure is transient and a retry may succeed. */
  retryable: boolean;
  constructor(status: number, message: string, retryable = false) {
    super(message);
    this.status = status;
    this.retryable = retryable;
  }
}

async function request<T>(path: string, options: RequestInit = {}, retry = true): Promise<T> {
  const token = getAccessToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch {
    // Network-level failure (offline, DNS, proxy down) — never crash the page.
    throw new ApiError(
      0,
      "You appear to be offline. Check your connection and try again.",
      true,
    );
  }

  if (res.status === 401 && retry) {
    const refreshed = await refreshTokens();
    if (refreshed) return request<T>(path, options, false);
    clearAuth();
  }

  if (!res.ok) {
    // Prefer the backend's own clean detail message; fall back to the
    // friendly status mapping; never surface raw internals.
    let detail = FRIENDLY_STATUS_MESSAGES[res.status] ?? `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail && typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      /* keep friendly default */
    }
    const retryable = res.status === 429 || res.status >= 500;
    throw new ApiError(res.status, detail, retryable);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export function downloadUrl(jobId: string, fmt: "pdf" | "csv" | "xlsx", filename: string) {
  const base = process.env.NEXT_PUBLIC_API_URL
    ? `${process.env.NEXT_PUBLIC_API_URL}/api`
    : "/backend/api";
  return `${base}/search/${jobId}/export/${fmt}?filename=${encodeURIComponent(filename)}`;
}

/** Authenticated download via fetch → blob (adds the Bearer header). */
export async function downloadFile(jobId: string, fmt: "pdf" | "csv" | "xlsx", filename: string) {
  const token = getAccessToken();
  const base = process.env.NEXT_PUBLIC_API_URL
    ? `${process.env.NEXT_PUBLIC_API_URL}/api`
    : "/backend/api";
  const res = await fetch(
    `${base}/search/${jobId}/export/${fmt}?filename=${encodeURIComponent(filename)}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  );
  if (!res.ok) throw new ApiError(res.status, "Download failed");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
