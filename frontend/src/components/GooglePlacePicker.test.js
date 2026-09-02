import fs from "fs";
import path from "path";
import {
  GOOGLE_PLACES_REQUIRED_CONFIG,
  GOOGLE_PLACES_UNCONFIGURED,
  LOCATION_PICK_MODES,
  confirmGooglePlace,
  googlePlaceCandidateFromDetails,
  parsePlacesSuggestions,
} from "../lib/independentRoute";

describe("GooglePlacePicker", () => {
  const src = fs.readFileSync(path.join(__dirname, "GooglePlacePicker.jsx"), "utf8");
  const editor = fs.readFileSync(path.join(__dirname, "../pages/OptimasiRuteEditor.jsx"), "utf8");

  test("search UI shows candidate results and confirmation gate", () => {
    expect(src).toMatch(/Cari nama outlet, alamat, atau tempat/);
    expect(src).toMatch(/iro-gmaps-results|testidPrefix\}-results/);
    expect(src).toMatch(/LOKASI TERPILIH/);
    expect(src).toMatch(/Gunakan Titik Ini/);
    expect(src).toMatch(/Buka di Google Maps/);
    expect(src).toMatch(/GOOGLE_SEARCH_DEBOUNCE_MS/);
    expect(src).toMatch(/shouldQueryPlaces/);
    expect(src).not.toMatch(/addIndependentStop/);
    expect(src).not.toMatch(/updateIndependentStop/);
    expect(src).not.toMatch(/createIndependentRoute/);
  });

  test("selecting a result is not a save; confirm builds exact snapshot", () => {
    const candidate = googlePlaceCandidateFromDetails({
      place_id: "ChIJ-abc",
      name: "Toko ABC",
      formatted_address: "Jl. Contoh",
      lat: -6.200001,
      lng: 106.816666,
    });
    expect(candidate.confirmed).toBeUndefined();
    expect(confirmGooglePlace(candidate)).toMatchObject({
      lat: -6.200001,
      lng: 106.816666,
      google_place_id: "ChIJ-abc",
      confirmed: true,
    });
    expect(src).toMatch(/onConfirm\?\.\(snap\)/);
    expect(src).not.toMatch(/onConfirm\(\s*next\s*\)/);
  });

  test("unconfigured and search failure never fabricate results", () => {
    expect(src).toMatch(/GOOGLE_PLACES_UNCONFIGURED/);
    expect(GOOGLE_PLACES_UNCONFIGURED).toBe("Google Maps belum dikonfigurasi.");
    expect(src).toMatch(/GOOGLE_PLACES_REQUIRED_CONFIG/);
    expect(GOOGLE_PLACES_REQUIRED_CONFIG).toBe("GOOGLE_PLACES_API_KEY");
    expect(src).toMatch(/Titik peta, koordinat manual, dan lokasi saya tetap bisa dipakai/);
    expect(parsePlacesSuggestions({ configured: false, suggestions: [{ place_id: "fake-1" }] })).toEqual([]);
    expect(src).toMatch(/setResults\(\[\]\)/);
    expect(src).not.toMatch(/Starbucks Sudirman/);
  });

  test("editor still keeps manual map coordinate and GPS methods", () => {
    expect(LOCATION_PICK_MODES.map((m) => m.label)).toEqual([
      "Cari Google Maps",
      "Pilih di Peta",
      "Koordinat Manual",
      "Lokasi Saya",
    ]);
    expect(editor).toMatch(/LOCATION_PICK_MODES/);
    expect(editor).toMatch(/Cari Google Maps|LOCATION_PICK_MODES/);
    expect(editor).toMatch(/iro-stop-gps/);
    expect(editor).toMatch(/LOCATION_SOURCES\.MAP_PIN/);
    expect(editor).toMatch(/LOCATION_SOURCES\.MANUAL_COORDINATE/);
    expect(editor).toMatch(/LOCATION_SOURCES\.CURRENT_LOCATION/);
    expect(editor).toMatch(/Stop khusus rute \(tidak membuat master\)/);
  });
});
