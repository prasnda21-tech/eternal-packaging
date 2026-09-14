import {
  buildOpsRoleSummaryModel,
  deriveOpsRoleCounts,
  isOpenHelperWork,
  isOpenPengirimanWork,
  isOpenProduksiWork,
  isOpenScreenerWork,
  OPS_ROLE_SUMMARY,
} from "./mobileOpsRoleSummary";

const today = new Date("2026-09-14T12:00:00");

describe("mobileOpsRoleSummary", () => {
  test("catalog is the locked six roles in source order", () => {
    expect(OPS_ROLE_SUMMARY.map((r) => r.id)).toEqual([
      "prep", "screener", "produksi", "packing", "loading", "pengiriman",
    ]);
  });

  test("production open-work predicates use existing helper/screener/print fields", () => {
    const helperOpen = {
      helper_required: 100,
      helper_prepared: 20,
      current_stage: 7,
    };
    const helperDone = { ...helperOpen, helper_prepared: 100 };
    const screenOpen = { current_stage: 6 };
    const screenDone = { current_stage: 6, screen_qty: 10 };
    const printOpen = {
      current_stage: 8,
      print_qty_total: 50,
      operators: [{ split_id: "s1", line_printable: true, qty_completed: 10 }],
    };
    expect(isOpenHelperWork(helperOpen)).toBe(true);
    expect(isOpenHelperWork(helperDone)).toBe(false);
    expect(isOpenScreenerWork(screenOpen)).toBe(true);
    expect(isOpenScreenerWork(screenDone)).toBe(false);
    expect(isOpenProduksiWork(printOpen)).toBe(true);
    expect(isOpenProduksiWork({ ...printOpen, operators: [{ split_id: "s1", line_printable: true, qty_completed: 50 }] })).toBe(false);
  });

  test("pengiriman count uses delivery semua_aktif semantics; packing/loading stay unsupported", () => {
    const active = { status: "hari_ini", effective_delivery_date: "2026-09-14", urgent: true };
    const done = { status: "terkirim", effective_delivery_date: "2026-09-14" };
    expect(isOpenPengirimanWork(active, today)).toBe(true);
    expect(isOpenPengirimanWork(done, today)).toBe(false);
    const counts = deriveOpsRoleCounts({
      productionRows: null,
      deliveryRows: [active, done],
      today,
    });
    expect(counts.packing).toBeNull();
    expect(counts.loading).toBeNull();
    expect(counts.prep).toBeNull();
    expect(counts.pengiriman).toEqual({ total: 1, urgent: 1 });
  });

  test("role counts match the supplied production dataset", () => {
    const rows = [
      {
        id: "a",
        current_stage: 8,
        urgent: true,
        helper_required: 100,
        helper_prepared: 0,
        screen_qty: 5,
        print_qty_total: 40,
        operators: [{ split_id: "1", line_printable: true, qty_completed: 10 }],
      },
      {
        id: "b",
        current_stage: 8,
        helper_required: 10,
        helper_prepared: 10,
        print_qty_total: 10,
        operators: [{ split_id: "2", line_printable: true, qty_completed: 10 }],
      },
    ];
    const counts = deriveOpsRoleCounts({ productionRows: rows, deliveryRows: null, today });
    expect(counts.prep).toEqual({ total: 1, urgent: 1 });
    expect(counts.screener).toEqual({ total: 1, urgent: 0 });
    expect(counts.produksi).toEqual({ total: 1, urgent: 1 });
    expect(counts.pengiriman).toBeNull();
    const model = buildOpsRoleSummaryModel({ productionRows: rows, deliveryRows: null, today });
    expect(model).toHaveLength(6);
    expect(model.find((r) => r.id === "prep").count.total).toBe(1);
  });
});
