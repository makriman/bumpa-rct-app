"use client";

import { useCallback, useEffect, useState } from "react";
import {
  sourcedApiRequest,
  type DataSource,
  type SourcedResponse,
} from "./api";

export type ResourceStatus = "loading" | "ready" | "error";

export type ApiResource<T> = {
  data: T | null;
  source: DataSource | null;
  status: ResourceStatus;
  error: string | null;
  reload: () => Promise<void>;
  replace: (data: T) => void;
};

function errorMessage(reason: unknown): string {
  return reason instanceof Error
    ? reason.message
    : "The service returned an unexpected error.";
}

export function useApiResource<T>(path: string, demoData?: T): ApiResource<T> {
  const [result, setResult] = useState<SourcedResponse<T> | null>(null);
  const [status, setStatus] = useState<ResourceStatus>("loading");
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const next = await sourcedApiRequest(path, demoData);
      setResult(next);
      setStatus("ready");
    } catch (reason) {
      setResult(null);
      setError(errorMessage(reason));
      setStatus("error");
    }
  }, [demoData, path]);

  useEffect(() => {
    let active = true;
    setStatus("loading");
    setError(null);
    void sourcedApiRequest(path, demoData)
      .then((next) => {
        if (!active) return;
        setResult(next);
        setStatus("ready");
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setResult(null);
        setError(errorMessage(reason));
        setStatus("error");
      });
    return () => {
      active = false;
    };
  }, [demoData, path]);

  const replace = useCallback((data: T) => {
    setResult((current) => ({ data, source: current?.source ?? "live" }));
    setError(null);
    setStatus("ready");
  }, []);

  return {
    data: result?.data ?? null,
    source: result?.source ?? null,
    status,
    error,
    reload,
    replace,
  };
}
