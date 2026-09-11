/**
 * Generic list → PO detail return context.
 * Presentation/navigation only: pathname+search (+ optional scroll).
 * Never stores PO payloads, tokens, or credentials.
 */

const PO_RETURN_PREFIX = "ep.poReturn.";
const LIST_UI_PREFIX = "ep.listUi.";

function sessionStore() {
  try {
    return typeof sessionStorage !== "undefined" ? sessionStorage : null;
  } catch {
    return null;
  }
}

export function storageUserKey(user) {
  if (!user || typeof user !== "object") return "anon";
  return String(user.id || user.email || user.sub || "anon");
}

/** Reject protocol-relative, absolute URLs, and empty paths. */
export function isSafeAppPath(path) {
  if (typeof path !== "string") return false;
  const p = path.trim();
  if (!p.startsWith("/") || p.startsWith("//")) return false;
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(p)) return false;
  return true;
}

export function currentListHref(pathname, search = "") {
  const path = String(pathname || "");
  if (!path) return "";
  const q = String(search || "");
  if (!q || q === "?") return path;
  return path + (q.startsWith("?") ? q : `?${q}`);
}

export function poReturnStorageKey(userId, orderId) {
  return `${PO_RETURN_PREFIX}${String(userId || "anon")}.${String(orderId || "")}`;
}

export function listUiStorageKey(userId, routeKey) {
  return `${LIST_UI_PREFIX}${String(userId || "anon")}.${String(routeKey || "")}`;
}

export function poNavStateFromList({ pathname, search = "", extra = {} } = {}) {
  const from = currentListHref(pathname, search);
  const base = extra && typeof extra === "object" ? { ...extra } : {};
  if (from && isSafeAppPath(from)) base.from = from;
  return base;
}

export function writePoReturnContext(userId, orderId, { href, scrollY } = {}) {
  const s = sessionStore();
  if (!s || !orderId) return;
  if (!href || !isSafeAppPath(href)) return;
  const payload = { href: String(href) };
  if (scrollY != null && Number.isFinite(Number(scrollY))) {
    payload.scrollY = Number(scrollY);
  }
  try {
    s.setItem(poReturnStorageKey(userId, orderId), JSON.stringify(payload));
  } catch {
    /* quota */
  }
}

export function readPoReturnContext(userId, orderId) {
  const s = sessionStore();
  if (!s || !orderId) return null;
  try {
    const raw = s.getItem(poReturnStorageKey(userId, orderId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed?.href || !isSafeAppPath(parsed.href)) return null;
    return {
      href: String(parsed.href),
      scrollY: parsed.scrollY != null ? Number(parsed.scrollY) : undefined,
    };
  } catch {
    return null;
  }
}

export function clearPoReturnContext(userId, orderId) {
  const s = sessionStore();
  if (!s || !orderId) return;
  try {
    s.removeItem(poReturnStorageKey(userId, orderId));
  } catch {
    /* ignore */
  }
}

export function writeListUiState(userId, routeKey, value) {
  const s = sessionStore();
  if (!s || !routeKey) return;
  try {
    s.setItem(listUiStorageKey(userId, routeKey), JSON.stringify(value ?? {}));
  } catch {
    /* quota */
  }
}

export function readListUiState(userId, routeKey, fallback = {}) {
  const s = sessionStore();
  if (!s || !routeKey) return fallback;
  try {
    const raw = s.getItem(listUiStorageKey(userId, routeKey));
    if (raw == null || raw === "") return fallback;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return fallback;
    return { ...fallback, ...parsed };
  } catch {
    return fallback;
  }
}

export function clearListUiState(userId, routeKey) {
  const s = sessionStore();
  if (!s || !routeKey) return;
  try {
    s.removeItem(listUiStorageKey(userId, routeKey));
  } catch {
    /* ignore */
  }
}

/** Clear all ep.poReturn.* and ep.listUi.* keys for a user (logout isolation). */
export function clearPoListReturnForUser(user) {
  const s = sessionStore();
  if (!s) return;
  const uid = storageUserKey(user);
  const prefixes = [
    `${PO_RETURN_PREFIX}${uid}.`,
    `${LIST_UI_PREFIX}${uid}.`,
  ];
  const toRemove = [];
  try {
    for (let i = 0; i < s.length; i += 1) {
      const k = s.key(i);
      if (k && prefixes.some((p) => k.startsWith(p))) toRemove.push(k);
    }
    toRemove.forEach((k) => s.removeItem(k));
  } catch {
    /* ignore */
  }
}

/**
 * Resolve app Back target for PO detail.
 * Prefer router state.from, then session return href, then role-safe fallback.
 * Does not use navigate(-1) (can leave the app).
 */
export function resolvePoBackTarget({
  location,
  userId,
  orderId,
  mobileFallback = "/m/tasks",
  webFallback = "/",
} = {}) {
  const fromState = location?.state?.from;
  if (isSafeAppPath(fromState)) return fromState;

  const stored = readPoReturnContext(userId, orderId);
  if (stored?.href) return stored.href;

  const path = String(location?.pathname || "");
  if (path.startsWith("/m/") || path.startsWith("/m?")) return mobileFallback;
  return webFallback;
}

/** Capture return context when opening a PO from a list (Link onClick / navigate). */
export function capturePoOpenFromList({
  user,
  orderId,
  pathname,
  search = "",
  scrollY,
} = {}) {
  const href = currentListHref(pathname, search);
  if (!orderId || !isSafeAppPath(href)) return null;
  writePoReturnContext(storageUserKey(user), orderId, { href, scrollY });
  return href;
}
