/** Preserve /m/* (and PWA Optimasi Rute) after login without changing desktop default (/). */

export function isSafeMobileNextPath(path) {
  if (typeof path !== "string") return false;
  const value = path.trim();
  if (value.startsWith("//") || value.includes("://")) return false;
  if (value.includes("\\")) return false;
  if (value.startsWith("/m")) return true;
  if (value === "/sales" || value.startsWith("/sales/")) return true;
  // Keep installed-PWA users on the shared in-app Optimasi Rute editor after re-auth.
  if (value === "/optimasi-rute" || value.startsWith("/optimasi-rute/")) return true;
  return false;
}

export function loginPathForLocation(pathname, search = "") {
  const next = `${pathname || ""}${search || ""}`;
  if (isSafeMobileNextPath(pathname)) {
    return `/login?next=${encodeURIComponent(next)}`;
  }
  return "/login";
}

export function postLoginPath(nextParam) {
  return isSafeMobileNextPath(nextParam) ? nextParam : "/";
}
