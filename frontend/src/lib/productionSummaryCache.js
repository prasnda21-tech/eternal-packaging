/** In-memory production dashboard summary for the two PWA schedule menus.

One successful response is kept per user and request parameters, only for a short TTL.
*/

export const PRODUCTION_SUMMARY_TTL_MS = 5000;

let clock = () => Date.now();
let sessionUser = null;
let generation = 0;
const entries = new Map();
const inflight = new Map();

export function setProductionSummaryClock(fn) {
  clock = typeof fn === "function" ? fn : () => Date.now();
}

export function productionSummaryRequestKey(userKey, requestParams) {
  const params = requestParams || {};
  return JSON.stringify({
    user: String(userKey || ""),
    include_cancelled: Boolean(params.include_cancelled),
    include_completed: Boolean(params.include_completed),
    view: String(params.view || ""),
  });
}

export function productionSummaryGeneration() {
  return generation;
}

export function clearProductionSummaryCache() {
  generation += 1;
  entries.clear();
}

export function invalidateProductionSummaryCache() {
  clearProductionSummaryCache();
}

export function syncProductionSummarySession(userKey) {
  const next = String(userKey || "");
  if (sessionUser === null) {
    sessionUser = next;
    return;
  }
  if (sessionUser !== next) {
    sessionUser = next;
    clearProductionSummaryCache();
  }
}

function rememberSuccess(key, payload, startedGen) {
  if (generation !== startedGen) return;
  entries.set(key, {
    payload,
    storedAt: clock(),
    generation: startedGen,
  });
}

export function loadProductionSummary({ userKey, requestParams, fetcher, bypass = false }) {
  syncProductionSummarySession(userKey);
  const key = productionSummaryRequestKey(userKey, requestParams);
  const startedGen = generation;
  if (!bypass) {
    const hit = entries.get(key);
    if (hit && hit.generation === startedGen && clock() - hit.storedAt < PRODUCTION_SUMMARY_TTL_MS) {
      return Promise.resolve(hit.payload);
    }
  }
  const flightKey = `${startedGen}\n${key}`;
  const existing = inflight.get(flightKey);
  if (existing) return existing;

  const promise = Promise.resolve().then(() => fetcher()).then((payload) => {
    rememberSuccess(key, payload, startedGen);
    return payload;
  });
  inflight.set(flightKey, promise);
  promise.then(() => {
    if (inflight.get(flightKey) === promise) inflight.delete(flightKey);
  }, () => {
    if (inflight.get(flightKey) === promise) inflight.delete(flightKey);
  });
  return promise;
}
