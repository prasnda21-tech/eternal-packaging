/** View-state for the bank reconciliation workspace. Error wins over empty. */

export function reconViewState({ loading = false, error = "", forbidden = false, conflict = false, count = 0 } = {}) {
  if (forbidden) return "forbidden";
  if (loading) return "loading";
  if (conflict) return "conflict";
  if (error) return "error";
  if (!count) return "empty";
  return "results";
}

export function shouldApplyResponse(requestId, latestId) {
  return requestId === latestId;
}

export function newOperationKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `op-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
