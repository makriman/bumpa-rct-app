import {
  ApiError,
  parseApiErrorPayload,
  responseError,
} from "@bumpabestie/web-foundation";

export { ApiError };

export type ApiState<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
};

const API_BASE = "/api/backend";

export type DataSource = "live";

export type SourcedResponse<T> = {
  data: T;
  source: DataSource;
};

/** Same-origin API wrapper. Production UI never substitutes fixture data. */
export function apiRequest<T>(path: string, init?: RequestInit): Promise<T>;
export async function apiRequest(
  path: string,
  init?: RequestInit,
): Promise<unknown> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const payload = parseApiErrorPayload(
      await response.json().catch(() => null),
    );
    throw responseError(response, payload);
  }
  if (response.status === 204 || response.status === 205) {
    return undefined;
  }
  return response.json();
}

/** Loads a resource while making its API origin explicit to the UI. */
export async function sourcedApiRequest<T>(
  path: string,
): Promise<SourcedResponse<T>> {
  return { data: await apiRequest<T>(path), source: "live" };
}
