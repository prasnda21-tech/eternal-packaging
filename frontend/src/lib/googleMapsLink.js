export const MAPS_LINK_MESSAGES = {
  RESOLVING: "Memproses link Google Maps...",
  SUCCESS: "Lokasi ditemukan.",
  INVALID_LINK: "Link Google Maps tidak valid.",
  UNSUPPORTED_HOST: "Gunakan link lokasi dari Google Maps.",
  NO_COORDINATES: "Titik lokasi tidak ditemukan dari link ini.",
  RESOLVE_FAILURE: "Link Google Maps tidak dapat diproses. Coba salin ulang link dari Google Maps.",
};

const COORD_PAIR = /^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$/;
const AT_COORDS = /@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/;
const BANG_COORDS = /!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)/;
const PATH_COORDS = /(?:^|\/)(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)(?:\/|$)/;

const QUERY_KEYS = ["q", "query", "ll", "destination", "daddr", "sll"];

export const GOOGLE_MAPS_EXACT_HOSTS = new Set([
  "maps.app.goo.gl",
  "goo.gl",
]);

export function isValidMapsCoordinate(lat, lng) {
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return false;
  return lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180;
}

export function mapsLinkMessage(code) {
  return MAPS_LINK_MESSAGES[code] || MAPS_LINK_MESSAGES.RESOLVE_FAILURE;
}

function hostAllowed(hostname) {
  const host = String(hostname || "").trim().toLowerCase().replace(/\.$/, "");
  if (!host) return false;
  if (GOOGLE_MAPS_EXACT_HOSTS.has(host)) return true;
  if (host === "google.com" || host.endsWith(".google.com")) return true;
  return false;
}

function isShortMapsHost(hostname) {
  const host = String(hostname || "").trim().toLowerCase();
  return host === "maps.app.goo.gl" || host === "goo.gl";
}

function parseCoordPair(text) {
  const m = String(text || "").trim().match(COORD_PAIR);
  if (!m) return null;
  const lat = Number(m[1]);
  const lng = Number(m[2]);
  if (!isValidMapsCoordinate(lat, lng)) return { invalid: true, lat, lng };
  return { lat, lng };
}

function firstValidPair(candidates) {
  let sawInvalid = false;
  for (const raw of candidates) {
    if (!raw) continue;
    const parsed = parseCoordPair(raw);
    if (!parsed) continue;
    if (parsed.invalid) {
      sawInvalid = true;
      continue;
    }
    return { coords: parsed, sawInvalid };
  }
  return { coords: null, sawInvalid };
}

function extractFromSearchParams(searchParams) {
  const candidates = [];
  for (const key of QUERY_KEYS) {
    const value = searchParams.get(key);
    if (value) candidates.push(value);
  }
  return firstValidPair(candidates);
}

function extractFromHref(href) {
  const bang = String(href || "").match(BANG_COORDS);
  if (bang) {
    const lat = Number(bang[1]);
    const lng = Number(bang[2]);
    if (isValidMapsCoordinate(lat, lng)) return { lat, lng };
    return { invalid: true };
  }
  const at = String(href || "").match(AT_COORDS);
  if (at) {
    const lat = Number(at[1]);
    const lng = Number(at[2]);
    if (isValidMapsCoordinate(lat, lng)) return { lat, lng };
    return { invalid: true };
  }
  return null;
}

export function parseGoogleMapsUrlRecord(raw) {
  const trimmed = String(raw || "").trim();
  if (!trimmed) return { status: "EMPTY" };

  let parsed;
  try {
    parsed = new URL(trimmed);
  } catch {
    return { status: "INVALID_LINK" };
  }

  if (parsed.protocol !== "https:") return { status: "INVALID_LINK", reason: "protocol" };
  if (parsed.username || parsed.password) return { status: "INVALID_LINK", reason: "credentials" };
  if (parsed.port && parsed.port !== "443") return { status: "UNSUPPORTED_HOST" };
  if (!hostAllowed(parsed.hostname)) return { status: "UNSUPPORTED_HOST" };
  if (parsed.hostname.toLowerCase() === "goo.gl" && !parsed.pathname.toLowerCase().startsWith("/maps")) {
    return { status: "UNSUPPORTED_HOST" };
  }

  const fromHref = extractFromHref(parsed.href);
  if (fromHref?.invalid) return { status: "INVALID_LINK", reason: "bounds" };
  if (fromHref?.lat != null) {
    return { status: "OK", lat: fromHref.lat, lng: fromHref.lng, url: parsed.href };
  }

  const fromQuery = extractFromSearchParams(parsed.searchParams);
  if (fromQuery.coords) {
    return { status: "OK", lat: fromQuery.coords.lat, lng: fromQuery.coords.lng, url: parsed.href };
  }
  if (fromQuery.sawInvalid) return { status: "INVALID_LINK", reason: "bounds" };

  const pathPair = parsed.pathname.match(PATH_COORDS);
  if (pathPair) {
    const lat = Number(pathPair[1]);
    const lng = Number(pathPair[2]);
    if (!isValidMapsCoordinate(lat, lng)) return { status: "INVALID_LINK", reason: "bounds" };
    return { status: "OK", lat, lng, url: parsed.href };
  }

  if (isShortMapsHost(parsed.hostname)) {
    return { status: "NEEDS_RESOLVE", url: parsed.href };
  }
  return { status: "NO_COORDINATES", url: parsed.href };
}

export function parseGoogleMapsCoordinates(raw) {
  const result = parseGoogleMapsUrlRecord(raw);
  if (result.status !== "OK") return null;
  return { lat: result.lat, lng: result.lng };
}

export function googleMapsLinkNeedsResolve(raw) {
  return parseGoogleMapsUrlRecord(raw).status === "NEEDS_RESOLVE";
}
