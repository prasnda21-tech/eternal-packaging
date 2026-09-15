import {
  HARI_INI_PREVIEW_DUMMY_COUNT,
  HARI_INI_PREVIEW_DUMMY_POS,
  buildProductionScheduleHariIniPreviewDummyRows,
  isProductionScheduleHariIniPreviewHost,
  mergeHariIniPreviewDummyRows,
  shiftProductionDateKey,
} from "./productionScheduleHariIniPreviewDummy";
import { applyProductionFilters, FILTER_DEFAULTS, toDateKey } from "./productionScheduleFilters";
import { wibDateKey } from "./suratJalan";

const NOW = new Date("2026-09-15T10:00:00+07:00");

describe("Jadwal Produksi Hari Ini preview dummy (production model)", () => {
  const prevEnv = process.env.NODE_ENV;

  afterEach(() => {
    process.env.NODE_ENV = prevEnv;
  });

  test("builds exactly 3 WIB-relative dummy rows", () => {
    const rows = buildProductionScheduleHariIniPreviewDummyRows(NOW);
    expect(rows).toHaveLength(HARI_INI_PREVIEW_DUMMY_COUNT);
    expect(rows.map((r) => r.po_number)).toEqual([...HARI_INI_PREVIEW_DUMMY_POS]);
    const today = wibDateKey(NOW);
    expect(rows[0].work_date).toBe(today);
    expect(toDateKey(rows[0].deadline)).toBe(today);
    expect(rows[1].work_date).toBe(today);
    expect(toDateKey(rows[1].deadline)).toBe(shiftProductionDateKey(today, 1));
    expect(rows[2].work_date).toBe(shiftProductionDateKey(today, -1));
    expect(toDateKey(rows[2].deadline)).toBe(today);
  });

  test("production runtime never injects dummy rows", () => {
    process.env.NODE_ENV = "production";
    const live = [{ po_number: "PO-LIVE" }];
    expect(isProductionScheduleHariIniPreviewHost({ location: { hostname: "localhost", port: "3003" } })).toBe(false);
    expect(mergeHariIniPreviewDummyRows(live, NOW, { location: { hostname: "localhost", port: "3003" } })).toEqual(live);
  });

  test("localhost:3003 development injects the 3 dummy rows", () => {
    process.env.NODE_ENV = "development";
    const merged = mergeHariIniPreviewDummyRows([], NOW, { location: { hostname: "localhost", port: "3003" } });
    expect(merged).toHaveLength(3);
  });
});

describe("date-range Hari Ini shortcuts stay independent", () => {
  const rows = buildProductionScheduleHariIniPreviewDummyRows(NOW);
  const today = wibDateKey(NOW);

  test("Tgl Pengerjaan today range keeps today work dates and excludes yesterday", () => {
    const filtered = applyProductionFilters(rows, { ...FILTER_DEFAULTS, workDateFrom: today, workDateTo: today });
    expect(filtered.map((r) => r.po_number)).toEqual(["PO-HI-D1", "PO-HI-D2"]);
  });

  test("Tanggal Pengiriman today range is independent of Tgl Pengerjaan", () => {
    const filtered = applyProductionFilters(rows, { ...FILTER_DEFAULTS, dateFrom: today, dateTo: today });
    expect(filtered.map((r) => r.po_number)).toEqual(["PO-HI-D1", "PO-HI-D3"]);
  });

  test("both date ranges compose without sharing state", () => {
    const filtered = applyProductionFilters(rows, {
      ...FILTER_DEFAULTS,
      workDateFrom: today,
      workDateTo: today,
      dateFrom: today,
      dateTo: today,
    });
    expect(filtered.map((r) => r.po_number)).toEqual(["PO-HI-D1"]);
  });

  test("Hari Ini range + operator keeps both constraints", () => {
    const filtered = applyProductionFilters(rows, {
      ...FILTER_DEFAULTS,
      workDateFrom: today,
      workDateTo: today,
      operatorFilter: "Fauzan",
    });
    expect(filtered.map((r) => r.po_number)).toEqual(["PO-HI-D1"]);
  });

  test("cleared ranges restore unfiltered dummy set", () => {
    const filtered = applyProductionFilters(rows, {
      ...FILTER_DEFAULTS,
      workDateFrom: "",
      workDateTo: "",
      dateFrom: "",
      dateTo: "",
    });
    expect(filtered.map((r) => r.po_number)).toEqual(["PO-HI-D1", "PO-HI-D2", "PO-HI-D3"]);
  });
});
