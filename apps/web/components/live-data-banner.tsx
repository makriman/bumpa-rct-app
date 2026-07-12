"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";

export function LiveDataBanner({
  endpoint,
  label,
}: {
  endpoint: string;
  label: string;
}) {
  const [status, setStatus] = useState<"loading" | "live" | "demo">("loading");
  const [count, setCount] = useState<number | null>(null);
  useEffect(() => {
    let active = true;
    void apiRequest<unknown>(endpoint)
      .then((data) => {
        if (!active) return;
        setCount(Array.isArray(data) ? data.length : null);
        setStatus("live");
      })
      .catch(() => active && setStatus("demo"));
    return () => {
      active = false;
    };
  }, [endpoint]);
  return (
    <div
      className={`alert ${status === "live" ? "alert-success" : status === "demo" ? "alert-warning" : "alert-info"}`}
      role="status"
    >
      {status === "loading"
        ? `Checking the ${label} API…`
        : status === "live"
          ? `Live ${label} API connected${count === null ? "" : ` · ${count} records available`}. Backend RBAC is authoritative.`
          : `Demo fallback: the ${label} API is unavailable or this session lacks permission. Values below are labelled preview fixtures, not live tenant data.`}
    </div>
  );
}
