/**
 * Share one in-flight Promise across concurrent identical callers.
 * Used to collapse duplicate mount-time GETs (e.g. two OperatorSplitPanels).
 *
 * Optional `keyFn` scopes the inflight slot (e.g. getOrder(id)).
 * Without it, all callers share one slot — same as the original helper.
 */
export function dedupeInflight(fn, keyFn) {
  const inflight = new Map();
  return function deduped(...args) {
    const key = keyFn ? String(keyFn(...args)) : "__default__";
    const existing = inflight.get(key);
    if (existing) return existing;
    const pending = Promise.resolve(fn(...args)).finally(() => {
      inflight.delete(key);
    });
    inflight.set(key, pending);
    return pending;
  };
}
