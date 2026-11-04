import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import StockSaatIni from "./StockSaatIni";

jest.mock("react-router-dom", () => require("../test/reactRouterMock"), { virtual: true });

const authState = {
  user: { id: "user-1", email: "ops@example.com" },
  hasPerm: () => true,
};

jest.mock("../lib/auth", () => ({
  useAuth: () => authState,
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    branches: jest.fn(),
    warehouses: jest.fn(),
    physicalStockCatalog: jest.fn(),
    physicalStockStatusSummary: jest.fn().mockResolvedValue({
      counts: {},
      operational_actions: [],
      read_only: true,
    }),
    ssiItemActivity: jest.fn().mockResolvedValue({ items: [], counts: {} }),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const BRANCH = { id: "br-a", code: "YGY", name: "Yogyakarta", active: true };
const WH_A = {
  id: "wh-a",
  branch_id: "br-a",
  code: "MAIN",
  name: "Gudang A",
  active: true,
  is_operational: true,
};
const WH_B = {
  id: "wh-b",
  branch_id: "br-a",
  code: "B",
  name: "Gudang B",
  active: true,
  is_operational: true,
};

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("StockSaatIni P0-B1 UI-only status contract", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.branches.mockResolvedValue([BRANCH]);
    api.warehouses.mockResolvedValue([WH_A, WH_B]);
    api.physicalStockCatalog.mockReset();
    api.ssiItemActivity.mockResolvedValue({ items: [], counts: {} });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("incomplete physical → Stock/Polos Belum direkonsiliasi; ATP may stay numeric", async () => {
    api.physicalStockCatalog.mockResolvedValue({
      stock_read_mode: "warehouse_standalone",
      r2_authority_active: false,
      warehouse_stock_standalone: true,
      can_adjust_physical_stock: true,
      can_switch_branches: true,
      effective_branch_id: BRANCH.id,
      effective_branch_code: "YGY",
      master_item_count: 1,
      warehouse_count: 2,
      total: 1,
      page: 1,
      page_count: 1,
      items: [
        {
          master_item_id: "m-partial",
          item_code: "100146",
          item_name: "Partial WH",
          base_unit: "PCS",
          physical_qty: 0,
          baseline_status: "unreconciled",
          stock_dapat_dijual: -8000,
          qty_siap_jual_status: "READY",
          qty_polos: null,
          qty_polos_status: "UNVERIFIED",
          qty_polos_reason: "WIP_COMPOSITION_UNPROVEN",
          warehouse_balances: [
            { warehouse_id: "wh-a", warehouse_code: "MAIN", physical_qty: null, balance_present: false },
            { warehouse_id: "wh-b", warehouse_code: "B", physical_qty: 0, balance_present: true },
          ],
        },
      ],
    });
    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    const physical = container.querySelector('[data-testid="stock-physical-m-partial"]');
    expect(physical?.textContent?.replace(/\s/g, "")).toMatch(/Belumlengkap|Belumdirekonsiliasi/);
    expect(physical?.textContent).not.toMatch(/Indikatif|tampilan/i);
    const dapat = container.querySelector('[data-testid="stock-siap-jual-m-partial"]');
    expect(dapat?.getAttribute("data-condition-status")).toBe("INDICATIVE");
    expect(dapat?.textContent).toMatch(/8/);
    expect(dapat?.textContent).not.toMatch(/Indikatif|tampilan/i);
    const polos = container.querySelector('[data-testid="stock-polos-m-partial"]');
    expect(polos?.getAttribute("data-condition-status")).toBe("UNVERIFIED");
    expect(polos?.getAttribute("data-presentation-fallback")).toBe("true");
    expect(polos?.textContent?.replace(/\s/g, "")).toMatch(/Belumdirekonsiliasi/);
  });

  test("complete baseline negative SO → Dapat/Polos numbers only; detail keeps presentation contract", async () => {
    api.physicalStockCatalog.mockResolvedValue({
      stock_read_mode: "warehouse_standalone",
      can_adjust_physical_stock: true,
      can_switch_branches: true,
      effective_branch_id: BRANCH.id,
      effective_branch_code: "YGY",
      master_item_count: 1,
      warehouse_count: 1,
      total: 1,
      page: 1,
      page_count: 1,
      items: [
        {
          master_item_id: "m-ok",
          item_code: "12OZ",
          item_name: "12 OZ SA",
          base_unit: "PCS",
          physical_qty: 6000,
          baseline_status: "reconciled",
          stock_dapat_dijual: -22000,
          qty_siap_jual_status: "READY",
          qty_polos: 2000,
          qty_polos_status: "READY",
          sisa_belum_cetak: 500,
          sisa_belum_cetak_status: "READY",
          warehouse_balances: [
            {
              warehouse_id: "wh-a",
              warehouse_code: "MAIN",
              physical_qty: 6000,
              balance_present: true,
              baseline_status: "reconciled",
            },
          ],
        },
      ],
    });
    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    const dapat = container.querySelector('[data-testid="stock-siap-jual-m-ok"]');
    expect(dapat?.getAttribute("data-condition-status")).toBe("READY");
    expect(dapat?.textContent).toMatch(/22/);
    expect(dapat?.textContent).not.toMatch(/Indikatif/i);
    const polos = container.querySelector('[data-testid="stock-polos-m-ok"]');
    expect(polos?.getAttribute("data-condition-status")).toBe("READY");
    expect(polos?.getAttribute("data-presentation-fallback")).toBeNull();
    expect(polos?.textContent).toMatch(/2/);
    expect(polos?.textContent).not.toMatch(/Indikatif/i);
    expect(polos?.textContent).not.toMatch(/Belum terverifikasi/i);
    expect(container.querySelector('[data-testid="stock-sisa-belum-cetak-header"]')?.textContent)
      .toMatch(/Sisa Belum Cetak/);
    const sisa = container.querySelector('[data-testid="stock-sisa-belum-cetak-m-ok"]');
    expect(sisa?.getAttribute("data-condition-status")).toBe("READY");
    expect(sisa?.getAttribute("data-presentation-fallback")).toBeNull();
    expect(sisa?.textContent).toMatch(/500/);

    await act(async () => {
      container.querySelector('[data-testid="stock-row-m-ok"]').click();
    });
    await flush();
    expect(container.querySelector('[data-testid="ssi-detail-drawer"]')?.getAttribute("data-ssi-detail-mode"))
      .toBe("centered-modal");
    expect(container.querySelector('[data-testid="stock-table"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ssi-detail-backdrop"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ssi-detail-stock_dapat_dijual"]')?.textContent)
      .toMatch(/22/);
    expect(container.querySelector('[data-testid="ssi-detail-stock_dapat_dijual"]')?.textContent)
      .not.toMatch(/PCS · terverifikasi/);
    expect(container.querySelector('[data-testid="ssi-detail-qty_polos"]')?.textContent)
      .toMatch(/2/);
    expect(container.querySelector('[data-testid="ssi-detail-qty_polos"]')?.textContent)
      .not.toMatch(/Indikatif/i);
    expect(container.querySelector('[data-testid="ssi-detail-sisa_belum_cetak"]')?.textContent)
      .toMatch(/500/);
    await act(async () => {
      container.querySelector('[data-testid="ssi-detail-close"]').click();
    });
    await flush();
    expect(container.querySelector('[data-testid="stock-table"]')).toBeTruthy();

    await act(async () => {
      container.querySelector('[data-testid="stock-row-m-ok"]').click();
    });
    await flush();
    expect(container.querySelector('[data-testid="ssi-detail-drawer"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="ssi-detail-backdrop"]')
        .dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="ssi-detail-drawer"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-table"]')).toBeTruthy();
  });

  test("null ATP/Polos show plain 0 in UI; CSV export keeps null empty", async () => {
    const { exportQtyCellValue } = require("../lib/ssiStatusDisplay");
    expect(exportQtyCellValue({ qty_polos: null, qty_polos_status: "UNVERIFIED" }, "qty_polos")).toBe("");
    expect(exportQtyCellValue({ stock_dapat_dijual: null }, "stock_dapat_dijual")).toBe("");
    expect(exportQtyCellValue({ qty_polos: 0 }, "qty_polos")).toBe(0);
    expect(exportQtyCellValue({ sisa_belum_cetak: null }, "sisa_belum_cetak")).toBe("");
    expect(exportQtyCellValue({ sisa_belum_cetak: 500 }, "sisa_belum_cetak")).toBe(500);

    api.physicalStockCatalog.mockResolvedValue({
      stock_read_mode: "warehouse_standalone",
      can_adjust_physical_stock: true,
      can_switch_branches: true,
      effective_branch_id: BRANCH.id,
      effective_branch_code: "YGY",
      master_item_count: 1,
      warehouse_count: 1,
      total: 1,
      page: 1,
      page_count: 1,
      items: [{
        master_item_id: "m-atp-null",
        item_code: "ATP0",
        item_name: "ATP Null",
        physical_qty: 10,
        baseline_status: "reconciled",
        stock_dapat_dijual: null,
        qty_siap_jual_status: "UNVERIFIED",
        qty_polos: null,
        qty_polos_status: "UNVERIFIED",
        qty_polos_reason: "WIP_COMPOSITION_UNPROVEN",
        sisa_belum_cetak: null,
        sisa_belum_cetak_status: "UNVERIFIED",
        sisa_belum_cetak_reason: "BLANK_PRINT_COMPLETION_UNPROVEN",
        warehouse_balances: [{
          warehouse_id: "wh-a",
          warehouse_code: "MAIN",
          physical_qty: 10,
          balance_present: true,
          baseline_status: "reconciled",
        }],
      }],
    });
    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    const dapat = container.querySelector('[data-testid="stock-siap-jual-m-atp-null"]');
    expect(dapat?.getAttribute("data-presentation-fallback")).toBe("true");
    expect(dapat?.textContent?.replace(/\s/g, "")).toBe("0");
    expect(dapat?.textContent).not.toMatch(/tampilan|Belum|Indikatif/i);
    const polos = container.querySelector('[data-testid="stock-polos-m-atp-null"]');
    expect(polos?.getAttribute("data-presentation-fallback")).toBe("true");
    expect(polos?.textContent?.replace(/\s/g, "")).toBe("0");
    const sisa = container.querySelector('[data-testid="stock-sisa-belum-cetak-m-atp-null"]');
    expect(sisa?.getAttribute("data-presentation-fallback")).toBe("true");
    expect(sisa?.textContent?.replace(/\s/g, "")).toBe("0");
    expect(container.querySelector('[data-testid="stock-presentation-zero-legend"]')).toBeNull();
  });

  test("Enter key on row opens detail; adjust control does not", async () => {
    api.physicalStockCatalog.mockResolvedValue({
      stock_read_mode: "warehouse_standalone",
      can_adjust_physical_stock: true,
      can_switch_branches: true,
      effective_branch_id: BRANCH.id,
      effective_branch_code: "YGY",
      master_item_count: 1,
      warehouse_count: 1,
      total: 1,
      page: 1,
      page_count: 1,
      items: [{
        master_item_id: "m-key",
        item_code: "KEY1",
        item_name: "Keyboard Row",
        physical_qty: 5,
        baseline_status: "reconciled",
        stock_dapat_dijual: 5,
        qty_siap_jual_status: "READY",
        qty_polos: 5,
        qty_polos_status: "READY",
        warehouse_balances: [{
          warehouse_id: "wh-a",
          warehouse_code: "MAIN",
          physical_qty: 5,
          balance_present: true,
          baseline_status: "reconciled",
        }],
      }],
    });
    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    const row = container.querySelector('[data-testid="stock-row-m-key"]');
    await act(async () => {
      row.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="ssi-detail-title"]')?.textContent).toMatch(/Keyboard Row/);
    await act(async () => {
      container.querySelector('[data-testid="ssi-detail-close"]').click();
    });
    await flush();
    const adjust = container.querySelector('[data-testid="stock-adjust-m-key"]');
    if (adjust) {
      await act(async () => { adjust.click(); });
      await flush();
      expect(container.querySelector('[data-testid="ssi-detail-drawer"]')).toBeNull();
    }
  });
});
