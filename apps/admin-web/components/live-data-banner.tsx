"use client";

import type { DataSource } from "@/lib/api";
import type { ResourceStatus } from "@/lib/use-api-resource";

export function LiveDataBanner({
  label,
  source,
  status,
  count,
  error,
}: {
  label: string;
  source: DataSource | null;
  status: ResourceStatus;
  count?: number;
  error?: string | null;
}) {
  const tone =
    status === "error"
      ? "alert-danger"
      : source === "live"
        ? "alert-success"
        : "alert-info";
  return (
    <div
      className={`alert ${tone}`}
      role={status === "error" ? "alert" : "status"}
    >
      {status === "loading"
        ? `Loading ${label} from the API…`
        : status === "error"
          ? `${label} could not be loaded${error ? `: ${error}` : "."}`
          : source === "live"
            ? `Live ${label}${count === undefined ? "" : ` · ${count} record${count === 1 ? "" : "s"}`}. The values below came from the API.`
            : `Waiting for ${label} from the API.`}
    </div>
  );
}
