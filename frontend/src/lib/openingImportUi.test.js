import {
  STATUS_SIAP,
  buildConfirmLines,
  classifyImportConfirmResponse,
  defaultSelectedKeys,
  isReadyRow,
  rowSelectKey,
  summarizeImportRows,
} from "./openingImportUi";

function row(n, status, extra = {}) {
  return {
    row_number: n,
    item_code: `C-${n}`,
    status,
    master_item_id: `m-${n}`,
    branch_id: "br",
    warehouse_id: "wh",
    physical_qty: n,
    ...extra,
  };
}

describe("openingImportUi", () => {
  test("defaults select every Siap row only", () => {
    const rows = [
      row(2, STATUS_SIAP),
      row(3, "Duplikat"),
      row(4, STATUS_SIAP),
      row(5, "Qty Tidak Valid"),
    ];
    const keys = defaultSelectedKeys(rows);
    expect([...keys].sort()).toEqual(["2", "4"]);
    expect(isReadyRow(rows[1])).toBe(false);
  });

  test("summarize 17 siap / 3 invalid and subset selection", () => {
    const rows = [];
    for (let i = 2; i <= 18; i += 1) rows.push(row(i, STATUS_SIAP));
    rows.push(row(19, "Duplikat"));
    rows.push(row(20, "Kode Tidak Ditemukan"));
    rows.push(row(21, "Qty Tidak Valid"));
    expect(rows.filter(isReadyRow)).toHaveLength(17);
    const all = summarizeImportRows(rows, defaultSelectedKeys(rows));
    expect(all.readyCount).toBe(17);
    expect(all.rejectedCount).toBe(3);
    expect(all.selectedCount).toBe(17);
    const subsetKeys = new Set(["2", "5", "10"]);
    const subset = summarizeImportRows(rows, subsetKeys);
    expect(subset.selectedCount).toBe(3);
    const lines = buildConfirmLines(subset.selectedRows);
    expect(lines).toHaveLength(3);
    expect(lines.map((l) => l.item_code).sort()).toEqual(["C-10", "C-2", "C-5"]);
  });

  test("rowSelectKey is stable on row_number", () => {
    expect(rowSelectKey({ row_number: 12 })).toBe("12");
  });

  test("classify confirm response without inventing PROCESSING", () => {
    expect(classifyImportConfirmResponse({ import: { status: "CONFIRMED" } })).toBe("confirmed");
    expect(classifyImportConfirmResponse({ import: { status: "PROCESSING" } })).toBe("processing");
    expect(classifyImportConfirmResponse({ idempotent: false })).toBe("confirmed");
    expect(classifyImportConfirmResponse({ import: {} })).toBe("unknown");
  });
});
