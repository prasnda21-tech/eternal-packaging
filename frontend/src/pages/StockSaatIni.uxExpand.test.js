import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import StockSaatIni from "./StockSaatIni";

jest.mock("react-router-dom", () => require("../test/reactRouterMock"), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: () => true }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    branches: jest.fn(),
    warehouses: jest.fn(),
    physicalStockCatalog: jest.fn(),
    physicalStockStatusSummary: jest.fn().mockResolvedValue({ counts: {}, operational_actions: [], read_only: true }),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const BRANCH = { id: "br-1", code: "YGY", name: "Yogyakarta", active: true };
const WH_A = { id: "wh-a", branch_id: "br-1", code: "MAIN", name: "Gudang A", active: true, is_operational: true };
const WH_B = { id: "wh-b", branch_id: "br-1", code: "B", name: "Gudang B", active: true, is_operational: true };

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("Stock Saat Ini UX — expandable warehouse + semantics", () => {
  beforeEach(() => {
    api.physicalStockStatusSummary.mockResolvedValue({
      counts: {},
      operational_actions: [],
      read_only: true,
    });
  });

  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.branches.mockResolvedValue({ items: [BRANCH] });
    api.warehouses.mockResolvedValue({ items: [WH_A, WH_B] });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.clearAllMocks();
  });

  test("standalone mode chip + warehouse expand is collapsed by default", async () => {
    api.physicalStockCatalog.mockResolvedValue({
      stock_read_mode: "warehouse_standalone",
      stock_read_mode_label: "Mode warehouse standalone — stok fisik per cabang/gudang (tanpa Replica Set).",
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
      items: [{
        master_item_id: "m-dual",
        item_code: "UAT-606-004",
        item_name: "Pouch Matte UAT Dual WH",
        base_unit: "PCS",
        physical_qty: 120,
        qty_pesanan: 0,
        qty_belum_terkirim: 0,
        qty_tersedia: 120,
        qty_tersedia_valid: true,
        warehouse_balances: [
          { warehouse_id: "wh-a", warehouse_code: "MAIN", physical_qty: 80, balance_present: true },
          { warehouse_id: "wh-b", warehouse_code: "B", physical_qty: 40, balance_present: true },
        ],
      }],
    });
    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    expect(container.querySelector('[data-testid="stock-mode-chip"]')?.textContent).toMatch(/Gudang/i);
    expect(container.querySelector('[data-testid="stock-read-mode"]')?.textContent).toMatch(/standalone|gudang/i);
    expect(container.querySelector('[data-testid="stock-item-m-dual"] .stock-item-primary')?.textContent)
      .toMatch(/Pouch Matte/i);
    expect(container.querySelector('[data-testid="stock-code-m-dual"]')?.textContent).toBe("UAT-606-004");
    expect(container.querySelector('[data-testid="stock-unit-header"]')?.textContent).toMatch(/Satuan Dasar/i);
    expect(container.querySelector('[data-testid="stock-unit-m-dual"]')?.textContent).toBe("PCS");
    expect(container.querySelector('[data-testid="stock-item-m-dual"]')?.textContent).not.toMatch(/Satuan Dasar PCS/i);
    expect(container.querySelector('[data-testid="stock-warehouse-breakdown-m-dual"]')).toBeNull();
    const expand = container.querySelector('[data-testid="stock-expand-m-dual"]');
    expect(expand).toBeTruthy();
    await act(async () => { expand.click(); });
    expect(container.querySelector('[data-testid="stock-warehouse-breakdown-m-dual"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-wh-bal-m-dual-MAIN"]')?.textContent).toMatch(/80/);
    expect(container.querySelector('[data-testid="stock-wh-bal-m-dual-B"]')?.textContent).toMatch(/40/);
  });

  test("aggregate keeps warehouse deficit visible without claiming zero for missing balance", async () => {
    api.physicalStockCatalog.mockResolvedValue({
      stock_read_mode: "warehouse_standalone",
      r2_authority_active: false,
      warehouse_stock_standalone: true,
      can_adjust_physical_stock: true,
      effective_branch_id: BRANCH.id,
      effective_branch_code: "YGY",
      master_item_count: 2,
      warehouse_count: 2,
      total: 2,
      page: 1,
      page_count: 1,
      items: [
        {
          master_item_id: "m-neg",
          item_code: "UAT-606-002",
          item_name: "Lid Connect UAT Negatif",
          base_unit: "PCS",
          physical_qty: 75,
          saldo_buku_negatif: false,
          qty_pesanan: 0,
          qty_belum_terkirim: 0,
          qty_tersedia: 75,
          qty_tersedia_valid: true,
          updated_at: "2026-10-17T01:09:00+00:00",
          updated_by_name: "Rafi Operator",
          warehouse_balances: [
            { warehouse_id: "wh-a", warehouse_code: "MAIN", physical_qty: -25, balance_present: true, saldo_buku_negatif: true },
            { warehouse_id: "wh-b", warehouse_code: "B", physical_qty: 100, balance_present: true, saldo_buku_negatif: false },
          ],
        },
        {
          master_item_id: "m-miss",
          item_code: "UAT-606-003",
          item_name: "Box Kraft UAT Belum Saldo",
          base_unit: "PCS",
          physical_qty: 200,
          qty_pesanan: 0,
          qty_belum_terkirim: 0,
          qty_tersedia: 0,
          qty_tersedia_valid: false,
          baseline_status: "unreconciled",
          baseline_label: "Saldo awal belum direkonsiliasi",
          updated_at: "2026-10-17T01:10:00+00:00",
          updated_by_name: null,
          warehouse_balances: [
            { warehouse_id: "wh-a", warehouse_code: "MAIN", physical_qty: null, balance_present: false, baseline_status: "unreconciled" },
            { warehouse_id: "wh-b", warehouse_code: "B", physical_qty: 200, balance_present: true },
          ],
        },
      ],
    });
    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    // Single primary status — never Belum tersedia + Belum lengkap together.
    expect(container.querySelector('[data-testid="stock-aggregate-hint-m-neg"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-flag-m-neg"]')?.textContent).toMatch(/Defisit/i);
    expect(container.querySelector('[data-testid="stock-physical-m-neg"]')?.textContent)
      .not.toMatch(/Saldo awal belum direkonsiliasi|Ada gudang belum saldo|Belum lengkap/i);
    expect(container.querySelector('[data-testid="stock-available-invalid-m-miss"]')?.textContent)
      .toMatch(/Belum valid/i);
    expect(container.querySelector('[data-testid="stock-physical-m-miss"]')?.getAttribute("data-baseline-label"))
      .toMatch(/belum direkonsiliasi|Belum valid/i);
    expect(container.querySelector('[data-testid="stock-physical-m-miss"]')?.textContent)
      .toMatch(/Belum lengkap/);
    expect(container.querySelector('[data-testid="stock-physical-m-miss"]')?.textContent)
      .not.toMatch(/Belum tersedia/i);
    expect(container.querySelector('[data-testid="stock-flag-m-miss"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-legend-bar"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-updated-by-m-neg"]')?.textContent)
      .toMatch(/oleh Rafi Operator/);
    expect(container.querySelector('[data-testid="stock-updated-by-m-miss"]')?.textContent)
      .toMatch(/oleh Tidak tercatat/);
    await act(async () => {
      container.querySelector('[data-testid="stock-expand-m-neg"]').click();
    });
    const negCell = container.querySelector('[data-testid="stock-wh-bal-m-neg-MAIN"]');
    expect(negCell?.getAttribute("data-negative")).toBe("true");
    expect(negCell?.textContent).toMatch(/-?25|−25/);
    expect(negCell?.textContent).toMatch(/Defisit|negatif/i);
    await act(async () => {
      container.querySelector('[data-testid="stock-expand-m-miss"]').click();
    });
    const miss = container.querySelector('[data-testid="stock-wh-bal-m-miss-MAIN"]');
    expect(miss?.getAttribute("data-balance-present")).toBe("false");
    expect(miss?.textContent).toMatch(/Belum tersedia/i);
    expect(miss?.textContent).not.toMatch(/\b0\b/);
  });

  test("compatibility mode keeps blocked actions and contextual engine notes", async () => {
    api.physicalStockCatalog.mockResolvedValue({
      stock_read_mode: "compatibility",
      r2_authority_active: false,
      can_adjust_physical_stock: false,
      master_item_count: 1,
      warehouse_count: 0,
      total: 1,
      page: 1,
      page_count: 1,
      items: [{
        master_item_id: "m1",
        item_code: "BOX",
        item_name: "Box",
        base_unit: "PCS",
        physical_qty: 10,
        qty_pesanan: 1,
        qty_belum_terkirim: 0,
        qty_tersedia: 9,
        qty_tersedia_valid: false,
        belum_terkirim_status: "BELUM_TERVERIFIKASI",
        baseline_status: "unreconciled",
        baseline_label: "Stok fisik legacy (perusahaan) — bukan saldo per gudang",
      }],
    });
    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    expect(container.querySelector('[data-testid="stock-mode-chip"]')?.textContent).toMatch(/Kompatibilitas/i);
    expect(container.querySelector('[data-testid="stock-context-info"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-read-mode"]')?.textContent).toMatch(/kompatibilitas|perusahaan/i);
    expect(container.querySelector('[data-testid="saldo-awal-button"]')?.disabled).toBe(true);
    expect(container.querySelector('[data-testid="stock-expand-m1"]')).toBeNull();
  });
});
