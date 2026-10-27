import {
  SSI_STATUS,
  isPhysicalBaselineIncomplete,
  resolveDapatDijualDisplay,
  resolvePolosDisplay,
} from "./ssiStatusDisplay";

describe("ssiStatusDisplay P0-B1 UI-only contract", () => {
  test("incomplete Stock cell → Dapat Dijual Indikatif keeps negative", () => {
    const item = {
      stock_dapat_dijual: -159100,
      qty_siap_jual_status: "READY",
      baseline_status: "unreconciled",
      warehouse_balances: [
        { warehouse_id: "a", balance_present: false },
        { warehouse_id: "b", balance_present: true, physical_qty: 0 },
      ],
    };
    const primary = { present: false, text: "Belum lengkap" };
    const disp = resolveDapatDijualDisplay(item, primary);
    expect(disp.mode).toBe("indicative");
    expect(disp.qty).toBe(-159100);
    expect(disp.label).toBe("Indikatif");
    expect(disp.status).toBe(SSI_STATUS.INDICATIVE);
  });

  test("complete baseline → Dapat Dijual numeric including negative SO", () => {
    const item = {
      stock_dapat_dijual: -22000,
      qty_siap_jual_status: "READY",
      baseline_status: "reconciled",
      warehouse_balances: [
        { warehouse_id: "a", balance_present: true, baseline_status: "reconciled", physical_qty: 6000 },
      ],
    };
    const primary = { present: true, qty: 6000 };
    const disp = resolveDapatDijualDisplay(item, primary);
    expect(disp.mode).toBe("qty");
    expect(disp.qty).toBe(-22000);
    expect(disp.label).toBeNull();
    expect(disp.status).toBe(SSI_STATUS.READY);
  });

  test("missing baseline Belum saldo → Dapat Indikatif", () => {
    const item = {
      stock_dapat_dijual: 100,
      qty_siap_jual_status: "BELUM_TERSEDIA",
      qty_siap_jual_reason: "NO_BALANCE",
    };
    const primary = { present: false, text: "Belum saldo" };
    expect(isPhysicalBaselineIncomplete(item, primary)).toBe(true);
    expect(resolveDapatDijualDisplay(item, primary).mode).toBe("indicative");
    expect(resolveDapatDijualDisplay(item, primary).qty).toBe(100);
  });

  test("Polos UNVERIFIED when no calc — not fake zero", () => {
    const item = {
      qty_polos: null,
      qty_polos_status: "UNVERIFIED",
      qty_polos_reason: "DEPENDENT_UNKNOWN",
      baseline_status: "reconciled",
    };
    const disp = resolvePolosDisplay(item, { present: true, qty: 1000 });
    expect(disp.mode).toBe("unverified");
    expect(disp.label).toBe("Belum terverifikasi");
  });

  test("Polos READY from API still displays as Indikatif (no WIP coverage)", () => {
    const item = {
      qty_polos: 2000,
      qty_polos_status: "READY",
      baseline_status: "reconciled",
      warehouse_balances: [
        { warehouse_id: "a", balance_present: true, baseline_status: "reconciled" },
      ],
    };
    const disp = resolvePolosDisplay(item, { present: true, qty: 6000 });
    expect(disp.mode).toBe("indicative");
    expect(disp.qty).toBe(2000);
    expect(disp.status).toBe(SSI_STATUS.INDICATIVE);
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
