export type ApiErrorPayload = {
  detail?: string | { code?: string; message?: string; retryable?: boolean };
  error?: { code?: string; message?: string };
};

function optionalString(value: unknown) {
  return typeof value === "string" ? value : undefined;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseApiErrorPayload(value: unknown): ApiErrorPayload | null {
  if (!isRecord(value)) return null;
  const detail = value.detail;
  const error = value.error;
  return {
    detail:
      typeof detail === "string"
        ? detail
        : isRecord(detail)
          ? {
              code: optionalString(detail.code),
              message: optionalString(detail.message),
              retryable:
                typeof detail.retryable === "boolean"
                  ? detail.retryable
                  : undefined,
            }
          : undefined,
    error: isRecord(error)
      ? {
          code: optionalString(error.code),
          message: optionalString(error.message),
        }
      : undefined,
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId: string | null;
  readonly retryable: boolean;

  constructor({
    status,
    code,
    message,
    correlationId,
    retryable = false,
  }: {
    status: number;
    code: string;
    message: string;
    correlationId: string | null;
    retryable?: boolean;
  }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.correlationId = correlationId;
    this.retryable = retryable;
  }
}

export function responseError(
  response: Response,
  payload: ApiErrorPayload | null,
): ApiError {
  const structuredDetail =
    payload?.detail && typeof payload.detail === "object"
      ? payload.detail
      : null;
  return new ApiError({
    status: response.status,
    code:
      structuredDetail?.code ??
      payload?.error?.code ??
      `http_${response.status}`,
    message:
      structuredDetail?.message ??
      (typeof payload?.detail === "string" ? payload.detail : null) ??
      payload?.error?.message ??
      `Request failed (${response.status})`,
    correlationId: response.headers.get("X-Correlation-ID"),
    retryable:
      structuredDetail?.retryable ??
      (response.status === 429 || response.status >= 500),
  });
}
