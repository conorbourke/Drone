/**
 * Fetch wrapper for the JSON API.
 *
 * - Always sends cookies (credentials: include) and the CSRF marker header
 *   `X-Requested-With: fetch` that the backend requires on state-changing requests.
 * - Parses FastAPI's `{detail: ...}` error bodies into a typed ApiError.
 * - On 401 (outside the login call) notifies the auth layer, which redirects to /login.
 */

/** One entry of a FastAPI 422 validation error. */
export interface FieldError {
  /** Location of the invalid value, for example ["body", "limits", "warn_mtow_kg"]. */
  loc: Array<string | number>;
  /** Plain-language message. */
  msg: string;
  type?: string;
}

export class ApiError extends Error {
  /** HTTP status; 0 when the request never reached the server. */
  readonly status: number;
  /** Plain-language detail, suitable for showing to the owner. */
  readonly detail: string;
  /** Per-field messages for 422 responses. */
  readonly fields: FieldError[];
  /** True when `detail` came from the server rather than a client-side fallback. */
  readonly hasDetail: boolean;

  constructor(status: number, detail: string, fields: FieldError[] = [], hasDetail = true) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.fields = fields;
    this.hasDetail = hasDetail;
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  /** JSON-serialisable request body. */
  body?: unknown;
  signal?: AbortSignal;
  /** Keep the request alive while the page unloads (used by the draft autosave). */
  keepalive?: boolean;
  /**
   * Do not treat a 401 as "session expired". Used by the login call (wrong password is a 401)
   * and the initial /me probe.
   */
  skipAuthRedirect?: boolean;
}

type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

/** Registered by AuthContext; called when any request (except login) returns 401. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

/** Strip Pydantic's "Value error, " prefix so messages read naturally. */
function cleanMessage(msg: string): string {
  return msg.replace(/^Value error,\s*/i, '').replace(/^Assertion failed,\s*/i, '');
}

/** Dotted path for a 422 location, without the leading "body". */
export function fieldErrorPath(error: FieldError): string {
  const parts = error.loc.filter((p, i) => !(i === 0 && (p === 'body' || p === 'query')));
  return parts.map(String).join('.');
}

function parseErrorBody(status: number, body: unknown, statusText: string): ApiError {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === 'string') {
      return new ApiError(status, detail);
    }
    if (Array.isArray(detail)) {
      const fields: FieldError[] = detail
        .filter((d): d is FieldError => !!d && typeof d === 'object' && 'msg' in d)
        .map((d) => ({ loc: Array.isArray(d.loc) ? d.loc : [], msg: cleanMessage(String(d.msg)), type: d.type }));
      const summary = fields
        .map((f) => {
          const path = fieldErrorPath(f);
          return path ? `${path}: ${f.msg}` : f.msg;
        })
        .join('; ');
      return new ApiError(status, summary || 'Validation failed', fields);
    }
  }
  return new ApiError(status, defaultMessage(status, statusText), [], false);
}

function defaultMessage(status: number, statusText: string): string {
  switch (status) {
    case 401:
      return 'Your session has expired. Please sign in again.';
    case 403:
      return 'The server refused this request.';
    case 404:
      return 'Not found.';
    case 409:
      return 'That name is already in use.';
    case 429:
      return 'Too many attempts. Please wait a few minutes and try again.';
    case 503:
      return 'The server is temporarily unavailable.';
    default:
      return statusText ? `Request failed (${status} ${statusText})` : `Request failed (${status})`;
  }
}

/**
 * Perform a JSON request against the API.
 *
 * Resolves with the parsed body (or undefined for 204). Rejects with ApiError.
 */
export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const headers: Record<string, string> = {
    Accept: 'application/json',
    'X-Requested-With': 'fetch',
  };
  let body: string | undefined;
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body,
      credentials: 'include',
      signal: options.signal,
      keepalive: options.keepalive,
      cache: 'no-store',
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error;
    }
    throw new ApiError(0, 'Could not reach the server. Check your connection and try again.', [], false);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  let parsed: unknown = null;
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) {
    try {
      parsed = await response.json();
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    const error = parseErrorBody(response.status, parsed, response.statusText);
    if (response.status === 401 && !options.skipAuthRedirect) {
      unauthorizedHandler?.();
    }
    throw error;
  }

  return parsed as T;
}

/** True for a 401 that the auth layer already handled (callers should stay quiet). */
export function isAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/** True when the request was aborted by the caller. */
export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

/** Plain-language message for any thrown value. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error) return error.message;
  return 'Something went wrong.';
}

/**
 * Send a multipart form (file uploads). Same headers, credentials, error parsing and 401
 * handling as `api`; the browser sets the multipart Content-Type with its boundary.
 */
export async function apiUpload<T>(path: string, form: FormData, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: { Accept: 'application/json', 'X-Requested-With': 'fetch' },
      body: form,
      credentials: 'include',
      signal,
      cache: 'no-store',
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, 'Could not reach the server. Check your connection and try again.', [], false);
  }
  let parsed: unknown = null;
  if ((response.headers.get('content-type') ?? '').includes('application/json')) {
    try {
      parsed = await response.json();
    } catch {
      parsed = null;
    }
  }
  if (!response.ok) {
    const error =
      response.status === 413 && !(parsed && typeof parsed === 'object' && 'detail' in parsed)
        ? new ApiError(413, 'The file is too large (at most 15 MB).')
        : parseErrorBody(response.status, parsed, response.statusText);
    if (response.status === 401) unauthorizedHandler?.();
    throw error;
  }
  return parsed as T;
}
