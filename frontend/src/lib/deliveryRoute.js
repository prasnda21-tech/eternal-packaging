/** Delivery route helpers. Navigation opens Google Maps; it must not mutate delivery. */

export const PRIORITY_OPTIONS = [
  { key: "normal", label: "Normal" },
  { key: "prioritas", label: "Prioritas" },
  { key: "mendesak", label: "Mendesak" },
];

export function priorityLabel(key) {
  return PRIORITY_OPTIONS.find((o) => o.key === key)?.label || key || "Normal";
}

/** Never build destination from customer/outlet name — only saved place/coords/address. */
export function navigationDestination(stop) {
  return {
    placeId: stop?.google_place_id || stop?.place_id || "",
    lat: stop?.lat ?? stop?.latitude ?? null,
    lng: stop?.lng ?? stop?.longitude ?? null,
    address: stop?.formatted_address || stop?.address || "",
  };
}

export function googleMapsNavUrl({ lat, lng, placeId, address } = {}) {
  const params = new URLSearchParams({ api: "1", travelmode: "driving" });
  if (placeId) params.set("destination_place_id", placeId);
  if (lat != null && lng != null && lat !== "" && lng !== "") {
    params.set("destination", `${lat},${lng}`);
  } else if (address) {
    params.set("destination", address);
  }
  return `https://www.google.com/maps/dir/?${params.toString()}`;
}

export function googleMapsEmbedUrl({ lat, lng } = {}) {
  if (lat == null || lng == null || lat === "" || lng === "") return "";
  return `https://maps.google.com/maps?q=${encodeURIComponent(`${lat},${lng}`)}&z=16&output=embed`;
}

export function openGoogleMapsNavigation(stop) {
  const dest = navigationDestination(stop);
  if (!dest.placeId && dest.lat == null && !dest.address) return "";
  const url = googleMapsNavUrl(dest);
  if (typeof window !== "undefined") window.open(url, "_blank", "noopener,noreferrer");
  return url;
}

export function formatKm(meters) {
  const n = Number(meters) || 0;
  if (n < 1000) return `${Math.round(n)} m`;
  return `${(n / 1000).toFixed(1)} km`;
}

export function formatDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  if (h <= 0) return `${m}m`;
  return `${h}j ${m}m`;
}

export function outletAddressLabel(row) {
  const name = String(row?.outlet_name || "").trim();
  const addr = String(row?.formatted_address || row?.address || "").trim();
  if (name && addr && name !== addr) return `${name} · ${addr}`;
  return name || addr || "—";
}

export function stopLocationOk(row) {
  return row?.location_status === "ok" && row?.lat != null && row?.lng != null;
}

export function selectAllState(rows, selectedIds) {
  const eligible = (rows || []).filter((r) => r.selectable !== false && stopLocationOk(r) && !r.occupied);
  const selected = new Set(selectedIds || []);
  const n = eligible.filter((r) => selected.has(r.stop_id)).length;
  if (!eligible.length) return { checked: false, indeterminate: false, eligible };
  if (n === 0) return { checked: false, indeterminate: false, eligible };
  if (n === eligible.length) return { checked: true, indeterminate: false, eligible };
  return { checked: false, indeterminate: true, eligible };
}

export function toggleSelectAll(rows, selectedIds) {
  const { checked, eligible } = selectAllState(rows, selectedIds);
  if (checked) return [];
  return eligible.map((r) => r.stop_id);
}

export const MISSING_LOCATION_REASON = "Lokasi outlet belum diatur";

export function remainingStops(stops) {
  return (stops || []).filter((s) => !["delivered", "removed", "rescheduled"].includes(s.status));
}
