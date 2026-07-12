export type ApiState<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
};

const API_BASE = "/api/backend";
export const demoFallbackEnabled =
  process.env.NEXT_PUBLIC_DEMO_MODE !== "false";

/** API-ready fetch wrapper. In local demo mode callers supply deterministic fixtures. */
export async function apiRequest<T>(
  path: string,
  init?: RequestInit,
  demoData?: T,
): Promise<T> {
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as {
        detail?: string;
      } | null;
      throw new Error(payload?.detail ?? `Request failed (${response.status})`);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  } catch (error) {
    if (demoData !== undefined && demoFallbackEnabled) {
      await new Promise((resolve) => setTimeout(resolve, 180));
      return structuredClone(demoData);
    }
    throw error;
  }
}

export const isDemoMode = demoFallbackEnabled;
