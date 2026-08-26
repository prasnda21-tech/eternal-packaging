import {
  allOperatorIds,
  exportOperatorIds,
  filterOperatorSummary,
  nextSelectAll,
  searchOperators,
  selectAllState,
  toggleOperatorId,
} from "./operatorFilter";

const OPERATORS = [
  { operator_user_id: "u-budi", name: "Budi", email: "budi@x.com" },
  { operator_user_id: "u-andi", name: "Andi", email: "andi@x.com" },
  { operator_user_id: "u-sinta", name: "Sinta", email: "sinta@x.com" },
  { operator_user_id: "u-rudi", name: "Rudi", email: "rudi@x.com" },
];

describe("operator compact multi-select", () => {
  const allIds = allOperatorIds(OPERATORS);

  test("primary identity is operator_user_id not name", () => {
    expect(allIds).toEqual(["u-budi", "u-andi", "u-sinta", "u-rudi"]);
    expect(allOperatorIds([{ name: "Budi", operator_user_id: "u-budi" }])[0]).toBe("u-budi");
  });

  test("0 selected click select all selects every operator one click", () => {
    expect(nextSelectAll([], allIds)).toEqual(allIds);
  });

  test("all selected click select all clears", () => {
    expect(nextSelectAll(allIds, allIds)).toEqual([]);
  });

  test("partial is indeterminate and click selects all", () => {
    expect(selectAllState(["u-budi", "u-andi"], allIds)).toBe("partial");
    expect(nextSelectAll(["u-budi", "u-andi"], allIds)).toEqual(allIds);
  });

  test("search filters display only; select all still uses full dataset", () => {
    const visible = searchOperators(OPERATORS, "Bud");
    expect(visible.map((o) => o.name)).toEqual(["Budi"]);
    expect(nextSelectAll([], allOperatorIds(OPERATORS))).toEqual(allIds);
  });

  test("summary labels", () => {
    expect(filterOperatorSummary([], OPERATORS)).toBe("Semua Operator");
    expect(filterOperatorSummary(allIds, OPERATORS)).toBe("Semua Operator");
    expect(filterOperatorSummary(["u-budi"], OPERATORS)).toBe("Budi");
    expect(filterOperatorSummary(["u-budi", "u-andi"], OPERATORS)).toBe("2 Operator dipilih");
  });

  test("export follows exact selection; empty means all", () => {
    expect(exportOperatorIds([], OPERATORS)).toBeUndefined();
    expect(exportOperatorIds(allIds, OPERATORS)).toBeUndefined();
    expect(exportOperatorIds(["u-budi", "u-andi"], OPERATORS)).toBe("u-budi,u-andi");
  });

  test("toggle operator", () => {
    expect(toggleOperatorId([], "u-budi")).toEqual(["u-budi"]);
    expect(toggleOperatorId(["u-budi"], "u-budi")).toEqual([]);
  });
});
