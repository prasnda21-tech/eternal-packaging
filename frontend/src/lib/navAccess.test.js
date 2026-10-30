import { productionNavVisible, productionMenuItems, dashboardNavVisible, inventoryMenuItems, inventoryNavVisible, purchasingMenuItems, purchasingNavVisible, reportsMenuItems, reportsNavVisible, salesMenuItems, salesNavVisible, settingsMenuItems, settingsNavVisible, settingsTabFromPath, tasksNavVisible, taskMenuItems, productionMenuItems, productionNavVisible, NAV_KEYS, NAV_MENU_FALLBACK, SALES_NAV_ROUTES, SETTINGS_TAB_IDS, TASK_NAV_ORDER } from "./navAccess";
import { SALES_WORKSPACE_TABS } from "./salesWorkspace";

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

describe("salesNavVisible", () => {
  test("hides parent when sales granted without children", () => {
    const v = salesNavVisible(has(NAV_KEYS.sales));
    expect(v.parent).toBe(false);
    expect(v.salesOrder).toBe(false);
    expect(v.importPo).toBe(false);
    expect(v.salesInvoice).toBe(false);
  });

  test("shows parent when Import PO is granted", () => {
    const v = salesNavVisible(has(NAV_KEYS.sales, NAV_KEYS.importPo));
    expect(v.parent).toBe(true);
    expect(v.importPo).toBe(true);
  });

  test("shows parent when Pesanan Penjualan is granted", () => {
    const v = salesNavVisible(has(NAV_KEYS.sales, NAV_KEYS.salesOrder));
    expect(v.parent).toBe(true);
    expect(v.salesOrder).toBe(true);
  });

  test("shows parent when Faktur Penjualan is granted", () => {
    const v = salesNavVisible(has(NAV_KEYS.sales, NAV_KEYS.salesInvoice));
    expect(v.parent).toBe(true);
    expect(v.salesInvoice).toBe(true);
  });

  test("shows parent when sales.invoice.view permission is granted", () => {
    const v = salesNavVisible(has(NAV_KEYS.sales, "sales.invoice.view"));
    expect(v.parent).toBe(true);
    expect(v.salesInvoice).toBe(true);
  });

  test("shows parent when Penerimaan Penjualan is granted", () => {
    const v = salesNavVisible(has(NAV_KEYS.sales, NAV_KEYS.salesReceipt));
    expect(v.parent).toBe(true);
    expect(v.salesReceipt).toBe(true);
  });

  test("shows parent when sales.receipt.view permission is granted", () => {
    const v = salesNavVisible(has(NAV_KEYS.sales, "sales.receipt.view"));
    expect(v.parent).toBe(true);
    expect(v.salesReceipt).toBe(true);
  });

  test("hides Faktur Penjualan and Penerimaan Penjualan without permissions", () => {
    const v = salesNavVisible(has(NAV_KEYS.sales, NAV_KEYS.importPo));
    expect(v.salesInvoice).toBe(false);
    expect(v.salesReceipt).toBe(false);
  });
});

describe("salesMenuItems", () => {
  test("builds tiles from existing Penjualan children only", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, NAV_KEYS.salesCustomers, NAV_KEYS.salesOrder, NAV_KEYS.importPo));
    expect(items).toEqual([
      { key: NAV_KEYS.salesCustomers, label: "Data Pelanggan", to: "/penjualan/data-pelanggan", tone: "teal", testid: "top-nav-sales-customers" },
      { key: NAV_KEYS.salesOrder, label: "Pesanan Penjualan", to: "/penjualan/pesanan-penjualan", tone: "blue", testid: "top-nav-sales-order" },
      { key: NAV_KEYS.importPo, label: "Import PO", to: "/admin/import", tone: "green", testid: "top-nav-import" },
    ]);
  });

  test("builds Data Pelanggan tile when granted alone", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, NAV_KEYS.salesCustomers));
    expect(items).toEqual([
      { key: NAV_KEYS.salesCustomers, label: "Data Pelanggan", to: "/penjualan/data-pelanggan", tone: "teal", testid: "top-nav-sales-customers" },
    ]);
  });

  test("builds Faktur Penjualan tile when granted", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, NAV_KEYS.salesInvoice));
    expect(items).toEqual([
      { key: NAV_KEYS.salesInvoice, label: "Faktur Penjualan", to: "/penjualan/faktur-penjualan", tone: "purple", testid: "top-nav-sales-invoice" },
    ]);
  });

  test("builds Penerimaan Penjualan tile when granted", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, NAV_KEYS.salesReceipt));
    expect(items).toEqual([
      { key: NAV_KEYS.salesReceipt, label: "Penerimaan Penjualan", to: "/penjualan/penerimaan-penjualan", tone: "emerald", testid: "top-nav-sales-receipt" },
    ]);
  });

  test("CRM create permission opens the new product request form", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, "product_request.create"));
    expect(items).toEqual([
      { key: NAV_KEYS.productRequests, label: "Permintaan Produk", to: "/penjualan/permintaan-produk/baru", tone: "green", testid: "top-nav-product-requests" },
    ]);
  });

  test("product request child nav alone surfaces Penjualan parent without other sales tiles", () => {
    const visible = salesNavVisible(has(NAV_KEYS.productRequests));
    expect(visible.parent).toBe(true);
    expect(visible.productRequests).toBe(true);
    expect(visible.salesOrder).toBe(false);
    expect(visible.salesInvoice).toBe(false);
    expect(visible.salesReceipt).toBe(false);
    const items = salesMenuItems(has(NAV_KEYS.productRequests));
    expect(items).toEqual([
      { key: NAV_KEYS.productRequests, label: "Permintaan Produk", to: "/penjualan/permintaan-produk", tone: "green", testid: "top-nav-product-requests" },
    ]);
  });

  test("Holding read_all opens the product request list", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, "product_request.read_all"));
    expect(items).toEqual([
      { key: NAV_KEYS.productRequests, label: "Permintaan Produk", to: "/penjualan/permintaan-produk", tone: "green", testid: "top-nav-product-requests" },
    ]);
  });

  test("create and read_all still land on the list so Buat Permintaan can appear", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, "product_request.create", "product_request.read_all"));
    expect(items[0].to).toBe("/penjualan/permintaan-produk");
  });

  test("NAV_MENU_FALLBACK includes Penjualan children", () => {
    expect(NAV_MENU_FALLBACK.filter((m) => m.parent === NAV_KEYS.sales).map((m) => m.label)).toEqual([
      "Data Pelanggan",
      "Permintaan Produk",
      "Pra PO",
      "Pesanan Penjualan",
      "Pengiriman Pesanan",
      "Faktur Penjualan",
      "Penerimaan Penjualan",
      "Import PO",
      "Aktivitas Sales",
      "Laporan Harian CRM",
      "Status PO",
      "Prediksi Order",
    ]);
  });

  test("builds Laporan Harian CRM tile when granted", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, NAV_KEYS.crmDailyReport));
    expect(items.some((i) => i.key === NAV_KEYS.crmDailyReport && i.to === "/penjualan/laporan-harian-crm")).toBe(true);
  });

  test("shows Laporan Harian CRM from capability without explicit nav key", () => {
    const vis = salesNavVisible(has("crm.daily_report.create"));
    expect(vis.crmDailyReport).toBe(true);
    expect(vis.parent).toBe(true);
  });

  test("Penerimaan Penjualan sidebar key stays nav.sales.sales_receipt", () => {
    expect(NAV_KEYS.salesReceipt).toBe("nav.sales.sales_receipt");
    expect(NAV_MENU_FALLBACK.find((m) => m.label === "Penerimaan Penjualan")).toEqual({
      key: "nav.sales.sales_receipt",
      label: "Penerimaan Penjualan",
      parent: NAV_KEYS.sales,
    });
  });

  test("filters out unauthorized children", () => {
    expect(salesMenuItems(has(NAV_KEYS.sales))).toEqual([]);
    expect(salesMenuItems(has(NAV_KEYS.importPo))).toEqual([]);
  });

  test("builds Aktivitas Sales tile when granted", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, NAV_KEYS.salesActivity));
    expect(items).toEqual([
      { key: NAV_KEYS.salesActivity, label: "Aktivitas Sales", to: "/penjualan/aktivitas-sales", tone: "blue", testid: "top-nav-sales-activity" },
    ]);
  });

  test("shows moved report pages inside Penjualan with existing report permissions", () => {
    const visible = salesNavVisible(has(NAV_KEYS.reports, NAV_KEYS.statusPo, NAV_KEYS.orderPrediction));
    expect(visible.parent).toBe(true);
    expect(visible.statusPo).toBe(true);
    expect(visible.orderPrediction).toBe(true);
  });

  test("does not invent Accurate-style sales menus", () => {
    const labels = salesMenuItems(has(NAV_KEYS.sales, NAV_KEYS.salesOrder, NAV_KEYS.importPo)).map((item) => item.label);
    expect(labels).not.toEqual(expect.arrayContaining(["Penawaran", "Faktur", "Retur", "Komisi"]));
    expect(NAV_MENU_FALLBACK.some((m) => ["Penawaran", "Faktur", "Retur", "Komisi"].includes(m.label))).toBe(false);
  });

  test("workspace catalog covers reviewed Penjualan routes without dropping Data Pelanggan nav", () => {
    const workspacePaths = SALES_WORKSPACE_TABS.map((t) => t.path);
    expect(workspacePaths).toEqual(expect.arrayContaining([
      SALES_NAV_ROUTES[NAV_KEYS.salesOrder],
      SALES_NAV_ROUTES[NAV_KEYS.salesDeliveryOrder],
      SALES_NAV_ROUTES[NAV_KEYS.salesInvoice],
      SALES_NAV_ROUTES[NAV_KEYS.salesReceipt],
      SALES_NAV_ROUTES[NAV_KEYS.importPo],
      SALES_NAV_ROUTES[NAV_KEYS.salesActivity],
    ]));
    expect(NAV_KEYS.salesCustomers).toBe("nav.sales.customers");
    expect(SALES_NAV_ROUTES[NAV_KEYS.salesCustomers]).toBe("/penjualan/data-pelanggan");
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

  test("explicit nav deny wins over module fallback when configured", () => {
    const v = purchasingNavVisible(has(
      "nav.configured",
      NAV_KEYS.purchasing,
      NAV_KEYS.purchaseOrders,
      "purchase_orders.view",
      "purchase_hpp.view",
    ));
    expect(v.orders).toBe(true);
    expect(v.hpp).toBe(false);
    expect(v.returns).toBe(false);
  });

  test("module fallback works only before nav.configured", () => {
    const before = purchasingNavVisible(has(
      NAV_KEYS.purchasing,
      "purchase_hpp.view",
      "purchase_orders.view",
    ));
    expect(before.hpp).toBe(true);
    expect(before.orders).toBe(true);
  });
});

describe("reportsNavVisible", () => {
  test("hides parent when no catalog report is granted", () => {
    const v = reportsNavVisible(has(NAV_KEYS.reports));
    expect(v.parent).toBe(false);
    expect(v.catalogList).toBe(false);
    expect(v.statusPo).toBe(false);
    expect(v.shipmentStatus).toBe(false);
  });

  test("shipment status alone no longer opens Laporan menu (consolidated to Jadwal)", () => {
    const v = reportsNavVisible(has(NAV_KEYS.reports, NAV_KEYS.shipmentStatus));
    expect(v.parent).toBe(false);
    expect(v.shipmentStatus).toBe(false);
    expect(v.statusPo).toBe(false);
  });

  test("shows parent when analytics catalog card is accessible", () => {
    const v = reportsNavVisible(has(NAV_KEYS.reports, NAV_KEYS.analytics, "schedule.view"));
    expect(v.parent).toBe(true);
    expect(v.catalogList).toBe(true);
    expect(v.analytics).toBe(true);
    expect(v.statusPo).toBe(false);
  });

  test("keeps status po authorization without opening Laporan", () => {
    const v = reportsNavVisible(has(NAV_KEYS.reports, NAV_KEYS.statusPo));
    expect(v.parent).toBe(false);
    expect(v.statusPo).toBe(true);
    expect(v.catalogList).toBe(false);
    expect(v.shipmentStatus).toBe(false);
  });

  test("shows parent for gaji operator catalog access", () => {
    const v = reportsNavVisible(has(NAV_KEYS.reports, NAV_KEYS.gajiOperator, "reports.gaji_operator.view"));
    expect(v.parent).toBe(true);
    expect(v.gajiOperator).toBe(true);
    expect(v.catalogList).toBe(true);
    expect(v.orderPrediction).toBe(false);
  });

  test("shows parent for sales reports without orphan leaf", () => {
    const v = reportsNavVisible(has(NAV_KEYS.reports, NAV_KEYS.salesReports, "reports.sales.view"));
    expect(v.parent).toBe(true);
    expect(v.salesReports).toBe(true);
    expect(v.catalogList).toBe(true);
    expect(reportsMenuItems(has(NAV_KEYS.reports, NAV_KEYS.salesReports, "reports.sales.view")).map((i) => i.label)).toEqual([
      "Daftar Laporan",
      "Laporan Tersimpan",
    ]);
  });

  test("keeps prediction authorization without opening Laporan", () => {
    const v = reportsNavVisible(has(NAV_KEYS.reports, NAV_KEYS.orderPrediction));
    expect(v.parent).toBe(false);
    expect(v.orderPrediction).toBe(true);
    expect(v.gajiOperator).toBe(false);
    expect(v.statusPo).toBe(false);
  });

  test("holding-like sales view opens hub without orphan leaf", () => {
    const perms = has(NAV_KEYS.reports, NAV_KEYS.salesReports, "reports.sales.view");
    const v = reportsNavVisible(perms);
    expect(v.parent).toBe(true);
    expect(reportsMenuItems(perms).map((i) => i.to)).toEqual(["/laporan", "/laporan?view=saved"]);
    expect(reportsMenuItems(perms).some((i) => i.label === "Laporan Penjualan")).toBe(false);
  });

  test("finance-only report opens hub with Daftar/Tersimpan leaves", () => {
    const perms = has(NAV_KEYS.reports, "profit_loss.view");
    const v = reportsNavVisible(perms);
    expect(v.parent).toBe(true);
    expect(v.salesReports).toBe(false);
    expect(reportsMenuItems(perms)).toHaveLength(2);
  });

  test("nav.configured cannot hide authorized reports", () => {
    const v = reportsNavVisible(has("nav.configured", "reports.sales.view", "profit_loss.view"));
    expect(v.parent).toBe(true);
    expect(v.catalogList).toBe(true);
    expect(reportsMenuItems(has("nav.configured", "reports.sales.view"))).toHaveLength(2);
  });

  test("role with no report access keeps Laporan hidden", () => {
    const v = reportsNavVisible(has(NAV_KEYS.sales, "po.view"));
    expect(v.parent).toBe(false);
    expect(reportsMenuItems(has(NAV_KEYS.sales, "po.view"))).toEqual([]);
  });

  test("sales_eternal style sales.pwa keys do not open Report Center", () => {
    const v = reportsNavVisible(has("sales.pwa.view", "sales.pwa.visit.create"));
    expect(v.parent).toBe(false);
    expect(v.salesReports).toBe(false);
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
    expect(keys).toEqual(expect.arrayContaining([NAV_KEYS.routeOptimizer]));
    expect(NAV_MENU_FALLBACK.find((m) => m.key === NAV_KEYS.routeOptimizer)).toEqual({
      key: NAV_KEYS.routeOptimizer,
      label: "Optimasi Rute",
      parent: null,
    });
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

  test("pengiriman hari ini menu is retired; status po alone does not open produksi", () => {
    const onlyStatus = productionNavVisible(has(NAV_KEYS.statusPo));
    expect(onlyStatus.parent).toBe(false);
    expect(onlyStatus.deliveryToday).toBe(false);
    const both = productionNavVisible(has(NAV_KEYS.schedule, NAV_KEYS.statusPo));
    expect(both.parent).toBe(true);
    expect(both.deliveryToday).toBe(false);
    const prodOnly = productionNavVisible(has(NAV_KEYS.schedule));
    expect(prodOnly.deliveryToday).toBe(false);
  });
});

describe("purchasingMenuItems", () => {
  test("builds tiles from existing Pembelian children only", () => {
    expect(purchasingMenuItems(has(NAV_KEYS.purchasing, NAV_KEYS.purchaseReceipts))).toEqual([
      { key: NAV_KEYS.purchaseReceipts, label: "Penerimaan Barang", to: "/pembelian/penerimaan-barang", tone: "amber", testid: "top-nav-penerimaan-barang" },
      { key: NAV_KEYS.purchaseReturns, label: "Retur Pembelian", to: "/pembelian/retur", tone: "rose", testid: "top-nav-retur-pembelian" },
    ]);
    expect(purchasingMenuItems(has(NAV_KEYS.purchasing))).toEqual([]);
  });

  test("adds Permintaan, Pesanan, Faktur, Pembayaran without duplicating Penerimaan Barang", () => {
    const items = purchasingMenuItems(has(
      NAV_KEYS.purchasing,
      NAV_KEYS.purchaseRequests,
      NAV_KEYS.purchaseOrders,
      NAV_KEYS.purchaseInvoices,
      NAV_KEYS.purchasePayments,
      NAV_KEYS.purchaseReceipts,
    ));
    expect(items.map((item) => item.label)).toEqual([
      "Permintaan",
      "Pesanan Pembelian",
      "Uang Muka",
      "Penerimaan Barang",
      "Retur Pembelian",
      "Faktur Pembelian",
      "Pembayaran Pembelian",
    ]);
    expect(items.filter((item) => item.label === "Penerimaan Barang")).toHaveLength(1);
  });

  test("places HPP Pembelian after Pembayaran without joining the transaction sequence", () => {
    const items = purchasingMenuItems(has(
      NAV_KEYS.purchasing,
      NAV_KEYS.purchaseRequests,
      NAV_KEYS.purchaseOrders,
      NAV_KEYS.purchaseInvoices,
      NAV_KEYS.purchasePayments,
      NAV_KEYS.purchaseReceipts,
      NAV_KEYS.purchaseHpp,
    ));
    expect(items.map((item) => item.label)).toEqual([
      "Permintaan",
      "Pesanan Pembelian",
      "Uang Muka",
      "Penerimaan Barang",
      "Retur Pembelian",
      "Faktur Pembelian",
      "Pembayaran Pembelian",
      "HPP Pembelian",
    ]);
    expect(items.find((item) => item.label === "HPP Pembelian").to).toBe("/pembelian/hpp-pembelian");
  });
});

describe("inventoryMenuItems", () => {
  test("shows authorized Barang/Jasa only", () => {
    const items = inventoryMenuItems(has(NAV_KEYS.inventory, NAV_KEYS.masterItems));
    expect(items).toEqual([
      { key: NAV_KEYS.masterItems, label: "Barang/Jasa", to: "/persediaan/master-barang-jasa", tone: "green", testid: "top-nav-master-barang-jasa" },
    ]);
    expect(items.map((item) => item.label)).not.toContain("Stock Saat Ini");
    expect(inventoryMenuItems(has(NAV_KEYS.inventory))).toEqual([]);
  });

  test("does not expose Nilai Persediaan through Stock Saat Ini permission", () => {
    const items = inventoryMenuItems(has(
      NAV_KEYS.inventory,
      NAV_KEYS.currentStock,
      "current_stock.view",
    ));
    expect(items.map((item) => [item.label, item.to])).toEqual([
      ["Stock Saat Ini", "/persediaan/stock-saat-ini"],
    ]);
  });

  test("shows Nilai Persediaan only with its dedicated permission", () => {
    const items = inventoryMenuItems(has(
      NAV_KEYS.inventory,
      NAV_KEYS.inventoryValue,
      "inventory_valuation.view",
    ));
    expect(items.map((item) => [item.label, item.to])).toEqual([
      ["Nilai Persediaan", "/persediaan/nilai-persediaan"],
    ]);
  });
});

describe("reportsMenuItems", () => {
  test("exposes Daftar Laporan and Laporan Tersimpan under Laporan", () => {
    const items = reportsMenuItems(has(
      NAV_KEYS.reports,
      NAV_KEYS.gajiOperator,
      "reports.gaji_operator.view",
      NAV_KEYS.statusPo,
      NAV_KEYS.orderPrediction,
    ));
    expect(items.map((item) => [item.label, item.to, item.testid])).toEqual([
      ["Daftar Laporan", "/laporan", "top-nav-daftar-laporan"],
      ["Laporan Tersimpan", "/laporan?view=saved", "top-nav-laporan-tersimpan"],
    ]);
    expect(reportsMenuItems(has(NAV_KEYS.reports, NAV_KEYS.statusPo, NAV_KEYS.orderPrediction))).toEqual([]);
  });

  test("moves Status PO and Prediksi Order to Penjualan without changing routes or keys", () => {
    const items = salesMenuItems(has(NAV_KEYS.reports, NAV_KEYS.statusPo, NAV_KEYS.orderPrediction));
    expect(items.map((item) => [item.key, item.label, item.to, item.testid])).toEqual([
      [NAV_KEYS.statusPo, "Status PO", "/laporan/status-po", "top-nav-status-po"],
      [NAV_KEYS.orderPrediction, "Prediksi Order", "/laporan/prediksi-order", "top-nav-prediksi-order"],
    ]);
    expect(NAV_KEYS.statusPo).toBe("nav.reports.status_po");
    expect(NAV_KEYS.orderPrediction).toBe("nav.reports.order_prediction");
  });
});

describe("productionMenuItems", () => {
  test("keeps Produksi children without Pengiriman Hari Ini menu", () => {
    const items = productionMenuItems(has(NAV_KEYS.schedule, NAV_KEYS.deliverySchedule, NAV_KEYS.deliveryReschedule, NAV_KEYS.statusPo));
    expect(items.map((item) => [item.label, item.to])).toEqual([
      ["Jadwal Produksi", "/produksi"],
      ["Jadwal Pengiriman", "/produksi/jadwal-pengiriman"],
      ["Rencana Jadwal Kirim", "/produksi/rencana-jadwal-kirim"],
      ["Jadwal Pengiriman Ulang", "/produksi/jadwal-pengiriman-ulang"],
    ]);
    expect(items.some((item) => item.label === "Pengiriman Hari Ini")).toBe(false);
  });

  test("omits Pengiriman Hari Ini without status po and does not invent Tugas Produksi tile", () => {
    const items = productionMenuItems(has(NAV_KEYS.schedule, NAV_KEYS.tasksOperator));
    expect(items.map((item) => item.label)).toEqual(["Jadwal Produksi"]);
    expect(items.some((item) => item.label === "Tugas Produksi")).toBe(false);
    expect(productionMenuItems(has(NAV_KEYS.statusPo))).toEqual([]);
  });
});

describe("settingsMenuItems", () => {
  test("hides tiles without nav.settings even when a child perm is granted", () => {
    expect(settingsMenuItems(has("settings.sales_accounting.view"))).toEqual([]);
    expect(settingsNavVisible(has(NAV_KEYS.settings)).parent).toBe(false);
  });

  test("shows authorized settings children and hides the rest", () => {
    const items = settingsMenuItems(has(
      NAV_KEYS.settings,
      "settings.sales_accounting.view",
      "payroll_accounting.view",
    ));
    expect(items.map((item) => [item.label, item.to, item.testid])).toEqual([
      ["Akuntansi Penjualan", "/settings/sales-accounting", "top-nav-settings-sales-accounting"],
      ["Akuntansi Payroll", "/settings/payroll-accounting", "top-nav-settings-payroll-accounting"],
    ]);
    expect(items.map((item) => item.label)).not.toContain("Karyawan");
    expect(items.map((item) => item.to)).not.toContain("/pengaturan");
    expect(items.some((item) => item.label === "Pengaturan")).toBe(false);
  });

  test("does not invent placeholder settings", () => {
    const items = settingsMenuItems(() => true);
    expect(items.map((item) => item.key).sort()).toEqual([...SETTINGS_TAB_IDS].sort());
    expect(items.some((item) => /placeholder|coming soon|akun tipe|account type/i.test(item.label))).toBe(false);
  });

  test("settingsTabFromPath reads child path and legacy query", () => {
    expect(settingsTabFromPath("/settings/sales-accounting")).toBe("sales-accounting");
    expect(settingsTabFromPath("/settings/sales-accounting?x=1")).toBe("sales-accounting");
    expect(settingsTabFromPath("/settings", "?tab=payroll-accounting")).toBe("payroll-accounting");
    expect(settingsTabFromPath("/settings", "?tab=not-a-real-tab")).toBe(null);
    expect(settingsTabFromPath("/settings/not-a-real-tab")).toBe(null);
  });
});

describe("taskMenuItems", () => {
  test("shows authorized Tugas Produksi children in TASK_NAV_ORDER", () => {
    const items = taskMenuItems(has(NAV_KEYS.tasksDesigner, NAV_KEYS.tasksOperator, "operator.update", "design.upload"));
    expect(items.map((item) => item.key)).toEqual(["designer", "operator"]);
    expect(items[1]).toEqual({
      key: "operator",
      label: "Operator",
      to: "/tasks/operator",
      tone: "green",
      testid: "top-nav-tasks-operator",
    });
  });
});


describe("permission-led sidebar effective deny regression", () => {
  test("delivery menu requires business view even if legacy nav is granted", () => {
    const permissions = has(NAV_KEYS.sales, NAV_KEYS.salesDeliveryOrder, "nav.configured");
    expect(salesNavVisible(permissions).salesDeliveryOrder).toBe(false);
    expect(salesMenuItems(permissions).some((item) => item.key === NAV_KEYS.salesDeliveryOrder)).toBe(false);
  });

  test("business delivery view opens menu even with legacy configured nav", () => {
    const permissions = has(NAV_KEYS.sales, "sales.delivery.view", "nav.configured");
    expect(salesNavVisible(permissions).salesDeliveryOrder).toBe(true);
    expect(salesMenuItems(permissions).some((item) => item.key === NAV_KEYS.salesDeliveryOrder)).toBe(true);
  });

  test("production dashboard menu needs effective production view", () => {
    const missingView = has(NAV_KEYS.dashboard, "nav.configured");
    expect(dashboardNavVisible(missingView).production).toBe(false);
    expect(dashboardNavVisible(has(NAV_KEYS.dashboard, "dashboard.production.view")).production).toBe(true);
  });
});


describe("automatic purchasing navigation enforces effective business permissions", () => {
  test("legacy nav only cannot display purchasing grants", () => {
    const perms = has(NAV_KEYS.purchasing, NAV_KEYS.purchaseOrders, NAV_KEYS.purchaseInvoices, "nav.configured");
    const visible = purchasingNavVisible(perms);
    expect(visible.orders).toBe(false);
    expect(visible.invoices).toBe(false);
    expect(visible.parent).toBe(false);
  });

  test("view permission displays purchasing child without manual nav configuration", () => {
    const perms = has(NAV_KEYS.purchasing, "purchase_orders.view", "nav.configured");
    const visible = purchasingNavVisible(perms);
    expect(visible.orders).toBe(true);
    expect(visible.invoices).toBe(false);
  });
});


describe("CRM production schedule permission parity", () => {
  test("schedule.view alone displays Jadwal Produksi without sidebar configuration", () => {
    const access = productionNavVisible(has("schedule.view", "nav.configured"));
    expect(access.schedule).toBe(true);
    expect(productionMenuItems(has("schedule.view", "nav.configured")).some((m) => m.to === "/produksi")).toBe(true);
  });

  test("legacy sidebar key cannot override denied schedule.view", () => {
    const access = productionNavVisible(has(NAV_KEYS.production, NAV_KEYS.schedule, "nav.configured"));
    expect(access.schedule).toBe(false);
    expect(productionMenuItems(has(NAV_KEYS.production, NAV_KEYS.schedule, "nav.configured")).some((m) => m.to === "/produksi")).toBe(false);
  });

  test("schedule.work_date.view alone does not grant production schedule", () => {
    expect(productionNavVisible(has("schedule.work_date.view")).schedule).toBe(false);
  });
});
