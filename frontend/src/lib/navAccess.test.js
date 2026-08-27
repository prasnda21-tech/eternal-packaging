import { inventoryNavVisible, purchasingNavVisible, reportsNavVisible, tasksNavVisible, productionNavVisible, NAV_KEYS, NAV_MENU_FALLBACK, TASK_NAV_ORDER } from "./navAccess";

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
      NAV_KEYS.tasksDesigner,
      NAV_KEYS.tasksKepalaProduksi,
      NAV_KEYS.tasksScreener,
      NAV_KEYS.tasksHelper,
      NAV_KEYS.tasksOperator,
      NAV_KEYS.tasksPacking,
      NAV_KEYS.tasksSuratJalan,
      NAV_KEYS.tasksLoading,
      NAV_KEYS.tasksDelivery,
    ));
    expect(v.parent).toBe(true);
    expect(v.designer).toBe(true);
    expect(v.kepala_produksi).toBe(true);
    expect(v.screener).toBe(true);
    expect(v.helper).toBe(true);
    expect(v.operator).toBe(true);
    expect(v.packing).toBe(true);
    expect(v.surat_jalan).toBe(true);
    expect(v.loading).toBe(true);
    expect(v.terkirim).toBe(true);
  });

  test("designer upload perm shows Designer not Kepala Produksi", () => {
    const v = tasksNavVisible(has("design.upload"));
    expect(v.parent).toBe(true);
    expect(v.designer).toBe(true);
    expect(v.kepala_produksi).toBe(false);
    expect(v.screener).toBe(false);
  });

  test("schedule.edit shows Kepala Produksi not Designer", () => {
    const v = tasksNavVisible(has("schedule.edit"));
    expect(v.parent).toBe(true);
    expect(v.kepala_produksi).toBe(true);
    expect(v.designer).toBe(false);
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
  test("includes Produksi parent, Tugas Produksi, and all division children", () => {
    const keys = NAV_MENU_FALLBACK.map((m) => m.key);
    expect(keys).toEqual(expect.arrayContaining([
      NAV_KEYS.production,
      NAV_KEYS.schedule,
      NAV_KEYS.deliverySchedule,
      NAV_KEYS.deliveryReschedule,
      NAV_KEYS.tasks,
      NAV_KEYS.tasksDesigner,
      NAV_KEYS.tasksKepalaProduksi,
      NAV_KEYS.tasksScreener,
      NAV_KEYS.tasksHelper,
      NAV_KEYS.tasksOperator,
      NAV_KEYS.tasksPacking,
      NAV_KEYS.tasksSuratJalan,
      NAV_KEYS.tasksLoading,
      NAV_KEYS.tasksDelivery,
    ]));
    expect(keys).not.toEqual(expect.arrayContaining([NAV_KEYS.driverRoute]));
    expect(NAV_MENU_FALLBACK.find((m) => m.key === NAV_KEYS.production).label).toBe("Produksi");
    expect(NAV_MENU_FALLBACK.find((m) => m.key === NAV_KEYS.schedule).parent).toBe(NAV_KEYS.production);
    const parent = NAV_MENU_FALLBACK.find((m) => m.key === NAV_KEYS.tasks);
    expect(parent.label).toBe("Tugas Produksi");
    expect(parent.parent).toBe(NAV_KEYS.production);
    expect(NAV_MENU_FALLBACK.some((m) => m.label === "Tugas per Divisi")).toBe(false);
    expect(NAV_MENU_FALLBACK.find((m) => m.key === NAV_KEYS.tasksDesigner).label).toBe("Designer");
    expect(NAV_MENU_FALLBACK.find((m) => m.key === NAV_KEYS.tasksKepalaProduksi).label).toBe("Kepala Produksi");
    expect(NAV_MENU_FALLBACK.find((m) => m.key === NAV_KEYS.tasksDelivery).label).toBe("Driver / Terkirim");
    const taskKeys = NAV_MENU_FALLBACK.filter((m) => m.parent === NAV_KEYS.tasks).map((m) => m.key);
    expect(taskKeys).toEqual([
      NAV_KEYS.tasksDesigner,
      NAV_KEYS.tasksScreener,
      NAV_KEYS.tasksKepalaProduksi,
      NAV_KEYS.tasksHelper,
      NAV_KEYS.tasksOperator,
      NAV_KEYS.tasksPacking,
      NAV_KEYS.tasksSuratJalan,
      NAV_KEYS.tasksLoading,
      NAV_KEYS.tasksDelivery,
    ]);
    expect(TASK_NAV_ORDER).toEqual([
      "designer", "screener", "kepala_produksi", "helper", "operator",
      "packing", "surat_jalan", "loading", "terkirim",
    ]);
  });
});

describe("productionNavVisible", () => {
  test("shows parent when only jadwal produksi is granted", () => {
    const v = productionNavVisible(has(NAV_KEYS.schedule));
    expect(v.parent).toBe(true);
    expect(v.schedule).toBe(true);
    expect(v.deliverySchedule).toBe(false);
    expect(v.tasks.parent).toBe(false);
  });

  test("shows parent when only tugas produksi child is granted", () => {
    const v = productionNavVisible(has(NAV_KEYS.tasksOperator, "operator.update"));
    expect(v.parent).toBe(true);
    expect(v.tasks.operator).toBe(true);
    expect(v.schedule).toBe(false);
  });
});
