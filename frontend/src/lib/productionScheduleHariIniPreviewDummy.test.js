import {
  HARI_INI_PREVIEW_DUMMY_COUNT,
  HARI_INI_PREVIEW_DUMMY_POS,
  buildProductionScheduleHariIniPreviewDummyRows,
  isProductionScheduleHariIniPreviewHost,
  mergeHariIniPreviewDummyRows,
  shiftProductionDateKey,
} from "./productionScheduleHariIniPreviewDummy";
import { applyDeadlineTodayShortcut, applyProductionFilters, applyWorkDateTodayShortcut, FILTER_DEFAULTS, productionTodayKey } from "./productionScheduleFilters";

const NOW = new Date("2026-09-15T10:00:00+07:00");

describe("Jadwal Produksi Hari Ini preview dummy", () => {
  const prevEnv = process.env.NODE_ENV;

  afterEach(() => {
    process.env.NODE_ENV = prevEnv;
  });

  test("builds exactly 3 WIB-relative dummy rows", () => {
    const rows = buildProductionScheduleHariIniPreviewDummyRows(NOW);
    expect(rows).toHaveLength(HARI_INI_PREVIEW_DUMMY_COUNT);
    expect(rows.map((r) => r.po_number)).toEqual([...HARI_INI_PREVIEW_DUMMY_POS]);
    const today = productionTodayKey(NOW);
    expect(rows[0].work_date).toBe(today);
    expect(rows[0].deadline.startsWith(today)).toBe(true);
    expect(rows[1].work_date).toBe(today);
    expect(rows[1].deadline.startsWith(shiftProductionDateKey(today, 1))).toBe(true);
    expect(rows[2].work_date).toBe(shiftProductionDateKey(today, -1));
    expect(rows[2].deadline.startsWith(today)).toBe(true);
  });

  test("production runtime never injects dummy rows", () => {
    process.env.NODE_ENV = "production";
    const live = [{ po_number: "PO-LIVE" }];
    expect(isProductionScheduleHariIniPreviewHost({ location: { hostname: "localhost", port: "3003" } })).toBe(false);
    expect(mergeHariIniPreviewDummyRows(live, NOW, { location: { hostname: "localhost", port: "3003" } })).toEqual(live);
  });

  test("localhost:3003 non-production injects the 3 dummy rows", () => {
    process.env.NODE_ENV = "development";
    const merged = mergeHariIniPreviewDummyRows([], NOW, { location: { hostname: "localhost", port: "3003" } });
    expect(merged).toHaveLength(3);
  });
});

describe("date-column Hari Ini shortcuts stay independent", () => {
  const NOW = new Date("2026-09-15T10:00:00+07:00");
  const rows = buildProductionScheduleHariIniPreviewDummyRows(NOW);

  test("Tgl Pengerjaan Hari Ini keeps today work dates and excludes yesterday", () => {
    const work = applyWorkDateTodayShortcut("all", NOW);
    const filtered = applyProductionFilters(rows, { ...FILTER_DEFAULTS, ...work });
    expect(filtered.map((r) => r.po_number)).toEqual(["PO-HI-D1", "PO-HI-D2"]);
  });

  test("Tanggal Pengiriman Hari Ini is independent of Tgl Pengerjaan", () => {
    const deadline = applyDeadlineTodayShortcut(NOW);
    const filtered = applyProductionFilters(rows, { ...FILTER_DEFAULTS, ...deadline });
    expect(filtered.map((r) => r.po_number)).toEqual(["PO-HI-D1", "PO-HI-D3"]);
  });

  test("both date shortcuts compose without sharing state", () => {
    const work = applyWorkDateTodayShortcut("all", NOW);
    const deadline = applyDeadlineTodayShortcut(NOW);
    const filtered = applyProductionFilters(rows, { ...FILTER_DEFAULTS, ...work, ...deadline });
    expect(filtered.map((r) => r.po_number)).toEqual(["PO-HI-D1"]);
  });

  test("Hari Ini + operator keeps both constraints", () => {
    const work = applyWorkDateTodayShortcut("all", NOW);
    const filtered = applyProductionFilters(rows, { ...FILTER_DEFAULTS, ...work, operatorFilter: "Fauzan" });
    expect(filtered.map((r) => r.po_number)).toEqual(["PO-HI-D1"]);
  });

  test("reset date fields restore unfiltered dummy set", () => {
    const filtered = applyProductionFilters(rows, {
      ...FILTER_DEFAULTS,
      today: NOW,
      workDateFrom: "",
      workDateTo: "",
      dateFrom: "",
      dateTo: "",
    });
    expect(filtered.map((r) => r.po_number)).toEqual(["PO-HI-D1", "PO-HI-D2", "PO-HI-D3"]);
  });
});
