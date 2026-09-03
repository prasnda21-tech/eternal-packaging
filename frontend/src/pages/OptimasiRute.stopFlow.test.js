import fs from "fs";
import path from "path";
import {
  ADD_STOP_PHASES,
  LOCATION_SOURCES,
  applyGooglePlaceToStopDraft,
  buildAddStopPayload,
  canMoveStop,
  changeAddStopLocation,
  emptyAddStopDraft,
  googlePlaceCandidateFromDetails,
  initialAddStopLocationMode,
  initialAddStopPhase,
} from "../lib/independentRoute";

describe("Tambah Stop fast flow", () => {
  const editor = fs.readFileSync(path.join(__dirname, "OptimasiRuteEditor.jsx"), "utf8");
  const picker = fs.readFileSync(path.join(__dirname, "../components/GooglePlacePicker.jsx"), "utf8");

  test("Tambah Stop opens Google search directly without old step 1", () => {
    expect(editor).toMatch(/iro-add-stop/);
    expect(editor).toMatch(/setModal\(\{ phase: initialAddStopPhase\(\) \}\)/);
    expect(initialAddStopPhase()).toBe(ADD_STOP_PHASES.LOCATION);
    expect(initialAddStopLocationMode()).toBe("GOOGLE");
    expect(editor).toMatch(/initialAddStopLocationMode/);
    expect(editor).toMatch(/iro-stop-phase-location/);
    expect(editor).toMatch(/GooglePlacePicker/);
    expect(editor).not.toMatch(/Langkah 1 dari 4/);
    expect(editor).not.toMatch(/Cari customer \/ outlet/);
    expect(editor).not.toMatch(/iro-stop-search/);
    expect(editor).not.toMatch(/searchIndependentRouteOutlets/);
  });

  test("Google result click selects location, drops Gunakan Titik Ini, and advances to priority", () => {
    expect(picker).not.toMatch(/Gunakan Titik Ini/);
    expect(editor).not.toMatch(/Gunakan Titik Ini/);
    expect(picker).toMatch(/onConfirm\?\.\(snap\)/);
    expect(editor).toMatch(/onGoogleConfirm/);
    expect(editor).toMatch(/applyGooglePlaceToStopDraft/);
    expect(editor).toMatch(/setPhase\(next\.phase\)/);
    expect(editor).toMatch(/iro-stop-phase-priority/);
    expect(editor).toMatch(/iro-stop-priority/);
    const snap = googlePlaceCandidateFromDetails({
      place_id: "ChIJ-fast",
      name: "Toko ABC",
      formatted_address: "Jl. Sudirman ...",
      lat: -6.175392,
      lng: 106.827153,
    });
    const next = applyGooglePlaceToStopDraft(emptyAddStopDraft(), snap);
    expect(next.phase).toBe(ADD_STOP_PHASES.PRIORITY);
    expect(next.persist).toBe(false);
    expect(next.draft.display_name).toBe("Toko ABC");
    expect(next.draft.formatted_address).toBe("Jl. Sudirman ...");
    expect(next.draft.lat).toBe(-6.175392);
    expect(next.draft.lng).toBe(106.827153);
    expect(next.draft.google_place_id).toBe("ChIJ-fast");
    expect(next.draft.location_source).toBe(LOCATION_SOURCES.GOOGLE_PLACES);
  });

  test("clicking a Google result does not persist; only Simpan Stop saves once", () => {
    expect(picker).not.toMatch(/addIndependentStop/);
    expect(picker).not.toMatch(/updateIndependentStop/);
    expect(editor).toMatch(/iro-stop-save/);
    expect(editor).toMatch(/Simpan Stop/);
    expect(editor).toMatch(/buildAddStopPayload/);
    const addCalls = editor.match(/api\.addIndependentStop/g) || [];
    expect(addCalls).toHaveLength(1);
    expect(editor).toMatch(/onSave=\{async \(payload\) => \{/);
    const snap = googlePlaceCandidateFromDetails({
      place_id: "ChIJ-one",
      name: "Satu Stop",
      formatted_address: "Alamat satu",
      lat: -6.2,
      lng: 106.8,
    });
    const next = applyGooglePlaceToStopDraft(emptyAddStopDraft("p3"), snap);
    expect(next.persist).toBe(false);
    const payload = buildAddStopPayload(next.draft);
    expect(payload.ref_kind).toBe("ROUTE_ONLY");
    expect(payload.display_name).toBe("Satu Stop");
  });

  test("Ubah lokasi returns to Google search", () => {
    expect(editor).toMatch(/iro-ubah-lokasi/);
    expect(editor).toMatch(/changeAddStopLocation/);
    expect(editor).toMatch(/Ubah lokasi/);
    const next = changeAddStopLocation(emptyAddStopDraft());
    expect(next.phase).toBe(ADD_STOP_PHASES.LOCATION);
    expect(next.persist).toBe(false);
  });

  test("manual map, coordinate, and current-location fallbacks remain", () => {
    expect(editor).toMatch(/LOCATION_SOURCES\.MAP_PIN/);
    expect(editor).toMatch(/LOCATION_SOURCES\.MANUAL_COORDINATE/);
    expect(editor).toMatch(/LOCATION_SOURCES\.CURRENT_LOCATION/);
    expect(editor).toMatch(/iro-stop-gps/);
    expect(editor).toMatch(/iro-stop-lat/);
    expect(editor).toMatch(/iro-stop-lng/);
    expect(editor).toMatch(/pickMode/);
    expect(editor).toMatch(/onPick=\{\(p\) => applyManualPoint\(p\.lat, p\.lng, LOCATION_SOURCES\.MAP_PIN, true\)\}/);
    expect(editor).toMatch(/captureGeolocation/);
    expect(editor).toMatch(/Pakai GPS saat ini/);
  });

  test("optimizer priority behavior is unchanged", () => {
    expect(canMoveStop([{ priority_rank: 1 }, { priority_rank: 2 }], 0, 1)).toBe(false);
    expect(canMoveStop([{ priority_rank: 2 }, { priority_rank: 2 }], 0, 1)).toBe(true);
    expect(editor).toMatch(/canMoveStop/);
    expect(editor).toMatch(/iro-stop-priority/);
    expect(editor).toMatch(/priority_id/);
  });
});
