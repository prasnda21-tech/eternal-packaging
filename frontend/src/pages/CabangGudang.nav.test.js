import { inventoryMenuItems, inventoryNavVisible, NAV_KEYS } from "../lib/navAccess";

const has = (...keys) => (p) => keys.includes(p);

describe("Cabang & Gudang nav", () => {
  test("inventoryNavVisible exposes branchesWarehouses", () => {
    const v = inventoryNavVisible(has(NAV_KEYS.inventory, NAV_KEYS.branchesWarehouses));
    expect(v.parent).toBe(true);
    expect(v.branchesWarehouses).toBe(true);
  });

  test("capability org.branches.view unlocks menu under inventory parent", () => {
    const v = inventoryNavVisible(has(NAV_KEYS.inventory, "org.branches.view"));
    expect(v.branchesWarehouses).toBe(true);
  });

  test("menu item routes to /persediaan/cabang-gudang between barang and stock", () => {
    const items = inventoryMenuItems(
      has(NAV_KEYS.inventory, NAV_KEYS.masterItems, NAV_KEYS.branchesWarehouses, NAV_KEYS.currentStock),
    );
    const labels = items.map((i) => i.label);
    expect(labels).toEqual(["Barang/Jasa", "Cabang & Gudang", "Stock Saat Ini"]);
    const cabang = items.find((i) => i.key === NAV_KEYS.branchesWarehouses);
    expect(cabang.to).toBe("/persediaan/cabang-gudang");
    expect(cabang.testid).toBe("top-nav-cabang-gudang");
  });
});
