import {
  GOOGLE_PLACES_UNCONFIGURED,
  LOCATION_SOURCES,
  accuracyWarning,
  applyMove,
  canAccessRoutePage,
  canCrossPriority,
  canMoveStop,
  captureGeolocation,
  confirmGooglePlace,
  formatAccuracy,
  formatCoord,
  formatKm,
  googleMapsDirUrl,
  googleMapsPlaceUrl,
  googleMapsStopUrl,
  googlePlaceCandidateFromDetails,
  isValidCoordinate,
  mapsLinkForStop,
  missingLocationStops,
  parsePlacesStatus,
  parsePlacesSuggestions,
  shouldQueryPlaces,
  startModeLabel,
  strategyLabel,
} from "./independentRoute";

describe("independent route helpers", () => {
  test("rejects invalid and 0,0 coordinates", () => {
    expect(isValidCoordinate(-6.2, 106.8)).toBe(true);
    expect(isValidCoordinate(0, 0)).toBe(false);
    expect(isValidCoordinate(null, 106)).toBe(false);
    expect(formatCoord(null, null)).toBe("Lokasi belum diset");
    expect(formatCoord(-6.123456, 106.123456)).toBe("-6.123456, 106.123456");
  });

  test("formats estimated distance without fake ETA", () => {
    expect(formatKm(420)).toBe("420 m");
    expect(formatKm(12500)).toBe("12.5 km");
    expect(formatKm(null)).toBe("—");
  });

  test("maps links use stored coordinates", () => {
    expect(googleMapsStopUrl(-6.2, 106.8)).toContain("-6.2,106.8");
    expect(googleMapsStopUrl(0, 0)).toBe("");
    expect(googleMapsDirUrl([{ lat: -6.1, lng: 106.8 }, { lat: -6.2, lng: 106.9 }])).toContain("/maps/dir/");
  });

  test("Google place helper confirms separately from save and keeps place id", () => {
    expect(shouldQueryPlaces("")).toBe(false);
    expect(shouldQueryPlaces("S")).toBe(false);
    expect(shouldQueryPlaces("St")).toBe(true);
    expect(parsePlacesStatus({ configured: false }).message).toBe(GOOGLE_PLACES_UNCONFIGURED);
    expect(parsePlacesSuggestions({ configured: false, suggestions: [{ place_id: "fake" }] })).toEqual([]);
    expect(parsePlacesSuggestions({ configured: true, suggestions: [{ place_id: "ChIJ1", label: "Toko" }] })).toHaveLength(1);
    const candidate = googlePlaceCandidateFromDetails({
      place_id: "ChIJ-starbucks",
      name: "Starbucks Sudirman",
      formatted_address: "Jl. Sudirman, Jakarta",
      lat: -6.2146,
      lng: 106.8451,
    });
    expect(candidate.google_place_id).toBe("ChIJ-starbucks");
    expect(candidate.location_source).toBe(LOCATION_SOURCES.GOOGLE_PLACES);
    expect(candidate.confirmed).toBeUndefined();
    const confirmed = confirmGooglePlace(candidate);
    expect(confirmed.confirmed).toBe(true);
    expect(confirmed.lat).toBe(-6.2146);
    expect(confirmed.lng).toBe(106.8451);
    expect(googleMapsPlaceUrl({ placeId: "ChIJ-starbucks", lat: -6.2146, lng: 106.8451, name: "Starbucks Sudirman" }))
      .toContain("query_place_id=ChIJ-starbucks");
    expect(mapsLinkForStop({ google_place_id: "ChIJ-starbucks", lat: -6.2146, lng: 106.8451 })).toContain("query_place_id=");
    expect(googlePlaceCandidateFromDetails({ place_id: "x", lat: 0, lng: 0 })).toBeNull();
  });

  test("priority reorder blocks crossing ranks", () => {
    const stops = [
      { id: "a", priority_rank: 1 },
      { id: "c", priority_rank: 1 },
      { id: "d", priority_rank: 2 },
    ];
    expect(canMoveStop(stops, 0, 1)).toBe(true);
    expect(canMoveStop(stops, 1, 1)).toBe(false);
    expect(canCrossPriority([{ priority_rank: 2 }, { priority_rank: 1 }])).toBe(true);
    expect(applyMove(stops, 0, 1).map((s) => s.id)).toEqual(["c", "a", "d"]);
    expect(applyMove(stops, 1, 1)).toBe(stops);
  });

  test("missing location summary", () => {
    const missing = missingLocationStops([
      { id: "1", lat: -6.1, lng: 106.8 },
      { id: "2" },
      { id: "3", lat: 0, lng: 0 },
    ]);
    expect(missing).toHaveLength(2);
  });

  test("labels for start and strategy", () => {
    expect(startModeLabel("CURRENT_LOCATION")).toBe("Lokasi Saya");
    expect(strategyLabel("SHORTEST")).toContain("estimasi");
  });

  test("GPS granted and denied", async () => {
    const granted = await captureGeolocation((ok) => ok({ coords: { latitude: -6.175392, longitude: 106.827153, accuracy: 12 } }));
    expect(granted.ok).toBe(true);
    expect(granted.status).toBe("GRANTED");
    expect(granted.location_source).toBe(LOCATION_SOURCES.CURRENT_LOCATION);
    expect(formatAccuracy(granted.accuracy)).toBe("Akurasi ±12 m");
    expect(accuracyWarning(12)).toBe(false);
    expect(accuracyWarning(120)).toBe(true);

    const denied = await captureGeolocation((_ok, err) => err({ code: 1 }));
    expect(denied.ok).toBe(false);
    expect(denied.status).toBe("DENIED");
    expect(denied.message).toMatch(/ditolak/i);
  });

  test("permissions helper does not treat delivery perms as enough", () => {
    const has = (p) => p === "delivery.route.optimize";
    expect(canAccessRoutePage(has)).toBe(false);
    expect(canAccessRoutePage((p) => p === "route_optimizer.view")).toBe(true);
  });
});
