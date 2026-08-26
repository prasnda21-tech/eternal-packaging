import { inventoryNavVisible, purchasingNavVisible, reportsNavVisible, tasksNavVisible, NAV_KEYS, NAV_MENU_FALLBACK } from "./navAccess";

const has = (...keys) => (k) => keys.includes(k);

describe("tasksNavVisible", () => {
  test("hides parent when no child is granted", () => {
    const v = tasksNavVisible(has(NAV_KEYS.tasks));
    expect(v.parent).toBe(false);
    expect(v.operator).toBe(false);
    expect(v.helper).toBe(false);
  });

  test("operator only sees Operator submenu", () => {
    const v = tasksNavVisible(has(NAV_KEYS.tasks, NAV_KEYS.tasksOperator));
    expect(v.parent).toBe(true);
    expect(v.operator).toBe(true);
    expect(v.helper).toBe(false);
    expect(v.packing).toBe(false);
  });

  test("submit perm operator.update shows Operator without stored nav.*", () => {
    const v = tasksNavVisible(has("operator.update"));
    expect(v.parent).toBe(true);
    expect(v.operator).toBe(true);
    expect(v.helper).toBe(false);
  });

  test("admin-like grant shows all children", () => {
    const v = tasksNavVisible(has(
      NAV_KEYS.tasks,
      NAV_KEYS.tasksScreener,
      NAV_KEYS.tasksHelper,
      NAV_KEYS.tasksOperator,
      NAV_KEYS.tasksPacking,
      NAV_KEYS.tasksSuratJalan,
      NAV_KEYS.tasksLoading,
      NAV_KEYS.tasksDelivery,
    ));
    expect(v.parent).toBe(true);
    expect(v.screener).toBe(true);
    expect(v.helper).toBe(true);
    expect(v.operator).toBe(true);
    expect(v.packing).toBe(true);
    expect(v.surat_jalan).toBe(true);
    expect(v.loading).toBe(true);
    expect(v.terkirim).toBe(true);
  });
});

describe("inventoryNavVisible", () => {
  test("hides parent when no child is granted", () => {
    const v = inventoryNavVisible(has(NAV_KEYS.inventory));
    expect(v.parent).toBe(false);
    expect(v.masterItems).toBe(false);
    expect(v.currentStock).toBe(false);
  });

  test("shows parent for master items only", () => {
    const v = inventoryNavVisible(has(NAV_KEYS.inventory, NAV_KEYS.masterItems));
    expect(v.parent).toBe(true);
    expect(v.masterItems).toBe(true);
    expect(v.currentStock).toBe(false);
  });

  test("shows parent for current stock only", () => {
    const v = inventoryNavVisible(has(NAV_KEYS.inventory, NAV_KEYS.currentStock));
    expect(v.parent).toBe(true);
    expect(v.masterItems).toBe(false);
    expect(v.currentStock).toBe(true);
  });
});

describe("purchasingNavVisible", () => {
  test("hides parent without receipts child", () => {
    const v = purchasingNavVisible(has(NAV_KEYS.purchasing));
    expect(v.parent).toBe(false);
    expect(v.receipts).toBe(false);
  });

  test("shows parent when receipts granted", () => {
    const v = purchasingNavVisible(has(NAV_KEYS.purchasing, NAV_KEYS.purchaseReceipts));
    expect(v.parent).toBe(true);
    expect(v.receipts).toBe(true);
  });
});

describe("reportsNavVisible", () => {
  test("hides parent when no child is granted", () => {
    const v = reportsNavVisible(has(NAV_KEYS.reports));
    expect(v.parent).toBe(false);
    expect(v.analytics).toBe(false);
    expect(v.statusPo).toBe(false);
    expect(v.shipmentStatus).toBe(false);
  });

  test("shows parent for shipment status only", () => {
    const v = reportsNavVisible(has(NAV_KEYS.reports, NAV_KEYS.shipmentStatus));
    expect(v.parent).toBe(true);
    expect(v.shipmentStatus).toBe(true);
    expect(v.statusPo).toBe(false);
    expect(v.analytics).toBe(false);
  });

  test("shows parent for analytics only", () => {
    const v = reportsNavVisible(has(NAV_KEYS.reports, NAV_KEYS.analytics));
    expect(v.parent).toBe(true);
    expect(v.analytics).toBe(true);
    expect(v.shipmentStatus).toBe(false);
    expect(v.statusPo).toBe(false);
  });

    test("shows parent for status po only", () => {
    const v = reportsNavVisible(has(NAV_KEYS.reports, NAV_KEYS.statusPo));
    expect(v.parent).toBe(true);
    expect(v.statusPo).toBe(true);
    expect(v.analytics).toBe(false);
    expect(v.shipmentStatus).toBe(false);
  });

  test("shows parent for gaji operator only", () => {
    const v = reportsNavVisible(has(NAV_KEYS.reports, NAV_KEYS.gajiOperator));
    expect(v.parent).toBe(true);
    expect(v.gajiOperator).toBe(true);
    expect(v.shipmentStatus).toBe(false);
  });
});

describe("NAV_MENU_FALLBACK Settings catalog", () => {
  test("includes Tugas per Divisi and all division children", () => {
    const keys = NAV_MENU_FALLBACK.map((m) => m.key);
    expect(keys).toEqual(expect.arrayContaining([
      NAV_KEYS.tasks,
      NAV_KEYS.tasksScreener,
      NAV_KEYS.tasksHelper,
      NAV_KEYS.tasksOperator,
      NAV_KEYS.tasksPacking,
      NAV_KEYS.tasksSuratJalan,
      NAV_KEYS.tasksLoading,
      NAV_KEYS.tasksDelivery,
    ]));
    const parent = NAV_MENU_FALLBACK.find((m) => m.key === NAV_KEYS.tasks);
    expect(parent.label).toBe("Tugas per Divisi");
    expect(NAV_MENU_FALLBACK.find((m) => m.key === NAV_KEYS.tasksDelivery).label).toBe("Driver / Terkirim");
  });
});
