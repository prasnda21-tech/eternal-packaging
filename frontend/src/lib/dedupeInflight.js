/**
 * Share one in-flight Promise across concurrent identical callers.
 * Used to collapse duplicate mount-time GETs (e.g. two OperatorSplitPanels).
 */
export function dedupeInflight(fn) {
  let inflight = null;
  return function deduped(...args) {
    if (inflight) return inflight;
    inflight = Promise.resolve(fn(...args)).finally(() => {
      inflight = null;
    });
    return inflight;
  };
}
