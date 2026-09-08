/** Preserve /m/* after login without changing desktop default (/). */

export function isSafeMobileNextPath(path) {
  if (typeof path !== "string") return false;
  const value = path.trim();
  if (!value.startsWith("/m")) return false;
  if (value.startsWith("//") || value.includes("://")) return false;
  if (value.includes("\\")) return false;
  return true;
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
