import { NAV_KEYS, SETTINGS_TAB_IDS, SETTINGS_TILE_LABELS, cashBankMenuItems, dashboardMenuItems, employeeAdminMenuItems, hrPayrollMenuItems, inventoryMenuItems, ledgerMenuItems, productionMenuItems, purchasingMenuItems, reportsMenuItems, salesMenuItems, settingsMenuItems, taskMenuItems } from "./navAccess";
import {
  SIDEBAR_MODE_STORAGE_KEY,
  buildDesktopSidebar,
  isDeliveryRailActive,
  isFinanceRailActive,
  isOperationalReportRailActive,
  isProductionRailActive,
  isSalesRailActive,
  presentSidebarLeaf,
  readSidebarMode,
  sidebarLeaves,
  writeSidebarMode,
} from "./sidebarPresentation";

const has = (...keys) => (key) => keys.includes(key);
const allow = () => true;

function modelFrom(hasPerm, extra = {}) {
  return buildDesktopSidebar({
    dashboardItems: dashboardMenuItems(hasPerm, extra.user || {}),
    salesItems: salesMenuItems(hasPerm),
    productionItems: productionMenuItems(hasPerm),
    taskItems: taskMenuItems(hasPerm),
    showRouteOptimizer: !!extra.route,
    purchasingItems: purchasingMenuItems(hasPerm),
    inventoryItems: inventoryMenuItems(hasPerm),
    cashBankItems: cashBankMenuItems(hasPerm),
    ledgerItems: ledgerMenuItems(hasPerm),
    employeeAdminItems: employeeAdminMenuItems(hasPerm),
    hrPayrollItems: hrPayrollMenuItems(hasPerm),
    reportItems: reportsMenuItems(hasPerm),
    settingsItems: settingsMenuItems(hasPerm),
    showSpk: !!extra.spk,
    showChat: !!extra.chat,
  });
}

function group(rails, id) {
  return rails.find((rail) => rail.id === id) || null;
}

function labels(rail) {
  return sidebarLeaves(rail ? [rail] : []).map((leaf) => leaf.label);
}

describe("desktop sidebar presentation", () => {
  test("full permission inventory keeps every existing leaf and settings tab", () => {
    const rails = modelFrom(allow, { route: true, spk: true, chat: true, user: { role: "admin" } });
    expect(rails.map((rail) => rail.label)).toEqual([
      "Dashboard",
      "SPK",
      "Chat",
      "Penjualan",
      "Produksi",
      "Pengiriman",
      "Pembelian",
      "Persediaan",
      "Keuangan",
      "Administrasi Karyawan",
      "HR & Payroll",
      "Laporan Operasional",
      "Pengaturan",
    ]);
    expect(labels(group(rails, "sales"))).toEqual([
      "Data Pelanggan",
      "Permintaan Produk",
      "Pesanan Penjualan",
      "Pengiriman Pesanan",
      "Faktur Penjualan",
      "Penerimaan Penjualan",
      "Import PO",
      "Aktivitas Sales",
      "Status PO",
      "Prediksi Order",
    ]);
    expect(labels(group(rails, "purchasing"))).toEqual([
      "Permintaan",
      "Pesanan Pembelian",
      "Penerimaan Barang",
      "Faktur Pembelian",
      "Pembayaran Pembelian",
    ]);
    expect(labels(group(rails, "inventory"))).toEqual(["Barang/Jasa", "Stock Saat Ini", "Nilai Persediaan"]);
    expect(group(rails, "keuangan").sections.map((section) => section.heading)).toEqual(["Kas & Bank", "Buku Besar"]);
    expect(labels(group(rails, "keuangan").sections[0] && { ...group(rails, "keuangan"), sections: [group(rails, "keuangan").sections[0]] })).toEqual([
      "Daftar Kas & Bank",
      "Penerimaan",
      "Pembayaran",
      "Transfer",
      "Mutasi Kas & Bank",
      "Rekonsiliasi Bank",
    ]);
    const finance = group(rails, "keuangan");
    expect(finance.sections[1].items.map((item) => item.label)).toEqual([
      "Akun Perkiraan",
      "Jurnal Umum",
      "Saldo Awal",
      "Periode Akuntansi",
      "Buku Besar",
      "Piutang Usaha",
      "Hutang Usaha",
      "Neraca Saldo",
      "Laba/Rugi",
      "Neraca",
    ]);
    expect(labels(group(rails, "employeeAdmin"))).toEqual([
      "Pengajuan Saya",
      "Buat Pengajuan",
      "Persetujuan",
      "Arsip Dokumen",
    ]);
    expect(labels(group(rails, "hrPayroll"))).toEqual([
      "Daftar Karyawan",
      "Data Kepegawaian",
      "Penggajian",
      "Slip Gaji",
      "Komponen Gaji",
      "Riwayat Payroll",
    ]);
    expect(group(rails, "settings").sections[0].items.map((item) => item.key)).toEqual([...SETTINGS_TAB_IDS]);
    expect(group(rails, "settings").sections[0].items.map((item) => item.label)).toEqual(
      SETTINGS_TAB_IDS.map((id) => SETTINGS_TILE_LABELS[id]),
    );
    const destinations = new Set(sidebarLeaves(rails).map((leaf) => leaf.to));
    [
      ...salesMenuItems(allow),
      ...purchasingMenuItems(allow),
      ...inventoryMenuItems(allow),
      ...cashBankMenuItems(allow),
      ...ledgerMenuItems(allow),
      ...employeeAdminMenuItems(allow),
      ...hrPayrollMenuItems(allow),
      ...settingsMenuItems(allow),
      ...productionMenuItems(allow),
      ...taskMenuItems(allow),
    ].forEach((item) => expect(destinations.has(item.to)).toBe(true));
    expect(destinations.has("/optimasi-rute")).toBe(true);
    expect(destinations.has("/spk")).toBe(true);
    expect(destinations.has("/chat")).toBe(true);
  });

  test("groups produksi, pengiriman, penjualan, and keuangan without new routes", () => {
    const rails = modelFrom(allow, { route: true, user: { role: "admin" } });
    const produksi = group(rails, "produksi");
    expect(produksi.sections.map((section) => section.heading)).toEqual(["Jadwal", "Tugas Produksi"]);
    expect(produksi.sections[0].items.map((item) => item.label)).toEqual(["Jadwal Produksi"]);
    expect(produksi.sections[1].items.map((item) => item.label)).toEqual([
      "Designer",
      "Screener",
      "Kepala Produksi",
      "Helper",
      "Operator",
      "Packing",
    ]);
    expect(labels(group(rails, "pengiriman"))).toEqual([
      "Jadwal Pengiriman",
      "Jadwal Pengiriman Ulang",
      "Surat Jalan",
      "Loading",
      "Kurir / Terkirim",
      "Optimasi Rute",
    ]);
    expect(group(rails, "pengiriman").sections[0].items.find((item) => item.label === "Kurir / Terkirim").to).toBe("/tasks/delivery");
    expect(group(rails, "pengiriman").sections[0].items.find((item) => item.label === "Optimasi Rute").to).toBe("/optimasi-rute");
    expect(labels(group(rails, "sales"))).toContain("Pengiriman Pesanan");
    expect(labels(group(rails, "reports"))).toEqual(["Analitik Output", "Gaji Operator"]);
    expect(sidebarLeaves(rails).some((leaf) => leaf.to === "/dashboard/penjualan" && leaf.label === "Dashboard Bisnis")).toBe(true);
    expect(dashboardMenuItems(allow, { role: "admin" }).some((item) => item.label === "Dashboard Penjualan")).toBe(true);
  });

  test("hides empty parents and does not invent a keuangan permission", () => {
    expect(group(modelFrom(has(NAV_KEYS.employeeAdmin)), "employeeAdmin")).toBeNull();
    expect(group(modelFrom(has(NAV_KEYS.settings)), "settings")).toBeNull();
    expect(group(modelFrom(has()), "sales")).toBeNull();
    expect(group(modelFrom(has(NAV_KEYS.cashBank)), "keuangan")).toBeNull();
    const kas = modelFrom(has(NAV_KEYS.cashBankAccounts));
    expect(group(kas, "keuangan").sections.map((section) => section.heading)).toEqual(["Kas & Bank"]);
    const buku = modelFrom(has(NAV_KEYS.ledgerAccounts));
    expect(group(buku, "keuangan").sections.map((section) => section.heading)).toEqual(["Buku Besar"]);
    const both = modelFrom(has(NAV_KEYS.cashBankAccounts, NAV_KEYS.ledgerAccounts));
    expect(group(both, "keuangan").sections.map((section) => section.heading)).toEqual(["Kas & Bank", "Buku Besar"]);
    expect(group(modelFrom(has(NAV_KEYS.schedule)), "produksi").sections.map((section) => section.heading)).toEqual(["Jadwal"]);
    expect(group(modelFrom(has(NAV_KEYS.schedule)), "pengiriman")).toBeNull();
    expect(group(modelFrom(has(NAV_KEYS.deliverySchedule)), "produksi")).toBeNull();
    expect(labels(group(modelFrom(has(NAV_KEYS.deliverySchedule)), "pengiriman"))).toEqual(["Jadwal Pengiriman"]);
    expect(labels(group(modelFrom(has("delivery_note.manage")), "pengiriman"))).toEqual(["Surat Jalan"]);
    expect(labels(group(modelFrom(has("loading.view")), "pengiriman"))).toEqual(["Loading"]);
    const widget = modelFrom(has("dashboard.finance.view"));
    expect(labels(group(widget, "dashboard"))).toEqual(["Dashboard Bisnis"]);
    expect(group(widget, "sales")).toBeNull();
  });

  test("active rails split production from delivery and keep sales report paths", () => {
    expect(isProductionRailActive("/tasks/packing")).toBe(true);
    expect(isDeliveryRailActive("/tasks/packing")).toBe(false);
    ["/tasks/surat-jalan", "/tasks/loading", "/tasks/delivery", "/produksi/jadwal-pengiriman", "/produksi/jadwal-pengiriman/batch/1", "/produksi/jadwal-pengiriman/review-optimasi", "/produksi/jadwal-pengiriman-ulang", "/optimasi-rute", "/optimasi-rute/abc"].forEach((path) => {
      expect(isDeliveryRailActive(path)).toBe(true);
      expect(isProductionRailActive(path)).toBe(false);
    });
    expect(isProductionRailActive("/produksi")).toBe(true);
    expect(isDeliveryRailActive("/produksi")).toBe(false);
    expect(isSalesRailActive("/laporan/status-po")).toBe(true);
    expect(isSalesRailActive("/laporan/prediksi-order")).toBe(true);
    expect(isSalesRailActive("/penjualan/permintaan-produk/abc")).toBe(true);
    expect(isSalesRailActive("/penjualan/aktivitas-sales/outlet/1")).toBe(true);
    expect(isSalesRailActive("/admin/import")).toBe(true);
    expect(isOperationalReportRailActive("/laporan/status-po")).toBe(false);
    expect(isOperationalReportRailActive("/laporan/prediksi-order")).toBe(false);
    expect(isOperationalReportRailActive("/laporan/analitik-output")).toBe(true);
    expect(isFinanceRailActive("/kas-bank/daftar")).toBe(true);
    expect(isFinanceRailActive("/buku-besar/neraca")).toBe(true);
    expect(isProductionRailActive("/kas-bank/daftar")).toBe(false);
  });

  test("sidebar mode preference stays local and falls back to compact", () => {
    const storage = { value: null, getItem: () => storage.value, setItem: (_k, v) => { storage.value = v; } };
    expect(readSidebarMode(null)).toBe("compact");
    expect(readSidebarMode({ getItem: () => { throw new Error("blocked"); } })).toBe("compact");
    expect(readSidebarMode(storage)).toBe("compact");
    writeSidebarMode(storage, "expanded");
    expect(storage.value).toBe("expanded");
    expect(readSidebarMode(storage)).toBe("expanded");
    writeSidebarMode(storage, "nope");
    expect(readSidebarMode(storage)).toBe("compact");
    expect(SIDEBAR_MODE_STORAGE_KEY).toBe("ep.desktopSidebar.mode");
    expect(presentSidebarLeaf({ key: "terkirim", label: "Driver / Terkirim", to: "/tasks/delivery" }).label).toBe("Kurir / Terkirim");
  });
});
