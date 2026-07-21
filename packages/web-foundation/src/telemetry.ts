export type WebSurface = "consumer" | "admin" | "research";

export type StructuredWebEvent = {
  event: string;
  surface: WebSurface;
  correlation_id?: string | null;
  status?: string;
  latency_ms?: number;
  route?: string;
};

/** Emit bounded operational metadata without accepting message content or PII. */
export function emitStructuredWebEvent(event: StructuredWebEvent): void {
  const payload = { ...event, emitted_at: new Date().toISOString() };
  if (event.status === "error") console.error(JSON.stringify(payload));
  else console.info(JSON.stringify(payload));
}
