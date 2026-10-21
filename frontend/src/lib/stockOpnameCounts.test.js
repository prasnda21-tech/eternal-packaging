import { buildStockOpnameCountPayload } from "./stockOpnameCounts";

describe("buildStockOpnameCountPayload", () => {
  const lines = [
    { line_id: "L1", item_code: "SKU-1", physical_count_at: null },
    { line_id: "L2", item_code: "SKU-2", physical_count_at: null },
  ];

  test("operational rejects qty without physical_count_at", () => {
    const built = buildStockOpnameCountPayload({
      sessionLines: lines,
      drafts: { L1: "980" },
      timeDrafts: { L1: "" },
      mode: "operational",
    });
    expect(built.ok).toBe(false);
    expect(built.error).toMatch(/waktu hitung/i);
  });

  test("operational accepts qty with WIB datetime-local", () => {
    const built = buildStockOpnameCountPayload({
      sessionLines: lines,
      drafts: { L1: "980" },
      timeDrafts: { L1: "2026-10-20T10:30" },
      mode: "operational",
    });
    expect(built.ok).toBe(true);
    expect(built.lines).toHaveLength(1);
    expect(built.lines[0].physical_count_qty).toBe("980");
    expect(built.lines[0].physical_count_at).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  test("operational bulk time applies to qty lines only when checkbox path set", () => {
    const built = buildStockOpnameCountPayload({
      sessionLines: lines,
      drafts: { L1: "980", L2: "1000" },
      timeDrafts: {},
      applyBulkTime: true,
      bulkCountAt: "2026-10-20T11:00",
      mode: "operational",
    });
    expect(built.ok).toBe(true);
    expect(built.lines).toHaveLength(2);
    expect(built.lines.every((row) => row.physical_count_at)).toBe(true);
  });

  test("freeze allows qty without count time", () => {
    const built = buildStockOpnameCountPayload({
      sessionLines: lines,
      drafts: { L1: "50" },
      timeDrafts: {},
      mode: "freeze",
    });
    expect(built.ok).toBe(true);
    expect(built.lines[0]).toEqual({ line_id: "L1", physical_count_qty: "50" });
  });

  test("operational preserves existing server time when draft time empty", () => {
    const built = buildStockOpnameCountPayload({
      sessionLines: [{ line_id: "L1", item_code: "SKU-1", physical_count_at: "2026-10-20T03:00:00.000Z" }],
      drafts: { L1: "980" },
      timeDrafts: { L1: "" },
      mode: "operational",
    });
    expect(built.ok).toBe(true);
    expect(built.lines[0].physical_count_qty).toBe("980");
    expect(built.lines[0].physical_count_at).toBeUndefined();
  });
});
