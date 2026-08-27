import { googleMapsNavUrl, openGoogleMapsNavigation, navigationDestination, selectAllState, toggleSelectAll, formatKm, formatDuration, outletAddressLabel } from "./deliveryRoute";

describe("google maps navigation", () => {
  test("builds destination url with place id and does not mutate", () => {
    const url = googleMapsNavUrl({ lat: -6.2, lng: 106.8, placeId: "ChIJA", address: "Jl X" });
    expect(url).toMatch(/google.com\/maps\/dir/);
    expect(url).toMatch(/destination=/);
    expect(url).toMatch(/destination_place_id=ChIJA/);
    expect(url).not.toMatch(/confirm-delivered/);
  });

  test("open helper returns url without delivery mutation", () => {
    const orig = window.open;
    window.open = jest.fn();
    const url = openGoogleMapsNavigation({ lat: -6.21, lng: 106.81, place_id: "ChIJB" });
    expect(window.open).toHaveBeenCalledWith(url, "_blank", "noopener,noreferrer");
    expect(url).toMatch(/maps\/dir/);
    window.open = orig;
  });

  test("uses saved place/coords and never customer name", () => {
    const dest = navigationDestination({ customer: "Toko ABC", outlet_name: "Jakarta", lat: -6.2, lng: 106.8, place_id: "ChIJX" });
    expect(dest.placeId).toBe("ChIJX");
    const url = googleMapsNavUrl(dest);
    expect(url).toMatch(/destination_place_id=ChIJX/);
    expect(url).toMatch(/destination=-6.2/);
    expect(url).not.toMatch(/Toko/);
    expect(openGoogleMapsNavigation({ customer: "Toko ABC", outlet_name: "Jakarta" })).toBe("");
  });

  test("outlet address cell does not expose lat lng place id", () => {
    expect(outletAddressLabel({ outlet_name: "Utama", address: "Jl Preview Dummy", lat: -6.1, lng: 106.8, place_id: "ChIJX" })).toBe("Utama · Jl Preview Dummy");
    expect(outletAddressLabel({ outlet_name: "Utama", address: "Jl Preview Dummy", lat: -6.1, lng: 106.8, place_id: "ChIJX" })).not.toMatch(/ChIJ/);
  });
});

describe("select all", () => {
  const rows = [
    { stop_id: "a", selectable: true, location_status: "ok", lat: 1, lng: 2 },
    { stop_id: "b", selectable: false, location_status: "need_location", exclude_reason: "Lokasi outlet belum diatur" },
    { stop_id: "c", selectable: true, location_status: "ok", lat: 3, lng: 4 },
  ];

  test("skips invalid location and supports indeterminate", () => {
    const mid = selectAllState(rows, ["a"]);
    expect(mid.indeterminate).toBe(true);
    expect(mid.eligible.map((r) => r.stop_id)).toEqual(["a", "c"]);
    expect(toggleSelectAll(rows, [])).toEqual(["a", "c"]);
    expect(toggleSelectAll(rows, ["a", "c"])).toEqual([]);
  });
});

describe("formatters", () => {
  test("distance and duration", () => {
    expect(formatKm(42000)).toBe("42.0 km");
    expect(formatDuration(3 * 3600 + 15 * 60)).toBe("3j 15m");
  });
});
