import { entriesForLine, findEntryByActionId, sumEntryQty, visibleEntries } from "./workflowEntries";

describe("append-only entries", () => {
  test("total is cumulative, not latest only", () => {
    expect(sumEntryQty([{ qty: 5000 }, { qty: 2000 }])).toBe(7000);
    expect(sumEntryQty([{ qty: 5000 }, { qty: 2000 }, { qty: 1500 }])).toBe(8500);
  });

  test("cancelled entries are excluded", () => {
    expect(sumEntryQty([{ qty: 5000 }, { qty: 2000, cancelled: true }])).toBe(5000);
  });

  test("legacy snapshot becomes a locked visible row", () => {
    const rows = visibleEntries([], 5000, "2026-08-26T08:15:00+07:00", "Budi");
    expect(rows).toHaveLength(1);
    expect(rows[0].qty).toBe(5000);
    expect(rows[0].legacy).toBe(true);
  });

  test("stored entries are not replaced by snapshot", () => {
    const stored = [{ id: "e1", qty: 5000 }, { id: "e2", qty: 2000 }];
    const rows = visibleEntries(stored, 9999, null, "Budi");
    expect(rows).toHaveLength(2);
    expect(sumEntryQty(rows)).toBe(7000);
  });

  test("idempotent action_id finds the same entry", () => {
    const entries = [{ id: "e1", qty: 5000, action_id: "act-1" }];
    expect(findEntryByActionId(entries, "act-1").qty).toBe(5000);
    expect(findEntryByActionId(entries, "act-2")).toBeNull();
  });

  test("entriesForLine filters by line", () => {
    const rows = [
      { id: "a", qty: 100, line_idx: 0 },
      { id: "b", qty: 50, line_idx: 1 },
    ];
    expect(entriesForLine(rows, 0).map((e) => e.id)).toEqual(["a"]);
  });
});
