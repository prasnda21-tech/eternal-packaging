import fs from "fs";
import path from "path";
import { poLateTrackingPath } from "../lib/poLateTracking";

test("App registers tracking route with production/delivery perms", () => {
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  expect(app).toContain('path="/produksi/tracking-po-terlambat"');
  expect(app).toContain("PoLateTrackingPage");
  expect(app).toContain("dashboard.production.view");
});

test("page keeps spreadsheet columns and URL late filter", () => {
  const src = fs.readFileSync(path.join(__dirname, "PoLateTrackingPage.jsx"), "utf8");
  for (const label of [
    "No PO",
    "Tgl PO Masuk",
    "Nama Pelanggan",
    "Status Saat Ini",
    "Umur PO",
    "Estimasi Tgl Kirim",
    "Hari Terlambat",
    "Kategori",
    "Cabang",
    "Qty Sisa",
  ]) {
    expect(src).toContain(label);
  }
  expect(src).toContain("active_late_total");
  expect(src).toContain("filtered_total");
  expect(src).toContain("branch_options");
  expect(src).toContain("Semua cabang (scope)");
  expect(src).not.toContain("Cabang ID");
  expect(src).toContain("plt-col-po");
  expect(poLateTrackingPath({ late: true })).toContain("late=1");
});

test("tracking css keeps horizontal scroll on table wrap and sticky PO", () => {
  const css = fs.readFileSync(path.join(__dirname, "PoLateTrackingPage.css"), "utf8");
  expect(css).toContain(".plt-table-wrap");
  expect(css).toContain("overflow-x: auto");
  expect(css).toContain(".plt-col-po");
  expect(css).toContain("position: sticky");
  expect(css).toContain("overflow-x: hidden");
});

test("api client exposes late tracking endpoints", () => {
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  expect(api).toContain("/delivery/po-late-tracking");
  expect(api).toContain("/delivery/po-late-tracking/summary");
});
