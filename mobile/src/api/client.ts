import { getApiRoot } from "../utils/config";
import { clearStoredToken, getStoredToken } from "../auth/session";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown, message?: string) {
    super(message || (typeof detail === "string" ? detail : `HTTP ${status}`));
    this.status = status;
    this.detail = detail;
  }
}

type RequestOpts = {
  method?: string;
  body?: unknown;
  formData?: FormData;
  token?: string | null;
  authQuery?: boolean;
};

async function resolveToken(explicit?: string | null): Promise<string | null> {
  if (explicit !== undefined) return explicit;
  return getStoredToken();
}

export async function apiRequest<T = unknown>(path: string, opts: RequestOpts = {}): Promise<T> {
  const token = await resolveToken(opts.token);
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  let body: BodyInit | undefined;
  if (opts.formData) {
    body = opts.formData;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  let url = `${getApiRoot()}${path.startsWith("/") ? path : `/${path}`}`;
  if (opts.authQuery && token) {
    const sep = url.includes("?") ? "&" : "?";
    url = `${url}${sep}auth=${encodeURIComponent(token)}`;
  }

  const res = await fetch(url, {
    method: opts.method || (opts.body || opts.formData ? "POST" : "GET"),
    headers,
    body,
  });

  if (res.status === 401) {
    await clearStoredToken();
  }

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    const detail =
      data && typeof data === "object" && data !== null && "detail" in data
        ? (data as { detail: unknown }).detail
        : data;
    throw new ApiError(res.status, detail);
  }

  return data as T;
}

export function authenticatedFileUrl(path: string, token: string | null): string {
  const base = `${getApiRoot()}${path.startsWith("/") ? path : `/${path}`}`;
  if (!token) return base;
  const sep = base.includes("?") ? "&" : "?";
  return `${base}${sep}auth=${encodeURIComponent(token)}`;
}
