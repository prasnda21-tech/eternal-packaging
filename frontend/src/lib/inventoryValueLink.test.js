import {
  acceptValuationFocus,
  CURRENT_STOCK_LINK_LABEL,
  CURRENT_STOCK_PATH,
  INVENTORY_VALUE_LINK_LABEL,
  inventoryEstimateText,
  inventoryValueItemPath,
  inventoryValueLinkVisible,
  inventoryValueMasterItemIdFromSearch,
  inventoryValueReconPath,
  inventoryValueRouteAllowed,
  inventoryValueTabFromSearch,
  reconciliationControlHref,
} from "./inventoryValueLink";

const allow = (key) => ["nav.inventory.inventory_value", "inventory_valuation.view"].includes(key);
const noView = (key) => key === "nav.inventory.inventory_value";
const noNav = (key) => key === "inventory_valuation.view";

describe("inventory value navigation", () => {
  test("shows the cross-page link only for an active INVENTORY master with access", () => {
    const visible = (item, hasPerm = allow) => inventoryValueLinkVisible({
      masterItemId: item.master_item_id,
      itemType: item.item_type,
      isActive: item.is_active,
      hasPerm,
    });
    expect(visible({ master_item_id: "mid-1", item_type: "INVENTORY", is_active: true })).toBe(true);
    expect(visible({ master_item_id: "mid-1", item_type: "INVENTORY" })).toBe(true);
    expect(visible({ master_item_id: "mid-svc", item_type: "SERVICE", is_active: true })).toBe(false);
    expect(visible({ master_item_id: "mid-non", item_type: "NON_INVENTORY", is_active: true })).toBe(false);
    expect(visible({ master_item_id: "mid-x", item_type: "WIDGET", is_active: true })).toBe(false);
    expect(visible({ master_item_id: "mid-x", item_type: "", is_active: true })).toBe(false);
    expect(visible({ master_item_id: "", item_type: "INVENTORY", is_active: true })).toBe(false);
    expect(visible({ master_item_id: "   ", item_type: "INVENTORY", is_active: true })).toBe(false);
    expect(visible({ item_type: "INVENTORY", accurate_item_id: "aid-only" })).toBe(false);
    expect(visible({ master_item_id: "mid-off", item_type: "INVENTORY", is_active: false })).toBe(false);
    expect(visible({ master_item_id: "mid-1", item_type: "INVENTORY", is_active: true }, noView)).toBe(false);
    expect(visible({ master_item_id: "mid-1", item_type: "INVENTORY", is_active: true }, noNav)).toBe(false);
  });

  test("labels point to valuation and current stock without inventing a quantity", () => {
    expect(INVENTORY_VALUE_LINK_LABEL).toBe("Lihat nilai persediaan");
    expect(CURRENT_STOCK_LINK_LABEL).toBe("Lihat di Stok Saat Ini");
    expect(CURRENT_STOCK_PATH).toBe("/persediaan/stock-saat-ini");
  });

  test("link carries the exact master id and the recon tab is selectable", () => {
    expect(inventoryValueItemPath("mid page/2")).toBe(
      "/persediaan/nilai-persediaan?master_item_id=mid+page%2F2",
    );
    expect(inventoryValueItemPath("mid-target")).not.toMatch(/accurate_item_id|item_code|item_name/);
    expect(inventoryValueReconPath()).toBe("/persediaan/nilai-persediaan?tab=recon");
    expect(inventoryValueTabFromSearch("?tab=recon")).toBe("recon");
    expect(inventoryValueTabFromSearch("?tab=ledger-recon")).toBe("ledger-recon");
    expect(inventoryValueTabFromSearch("?tab=hpp")).toBe("hpp");
    expect(inventoryValueTabFromSearch("?tab=unknown")).toBe("daftar");
    expect(inventoryValueMasterItemIdFromSearch("?master_item_id=mid-target&tab=recon")).toBe("mid-target");
    expect(reconciliationControlHref({
      key: "inventory_reconciliation",
      href: "/persediaan/nilai-persediaan",
      status: "incomplete",
    })).toBe("/persediaan/nilai-persediaan?tab=ledger-recon");
    expect(reconciliationControlHref({
      key: "inventory_reconciliation",
      href: "/persediaan/nilai-persediaan?tab=ledger-recon",
      status: "incomplete",
    })).toBe("/persediaan/nilai-persediaan?tab=ledger-recon");
    expect(reconciliationControlHref({
      key: "cogs_unposted",
      href: "/persediaan/nilai-persediaan",
      status: "incomplete",
    })).toBe("/persediaan/nilai-persediaan");
    expect(reconciliationControlHref({
      key: "inventory_recovery",
      href: "/pembelian/penerimaan-barang",
      status: "incomplete",
    })).toBe("/pembelian/penerimaan-barang");
    expect(reconciliationControlHref({
      key: "opening_incomplete",
      href: "/persediaan/nilai-persediaan?tab=opening",
      status: "incomplete",
    })).toBe("/persediaan/nilai-persediaan?tab=opening");
    expect(reconciliationControlHref({
      key: "opening_incomplete",
      href: "/persediaan/nilai-persediaan?tab=recon",
      status: "incomplete",
    })).toBe("/persediaan/nilai-persediaan?tab=recon");
    expect(reconciliationControlHref({
      key: "inventory_reconciliation",
      href: "/persediaan/nilai-persediaan",
      status: "error",
    })).toBeNull();
  });

  test("a miss stays unexplained as money and a real zero stays zero", () => {
    const formatMoney = (value) => `Rp ${value}`;
    expect(inventoryEstimateText(null, formatMoney)).toBe("Belum ada dasar");
    expect(inventoryEstimateText({ inventory_value: null }, formatMoney)).toBe("Belum ada dasar");
    expect(inventoryEstimateText({ inventory_value: 0 }, formatMoney)).toBe("Rp 0");
    const missed = acceptValuationFocus({
      covered: false,
      master_item_id: "mid-service",
      reason: "Item berjenis Jasa tidak tercakup dalam daftar Nilai Persediaan.",
      inventory_value: 0,
      item: { master_item_id: "mid-service", inventory_value: 0 },
    }, "mid-service");
    expect(missed.item).toBeNull();
    expect(missed.inventory_value).toBeNull();
    expect(missed.book_value_per_item).toBeNull();
    expect(inventoryEstimateText(missed.item, formatMoney)).toBe("Belum ada dasar");
    const swapped = acceptValuationFocus({
      covered: true,
      matched_by: "master_item_id",
      master_item_id: "mid-twin",
      item: { master_item_id: "mid-twin", inventory_value: 10000 },
    }, "mid-target");
    expect(swapped.covered).toBe(false);
    expect(swapped.inventory_value).toBeNull();
  });

  test("route allows valuation and ledger reads separately", () => {
    const allow = (keys) => (key) => keys.includes(key);
    expect(inventoryValueRouteAllowed(allow(["nav.inventory.inventory_value", "inventory_valuation.view"]))).toBe(true);
    expect(inventoryValueRouteAllowed(allow(["inventory_reconciliation.view"]))).toBe(true);
    expect(inventoryValueRouteAllowed(allow(["inventory_valuation.view"]))).toBe(false);
    expect(inventoryValueRouteAllowed(allow(["nav.inventory.inventory_value"]))).toBe(false);
    expect(inventoryValueRouteAllowed(allow([]))).toBe(false);
  });
});
