import fs from "fs";
import path from "path";

describe("delivery reschedule UI", () => {
  const proof = fs.readFileSync(path.join(__dirname, "../components/DeliveryProofPanel.jsx"), "utf8");
  const modal = fs.readFileSync(path.join(__dirname, "../components/RescheduleDeliveryModal.jsx"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const schedule = fs.readFileSync(path.join(__dirname, "DeliverySchedulePage.jsx"), "utf8");
  const reschedule = fs.readFileSync(path.join(__dirname, "DeliveryReschedulePage.jsx"), "utf8");

  test("driver panel keeps upload confirm and adds reschedule", () => {
    expect(proof).toMatch(/Konfirmasi Terkirim/);
    expect(proof).toMatch(/confirm-delivered-button/);
    expect(proof).toMatch(/Jadwalkan Ulang/);
    expect(proof).toMatch(/reschedule-delivery-button/);
    expect(proof).toMatch(/Upload foto tidak menandai Terkirim/);
  });

  test("reschedule modal reason required and date permission", () => {
    expect(modal).toMatch(/JADWALKAN ULANG PENGIRIMAN/);
    expect(modal).toMatch(/reschedule-reason/);
    expect(modal).toMatch(/Alasan penjadwalan ulang wajib diisi/);
    expect(modal).toMatch(/canSetDate/);
    expect(modal).toMatch(/reschedule-new-date/);
    expect(modal).toMatch(/Batal/);
    expect(modal).toMatch(/rescheduleConfirmLabel/);
    expect(modal).toMatch(/newActionId/);
  });

  test("sidebar Produksi hierarchy without old labels", () => {
    expect(shell).toMatch(/title="Produksi"/);
    expect(nav).toMatch(/Jadwal Produksi/);
    expect(nav).toMatch(/Jadwal Pengiriman Ulang/);
    expect(shell).toMatch(/Tugas Produksi/);
    expect(shell).not.toMatch(/Tugas per Divisi/);
    expect(nav).toMatch(/"\/produksi"/);
    expect(nav).toMatch(/jadwal-pengiriman-ulang/);
  });

  test("routes preserve jadwal produksi and add schedule pages", () => {
    expect(app).toMatch(/path="\/produksi"/);
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman"/);
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman-ulang"/);
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman\/buat"/);
    expect(app).toMatch(/Navigate to="\/produksi\/jadwal-pengiriman"/);
    expect(app).toMatch(/path="\/produksi\/rute-saya"/);
    expect(app).toMatch(/Navigate to="\/"/);
    expect(app).toMatch(/path="\/tasks\/delivery"/);
  });

  test("jadwal pengiriman table distinguishes deadline and manual", () => {
    expect(schedule).toMatch(/Deadline/);
    expect(schedule).toMatch(/Jadwal Manual/);
    expect(schedule).toMatch(/effective_delivery_date/);
    expect(schedule).toMatch(/Tanggal Mulai/);
    expect(schedule).toMatch(/ds-filter-reset/);
    expect(schedule).not.toMatch(/Buat Jadwal Pengiriman/);
    expect(schedule).not.toMatch(/delivery.schedule.create/);
    expect(schedule).toMatch(/Atur Lokasi/);
    expect(schedule).toMatch(/OutletLocationModal/);
  });

  test("reschedule page has reason history and set date action", () => {
    expect(reschedule).toMatch(/Alasan/);
    expect(reschedule).toMatch(/Tanggal Sebelumnya/);
    expect(reschedule).toMatch(/Atur Jadwal/);
    expect(reschedule).toMatch(/delivery.reschedule.set_date/);
    expect(reschedule).toMatch(/Menunggu Penjadwalan/);
  });
});
