/**
 * Tiny bridge so PODetail can register/enrich a dynamic workspace tab
 * in place without navigate()/replace. Registered by useWorkspaceTabs.
 * Avoids a second tab system and avoids router lifecycle races.
 *
 * Child layout/effects run before the parent hook. Queue calls that arrive
 * before the hook registers so initial /po/:id existence is not dropped.
 */

let enrichHandler = null;
const pending = [];

export function registerWorkspaceTabEnricher(handler) {
  enrichHandler = typeof handler === "function" ? handler : null;
  if (enrichHandler && pending.length) {
    const queued = pending.splice(0, pending.length);
    queued.forEach(({ pathname, meta }) => enrichHandler(pathname, meta));
  }
  return () => {
    if (enrichHandler === handler) enrichHandler = null;
  };
}

export function enrichWorkspaceTab(pathname, meta) {
  if (typeof enrichHandler !== "function") {
    pending.push({ pathname, meta });
    return;
  }
  enrichHandler(pathname, meta);
}
