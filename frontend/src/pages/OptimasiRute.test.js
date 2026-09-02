import fs from "fs";
import path from "path";
import { NAV_KEYS } from "../lib/navAccess";
import { END_MODES, LOCATION_PICK_MODES, START_MODES, canMoveStop, missingLocationStops } from "../lib/independentRoute";

describe("Optimasi Rute pages", () => {
  const list = fs.readFileSync(path.join(__dirname, "OptimasiRute.jsx"), "utf8");
  const editor = fs.readFileSync(path.join(__dirname, "OptimasiRuteEditor.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const settings = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");

  test("independent routes and permissions", () => {
    expect(NAV_KEYS.routeOptimizer).toBe("nav.route_optimizer");
    expect(app).toMatch(/path="\/optimasi-rute"/);
    expect(app).toMatch(/path="\/optimasi-rute\/baru"/);
    expect(app).toMatch(/path="\/produksi\/rute-saya" element=\{<Navigate to="\/" replace \/>\}/);
    expect(app).not.toMatch(/delivery\.route\.optimize/);
    expect(shell).toMatch(/top-nav-optimasi-rute/);
    expect(shell).not.toMatch(/Rute Saya/);
  });

  test("create route, add stop, location and GPS controls", () => {
    expect(list).toMatch(/Buat Rute/);
    expect(list).toMatch(/iro-create-btn/);
    expect(editor).toMatch(/iro-save-route/);
    expect(editor).toMatch(/iro-add-stop/);
    expect(editor).toMatch(/IRO-TEST|Stop khusus rute|iro-route-only/);
    expect(editor).toMatch(/iro-stop-lat/);
    expect(editor).toMatch(/LOKASI BELUM DISET|iro-missing-location/);
    expect(editor).toMatch(/iro-gps-btn/);
    expect(editor).toMatch(/Izin lokasi ditolak|captureGeolocation/);
    expect(editor).toMatch(/iro-start-\$\{m\.id\}/);
    expect(editor).toMatch(/iro-end-\$\{m\.id\}/);
    expect(editor).toMatch(/START_MODES\.map/);
    expect(editor).toMatch(/END_MODES\.map/);
    expect(editor).toMatch(/WAREHOUSE/);
    expect(editor).toMatch(/CURRENT_LOCATION/);
    expect(START_MODES.map((m) => m.id)).toEqual(expect.arrayContaining(["CURRENT_LOCATION", "WAREHOUSE", "MANUAL"]));
    expect(END_MODES.map((m) => m.id)).toEqual(expect.arrayContaining(["LAST_STOP", "RETURN_START", "WAREHOUSE", "MANUAL"]));
    expect(editor).toMatch(/iro-strategy/);
    expect(LOCATION_PICK_MODES.map((m) => m.label)).toEqual(expect.arrayContaining(["Cari Google Maps", "Pilih di Peta", "Koordinat Manual", "Lokasi Saya"]));
    expect(editor).toMatch(/LOCATION_PICK_MODES/);
    expect(editor).toMatch(/GooglePlacePicker/);
    expect(editor).toMatch(/LOCATION_SOURCES\.GOOGLE_PLACES/);
    expect(editor).toMatch(/iro-start-manual/);
    expect(editor).toMatch(/iro-end-manual/);
  });

  test("colorful priority badges and optimize / reorder", () => {
    expect(editor).toMatch(/iro-priority-legend/);
    expect(editor).toMatch(/priority_color/);
    expect(css).toMatch(/iro-chip/);
    expect(css).toMatch(/#e11d48/);
    expect(editor).toMatch(/iro-optimize-btn/);
    expect(editor).toMatch(/Optimize Ulang/);
    expect(editor).toMatch(/iro-up-/);
    expect(editor).toMatch(/canMoveStop/);
    expect(editor).toMatch(/Buka Stop di Maps/);
    expect(canMoveStop([{ priority_rank: 1 }, { priority_rank: 2 }], 0, 1)).toBe(false);
    expect(missingLocationStops([{ lat: null }])).toHaveLength(1);
  });

  test("settings and route-only stop safety", () => {
    expect(settings).toMatch(/settings-tab-route-optimizer/);
    expect(settings).toMatch(/RouteOptimizerSettings/);
    expect(editor).toMatch(/ROUTE_ONLY/);
    expect(editor).toMatch(/tidak membuat master/);
    expect(list).toMatch(/Tidak terhubung ke Pesanan Penjualan/);
  });
});
