import { useCallback, useEffect, useState } from "react";

/** Debounce for server-backed list/table product search (ms). */
export const LIVE_SEARCH_DEBOUNCE_MS = 300;

/**
 * Live list search: typing updates `q` after debounce; Enter flushes immediately.
 * Pure UI/state helper — callers own the GET; this never writes.
 */
export function useDebouncedLiveSearch(initial = "", debounceMs = LIVE_SEARCH_DEBOUNCE_MS) {
  const [input, setInput] = useState(initial);
  const [q, setQ] = useState(typeof initial === "string" ? initial.trim() : "");

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setQ(String(input ?? "").trim());
    }, debounceMs);
    return () => window.clearTimeout(handle);
  }, [input, debounceMs]);

  const flush = useCallback((event) => {
    event?.preventDefault?.();
    setQ(String(input ?? "").trim());
  }, [input]);

  const clear = useCallback(() => {
    setInput("");
    setQ("");
  }, []);

  return { input, setInput, q, flush, clear };
}
