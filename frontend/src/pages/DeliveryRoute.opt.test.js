import fs from "fs";
import path from "path";

describe("delivery route optimization UI", () => {
  const create = fs.readFileSync(path.join(__dirname, "CreateDeliverySchedulePage.jsx"), "utf8");
  const batch = fs.readFileSync(path.join(__dirname, "DeliveryBatchPage.jsx"), "utf8");
  const mine = fs.readFileSync(path.join(__dirname, "MyRoutePage.jsx"), "utf8");
  const proof = fs.readFileSync(path.join(__dirname, "../components/DeliveryProofPanel.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

  test("create schedule has select all priority closing publish", () => {
    expect(create).toMatch(/Buat Jadwal Pengiriman/);
    expect(create).toMatch(/cds-select-all/);
    expect(create).toMatch(/cds-check-all/);
    expect(create).toMatch(/indeterminate/);
    expect(create).toMatch(/Belum Ada Lokasi/);
    expect(create).toMatch(/Lokasi Siap/);
    expect(create).not.toMatch(/OutletLocationModal/);
    expect(create).not.toMatch(/Atur Lokasi/);
    expect(create).toMatch(/Jam Tutup/);
    expect(create).toMatch(/cds-priority-/);
    expect(create).toMatch(/cds-closing-/);
    expect(create).toMatch(/Publish ke Driver/);
    expect(create).toMatch(/delivery.schedule.create/);
  });

  test("batch workspace uses auth name and vehicle then optimize draft", () => {
    expect(batch).toMatch(/dbatch-driver-name/);
    expect(batch).toMatch(/otomatis dari akun login/);
    expect(batch).toMatch(/dbatch-vehicle-plate/);
    expect(batch).toMatch(/Siap Berangkat/);
    expect(batch).toMatch(/OPTIMIZE RUTE/);
    expect(batch).toMatch(/KONFIRMASI JADWAL/);
    expect(batch).toMatch(/dbatch-unassigned/);
    expect(batch).not.toMatch(/Atur Lokasi/);
    expect(batch).not.toMatch(/OutletLocationModal/);
    expect(batch).toMatch(/deliveryBatchReady/);
    expect(batch).not.toMatch(/driver_user_id:/);
  });

  test("rute saya shows auth driver and navigasi without delivery mutation", () => {
    expect(mine).toMatch(/Rute Saya/);
    expect(mine).toMatch(/my-route-driver-name/);
    expect(mine).toMatch(/MULAI RUTE/);
    expect(mine).toMatch(/Navigasi/);
    expect(mine).toMatch(/openGoogleMapsNavigation/);
    expect(mine).toMatch(/Bukti Kirim/);
    expect(mine).toMatch(/kerja\?divisi=terkirim/);
    expect(mine).not.toMatch(/watchPosition/);
    expect(mine).not.toMatch(/geolocation/);
  });

  test("bukti kirim still requires two photos and explicit confirm", () => {
    expect(proof).toMatch(/Konfirmasi Terkirim/);
    expect(proof).toMatch(/confirm-delivered-button/);
    expect(proof).toMatch(/Jadwalkan Ulang/);
    expect(proof).toMatch(/Upload foto tidak menandai Terkirim/);
  });

  test("routes include create batch and rute saya", () => {
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman\/buat"/);
    expect(app).toMatch(/path="\/produksi\/rute-saya"/);
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman\/batch\/:batchId"/);
  });
});
