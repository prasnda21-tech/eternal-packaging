/** Multi-column AND filter contract for Jadwal Produksi table mode. */
import {
  FILTER_DEFAULTS,
  applyProductionFilters,
  applyProductionScheduleDisplayRowFilters,
  countUniqueProductionSchedulePos,
  productionScheduleDisplayedShippingDate,
  productionScheduleDisplayedShippingDateKey,
  sortProductionRows,
  sortScheduleDisplayRows,
} from "./productionScheduleFilters";
import { buildProductionScheduleDisplayRows } from "./productionScheduleDisplayRows";

const DATE_C = "2026-10-01";
const DATE_D = "2026-10-08";

function op(name, splitId) {
  return {
    split_id: splitId,
    operator_name: name,
    operator_email: `${name.toLowerCase()}@x.com`,
    quantity: 500,
    line_idx: 0,
    line_id: `${splitId}-line`,
    line_name: "Cup",
    line_printable: true,
    work_date: "2026-09-20",
  };
}

function po({ id, poNumber, operatorName, shipDate, legacyDeadline, operators }) {
  return {
    id,
    po_number: poNumber,
    customer: `Cust-${poNumber}`,
    outlet: "Outlet",
    deadline: legacyDeadline || "1999-01-01",
    delivery_deadline_effective: `${shipDate}T00:00:00+07:00`,
    work_date: "2026-09-20",
    current_stage: 8,
    total_quantity: 500,
    print_qty_total: 500,
    operators: operators || [op(operatorName, `${id}-op`)],
  };
}

/** Same pipeline as ProductionSchedule.jsx table mode. */
function tablePipeline(rows, filters) {
  const filtered = applyProductionFilters(rows, { ...FILTER_DEFAULTS, ...filters });
  const sorted = sortProductionRows(filtered, filters.sortBy || "work_date");
  const expanded = buildProductionScheduleDisplayRows(sorted);
  const ordered = sortScheduleDisplayRows(expanded, filters.sortBy || "work_date");
  return applyProductionScheduleDisplayRowFilters(ordered, filters);
}

const matrix = [
  po({ id: "id-001", poNumber: "PO-001", operatorName: "A", shipDate: DATE_C }),
  po({ id: "id-002", poNumber: "PO-002", operatorName: "A", shipDate: DATE_D }),
  po({ id: "id-003", poNumber: "PO-003", operatorName: "B", shipDate: DATE_C }),
  po({ id: "id-004", poNumber: "PO-004", operatorName: "B", shipDate: DATE_D }),
];

describe("production schedule multi-column filter AND", () => {
  test("1. Operator A + Tanggal C → only A/C", () => {
    const rows = tablePipeline(matrix, {
      operatorFilter: "A",
      dateFrom: DATE_C,
      dateTo: DATE_C,
    });
    expect(rows.map((r) => r.po_number)).toEqual(["PO-001"]);
    expect(rows.map((r) => r.displayOperator.operator_name)).toEqual(["A"]);
  });

  test("2. Operator A + Tanggal D", () => {
    const rows = tablePipeline(matrix, {
      operatorFilter: "A",
      dateFrom: DATE_D,
      dateTo: DATE_D,
    });
    expect(rows.map((r) => r.po_number)).toEqual(["PO-002"]);
  });

  test("3. Operator B + Tanggal C", () => {
    const rows = tablePipeline(matrix, {
      operatorFilter: "B",
      dateFrom: DATE_C,
      dateTo: DATE_C,
    });
    expect(rows.map((r) => r.po_number)).toEqual(["PO-003"]);
  });

  test("4. filter combination with zero rows", () => {
    const rows = tablePipeline(matrix, {
      operatorFilter: "A",
      dateFrom: "2026-12-31",
      dateTo: "2026-12-31",
    });
    expect(rows).toHaveLength(0);
    expect(countUniqueProductionSchedulePos(rows)).toBe(0);
  });

  test("5. multi-operator PO keeps only selected operator row", () => {
    const multi = po({
      id: "id-multi",
      poNumber: "PO-MULTI",
      shipDate: DATE_C,
      operators: [op("A", "m-a"), op("B", "m-b")],
    });
    const rows = tablePipeline([multi], {
      operatorFilter: "A",
      dateFrom: DATE_C,
      dateTo: DATE_C,
    });
    expect(rows).toHaveLength(1);
    expect(rows[0].displayOperator.operator_name).toBe("A");
    expect(rows[0].po_number).toBe("PO-MULTI");
  });

  test("6. filter uses displayed shipping date, not legacy deadline", () => {
    const row = po({
      id: "id-legacy",
      poNumber: "PO-LEGACY",
      operatorName: "A",
      shipDate: DATE_C,
      legacyDeadline: DATE_D,
    });
    expect(productionScheduleDisplayedShippingDateKey(row)).toBe(DATE_C);
    expect(toLegacyKey(row.deadline)).toBe(DATE_D);
    expect(productionScheduleDisplayedShippingDate(row)).toBe(`${DATE_C}T00:00:00+07:00`);

    const matchDisplayed = tablePipeline([row], {
      operatorFilter: "A",
      dateFrom: DATE_C,
      dateTo: DATE_C,
    });
    expect(matchDisplayed.map((r) => r.po_number)).toEqual(["PO-LEGACY"]);

    const matchLegacyOnly = tablePipeline([row], {
      operatorFilter: "A",
      dateFrom: DATE_D,
      dateTo: DATE_D,
    });
    expect(matchLegacyOnly).toHaveLength(0);
  });

  test("7. clear Operator keeps Tanggal filter", () => {
    const withBoth = tablePipeline(matrix, {
      operatorFilter: "A",
      dateFrom: DATE_C,
      dateTo: DATE_C,
    });
    expect(withBoth.map((r) => r.po_number)).toEqual(["PO-001"]);

    const clearedOp = tablePipeline(matrix, {
      operatorFilter: "all",
      dateFrom: DATE_C,
      dateTo: DATE_C,
    });
    expect(clearedOp.map((r) => r.po_number).sort()).toEqual(["PO-001", "PO-003"]);
  });

  test("8. clear Tanggal keeps Operator filter", () => {
    const clearedDate = tablePipeline(matrix, {
      operatorFilter: "A",
      dateFrom: "",
      dateTo: "",
    });
    expect(clearedDate.map((r) => r.po_number).sort()).toEqual(["PO-001", "PO-002"]);
    expect(clearedDate.every((r) => r.displayOperator.operator_name === "A")).toBe(true);
  });

  test("9. Reset Filter defaults restore full matrix", () => {
    const rows = tablePipeline(matrix, { ...FILTER_DEFAULTS });
    expect(rows.map((r) => r.po_number).sort()).toEqual(["PO-001", "PO-002", "PO-003", "PO-004"]);
  });

  test("10. global search + two column filters AND", () => {
    const rows = tablePipeline(matrix, {
      q: "PO-001",
      operatorFilter: "A",
      dateFrom: DATE_C,
      dateTo: DATE_C,
    });
    expect(rows.map((r) => r.po_number)).toEqual(["PO-001"]);

    const noMatch = tablePipeline(matrix, {
      q: "PO-003",
      operatorFilter: "A",
      dateFrom: DATE_C,
      dateTo: DATE_C,
    });
    expect(noMatch).toHaveLength(0);
  });

  test("11. filter + sorting keeps correct membership", () => {
    const rows = tablePipeline(matrix, {
      operatorFilter: "B",
      dateFrom: DATE_C,
      dateTo: DATE_D,
      sortBy: "deadline",
    });
    expect(rows.map((r) => r.po_number).sort()).toEqual(["PO-003", "PO-004"]);
    expect(rows.every((r) => r.displayOperator.operator_name === "B")).toBe(true);
  });

  test("12. unique PO counter uses stable id after display filters", () => {
    const multi = po({
      id: "id-multi",
      poNumber: "PO-MULTI",
      shipDate: DATE_C,
      operators: [op("A", "m-a"), op("B", "m-b")],
    });
    const unfiltered = tablePipeline([multi, ...matrix], FILTER_DEFAULTS);
    expect(countUniqueProductionSchedulePos(unfiltered)).toBe(5);

    const onlyA = tablePipeline([multi, ...matrix], {
      operatorFilter: "A",
      dateFrom: DATE_C,
      dateTo: DATE_C,
    });
    expect(onlyA).toHaveLength(2); // PO-MULTI A + PO-001 A
    expect(countUniqueProductionSchedulePos(onlyA)).toBe(2);
    expect(new Set(onlyA.map((r) => r.id)).size).toBe(2);
  });

  test("Kartu-level PO filter still includes multi-operator PO when any operator matches", () => {
    const multi = po({
      id: "id-multi",
      poNumber: "PO-MULTI",
      shipDate: DATE_C,
      operators: [op("A", "m-a"), op("B", "m-b")],
    });
    const kartu = applyProductionFilters([multi], {
      ...FILTER_DEFAULTS,
      operatorFilter: "A",
      dateFrom: DATE_C,
      dateTo: DATE_C,
    });
    expect(kartu).toHaveLength(1);
    expect(kartu[0].operators.map((o) => o.operator_name)).toEqual(["A", "B"]);
  });

  test("active operator filter excludes unassigned placeholder rows", () => {
    const bare = {
      id: "id-bare",
      po_number: "PO-BARE",
      customer: "Bare",
      deadline: DATE_C,
      delivery_deadline_effective: `${DATE_C}T00:00:00+07:00`,
      work_date: "2026-09-20",
      current_stage: 8,
      print_qty_total: 100,
      operators: [],
    };
    const expanded = buildProductionScheduleDisplayRows(
      applyProductionFilters([bare], { ...FILTER_DEFAULTS, dateFrom: DATE_C, dateTo: DATE_C }),
    );
    expect(expanded).toHaveLength(1);
    expect(expanded[0].displayOperator).toBeNull();
    const afterOp = applyProductionScheduleDisplayRowFilters(expanded, { operatorFilter: "A" });
    expect(afterOp).toHaveLength(0);
  });
});

function toLegacyKey(value) {
  const s = String(value || "");
  const m = s.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : null;
}
