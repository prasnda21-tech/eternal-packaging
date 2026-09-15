import fs from "fs";
import path from "path";
import { activeMobileNav } from "../lib/mobileWorkspaces";
import { loginPathForLocation, postLoginPath } from "../lib/mobileAuthRedirect";

describe("mobile route wiring", () => {
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const layout = fs.readFileSync(path.join(__dirname, "../mobile/MobileOperationalLayout.jsx"), "utf8");
  const detail = fs.readFileSync(path.join(__dirname, "../mobile/MobileOrderDetailPage.jsx"), "utf8");
  const delivery = fs.readFileSync(path.join(__dirname, "../mobile/workspaces/MobileDeliveryWorkspace.jsx"), "utf8");
  const screener = fs.readFileSync(path.join(__dirname, "../mobile/workspaces/MobileScreenerWorkspace.jsx"), "utf8");
  const packing = fs.readFileSync(path.join(__dirname, "../mobile/workspaces/MobilePackingWorkspace.jsx"), "utf8");
  const print = fs.readFileSync(path.join(__dirname, "../mobile/workspaces/MobilePrintWorkspace.jsx"), "utf8");
  const loading = fs.readFileSync(path.join(__dirname, "../mobile/workspaces/MobileLoadingWorkspace.jsx"), "utf8");
  const ordersPage = fs.readFileSync(path.join(__dirname, "../mobile/MobileOrdersPage.jsx"), "utf8");
  const tasksPage = fs.readFileSync(path.join(__dirname, "../mobile/MobileTasksPage.jsx"), "utf8");
  const schedulePage = fs.readFileSync(path.join(__dirname, "../mobile/MobileProductionSchedulePage.jsx"), "utf8");
  const mobileSchedule = fs.readFileSync(path.join(__dirname, "../lib/mobileSchedule.js"), "utf8");
  const sidebar = fs.readFileSync(path.join(__dirname, "../mobile/MobileSidebar.jsx"), "utf8");
  const topBar = fs.readFileSync(path.join(__dirname, "../mobile/MobileTopBar.jsx"), "utf8");

  test("mobile namespace is outside AppShell", () => {
    expect(app).toMatch(/path="\/m\/tasks"/);
    expect(app).toMatch(/path="\/m\/orders"/);
    expect(app).toMatch(/path="\/m\/orders\/:id"/);
    expect(app).toMatch(/path="\/m\/schedule"/);
    expect(app).toMatch(/path="\/m\/delivery-schedule"/);
    expect(app).toMatch(/path="\/m\/delivery-schedule\/review-optimasi"/);
    expect(app).toMatch(/MobileDeliveryRouteReviewPage/);
    expect(app).toMatch(/path="\/m\/notifications"/);
    expect(app).toMatch(/path="\/m\/profile"/);
    expect(app).toMatch(/MobileOperationalLayout/);
    expect(app).toMatch(/loginPathForLocation/);
    expect(app).toMatch(/postLoginPath/);
    const mobileIdx = app.indexOf("MobileOperationalLayout");
    const shellIdx = app.indexOf("<AppShell");
    expect(mobileIdx).toBeGreaterThan(-1);
    expect(shellIdx).toBeGreaterThan(mobileIdx);
  });

  test("desktop routes remain unchanged", () => {
    expect(app).toMatch(/path="\/po\/:id"/);
    expect(app).toMatch(/path="\/tasks\/helper"/);
    expect(app).not.toMatch(/path="\/" element=\{<Navigate to="\/m/);
  });

  test("unauthorized workspace cannot render another role action panel", () => {
    expect(detail).toMatch(/selectMobileWorkspace/);
    expect(detail).toMatch(/hasPerm\(selected\.permission\)/);
    expect(detail).toMatch(/WORKSPACE_VIEWS\[selected\.id\]/);
    expect(detail).toMatch(/MobileTaskModal/);
    expect(detail).toMatch(/onRequestClose/);
    expect(detail).toMatch(/getPwaDeliveryTaskPreviewOrder/);
  });

  test("order detail topbar uses customer primary title and PO secondary meta", () => {
    expect(detail).toMatch(/getCanonicalCustomerName\(order\)/);
    expect(detail).toMatch(/meta=\{order\?\.po_number/);
    expect(detail).not.toMatch(/title=\{order\?\.po_number/);
    expect(topBar).toMatch(/mop-topbar-meta/);
    expect(topBar).toMatch(/mobile-topbar-title/);
  });

  test("top-left Menu opens left sidebar navigation", () => {
    expect(activeMobileNav("/m/tasks")).toBe("tasks");
    expect(activeMobileNav("/m/schedule")).toBe("schedule");
    expect(activeMobileNav("/m/delivery-schedule")).toBe("deliverySchedule");
    expect(activeMobileNav("/optimasi-rute/iro-1")).toBe("optimasiRute");
    expect(layout).toMatch(/MobileSidebar/);
    expect(layout).toMatch(/MobileNavProvider/);
    expect(layout).toMatch(/markMobileOpsShell/);
    expect(layout).not.toMatch(/MobileBottomNav/);
    expect(topBar).toMatch(/mobile-nav-menu/);
    expect(topBar).toMatch(/Menu/);
    expect(sidebar).toMatch(/mop-sidebar/);
    expect(sidebar).toMatch(/Jadwal Pengiriman|deliverySchedule/);
    expect(sidebar).toMatch(/optimasiRute/);
    expect(sidebar).not.toMatch(/mop-menu-popup/);
    expect(app).toMatch(/RouteOptimizerLayout/);
  });

  test("delivery schedule page reuses canonical schedule API and production sheet zoom", () => {
    const dsPage = fs.readFileSync(path.join(__dirname, "../mobile/MobileDeliverySchedulePage.jsx"), "utf8");
    const dsLib = fs.readFileSync(path.join(__dirname, "../lib/mobileDeliverySchedule.js"), "utf8");
    expect(dsPage).toMatch(/api\.deliverySchedule/);
    expect(dsPage).toMatch(/matchesDeliveryScheduleFilters/);
    expect(dsPage).toMatch(/EMPTY_DELIVERY_FILTERS/);
    expect(dsPage).toMatch(/useProductionSheetZoom/);
    expect(dsPage).toMatch(/production-sheet-viewport/);
    expect(dsPage).toMatch(/canViewMobileDeliverySchedule/);
    expect(dsLib).toMatch(/canEditMobileDeliveryScheduleLocation/);
    expect(dsPage).toMatch(/mobileScheduleWorkHref/);
    expect(dsPage).toMatch(/OPS_QUICK_FILTERS|mds-quick/);
    expect(dsPage).toMatch(/qty_packed|PackingCell|packingProgressText/);
    expect(dsPage).toMatch(/MOBILE_DELIVERY_FILTER_KEY|writeJsonSession/);
    expect(dsLib).toMatch(/delivery\.location\.manage/);
    expect(dsLib).toMatch(/NAV_KEYS\.deliverySchedule/);
    expect(dsPage).not.toMatch(/roleName\s*===\s*['"]driver['"]/);
    expect(app).toMatch(/MobileDeliverySchedulePage/);
  });

  test("PWA customer labels never fall back to outlet", () => {
    expect(detail).toMatch(/getCanonicalCustomerName\(order\)/);
    expect(detail).not.toMatch(/customer_name \|\| order\.outlet/);
    expect(delivery).toMatch(/getCanonicalCustomerName\(order\)/);
    expect(delivery).not.toMatch(/customer_name \|\| order\.outlet/);
    expect(ordersPage).toMatch(/getCanonicalCustomerName\(o, ""\)/);
    expect(ordersPage).not.toMatch(/customer_name \|\| o\.outlet/);
  });

  test("delivery confirm uses existing contract", () => {
    expect(delivery).toMatch(/confirmDeliveredBlockReason/);
    expect(delivery).toMatch(/uploadDeliveryRecipientSignature/);
    expect(delivery).toMatch(/uploadDeliveryDriverSignature/);
    expect(delivery).toMatch(/confirmDelivered/);
    expect(delivery).not.toMatch(/\/delivered\?/);
  });

  test("delivery proof offers camera and gallery without capture on gallery", () => {
    expect(delivery).toMatch(/mobile-delivery-camera-input/);
    expect(delivery).toMatch(/mobile-delivery-gallery-input/);
    expect(delivery).toMatch(/Pilih dari Galeri/);
    expect(delivery).toMatch(/Ambil Foto/);
    expect(delivery).toMatch(/capture="environment"/);
    const galleryIdx = delivery.indexOf("mobile-delivery-gallery-input");
    const galleryBlock = delivery.slice(galleryIdx - 220, galleryIdx + 80);
    expect(galleryBlock).not.toMatch(/capture=/);
  });

  test("screener metrics omit Sisa while other workspaces keep it", () => {
    expect(screener).toMatch(/label: "Target"/);
    expect(screener).toMatch(/label: "Selesai"/);
    expect(screener).not.toMatch(/label: "Sisa"/);
    expect(screener).toMatch(/mop-metrics--2col/);
    expect(packing).toMatch(/label: "Sisa"/);
    expect(print).toMatch(/label: "Sisa"/);
    expect(loading).toMatch(/Remaining Qty/);
    expect(loading).toMatch(/Item non-cetak — Packing dilewati/);
    expect(loading).toMatch(/loadingAvailability/);
    expect(loading).toMatch(/avail\.blocked/);
  });

  test("orders list uses canonical listOrders and cannot stay loading after error", () => {
    expect(ordersPage).toMatch(/api\.listOrders\("aktif", \{ view: "summary" \}\)/);
    expect(ordersPage).not.toMatch(/api\.getOrder\(/);
    expect(ordersPage).toMatch(/formatApiError/);
    expect(ordersPage).toMatch(/Coba Lagi/);
    expect(ordersPage).toMatch(/Belum ada pesanan\./);
    expect(ordersPage).toMatch(/if \(!cancelled\) setLoading\(false\)/);
  });

  test("schedule page uses production dashboard once and cannot stay loading after error", () => {
    expect(schedulePage).toMatch(/api\.productionDashboard/);
    expect(schedulePage).toMatch(/mobileProductionDashboardParams/);
    expect(mobileSchedule).toMatch(/view: "summary"/);
    expect(schedulePage).not.toMatch(/api\.getOrder\(/);
    expect(schedulePage).toMatch(/formatApiError/);
    expect(schedulePage).toMatch(/Coba Lagi/);
    expect(schedulePage).toMatch(/Belum ada jadwal produksi\./);
    expect(schedulePage).toMatch(/if \(!cancelled\) setLoading\(false\)/);
    expect(schedulePage).toMatch(/canViewMobileSchedule/);
    expect(schedulePage).toMatch(/production-sheet-table/);
    expect(schedulePage).toMatch(/MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS/);
    expect(schedulePage).toMatch(/applyProductionFilters/);
    expect(schedulePage).toMatch(/FILTER_DEFAULTS/);
    expect(schedulePage).not.toMatch(/className="mop-card"/);
    expect(schedulePage).not.toMatch(/mobile-schedule-prev/);
    expect(schedulePage).not.toMatch(/pada tanggal ini/);
    expect(app).toMatch(/path="\/m" element=\{<Navigate to="\/m\/tasks"/);
  });

  test("schedule badge zoom is shared spreadsheet CSS, not a page-zoom API", () => {
    const css = fs.readFileSync(path.join(__dirname, "../productionSheet.css"), "utf8");
    const appCss = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
    const mobileCss = fs.readFileSync(path.join(__dirname, "./mobile.css"), "utf8");
    expect(css).toMatch(/\.production-sheet-table \.urgent-badge/);
    expect(css).toMatch(/\.production-sheet-table \.standard-badge/);
    expect(css).toMatch(/\.production-sheet-table \.pill\.sm/);
    expect(css).toMatch(/font-size:\s*calc\(9px \* var\(--production-sheet-zoom/);
    expect(css).toMatch(/padding:\s*calc\(1px \* var\(--production-sheet-zoom/);
    expect(css).not.toMatch(/transform:\s*scale\(/);
    expect(appCss).toMatch(/\.urgent-badge,\.standard-badge,\.status\{font:10\.5px 'IBM Plex Mono'/);
    expect(appCss).not.toMatch(/mop-sched-zoom/);
    expect(mobileCss).not.toMatch(/mop-schedule-zoom-btn/);
    expect(mobileCss).not.toMatch(/--mop-sched-zoom/);
  });

  test("task navigation uses compact Pilih Tugas dropdown, not horizontal chips", () => {
    expect(tasksPage).toMatch(/MobileWorkspaceSelect/);
    expect(tasksPage).toMatch(/permittedMobileWorkspaces/);
    expect(tasksPage).not.toMatch(/mop-ws-tabs/);
    expect(detail).toMatch(/MobileWorkspaceSelect/);
    expect(detail).toMatch(/po\.view/);
    expect(detail).toMatch(/mobile-po-ringkasan/);
    expect(detail).toMatch(/Ringkasan PO/);
    const selectSrc = fs.readFileSync(path.join(__dirname, "../mobile/components/MobileWorkspaceSelect.jsx"), "utf8");
    expect(selectSrc).toMatch(/Pilih Tugas/);
  });

  test("tasks page uses one division-tasks request and sorts without extra fetches", () => {
    expect(tasksPage).toMatch(/api\.divisionTasks/);
    expect(tasksPage).toMatch(/sortOperationalTasks/);
    expect(tasksPage).not.toMatch(/api\.getOrder\(/);
    expect(tasksPage).not.toMatch(/productionDashboard/);
  });

  test("auth redirect helpers used by router", () => {
    expect(loginPathForLocation("/m/tasks", "")).toContain("next=");
    expect(postLoginPath("/m/profile")).toBe("/m/profile");
    expect(postLoginPath("/dashboard")).toBe("/");
  });
});
