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
    ssiItemActivity: jest.fn().mockResolvedValue({
      items: [],
      counts: { fisik: 0, so: 0, cetak: 0 },
      history_incomplete: true,
      coverage: { limitation: "Riwayat uji — hanya event berbukti." },
    }),
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

describe("Stock Saat Ini UX — compact list + detail drawer", () => {
  beforeEach(() => {
    api.physicalStockStatusSummary.mockResolvedValue({
      counts: {},
      operational_actions: [],
      read_only: true,
    });
    api.ssiItemActivity.mockResolvedValue({
      items: [
        {
          lane: "FISIK",
          activity: "Purchase Receipt",
          activity_code: "PURCHASE_RECEIPT",
          qty_pcs: 10,
          metric_affected: "stock",
          source_label: "PB-1",
          source_path: "/pembelian/penerimaan/pb-1",
          actor_name: "UAT",
          occurred_at: "2026-10-20T10:00:00+00:00",
          affects_physical_stock: true,
          before_after_supported: false,
        },
      ],
      counts: { fisik: 1, so: 0, cetak: 0 },
      history_incomplete: false,
      coverage: {},
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

  test("compact headers + detail drawer show warehouse breakdown and history", async () => {
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
        stock_dapat_dijual: 120,
        qty_polos: 80,
        qty_polos_status: "READY",
        warehouse_balances: [
          { warehouse_id: "wh-a", warehouse_code: "MAIN", physical_qty: 80, balance_present: true },
          { warehouse_id: "wh-b", warehouse_code: "B", physical_qty: 40, balance_present: true },
        ],
      }],
    });
    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    expect(container.querySelector('[data-testid="stock-mode-chip"]')?.textContent).toMatch(/Gudang/i);
    expect(container.querySelector('[data-testid="stock-legend-toggle"]')?.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector('[data-testid="stock-context-info"]')?.hidden).toBe(true);
    expect(container.querySelector('[data-testid="stock-status-summary-toggle"]')?.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector('[data-testid="stock-unit-header"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-cetak-dikirim-header"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-physical-header"]')?.textContent).toMatch(/^Stock$/);
    expect(container.querySelector('[data-testid="stock-siap-jual-header"]')?.textContent).toMatch(/Stock Dapat Dijual/i);
    expect(container.querySelector('[data-testid="stock-polos-header"]')?.textContent).toMatch(/Qty Polos/i);
    expect(container.querySelector('[data-testid="stock-detail-header"]')?.textContent).toMatch(/Aksi/i);
    expect(container.querySelector('[data-testid="stock-item-m-dual"] .stock-item-primary')?.textContent)
      .toMatch(/Pouch Matte/i);
    expect(container.querySelector('[data-testid="stock-code-m-dual"]')?.textContent).toBe("UAT-606-004");
    expect(container.querySelector('[data-testid="stock-expand-m-dual"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-mutasi-nav-m-dual"]')).toBeNull();
    const detailBtn = container.querySelector('[data-testid="stock-detail-m-dual"]');
    expect(detailBtn?.textContent).toMatch(/Lihat Detail/i);
    await act(async () => { detailBtn.click(); });
    await flush();
    expect(container.querySelector('[data-testid="ssi-detail-drawer"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ssi-detail-title"]')?.textContent).toMatch(/Pouch Matte/i);
    expect(container.querySelector('[data-testid="ssi-detail-wh-wh-a"]')?.textContent).toMatch(/80/);
    expect(container.querySelector('[data-testid="ssi-detail-wh-wh-b"]')?.textContent).toMatch(/40/);
    expect(container.querySelector('[data-testid="ssi-detail-mutasi-link"]')?.getAttribute("href")).toMatch(/tab=mutasi/);
    expect(api.ssiItemActivity).toHaveBeenCalledWith("m-dual", expect.objectContaining({ lane: "SEMUA" }));
    expect(container.querySelector('[data-testid="ssi-history-row-0"]')?.textContent).toMatch(/Purchase Receipt/i);
    await act(async () => {
      container.querySelector('[data-testid="ssi-history-filter-SO"]').click();
    });
    await flush();
    expect(api.ssiItemActivity).toHaveBeenCalledWith("m-dual", expect.objectContaining({ lane: "SO" }));
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
          stock_dapat_dijual: 75,
          qty_polos: 75,
          qty_polos_status: "READY",
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
          qty_tercetak: null,
          qty_tercetak_status: "UNVERIFIED",
          qty_tercetak_reason: "NO_OPERATOR_EVIDENCE",
          qty_polos: null,
          qty_polos_status: "UNVERIFIED",
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
    expect(container.querySelector('[data-testid="stock-aggregate-hint-m-neg"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-flag-m-neg"]')?.textContent).toMatch(/Defisit/i);
    expect(container.querySelector('[data-testid="stock-physical-m-neg"]')?.textContent)
      .not.toMatch(/Saldo awal belum direkonsiliasi|Ada gudang belum saldo|Belum lengkap/i);
    expect(container.querySelector('[data-testid="stock-siap-jual-header"]')?.textContent)
      .toMatch(/Stock Dapat Dijual/i);
    expect(container.querySelector('[data-testid="stock-physical-m-miss"]')?.getAttribute("data-baseline-label"))
      .toMatch(/belum direkonsiliasi|Belum valid/i);
    expect(container.querySelector('[data-testid="stock-physical-m-miss"]')?.textContent)
      .toMatch(/Belum lengkap/);
    expect(container.querySelector('[data-testid="stock-physical-m-miss"]')?.textContent)
      .not.toMatch(/Belum tersedia|Belum terverifikasi/i);
    expect(container.querySelector('[data-testid="stock-polos-m-miss"]')?.textContent)
      .toMatch(/Belum terverifikasi/i);
    expect(container.querySelector('[data-testid="stock-flag-m-miss"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-legend-bar"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="stock-detail-m-neg"]').click();
    });
    await flush();
    const negWh = container.querySelector('[data-testid="ssi-detail-wh-wh-a"]');
    expect(negWh?.textContent).toMatch(/-?25|−25/);
    expect(negWh?.querySelector(".stock-book-negative")).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="ssi-detail-close"]').click();
    });
    await act(async () => {
      container.querySelector('[data-testid="stock-detail-m-miss"]').click();
    });
    await flush();
    const missWh = container.querySelector('[data-testid="ssi-detail-wh-wh-a"]');
    expect(missWh?.textContent).toMatch(/Belum saldo/i);
    expect(missWh?.textContent).not.toMatch(/\b0\b/);
    expect(container.querySelector('[data-testid="ssi-detail-qty_tercetak"]')?.textContent)
      .toMatch(/Belum terverifikasi/i);
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
    expect(container.querySelector('[data-testid="stock-detail-m1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-expand-m1"]')).toBeNull();
  });
});
