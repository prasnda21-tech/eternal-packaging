import fs from "fs";
import path from "path";

describe("integrated route optimization removed from active UI", () => {
  const batch = fs.readFileSync(path.join(__dirname, "DeliveryBatchPage.jsx"), "utf8");
  const proof = fs.readFileSync(path.join(__dirname, "../components/DeliveryProofPanel.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const schedule = fs.readFileSync(path.join(__dirname, "DeliverySchedulePage.jsx"), "utf8");
  const reschedule = fs.readFileSync(path.join(__dirname, "DeliveryReschedulePage.jsx"), "utf8");
  const pagesDir = __dirname;
  const componentsDir = path.join(__dirname, "../components");

  test("create schedule page is not an active route", () => {
    expect(app).not.toMatch(/CreateDeliverySchedulePage/);
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman\/buat"/);
    expect(app).toMatch(/Navigate to="\/produksi\/jadwal-pengiriman"/);
    expect(schedule).not.toMatch(/Buat Jadwal Pengiriman/);
    expect(schedule).not.toMatch(/ds-create-schedule/);
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
    expect(schedule).not.toMatch(/Buat Jadwal Pengiriman/);
    expect(schedule).toMatch(/Atur Lokasi/);
    expect(schedule).toMatch(/OutletLocationModal/);
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
});
