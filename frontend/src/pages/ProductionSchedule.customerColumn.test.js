import fs from "fs";
import path from "path";
import { PRODUCTION_SCHEDULE_TABLE_COLS } from "../lib/productionScheduleTable";
import { isScheduledToday } from "../lib/productionScheduleFilters";

describe("ProductionSchedule: Nama Customer column & today yellow cell highlight", () => {
  const scheduleSrc = fs.readFileSync(path.join(__dirname, "ProductionSchedule.jsx"), "utf8");
  const cssSrc = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");

  // Expected 19 columns preserving their exact previous relative order
  const EXPECTED_OTHER_COLS = [
    "deadline",
    "deadline_ship",
    "work_date",
    "prio",
    "buffer",
    "po",
    "outlet",
    "qty",
    "qty_cetak",
    "operator",
    "design",
    "payment",
    "hold",
    "screen",
    "helper",
    "packing",
    "sj",
    "driver",
    "status",
  ];

  test("1. Nama Customer is now the first normal data column in TABLE_COLS", () => {
    expect(scheduleSrc).toMatch(/PRODUCTION_SCHEDULE_TABLE_COLS as TABLE_COLS/);
    expect(PRODUCTION_SCHEDULE_TABLE_COLS[0].key).toBe("customer");
    expect(PRODUCTION_SCHEDULE_TABLE_COLS).toHaveLength(20);
  });

  test("2. Every other column remains in its previous relative order exactly", () => {
    const otherCols = PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.key).filter((k) => k !== "customer");
    expect(otherCols).toEqual(EXPECTED_OTHER_COLS);
  });

  test("3. In table body rows, Nama Customer cell is the first <td>", () => {
    // Find tbody map block
    const tbodyMatch = scheduleSrc.match(/<tbody>[\s\S]*?{sorted\.map\(\(r\) => {([\s\S]*?)}\s*\)}[\s\S]*?<\/tbody>/);
    expect(tbodyMatch).toBeTruthy();
    const rowBody = tbodyMatch[1];

    // The first td rendered right after <tr ...> must be customer
    const firstTdMatch = rowBody.match(/<tr[^>]*>\s*(<td[\s\S]*?<\/td>)/);
    expect(firstTdMatch).toBeTruthy();
    const firstTd = firstTdMatch[1];

    expect(firstTd).toMatch(/data-testid=\{`prod-customer-\$\{r\.po_number\}`\}/);
    expect(firstTd).toMatch(/r\.customer/);

    // Customer td must not appear between po and outlet anymore
    expect(rowBody).not.toMatch(/prod-po-link[\s\S]*?<td[^>]*><strong>\{r\.customer[\s\S]*?prod-cell-wrap/);
  });

  test("4. Only Nama Customer cell has today yellow highlight", () => {
    const tbodyMatch = scheduleSrc.match(/<tbody>[\s\S]*?{sorted\.map\(\(r\) => {([\s\S]*?)}\s*\)}[\s\S]*?<\/tbody>/);
    const rowBody = tbodyMatch[1];

    // Customer td has isToday, prod-cell-today class and yellow background
    expect(rowBody).toMatch(/isToday\s*\?\s*" prod-cell-today"/);
    expect(rowBody).toMatch(/backgroundColor:\s*"#fef08a"/);
    expect(rowBody).toMatch(/color:\s*"#713f12"/);

    // Other cells in row do NOT have prod-cell-today or #fef08a
    const cellsWithoutFirst = rowBody.replace(/<td[\s\S]*?data-testid=\{`prod-customer-\$\{r\.po_number\}`\}[\s\S]*?<\/td>/, "");
    expect(cellsWithoutFirst).not.toMatch(/prod-cell-today/);
    expect(cellsWithoutFirst).not.toMatch(/#fef08a/);
  });

  test("5. isScheduledToday deterministic logic handles today, future, past, completed, and cancelled", () => {
    const mockToday = new Date("2026-09-04T10:00:00+07:00");

    // Today row via work_date -> true
    const todayRow = {
      id: "1",
      po_number: "PO-TODAY",
      work_date: "2026-09-04",
      current_stage: 8,
      customer: "Toko Abadi",
    };
    expect(isScheduledToday(todayRow, mockToday)).toBe(true);

    // Today row via operator split work_date -> true
    const operatorTodayRow = {
      id: "2",
      po_number: "PO-OP-TODAY",
      work_date: null,
      operator_work_dates: [{ split_id: "s1", operator_name: "Budi", work_date: "2026-09-04" }],
      current_stage: 9,
      customer: "CV Maju",
    };
    expect(isScheduledToday(operatorTodayRow, mockToday)).toBe(true);

    // Future schedule -> false
    const futureRow = {
      ...todayRow,
      id: "3",
      po_number: "PO-FUTURE",
      work_date: "2026-09-05",
    };
    expect(isScheduledToday(futureRow, mockToday)).toBe(false);

    // Past schedule -> false
    const pastRow = {
      ...todayRow,
      id: "4",
      po_number: "PO-PAST",
      work_date: "2026-09-03",
    };
    expect(isScheduledToday(pastRow, mockToday)).toBe(false);

    // Cancelled row scheduled today -> false
    const cancelledRow = {
      ...todayRow,
      id: "5",
      po_number: "PO-CANCELLED",
      cancelled: true,
    };
    expect(isScheduledToday(cancelledRow, mockToday)).toBe(false);

    const cancelledStatusRow = {
      ...todayRow,
      id: "6",
      po_number: "PO-CANCELLED-STATUS",
      status_final: "Cancelled",
    };
    expect(isScheduledToday(cancelledStatusRow, mockToday)).toBe(false);

    // Completed / Terkirim row scheduled today -> false
    const completedRow = {
      ...todayRow,
      id: "7",
      po_number: "PO-COMPLETED",
      current_stage: 14,
    };
    expect(isScheduledToday(completedRow, mockToday)).toBe(false);

    const terkirimStatusRow = {
      ...todayRow,
      id: "8",
      po_number: "PO-TERKIRIM",
      status_final: "Terkirim",
    };
    expect(isScheduledToday(terkirimStatusRow, mockToday)).toBe(false);

    // Unscheduled row -> false
    const unscheduledRow = {
      id: "9",
      po_number: "PO-UNSCHED",
      work_date: null,
      operator_work_dates: [],
      current_stage: 5,
    };
    expect(isScheduledToday(unscheduledRow, mockToday)).toBe(false);

    // HOLD row scheduled today and not cancelled/completed -> true
    const holdRow = {
      ...todayRow,
      id: "10",
      po_number: "PO-HOLD",
      hold: true,
      status_final: "HOLD",
    };
    expect(isScheduledToday(holdRow, mockToday)).toBe(true);
  });

  test("6. CSS rules in App.css define .prod-cell-today with yellow background and readable dark text", () => {
    expect(cssSrc).toMatch(/\.prod-table tbody td\.prod-cell-today/);
    expect(cssSrc).toMatch(/background-color:\s*#fef08a/);
    expect(cssSrc).toMatch(/color:\s*#713f12/);
  });
});
