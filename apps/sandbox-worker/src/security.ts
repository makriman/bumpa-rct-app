export interface JsonObject {
  [key: string]: unknown;
}

export function timeoutValue(body: JsonObject): number {
  const value = body.timeout_ms;
  return typeof value === "number" && Number.isInteger(value)
    ? Math.min(30_000, Math.max(1_000, value))
    : 30_000;
}

export function workspacePath(value: string): string {
  if (value !== "/workspace" && !value.startsWith("/workspace/")) {
    throw new RequestError("invalid_path", 400);
  }
  const parts = value.split("/");
  if (parts.some((part) => part === ".." || part === ".")) {
    throw new RequestError("invalid_path", 400);
  }
  return value;
}

export class RequestError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
  ) {
    super(code);
  }
}
