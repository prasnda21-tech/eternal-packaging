/** Independent Route Optimizer helpers. No PO / Pengiriman / SJ coupling. */

export const ROUTE_PERMS = {
  view: "route_optimizer.view",
  create: "route_optimizer.create",
  editOwn: "route_optimizer.edit_own",
  optimize: "route_optimizer.optimize",
  manage: "route_optimizer.manage",
  settingsView: "settings.route_optimizer.view",
  settingsManage: "settings.route_optimizer.manage",
};

export const START_MODES = [
  { id: "CURRENT_LOCATION", label: "Lokasi Saya", hint: "Ambil GPS perangkat" },
  { id: "WAREHOUSE", label: "Gudang", hint: "Titik gudang terkonfigurasi" },
  { id: "MANUAL", label: "Titik Manual", hint: "Klik peta atau tempel koordinat" },
];

export const END_MODES = [
  { id: "LAST_STOP", label: "Berhenti di customer terakhir" },
  { id: "RETURN_START", label: "Kembali ke titik awal" },
  { id: "WAREHOUSE", label: "Kembali ke gudang" },
  { id: "MANUAL", label: "Titik akhir manual" },
];

export const STRATEGIES = [
  { id: "NEAREST", label: "Terdekat dulu" },
  { id: "FARTHEST", label: "Terjauh dulu" },
  { id: "SHORTEST", label: "Rute terpendek (estimasi)" },
];

export const STATUS_LABELS = {
  DRAFT: "Draft",
  OPTIMIZED: "Dioptimasi",
  FINAL: "Final",
};

export const DEFAULT_PRIORITIES = [
  { id: "p1", rank: 1, label: "Urgent", color: "#e11d48", enabled: true },
  { id: "p2", rank: 2, label: "High", color: "#f59e0b", enabled: true },
  { id: "p3", rank: 3, label: "Normal", color: "#0ea5e9", enabled: true },
  { id: "p4", rank: 4, label: "Low", color: "#10b981", enabled: true },
];

export const LOCATION_SOURCES = {
  MAP_PIN: "MAP_PIN",
  MANUAL_COORDINATE: "MANUAL_COORDINATE",
  CURRENT_LOCATION: "CURRENT_LOCATION",
  GEOCODER: "GEOCODER",
  GOOGLE_PLACES: "GOOGLE_PLACES",
};

export const LOCATION_PICK_MODES = [
  { id: "GOOGLE", label: "Cari Google Maps", source: "GOOGLE_PLACES" },
  { id: "MAP", label: "Pilih di Peta", source: "MAP_PIN" },
  { id: "MANUAL", label: "Koordinat Manual", source: "MANUAL_COORDINATE" },
  { id: "GPS", label: "Lokasi Saya", source: "CURRENT_LOCATION" },
];

export const GOOGLE_PLACES_UNCONFIGURED = "Google Maps belum dikonfigurasi.";
export const GOOGLE_PLACES_REQUIRED_CONFIG = "GOOGLE_PLACES_API_KEY";
export const GOOGLE_PLACES_REQUIRED_CONFIG_FALLBACK = "GOOGLE_MAPS_API_KEY";
export const GOOGLE_SEARCH_MIN_CHARS = 2;
export const GOOGLE_SEARCH_DEBOUNCE_MS = 350;

export const OSM_TILE_SIZE = 256;

export function isValidCoordinate(lat, lng) {
  if (lat == null || lng == null || lat === "" || lng === "") return false;
  const a = Number(lat);
  const b = Number(lng);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
  if (Math.abs(a) < 1e-12 && Math.abs(b) < 1e-12) return false;
  return a >= -90 && a <= 90 && b >= -180 && b <= 180;
}

export function formatCoord(lat, lng) {
  if (!isValidCoordinate(lat, lng)) return "Lokasi belum diset";
  return `${Number(lat).toFixed(6)}, ${Number(lng).toFixed(6)}`;
}

export function formatKm(meters) {
  if (meters == null || meters === "") return "—";
  const n = Number(meters);
  if (!Number.isFinite(n)) return "—";
  if (n < 1000) return `${Math.round(n)} m`;
  return `${(n / 1000).toFixed(1)} km`;
}

export function formatAccuracy(meters) {
  const n = Number(meters);
  if (!Number.isFinite(n)) return "";
  return `Akurasi ±${Math.round(n)} m`;
}

export function accuracyWarning(meters) {
  const n = Number(meters);
  return Number.isFinite(n) && n > 80;
}

export function googleMapsStopUrl(lat, lng, placeId) {
  if (placeId) {
    return googleMapsPlaceUrl({ placeId, lat, lng });
  }
  if (!isValidCoordinate(lat, lng)) return "";
  return `https://www.google.com/maps?q=${Number(lat)},${Number(lng)}`;
}

export function googleMapsPlaceUrl({ placeId, lat, lng, name } = {}) {
  const pid = String(placeId || "").trim();
  if (pid) {
    const query = encodeURIComponent(
      (name || "").trim() || (isValidCoordinate(lat, lng) ? `${Number(lat)},${Number(lng)}` : "place"),
    );
    return `https://www.google.com/maps/search/?api=1&query=${query}&query_place_id=${encodeURIComponent(pid)}`;
  }
  if (!isValidCoordinate(lat, lng)) return "";
  return `https://www.google.com/maps?q=${Number(lat)},${Number(lng)}`;
}

export function mapsLinkForStop(stop) {
  if (stop?.google_maps_url) return stop.google_maps_url;
  return googleMapsPlaceUrl({
    placeId: stop?.google_place_id,
    lat: stop?.lat,
    lng: stop?.lng,
    name: stop?.place_name || stop?.display_name,
  });
}

export function shouldQueryPlaces(input) {
  return String(input || "").trim().length >= GOOGLE_SEARCH_MIN_CHARS;
}

export function parsePlacesStatus(payload) {
  const configured = Boolean(payload?.configured);
  return {
    configured,
    provider: payload?.provider || (configured ? "google" : "none"),
    message: configured ? "" : (payload?.message || GOOGLE_PLACES_UNCONFIGURED),
    required_config: payload?.required_config || GOOGLE_PLACES_REQUIRED_CONFIG,
    required_config_fallback: payload?.required_config_fallback || GOOGLE_PLACES_REQUIRED_CONFIG_FALLBACK,
  };
}

export function parsePlacesSuggestions(payload) {
  if (!payload || payload.configured === false) return [];
  return Array.isArray(payload.suggestions) ? payload.suggestions.filter((s) => s && s.place_id) : [];
}

export function googlePlaceCandidateFromDetails(place) {
  if (!place || !isValidCoordinate(place.lat, place.lng)) return null;
  const placeId = place.place_id || place.google_place_id || "";
  const name = place.name || place.place_name || "";
  const formatted = place.formatted_address || "";
  return {
    lat: Number(place.lat),
    lng: Number(place.lng),
    location_source: LOCATION_SOURCES.GOOGLE_PLACES,
    google_place_id: placeId,
    place_name: name,
    formatted_address: formatted,
    google_maps_url: place.google_maps_url || googleMapsPlaceUrl({
      placeId,
      lat: place.lat,
      lng: place.lng,
      name,
    }),
  };
}

export function confirmGooglePlace(candidate) {
  if (!candidate || !isValidCoordinate(candidate.lat, candidate.lng)) return null;
  return { ...candidate, location_source: LOCATION_SOURCES.GOOGLE_PLACES, confirmed: true };
}

export function googleMapsDirUrl(points) {
  const valid = (points || []).filter((p) => isValidCoordinate(p.lat, p.lng));
  if (valid.length < 2) {
    return valid[0] ? googleMapsStopUrl(valid[0].lat, valid[0].lng) : "";
  }
  const path = valid.map((p) => `${Number(p.lat)},${Number(p.lng)}`).join("/");
  return `https://www.google.com/maps/dir/${path}`;
}

export function startModeLabel(mode) {
  return START_MODES.find((m) => m.id === mode)?.label || mode || "—";
}

export function endModeLabel(mode) {
  return END_MODES.find((m) => m.id === mode)?.label || mode || "—";
}

export function strategyLabel(mode) {
  return STRATEGIES.find((m) => m.id === mode)?.label || mode || "—";
}

export function statusLabel(status) {
  return STATUS_LABELS[status] || status || "Draft";
}

export function priorityById(levels, id) {
  const list = levels?.length ? levels : DEFAULT_PRIORITIES;
  return list.find((p) => p.id === id) || list[0];
}

export function canCrossPriority(orderedStops) {
  let last = null;
  for (const stop of orderedStops || []) {
    const rank = Number(stop.priority_rank);
    if (last != null && rank < last) return true;
    last = rank;
  }
  return false;
}

export function canMoveStop(stops, index, direction) {
  const next = index + direction;
  if (next < 0 || next >= stops.length) return false;
  return Number(stops[index].priority_rank) === Number(stops[next].priority_rank);
}

export function applyMove(stops, index, direction) {
  if (!canMoveStop(stops, index, direction)) return stops;
  const copy = stops.slice();
  const next = index + direction;
  const tmp = copy[index];
  copy[index] = copy[next];
  copy[next] = tmp;
  return copy;
}

export function missingLocationStops(stops) {
  return (stops || []).filter((s) => !isValidCoordinate(s.lat, s.lng));
}

export function captureGeolocation(getCurrentPosition = defaultGetCurrentPosition) {
  return new Promise((resolve) => {
    if (typeof getCurrentPosition !== "function") {
      resolve({ ok: false, status: "UNAVAILABLE", message: "Geolocation tidak tersedia." });
      return;
    }
    getCurrentPosition(
      (pos) => {
        const lat = pos?.coords?.latitude;
        const lng = pos?.coords?.longitude;
        const accuracy = pos?.coords?.accuracy;
        if (!isValidCoordinate(lat, lng)) {
          resolve({ ok: false, status: "UNAVAILABLE", message: "GPS tidak mengembalikan koordinat valid." });
          return;
        }
        resolve({
          ok: true,
          status: "GRANTED",
          lat,
          lng,
          accuracy,
          captured_at: new Date().toISOString(),
          location_source: LOCATION_SOURCES.CURRENT_LOCATION,
        });
      },
      (err) => {
        const code = err?.code;
        if (code === 1) resolve({ ok: false, status: "DENIED", message: "Izin lokasi ditolak." });
        else if (code === 2) resolve({ ok: false, status: "UNAVAILABLE", message: "Lokasi tidak tersedia." });
        else if (code === 3) resolve({ ok: false, status: "TIMEOUT", message: "Waktu menunggu GPS habis." });
        else resolve({ ok: false, status: "UNAVAILABLE", message: "GPS tidak tersedia." });
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  });
}

export function defaultGetCurrentPosition(success, error, options) {
  if (typeof navigator === "undefined" || !navigator.geolocation) {
    error({ code: 2, message: "Geolocation API tidak tersedia." });
    return;
  }
  navigator.geolocation.getCurrentPosition(success, error, options);
}

export function lngToWorldX(lng, zoom, tileSize = OSM_TILE_SIZE) {
  const n = 2 ** zoom;
  return ((Number(lng) + 180) / 360) * n * tileSize;
}

export function latToWorldY(lat, zoom, tileSize = OSM_TILE_SIZE) {
  const n = 2 ** zoom;
  const latRad = (Number(lat) * Math.PI) / 180;
  const y = (1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2;
  return y * n * tileSize;
}

export function worldToLng(worldX, zoom, tileSize = OSM_TILE_SIZE) {
  const n = 2 ** zoom;
  return (worldX / (n * tileSize)) * 360 - 180;
}

export function worldToLat(worldY, zoom, tileSize = OSM_TILE_SIZE) {
  const n = 2 ** zoom;
  const y = worldY / (n * tileSize);
  const latRad = Math.atan(Math.sinh(Math.PI * (1 - 2 * y)));
  return (latRad * 180) / Math.PI;
}

export function pixelToLatLng(offsetX, offsetY, center, width, height, zoom) {
  const cx = lngToWorldX(center.lng, zoom);
  const cy = latToWorldY(center.lat, zoom);
  return {
    lat: Number(worldToLat(cy + (offsetY - height / 2), zoom).toFixed(6)),
    lng: Number(worldToLng(cx + (offsetX - width / 2), zoom).toFixed(6)),
  };
}

export function latLngToPixel(lat, lng, center, width, height, zoom) {
  return {
    x: width / 2 + (lngToWorldX(lng, zoom) - lngToWorldX(center.lng, zoom)),
    y: height / 2 + (latToWorldY(lat, zoom) - latToWorldY(center.lat, zoom)),
  };
}

export function tilesForView(center, width, height, zoom) {
  const cx = lngToWorldX(center.lng, zoom);
  const cy = latToWorldY(center.lat, zoom);
  const left = cx - width / 2;
  const top = cy - height / 2;
  const x0 = Math.floor(left / OSM_TILE_SIZE);
  const y0 = Math.floor(top / OSM_TILE_SIZE);
  const x1 = Math.floor((cx + width / 2) / OSM_TILE_SIZE);
  const y1 = Math.floor((cy + height / 2) / OSM_TILE_SIZE);
  const tiles = [];
  const n = 2 ** zoom;
  for (let ty = y0; ty <= y1; ty += 1) {
    for (let tx = x0; tx <= x1; tx += 1) {
      const wrappedX = ((tx % n) + n) % n;
      tiles.push({
        key: `${zoom}-${tx}-${ty}`,
        src: `https://tile.openstreetmap.org/${zoom}/${wrappedX}/${ty}.png`,
        left: tx * OSM_TILE_SIZE - left,
        top: ty * OSM_TILE_SIZE - top,
      });
    }
  }
  return tiles;
}

export function fitCenter(points, fallback = { lat: -2.5, lng: 118, zoom: 5 }) {
  const valid = (points || []).filter((p) => isValidCoordinate(p.lat, p.lng));
  if (!valid.length) return fallback;
  const lat = valid.reduce((s, p) => s + Number(p.lat), 0) / valid.length;
  const lng = valid.reduce((s, p) => s + Number(p.lng), 0) / valid.length;
  return { lat, lng, zoom: valid.length === 1 ? 14 : 12 };
}

export function markerTone(kind, priorityId) {
  if (kind === "START") return { bg: "#2563eb", label: "S" };
  if (kind === "END") return { bg: "#7c3aed", label: "E" };
  if (kind === "ME") return { bg: "#06b6d4", label: "●" };
  if (kind === "WAREHOUSE") return { bg: "#4338ca", label: "G" };
  const map = { p1: "#e11d48", p2: "#f59e0b", p3: "#0ea5e9", p4: "#10b981" };
  return { bg: map[priorityId] || "#64748b", label: (priorityId || "P").toUpperCase() };
}

export function canAccessRoutePage(hasPerm) {
  return [
    ROUTE_PERMS.view,
    ROUTE_PERMS.create,
    ROUTE_PERMS.editOwn,
    ROUTE_PERMS.optimize,
    ROUTE_PERMS.manage,
    "nav.route_optimizer",
  ].some((p) => hasPerm(p));
}
