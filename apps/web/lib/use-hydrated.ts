"use client";

import { useSyncExternalStore } from "react";

const subscribe = () => () => undefined;
const getClientSnapshot = () => true;
const getServerSnapshot = () => false;

/**
 * Keeps controls inert until React owns their event handlers. The server and
 * first hydration render agree on `false`; the client snapshot then enables
 * interaction without setting state from an effect.
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(subscribe, getClientSnapshot, getServerSnapshot);
}
