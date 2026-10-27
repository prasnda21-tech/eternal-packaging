import {
  SSI_STATUS,
  isPhysicalBaselineIncomplete,
  resolveDapatDijualDisplay,
  resolvePolosDisplay,
} from "./ssiStatusDisplay";

describe("ssiStatusDisplay P0-B1 contract", () => {
  test("incomplete baseline → Dapat Dijual Indikatif keeps negative", () => {
    const item = {
      stock_dapat_dijual: -159100,
      qty_siap_jual_status: "INDICATIVE",
      qty_siap_jual_reason: "BASELINE_INCOMPLETE",
      baseline_status: "unreconciled",
    };
    const primary = { present: false, text: "Belum lengkap" };
    const disp = resolveDapatDijualDisplay(item, primary);
    expect(disp.mode).toBe("indicative");
    expect(disp.qty).toBe(-159100);
    expect(disp.label).toBe("Indikatif");
    expect(disp.status).toBe(SSI_STATUS.INDICATIVE);
  });

  test("complete baseline → Dapat Dijual numeric READY including negative SO", () => {
    const item = {
      stock_dapat_dijual: -22000,
      qty_siap_jual_status: "READY",
      physical_baseline_complete: true,
      baseline_status: "reconciled",
    };
    const primary = { present: true, qty: 6000 };
    const disp = resolveDapatDijualDisplay(item, primary);
    expect(disp.mode).toBe("qty");
    expect(disp.qty).toBe(-22000);
    expect(disp.label).toBeNull();
    expect(disp.status).toBe(SSI_STATUS.READY);
  });

  test("missing baseline Belum saldo → Dapat Indikatif", () => {
    const item = { stock_dapat_dijual: 100, physical_baseline_complete: false };
    const primary = { present: false, text: "Belum saldo" };
    expect(isPhysicalBaselineIncomplete(item, primary)).toBe(true);
    expect(resolveDapatDijualDisplay(item, primary).mode).toBe("indicative");
  });

  test("Polos UNVERIFIED when no calc — not fake zero", () => {
    const item = {
      qty_polos: null,
      qty_polos_status: "UNVERIFIED",
      qty_polos_reason: "DEPENDENT_UNKNOWN",
      physical_baseline_complete: true,
    };
    const disp = resolvePolosDisplay(item, { present: true, qty: 1000 });
    expect(disp.mode).toBe("unverified");
    expect(disp.label).toBe("Belum terverifikasi");
  });

  test("Polos never VERIFIED even when print READY and baseline complete", () => {
    const item = {
      qty_polos: 2000,
      qty_polos_status: "INDICATIVE",
      qty_polos_reason: "SIMPLE_POLOS_NO_WIP_COVERAGE",
      physical_baseline_complete: true,
      baseline_status: "reconciled",
    };
    const disp = resolvePolosDisplay(item, { present: true, qty: 6000 });
    expect(disp.mode).toBe("indicative");
    expect(disp.qty).toBe(2000);
    expect(disp.status).toBe(SSI_STATUS.INDICATIVE);
  });

  test("legacy READY polos status still displays as Indikatif", () => {
    const item = {
      qty_polos: 75,
      qty_polos_status: "READY",
      physical_baseline_complete: true,
    };
    const disp = resolvePolosDisplay(item, { present: true, qty: 75 });
    expect(disp.mode).toBe("indicative");
    expect(disp.qty).toBe(75);
  });

  test("precedence: incomplete FE primary overrides API READY for Dapat", () => {
    const item = {
      stock_dapat_dijual: -8000,
      qty_siap_jual_status: "READY",
    };
    const disp = resolveDapatDijualDisplay(item, {
      present: false,
      text: "Belum lengkap",
    });
    expect(disp.mode).toBe("indicative");
    expect(disp.qty).toBe(-8000);
  });
});
