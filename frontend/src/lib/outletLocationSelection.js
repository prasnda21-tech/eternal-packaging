export function formatCoordinateAddress(lat, lng) {
  return `Koordinat ${lat}, ${lng}`;
}

export function mapsLinkSelectionKey(lat, lng) {
  return `maps-link:${lat},${lng}`;
}

export function nextOutletName(currentName, incomingName) {
  const current = String(currentName || "");
  if (current.trim()) return current;
  return incomingName || current;
}

export function applyPlacesSelection(place, currentName) {
  const lat = Number(place.lat);
  const lng = Number(place.lng);
  const placeId = place.place_id || place.google_place_id || "";
  return {
    picked: {
      place_id: placeId,
      selection_key: placeId || mapsLinkSelectionKey(lat, lng),
      formatted_address: place.formatted_address || place.label || formatCoordinateAddress(lat, lng),
      lat,
      lng,
      name: place.name || "",
    },
    name: nextOutletName(currentName, place.name || ""),
  };
}

export function applyMapsLinkSelection(coords, currentName, previousPicked = null) {
  const lat = Number(coords.lat);
  const lng = Number(coords.lng);
  return {
    picked: {
      place_id: "",
      selection_key: mapsLinkSelectionKey(lat, lng),
      formatted_address: coords.formatted_address || formatCoordinateAddress(lat, lng),
      lat,
      lng,
      name: previousPicked?.name || "",
    },
    name: currentName,
  };
}

export function buildSaveDeliveryOutletPayload({ row, batchId, name, picked }) {
  return {
    order_id: row.order_id,
    batch_id: batchId || undefined,
    stop_id: row.stop_id,
    name: name || picked.name || row.customer,
    place_id: picked.place_id,
    google_place_id: picked.place_id,
    formatted_address: picked.formatted_address,
    lat: Number(picked.lat),
    lng: Number(picked.lng),
  };
}
