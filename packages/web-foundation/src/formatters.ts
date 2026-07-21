const LAGOS_DATE_TIME = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Africa/Lagos",
});

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

const SUCCESS_STATES = new Set([
  "active",
  "approved",
  "healthy",
  "success",
  "ready",
  "good",
  "granted",
  "resolved",
  "connected",
]);
const DANGER_STATES = new Set([
  "failed",
  "offline",
  "suspended",
  "withdrawn",
  "high",
  "open",
]);
const WARNING_STATES = new Set([
  "pending",
  "partial",
  "attention",
  "onboarding",
  "review",
  "medium",
  "investigating",
  "running",
  "setup",
]);

export function statusTone(status: string): Tone {
  const normalized = status.toLowerCase();
  if (SUCCESS_STATES.has(normalized)) return "success";
  if (DANGER_STATES.has(normalized)) return "danger";
  if (WARNING_STATES.has(normalized)) return "warning";
  if (normalized === "web" || normalized === "whatsapp") return "info";
  return "neutral";
}

export function titleCase(value: string | null | undefined): string {
  if (!value) return "Not available";
  return value
    .replaceAll("_", " ")
    .replaceAll(".", " · ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function formatLagosDate(value: string | null | undefined): string {
  if (!value) return "Not yet";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unavailable";
  return LAGOS_DATE_TIME.format(date);
}

export function durationBetween(start: string, finish?: string | null): string {
  if (!finish) return "In progress";
  const milliseconds = new Date(finish).getTime() - new Date(start).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "Unavailable";
  const seconds = Math.round(milliseconds / 1000);
  return seconds < 60
    ? `${seconds}s`
    : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function maskPhone(value: string): string {
  if (value.length < 7) return value;
  return `${value.slice(0, 6)} ••• ${value.slice(-4)}`;
}

export function countValues<T>(
  values: T[],
  key: (value: T) => string | null | undefined,
): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const item of values) {
    const label = key(item) || "unclassified";
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}
