import fs from "fs";
import path from "path";
import { PRODUCTION_SCHEDULE_COLUMN_IDS, PRODUCTION_SCHEDULE_TABLE_COLS } from "../lib/productionScheduleTable";
import { isScheduledToday } from "../lib/productionScheduleFilters";

describe("ProductionSchedule: customer column, today highlight, and sheet order", () => {
  const scheduleSrc = fs.readFileSync(path.join(__dirname, "ProductionSchedule.jsx"), "utf8");
  const cellsSrc = fs.readFileSync(path.join(__dirname, "../components/ProductionScheduleSheetCells.jsx"), "utf8");
  const mobileSrc = fs.readFileSync(path.join(__dirname, "../mobile/MobileProductionSchedulePage.jsx"), "utf8");
  const cssSrc = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const sheetCss = fs.readFileSync(path.join(__dirname, "../productionSheet.css"), "utf8");

  test("desktop and PWA consume the same ordered column definition", () => {
    expect(scheduleSrc).toMatch(/PRODUCTION_SCHEDULE_TABLE_COLS as TABLE_COLS/);
    expect(mobileSrc).toMatch(/PRODUCTION_SCHEDULE_TABLE_COLS/);
    expect(cellsSrc).toMatch(/PRODUCTION_SCHEDULE_TABLE_COLS\)\.map/);
    expect(PRODUCTION_SCHEDULE_TABLE_COLS).toHaveLength(22);
    expect(PRODUCTION_SCHEDULE_COLUMN_IDS).toEqual(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.key));
    expect(PRODUCTION_SCHEDULE_COLUMN_IDS[0]).toBe("po");
    expect(PRODUCTION_SCHEDULE_COLUMN_IDS.slice(1, 7)).toEqual(["customer", "work_date", "operator", "item", "qty_cetak", "qty"]);
    expect(PRODUCTION_SCHEDULE_COLUMN_IDS).toContain("customer");
  });

  test("Tanggal Pengerjaan cell formats with shared display helper only", () => {
    expect(cellsSrc).toMatch(/formatDisplayDate/);
    expect(cellsSrc).toMatch(/formatWorkDateDisplay/);
    expect(cellsSrc).toMatch(/case "work_date":\s*return <WorkDateCell/);
    expect(cellsSrc).not.toMatch(/\{entry\.work_date \|\| "—"\}/);
    expect(cellsSrc).not.toMatch(/\{displayDate\}/);
  });

  test("customer cell stays canonical and outlet stays a separate column", () => {
    expect(cellsSrc).toMatch(/case "customer":\s*return <strong>\{row\.customer \|\| "-"\}<\/strong>/);
    expect(cellsSrc).toMatch(/case "outlet":\s*return row\.outlet \|\| "-"/);
    expect(cellsSrc).not.toMatch(/row\.customer \|\| row\.outlet/);
    expect(cellsSrc).not.toMatch(/row\.customer_name \|\| row\.outlet/);
  });

  test("customer today highlight remains on the customer cell", () => {
    expect(cellsSrc).toMatch(/prod-customer-\$\{row\.po_number\}/);
    expect(cellsSrc).toMatch(/prod-cell-today/);
    expect(cellsSrc).toMatch(/backgroundColor:\s*"#fef08a"/);
    expect(cellsSrc).toMatch(/color:\s*"#713f12"/);
  });

  test("new sheet cells do not force ellipsis truncation", () => {
    expect(sheetCss).toMatch(/text-overflow:\s*clip/);
    expect(sheetCss).toMatch(/\.production-sheet-cell-wrap\s*\{\s*white-space:\s*normal/);
    expect(sheetCss).toMatch(/\.production-sheet-table \.prod-line-qty-name[\s\S]*text-overflow:\s*clip/);
    expect(sheetCss).not.toMatch(/\.production-sheet-cell[^{]*\{[^}]*text-overflow:\s*ellipsis/);
  });

  test("shared spreadsheet header is slightly larger than body and still zoom-scaled", () => {
    expect(sheetCss).toMatch(/\.production-sheet-header/);
    expect(sheetCss).toMatch(/position:\s*sticky/);
    expect(sheetCss).toMatch(/font-size:\s*max\(9\.5px,\s*calc\(13px \* var\(--production-sheet-zoom/);
    expect(sheetCss).toMatch(/padding:\s*calc\(5px \* var\(--production-sheet-zoom/);
    expect(sheetCss).toMatch(/font-weight:\s*700/);
    expect(sheetCss).toMatch(/\.production-sheet-header[\s\S]*font-weight:\s*700/);
    expect(sheetCss).toMatch(/\.production-sheet-table tbody td[\s\S]*font-weight:\s*600/);
    expect(sheetCss).toMatch(/\.production-sheet-table tbody td[\s\S]*font-size:\s*calc\(11px \* var\(--production-sheet-zoom/);
    expect(scheduleSrc).toMatch(/buildProductionScheduleDisplayRows/);
    expect(mobileSrc).toMatch(/buildProductionScheduleDisplayRows/);
    expect(scheduleSrc).toMatch(/useProductionSheetZoom/);
    expect(mobileSrc).toMatch(/useProductionSheetZoom/);
    expect(scheduleSrc).toMatch(/payload\.ids = filtered\.map/);
    expect(mobileSrc).toMatch(/payload\.ids = filtered\.map/);
  });

  test("isScheduledToday deterministic logic handles today, future, past, completed, and cancelled", () => {
    const mockToday = new Date("2026-09-04T10:00:00+07:00");
    const todayRow = {
      id: "1",
      po_number: "PO-TODAY",
      work_date: "2026-09-04",
      current_stage: 8,
      customer: "Toko Abadi",
    };
    expect(isScheduledToday(todayRow, mockToday)).toBe(true);
    expect(isScheduledToday({
      id: "2",
      po_number: "PO-OP-TODAY",
      work_date: null,
      operator_work_dates: [{ split_id: "s1", operator_name: "Budi", work_date: "2026-09-04" }],
      current_stage: 9,
      customer: "CV Maju",
    }, mockToday)).toBe(true);
    expect(isScheduledToday({ ...todayRow, id: "3", po_number: "PO-FUTURE", work_date: "2026-09-05" }, mockToday)).toBe(false);
    expect(isScheduledToday({ ...todayRow, id: "4", po_number: "PO-PAST", work_date: "2026-09-03" }, mockToday)).toBe(false);
    expect(isScheduledToday({ ...todayRow, id: "5", po_number: "PO-CANCELLED", cancelled: true }, mockToday)).toBe(false);
    expect(isScheduledToday({ ...todayRow, id: "6", po_number: "PO-CANCELLED-STATUS", status_final: "Cancelled" }, mockToday)).toBe(false);
    expect(isScheduledToday({ ...todayRow, id: "7", po_number: "PO-COMPLETED", current_stage: 14 }, mockToday)).toBe(false);
    expect(isScheduledToday({ ...todayRow, id: "8", po_number: "PO-TERKIRIM", status_final: "Terkirim" }, mockToday)).toBe(false);
    expect(isScheduledToday({
      id: "9",
      po_number: "PO-UNSCHED",
      work_date: null,
      operator_work_dates: [],
      current_stage: 5,
    }, mockToday)).toBe(false);
    expect(isScheduledToday({ ...todayRow, id: "10", po_number: "PO-HOLD", hold: true, status_final: "HOLD" }, mockToday)).toBe(true);
  });

  test("today highlight CSS remains available", () => {
    expect(cssSrc).toMatch(/\.prod-table tbody td\.prod-cell-today/);
    expect(cssSrc).toMatch(/background-color:\s*#fef08a/);
    expect(cssSrc).toMatch(/color:\s*#713f12/);
    expect(sheetCss).toMatch(/td\.prod-cell-today/);
  });
});
