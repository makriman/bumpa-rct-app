"use client";

import {
  readUrlFilters,
  type UrlFilterDefinitions,
  type UrlFilterValues,
  writeUrlFilters,
} from "@bumpabestie/web-foundation";
import { useCallback, useMemo, useSyncExternalStore } from "react";

const FILTER_CHANGE_EVENT = "bumpabestie:url-filters-change";

function subscribe(onStoreChange: () => void) {
  window.addEventListener("popstate", onStoreChange);
  window.addEventListener(FILTER_CHANGE_EVENT, onStoreChange);
  return () => {
    window.removeEventListener("popstate", onStoreChange);
    window.removeEventListener(FILTER_CHANGE_EVENT, onStoreChange);
  };
}

function clientSnapshot() {
  return window.location.search;
}

function serverSnapshot() {
  return "";
}

export function usePersistentFilters<TDefinitions extends UrlFilterDefinitions>(
  definitions: TDefinitions,
) {
  const search = useSyncExternalStore(
    subscribe,
    clientSnapshot,
    serverSnapshot,
  );
  const values = useMemo<UrlFilterValues<TDefinitions>>(
    () => readUrlFilters(search, definitions),
    [definitions, search],
  );

  const setFilter = useCallback(
    <TKey extends keyof TDefinitions>(key: TKey, value: string) => {
      const current = readUrlFilters(window.location.search, definitions);
      const nextValues = { ...current, [key]: value };
      const nextUrl = writeUrlFilters(
        window.location.href,
        definitions,
        nextValues,
      );
      window.history.replaceState(window.history.state, "", nextUrl);
      window.dispatchEvent(new Event(FILTER_CHANGE_EVENT));
    },
    [definitions],
  );

  return { setFilter, values };
}
