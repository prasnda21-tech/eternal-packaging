import {
  SALES_MASTER_COL_STORAGE_KEY,
  SALES_MASTER_DEFAULT_COL_ORDER,
  SALES_MASTER_DEFAULT_COL_WIDTHS,
  SALES_MASTER_MIN_COL_WIDTHS,
  SALES_MASTER_UNASSIGNED_BRANCH,
  SALES_MASTER_UNLINKED_USER,
  clampSalesMasterColWidth,
  clearSalesMasterColPrefs,
  countActiveSalesMasterFilters,
  filterSalesMasterRows,
  isSalesMasterColFilterActive,
  moveSalesMasterColumn,
  normalizeSalesMasterColFilters,
  readSalesMasterColPrefs,
  salesMasterColStorageKey,
  shiftSalesMasterColumn,
  writeSalesMasterColPrefs,
} from "./salesMasterColumns";

describe("salesMasterColumns prefs", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  test("clamp respects minimum widths", () => {
    expect(clampSalesMasterColWidth("name", 10)).toBe(SALES_MASTER_MIN_COL_WIDTHS.name);
    expect(clampSalesMasterColWidth("actions", 200)).toBe(200);
  });

  test("move and shift reorder without dropping columns", () => {
    const moved = moveSalesMasterColumn(SALES_MASTER_DEFAULT_COL_ORDER, "status", "name");
    expect(moved[0]).toBe("status");
    expect(moved).toHaveLength(SALES_MASTER_DEFAULT_COL_ORDER.length);
    const shifted = shiftSalesMasterColumn(moved, "status", 1);
    expect(shifted[1]).toBe("status");
  });

  test("read/write/clear prefs round-trip including filters", () => {
    writeSalesMasterColPrefs({
      order: ["status", "name", "branches", "user", "sort_order", "actions"],
      widths: { ...SALES_MASTER_DEFAULT_COL_WIDTHS, name: 280 },
      filters: { name: "ayu", branches: ["b1"], user: SALES_MASTER_UNLINKED_USER, status: "active", sort_min: "1", sort_max: "50" },
    }, undefined, "user-a");
    const prefs = readSalesMasterColPrefs(undefined, "user-a");
    expect(prefs.order[0]).toBe("status");
    expect(prefs.widths.name).toBe(280);
    expect(prefs.filters.name).toBe("ayu");
    expect(prefs.filters.branches).toEqual(["b1"]);
    expect(countActiveSalesMasterFilters(prefs.filters)).toBe(5);
    expect(window.localStorage.getItem(salesMasterColStorageKey("user-a"))).toBeTruthy();
    clearSalesMasterColPrefs(undefined, "user-a");
    expect(window.localStorage.getItem(salesMasterColStorageKey("user-a"))).toBeNull();
  });

  test("user-scoped key falls back to legacy shared key", () => {
    writeSalesMasterColPrefs({
      order: SALES_MASTER_DEFAULT_COL_ORDER,
      widths: SALES_MASTER_DEFAULT_COL_WIDTHS,
      filters: { name: "legacy" },
    });
    expect(window.localStorage.getItem(SALES_MASTER_COL_STORAGE_KEY)).toBeTruthy();
    const prefs = readSalesMasterColPrefs(undefined, "user-b");
    expect(prefs.filters.name).toBe("legacy");
  });

  test("normalize filters drops invalid status", () => {
    expect(normalizeSalesMasterColFilters({ status: "weird" }).status).toBe("");
  });
});

describe("filterSalesMasterRows", () => {
  const rows = [
    { id: "1", sales_name: "Ayub MS", branch_ids: ["jkt"], user_id: "u1", active: true, sort_order: 10 },
    { id: "2", sales_name: "Vira", branch_ids: [], user_id: null, active: false, sort_order: 20 },
    { id: "3", sales_name: "Budi Ayub", branch_ids: ["sby", "jkt"], user_id: "u2", active: true, sort_order: 5 },
  ];

  test("global query AND column name filter", () => {
    const out = filterSalesMasterRows(rows, { query: "ayu", filters: { name: "budi" } });
    expect(out.map((r) => r.id)).toEqual(["3"]);
  });

  test("multi filters branches + status + sort range", () => {
    const out = filterSalesMasterRows(rows, {
      filters: {
        branches: ["jkt"],
        status: "active",
        sort_min: "6",
        sort_max: "15",
      },
    });
    expect(out.map((r) => r.id)).toEqual(["1"]);
  });

  test("unassigned branch and unlinked user", () => {
    const out = filterSalesMasterRows(rows, {
      filters: {
        branches: [SALES_MASTER_UNASSIGNED_BRANCH],
        user: SALES_MASTER_UNLINKED_USER,
      },
    });
    expect(out.map((r) => r.id)).toEqual(["2"]);
  });

  test("active filter helpers", () => {
    expect(isSalesMasterColFilterActive("name", { name: "x" })).toBe(true);
    expect(isSalesMasterColFilterActive("actions", { name: "x" })).toBe(false);
  });
});
