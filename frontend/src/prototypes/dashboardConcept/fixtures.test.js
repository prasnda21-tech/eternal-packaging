import {
  AS_OF_DATE,
  buildFinanceSnapshot,
  buildProductionSnapshot,
  DEFAULT_FILTERS,
} from "./fixtures";

describe("dashboardConcept fixtures", () => {
  test("finance P&L and AR buckets stay consistent", () => {
    const snap = buildFinanceSnapshot(DEFAULT_FILTERS);
    expect(snap.empty).toBe(false);
    expect(snap.asOfDate).toBe(AS_OF_DATE);
    expect(snap.pl.gross).toBe(snap.pl.omzet - snap.pl.hpp);
    expect(snap.pl.net).toBe(snap.pl.gross - snap.pl.opex);
    const arSum =
      snap.ar.current + snap.ar.d1_30 + snap.ar.d31_60 + snap.ar.d60_plus;
    expect(arSum).toBe(snap.arTotal);
  });

  test("branch filter changes finance follow-ups", () => {
    const all = buildFinanceSnapshot({ ...DEFAULT_FILTERS, branchId: "all" });
    const yogya = buildFinanceSnapshot({ ...DEFAULT_FILTERS, branchId: "yogya" });
    expect(yogya.followUps.length).toBeLessThan(all.followUps.length);
    expect(yogya.followUps.every((r) => r.branchId === "yogya")).toBe(true);
    expect(yogya.pl.omzet).toBeLessThan(all.pl.omzet);
  });

  test("unknown period yields empty finance state", () => {
    const snap = buildFinanceSnapshot({
      branchId: "all",
      dateFrom: "2025-01-01",
      dateTo: "2025-01-31",
    });
    expect(snap.empty).toBe(true);
  });

  test("production unique PO KPIs and loading sisa produksi", () => {
    const snap = buildProductionSnapshot(DEFAULT_FILTERS);
    expect(snap.empty).toBe(false);
    expect(snap.kpis.activePoUnique).toBe(snap.priorities.length);
    expect(snap.kpis.latePoUnique).toBe(
      new Set(snap.priorities.filter((p) => p.late).map((p) => p.poNumber)).size,
    );
    expect(snap.kpis.donePcs).toBe(Math.round(snap.kpis.targetPcs * 0.625));
    const loading = snap.priorities.find((p) => p.stage === "loading");
    expect(loading.sisaProduksi).toBe(0);
    expect(loading.sisaKirim).toBeGreaterThan(0);
    const jobSum = snap.stageQueue.reduce((s, st) => s + st.jobCount, 0);
    expect(jobSum).toBeGreaterThan(snap.kpis.activePoUnique);
  });

  test("capacity util matches planned/capacity", () => {
    const snap = buildProductionSnapshot(DEFAULT_FILTERS);
    snap.capacity.forEach((c) => {
      expect(c.utilPct).toBeCloseTo((c.planned / c.capacity) * 100, 5);
    });
  });
});
