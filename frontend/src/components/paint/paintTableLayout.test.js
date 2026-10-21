import {
  clientFilterRows,
  clientSortRows,
  encodeHeaderFilters,
  mergePaintLayout,
  moveReportColumn,
  paintLayoutKey,
} from "./paintTableLayout";

describe("paintTableLayout", () => {
  test("layout key isolates user and table", () => {
    expect(paintLayoutKey("u1", "paint-tx")).toContain("u1");
    expect(paintLayoutKey("u1", "paint-tx")).toContain("paint-tx");
    expect(paintLayoutKey("u1", "a")).not.toEqual(paintLayoutKey("u2", "a"));
  });

  test("merge keeps unknown saved columns out and adds new defaults", () => {
    const defs = [
      { key: "a", label: "A", width: 100 },
      { key: "b", label: "B", width: 120 },
    ];
    const merged = mergePaintLayout(defs, { order: ["b", "gone", "a"], widths: { a: 200, gone: 50 } });
    expect(merged.order).toEqual(["b", "a"]);
    expect(merged.widths.a).toBe(200);
    expect(merged.widths.b).toBe(120);
  });

  test("moveReportColumn reorders", () => {
    expect(moveReportColumn(["a", "b", "c"], "c", "a")).toEqual(["c", "a", "b"]);
  });

  test("encodeHeaderFilters", () => {
    expect(encodeHeaderFilters({ notes: { op: "contains", value: "abc" } })).toEqual(["notes:contains:abc"]);
    expect(encodeHeaderFilters({ notes: { op: "contains", value: "" } })).toEqual([]);
  });

  test("client sort numeric and filter contains", () => {
    const rows = [
      { id: "1", qty: 10, name: "Biru" },
      { id: "2", qty: 2, name: "Merah" },
      { id: "3", qty: 30, name: "Biru Laut" },
    ];
    expect(clientSortRows(rows, "qty", "asc").map((r) => r.id)).toEqual(["2", "1", "3"]);
    expect(clientFilterRows(rows, { name: { op: "contains", value: "biru" } }).map((r) => r.id)).toEqual(["1", "3"]);
  });
});
