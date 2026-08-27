import {
  GOOGLE_MAPS_JS_SRC,
  googleMapsBrowserKey,
  latLngToPixel,
  pixelToLatLng,
  tilesForView,
} from "./outletMap";

describe("outlet map preview", () => {
  test("round-trips click offset back to lat lng without typing coordinates", () => {
    const center = { lat: -6.15801, lng: 106.91002 };
    const width = 400;
    const height = 240;
    const moved = pixelToLatLng(220, 100, center, width, height);
    expect(moved.lat).not.toBe(center.lat);
    expect(moved.lng).not.toBe(center.lng);
    const pixel = latLngToPixel(moved.lat, moved.lng, center, width, height);
    expect(Math.abs(pixel.x - 220)).toBeLessThan(2);
    expect(Math.abs(pixel.y - 100)).toBeLessThan(2);
  });

  test("builds osm tiles for the preview viewport", () => {
    const tiles = tilesForView({ lat: -6.15801, lng: 106.91002 }, 400, 240);
    expect(tiles.length).toBeGreaterThan(0);
    expect(tiles[0].src).toMatch(/tile\.openstreetmap\.org\//);
  });

  test("browser maps key is separate from backend places key", () => {
    expect(GOOGLE_MAPS_JS_SRC).toBe("https://maps.googleapis.com/maps/api/js");
    expect(googleMapsBrowserKey()).not.toMatch(/GOOGLE_PLACES_API_KEY/);
  });
});
