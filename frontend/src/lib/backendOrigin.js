const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1"]);

/**
 * Auth uses an HttpOnly cookie on the API host. `localhost` and `127.0.0.1`
 * are different sites, so a page on one host cannot store/send cookies set
 * by the other. When both sides are loopback, keep the API host aligned
 * with the page host. Non-loopback (production) URLs are left unchanged.
 */
export function resolveBackendOrigin(configured, pageHostname) {
  const raw = String(configured || "").trim().replace(/\/$/, "");
  if (!raw) return "";
  try {
    const api = new URL(raw.includes("://") ? raw : `http://${raw}`);
    const page = String(pageHostname || "");
    if (LOOPBACK_HOSTS.has(page) && LOOPBACK_HOSTS.has(api.hostname)) {
      api.hostname = page;
    }
    return api.origin;
  } catch {
    return raw;
  }
}
