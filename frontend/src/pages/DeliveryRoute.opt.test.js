import fs from "fs";
import path from "path";

describe("integrated route optimization removed from active UI", () => {
  const create = fs.readFileSync(path.join(__dirname, "CreateDeliverySchedulePage.jsx"), "utf8");
  const batch = fs.readFileSync(path.join(__dirname, "DeliveryBatchPage.jsx"), "utf8");
  const proof = fs.readFileSync(path.join(__dirname, "../components/DeliveryProofPanel.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const schedule = fs.readFileSync(path.join(__dirname, "DeliverySchedulePage.jsx"), "utf8");
  const reschedule = fs.readFileSync(path.join(__dirname, "DeliveryReschedulePage.jsx"), "utf8");
  const settings = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
  const pagesDir = __dirname;
  const componentsDir = path.join(__dirname, "../components");

  test("create schedule keeps location and drops optimizer fields", () => {
    expect(create).toMatch(/Buat Jadwal Pengiriman/);
    expect(create).toMatch(/cds-select-all/);
    expect(create).toMatch(/cds-check-all/);
    expect(create).toMatch(/indeterminate/);
    expect(create).toMatch(/Belum Ada Lokasi/);
    expect(create).toMatch(/Atur Lokasi/);
    expect(create).toMatch(/Lokasi Siap/);
    expect(create).toMatch(/OutletLocationModal/);
    expect(create).toMatch(/Alamat \/ Outlet/);
    expect(create).toMatch(/Status Lokasi/);
    expect(create).toMatch(/cds-qty-/);
    expect(create).toMatch(/cds-status-/);
    expect(create).toMatch(/delivery.schedule.create/);
    expect(create).not.toMatch(/Jam Tutup/);
    expect(create).not.toMatch(/cds-priority-/);
    expect(create).not.toMatch(/cds-closing-/);
    expect(create).not.toMatch(/Publish ke Driver/);
    expect(create).not.toMatch(/sebelum optimize/);
    expect(create).not.toMatch(/placeholder="Lat"/);
    expect(create).not.toMatch(/placeholder="Place ID"/);
  });

  test("batch page keeps Atur Lokasi and has no optimizer workflow", () => {
    expect(batch).toMatch(/Atur Lokasi/);
    expect(batch).toMatch(/Lokasi outlet belum diatur/);
    expect(batch).toMatch(/OutletLocationModal/);
    expect(batch).toMatch(/Kembali ke Jadwal Pengiriman/);
    expect(batch).not.toMatch(/OPTIMIZE RUTE/);
    expect(batch).not.toMatch(/OPTIMIZE ULANG/);
    expect(batch).not.toMatch(/KONFIRMASI JADWAL/);
    expect(batch).not.toMatch(/Siap Berangkat/);
    expect(batch).not.toMatch(/RouteMap/);
    expect(batch).not.toMatch(/dbatch-draft-plan/);
    expect(batch).not.toMatch(/Publish ke Driver/);
    expect(batch).not.toMatch(/rute-saya/);
    expect(batch).not.toMatch(/optimizeDeliveryBatch/);
  });

  test("rute saya page and mulai rute are gone", () => {
    expect(fs.existsSync(path.join(pagesDir, "MyRoutePage.jsx"))).toBe(false);
    expect(fs.existsSync(path.join(componentsDir, "RouteMap.jsx"))).toBe(false);
    expect(shell).not.toMatch(/Rute Saya/);
    expect(shell).not.toMatch(/rute-saya/);
    expect(app).toMatch(/path="\/produksi\/rute-saya"/);
    expect(app).toMatch(/Navigate to="\/"/);
    expect(app).not.toMatch(/MyRoutePage/);
    expect(app).not.toMatch(/MULAI RUTE/);
  });

  test("jadwal pengiriman and ulang remain", () => {
    expect(schedule).toMatch(/Jadwal Pengiriman/);
    expect(schedule).toMatch(/Buat Jadwal Pengiriman/);
    expect(schedule).toMatch(/Jadwal Manual/);
    expect(reschedule).toMatch(/Jadwal Pengiriman Ulang|Atur Jadwal/);
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman"/);
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman-ulang"/);
    expect(app).toMatch(/path="\/tasks\/delivery"/);
  });

  test("bukti kirim still requires two photos and explicit confirm", () => {
    expect(proof).toMatch(/Konfirmasi Terkirim/);
    expect(proof).toMatch(/confirm-delivered-button/);
    expect(proof).toMatch(/Jadwalkan Ulang/);
    expect(proof).toMatch(/Upload foto tidak menandai Terkirim/);
  });

  test("depot optimizer settings tab is gone", () => {
    expect(settings).not.toMatch(/settings-tab-depot/);
    expect(settings).not.toMatch(/DepotSettingsTab/);
    expect(settings).not.toMatch(/Gudang \/ Depot/);
  });
});
