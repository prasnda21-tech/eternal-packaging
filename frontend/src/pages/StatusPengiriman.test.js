import fs from "fs";
import path from "path";

describe("Status Pengiriman consolidated into Jadwal Pengiriman", () => {
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  const schedule = fs.readFileSync(path.join(__dirname, "DeliverySchedulePage.jsx"), "utf8");

  test("old Status Pengiriman route redirects to Jadwal Pengiriman", () => {
    expect(app).toMatch(/path="\/laporan\/status-pengiriman"/);
    expect(app).toMatch(/Navigate to="\/produksi\/jadwal-pengiriman"/);
    expect(app).not.toMatch(/import StatusPengiriman/);
    expect(app).not.toMatch(/<StatusPengiriman/);
  });

  test("Status Pengiriman menu entry removed from Laporan", () => {
    expect(nav).not.toMatch(/label: "Status Pengiriman"/);
    expect(nav).not.toMatch(/\[NAV_KEYS\.shipmentStatus\]: "\/laporan\/status-pengiriman"/);
    expect(nav).toMatch(/shipmentStatus = false/);
  });

  test("Jadwal Pengiriman renders Terkirim and Dapat Dikirim", () => {
    expect(schedule).toMatch(/ds-sort-terkirim/);
    expect(schedule).toMatch(/ds-sort-dapat/);
    expect(schedule).toMatch(/percent_delivered_display \?\? r\.percent_delivered/);
    expect(schedule).toMatch(/percent_ready_to_ship_display \?\? r\.percent_ready_to_ship/);
  });

  test("Shipment Item column removed from Jadwal UI", () => {
    expect(schedule).not.toMatch(/Shipment \/ Item/);
    expect(schedule).not.toMatch(/item_label/);
  });

  test("header sort and filter panel moved to Jadwal", () => {
    expect(schedule).toMatch(/ds-sort-customer/);
    expect(schedule).toMatch(/ds-sort-po/);
    expect(schedule).toMatch(/cycleDeliverySort/);
    expect(schedule).toMatch(/ds-filter-btn/);
    expect(schedule).toMatch(/ds-filter-panel/);
    expect(schedule).toMatch(/ds-adv-terkirim/);
    expect(schedule).toMatch(/ds-adv-dapat/);
    expect(schedule).not.toMatch(/ship-sort-btn/);
    expect(schedule).not.toMatch(/>A–Z</);
  });

  test("Perlu Pesan report-only control is not moved", () => {
    expect(schedule).not.toMatch(/Perlu Pesan/);
    expect(schedule).not.toMatch(/ship-perlu-pesan/);
  });

  test("sorting does not call extra APIs", () => {
    expect(schedule).toMatch(/api\.deliverySchedule/);
    expect(schedule).not.toMatch(/api\.shipmentStatus/);
    expect(schedule).toMatch(/onHeaderSort/);
  });
});
