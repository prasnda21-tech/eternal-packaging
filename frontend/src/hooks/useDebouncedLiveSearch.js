import { useCallback, useEffect, useState } from "react";

/** Debounce for server-backed list/table product search (ms). */
export const LIVE_SEARCH_DEBOUNCE_MS = 300;

/** True while typed input has not yet committed to settled query `q`. */
export function isDebouncedSearchPending(input, settledQ) {
  return String(input ?? "").trim() !== String(settledQ ?? "").trim();
}

/**
 * Table body view for live list search.
 * Pending/loading clears interactive rows so stale results are never editable.
 * Empty only after the latest query has settled.
 */
export function liveSearchTableView({ pending, loading, itemCount }) {
  if (pending || loading) return "loading";
  if (!itemCount) return "empty";
  return "results";
}

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

  const pending = isDebouncedSearchPending(input, q);

  return { input, setInput, q, flush, clear, pending };
}
