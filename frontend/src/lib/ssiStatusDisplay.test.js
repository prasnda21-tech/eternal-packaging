import {
  SSI_STATUS,
  exportQtyCellValue,
  isPhysicalBaselineIncomplete,
  resolveDapatDijualDisplay,
  resolvePhysicalDisplay,
  resolvePolosDisplay,
} from "./ssiStatusDisplay";

describe("ssiStatusDisplay simplified numeric cells", () => {
  test("T2/T3 incomplete baseline → Dapat stays numeric negative without label", () => {
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
    expect(disp.mode).toBe("qty");
    expect(disp.qty).toBe(-159100);
    expect(disp.label).toBeNull();
    expect(disp.presentationFallback).toBe(false);
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

  test("null ATP → presentation 0 (not sellable math)", () => {
    const item = { stock_dapat_dijual: null, qty_siap_jual_status: "UNVERIFIED" };
    const disp = resolveDapatDijualDisplay(item, { present: true, qty: 1 });
    expect(disp.mode).toBe("presentation_zero");
    expect(disp.qty).toBe(0);
    expect(disp.presentationFallback).toBe(true);
  });

  test("missing/unreconciled physical → status text (never invent 0); proven qty unchanged", () => {
    expect(resolvePhysicalDisplay({ present: false, text: "Belum direkonsiliasi" })).toEqual({
      mode: "status_text",
      qty: null,
      presentationFallback: true,
      statusText: "Belum direkonsiliasi",
    });
    expect(resolvePhysicalDisplay({ present: true, qty: -8300 })).toMatchObject({
      mode: "qty",
      qty: -8300,
      presentationFallback: false,
    });
  });

  test("incomplete physical → Polos shows Belum direkonsiliasi (not derived numeric)", () => {
    const item = {
      qty_polos: 500,
      qty_polos_status: "READY",
      baseline_status: "unreconciled",
    };
    const disp = resolvePolosDisplay(item, { present: false, text: "Belum direkonsiliasi" });
    expect(disp.mode).toBe("status_text");
    expect(disp.label).toBe("Belum direkonsiliasi");
    expect(disp.qty).toBeNull();
  });

  test("missing baseline with numeric ATP → qty mode keeps number", () => {
    const item = {
      stock_dapat_dijual: 100,
      qty_siap_jual_status: "BELUM_TERSEDIA",
      qty_siap_jual_reason: "NO_BALANCE",
    };
    const primary = { present: false, text: "Belum saldo" };
    expect(isPhysicalBaselineIncomplete(item, primary)).toBe(true);
    const disp = resolveDapatDijualDisplay(item, primary);
    expect(disp.mode).toBe("qty");
    expect(disp.qty).toBe(100);
    expect(disp.label).toBeNull();
  });

  test("T6 Polos null → presentation 0; T7 reason retained", () => {
    const item = {
      qty_polos: null,
      qty_polos_status: "UNVERIFIED",
      qty_polos_reason: "WIP_COMPOSITION_UNPROVEN",
      baseline_status: "reconciled",
    };
    const disp = resolvePolosDisplay(item, { present: true, qty: 1000 });
    expect(disp.mode).toBe("presentation_zero");
    expect(disp.qty).toBe(0);
    expect(disp.presentationFallback).toBe(true);
    expect(disp.status).toBe(SSI_STATUS.UNVERIFIED);
    expect(disp.reason).toBe("WIP_COMPOSITION_UNPROVEN");
    expect(item.qty_polos).toBeNull();
  });

  test("T4 known qty_polos=0 displays exact 0", () => {
    const item = {
      qty_polos: 0,
      qty_polos_status: "READY",
      baseline_status: "reconciled",
      warehouse_balances: [
        { warehouse_id: "a", balance_present: true, baseline_status: "reconciled" },
      ],
    };
    const disp = resolvePolosDisplay(item, { present: true, qty: 0 });
    expect(disp.mode).toBe("qty");
    expect(disp.qty).toBe(0);
    expect(disp.presentationFallback).toBe(false);
  });

  test("T5 known qty_polos>0 displays exact number", () => {
    const item = {
      qty_polos: 2000,
      qty_polos_status: "READY",
      baseline_status: "reconciled",
      warehouse_balances: [
        { warehouse_id: "a", balance_present: true, baseline_status: "reconciled" },
      ],
    };
    const disp = resolvePolosDisplay(item, { present: true, qty: 6000 });
    expect(disp.mode).toBe("qty");
    expect(disp.qty).toBe(2000);
    expect(disp.label).toBeNull();
    expect(disp.presentationFallback).toBe(false);
  });

  test("T13 export never converts unknown Polos into confirmed 0", () => {
    expect(exportQtyCellValue({ qty_polos: null }, "qty_polos")).toBe("");
    expect(exportQtyCellValue({ qty_polos: 0 }, "qty_polos")).toBe(0);
    expect(exportQtyCellValue({ qty_polos: 1500 }, "qty_polos")).toBe(1500);
  });
});
