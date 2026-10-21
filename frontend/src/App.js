import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Protected as AuthProtected, PublicOnly as AuthPublicOnly } from "./components/AppBootScreen";
import WorkspaceChunkFallback from "./components/WorkspaceChunkFallback";
import { AuthProvider, useAuth } from "./lib/auth";
import MobileOperationalLayout from "./mobile/MobileOperationalLayout";
import MobileTasksPage from "./mobile/MobileTasksPage";
import Login from "./pages/Login";
import { canOpenWidgetDashboard } from "./components/widgetDashboard/widgetDashboardCatalog";
import { canAccessOwnerDashboard } from "./lib/ownerAnalytics";
import { widgetDashboardAliasTarget, widgetDashboardFinanceTarget } from "./components/widgetDashboard/widgetDashboardLocation";
import { NAV_KEYS, cashBankNavVisible, employeeAdminNavVisible, hrPayrollNavVisible, inventoryNavVisible, ledgerNavVisible, purchasingNavVisible, reportsNavVisible } from "./lib/navAccess";
import { inventoryValueRouteAllowed } from "./lib/inventoryValueLink";
import { SPK_ACCESS_KEYS } from "./lib/spk";
import "./lib/productRequestLeaveGuard";
import { isCustomerDesignApprovalPath, isSalesPwaRole } from "./lib/salesPwa";
import "@/App.css";
import "@/ui-system.css";

const Settings = lazy(() => import("./pages/Settings"));
const FakturPenjualan = lazy(() => import("./pages/FakturPenjualan"));
const PengirimanPesanan = lazy(() => import("./pages/PengirimanPesanan"));
const PenerimaanPenjualan = lazy(() => import("./pages/PenerimaanPenjualan"));
const DataPelanggan = lazy(() => import("./pages/DataPelanggan"));
const ProductRequestForm = lazy(() => import("./pages/ProductRequestForm"));
const ProductRequestList = lazy(() => import("./pages/ProductRequestList"));
const CrmDailyReportList = lazy(() => import("./pages/CrmDailyReportList"));
const CrmDailyReportForm = lazy(() => import("./pages/CrmDailyReportForm"));
const ImportPO = lazy(() => import("./pages/ImportPO"));
const AkunPerkiraan = lazy(() => import("./pages/AkunPerkiraan"));
const JurnalUmum = lazy(() => import("./pages/JurnalUmum"));
const SaldoAwal = lazy(() => import("./pages/SaldoAwal"));
const PeriodeAkuntansi = lazy(() => import("./pages/PeriodeAkuntansi"));
const BukuBesar = lazy(() => import("./pages/BukuBesar"));
const PiutangUsaha = lazy(() => import("./pages/PiutangUsaha"));
const HutangUsaha = lazy(() => import("./pages/HutangUsaha"));
const NeracaSaldo = lazy(() => import("./pages/NeracaSaldo"));
const LabaRugi = lazy(() => import("./pages/LabaRugi"));
const Neraca = lazy(() => import("./pages/Neraca"));
const DaftarKasBank = lazy(() => import("./pages/DaftarKasBank"));
const PenerimaanKasBank = lazy(() => import("./pages/PenerimaanKasBank"));
const PembayaranKasBank = lazy(() => import("./pages/PembayaranKasBank"));
const TransferKasBank = lazy(() => import("./pages/TransferKasBank"));
const MutasiKasBank = lazy(() => import("./pages/MutasiKasBank"));
const RekonsiliasiOwnerKasBank = lazy(() => import("./pages/RekonsiliasiOwnerKasBank"));
const RekonsiliasiKasBank = lazy(() => import("./pages/RekonsiliasiKasBank"));
const HrPayroll = lazy(() => import("./pages/HrPayroll"));
const PayrollReconciliation = lazy(() => import("./pages/PayrollReconciliation"));
const EmployeeAdmin = lazy(() => import("./pages/EmployeeAdmin"));
const EmployeeAdminDetail = lazy(() => import("./pages/EmployeeAdminDetail"));
const AnalitikOutput = lazy(() => import("./pages/AnalitikOutput"));
const AnalyticsOperators = lazy(() => import("./pages/AnalyticsOperators"));
const StatusPO = lazy(() => import("./pages/StatusPO"));
const GajiOperator = lazy(() => import("./pages/GajiOperator"));
const PrediksiOrder = lazy(() => import("./pages/PrediksiOrder"));
const OptimasiRute = lazy(() => import("./pages/OptimasiRute"));
const OptimasiRuteEditor = lazy(() => import("./pages/OptimasiRuteEditor"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const PODetail = lazy(() => import("./pages/PODetail"));
const HoPDashboard = lazy(() => import("./pages/HoPDashboard"));
const OwnerDashboard = lazy(() => import("./pages/OwnerDashboard"));
const WidgetDashboard = lazy(() => import("./pages/WidgetDashboard"));
const ProductionSchedule = lazy(() => import("./pages/ProductionSchedule"));
const SuratJalan = lazy(() => import("./pages/SuratJalan"));
const PesananPenjualan = lazy(() => import("./pages/PesananPenjualan"));
const MasterBarangJasa = lazy(() => import("./pages/MasterBarangJasa"));
const ProductDetail = lazy(() => import("./pages/ProductDetail"));
const CabangGudang = lazy(() => import("./pages/CabangGudang"));
const StockSaatIni = lazy(() => import("./pages/StockSaatIni"));
const NilaiPersediaan = lazy(() => import("./pages/NilaiPersediaan"));
const StockOpname = lazy(() => import("./pages/StockOpname"));
const PenerimaanBarang = lazy(() => import("./pages/PenerimaanBarang"));
const PermintaanPembelian = lazy(() => import("./pages/PermintaanPembelian"));
const PesananPembelian = lazy(() => import("./pages/PesananPembelian"));
const UangMukaPembelian = lazy(() => import("./pages/UangMukaPembelian"));
const ReturPembelian = lazy(() => import("./pages/ReturPembelian"));
const FakturPembelian = lazy(() => import("./pages/FakturPembelian"));
const PembayaranPembelian = lazy(() => import("./pages/PembayaranPembelian"));
const HppPembelian = lazy(() => import("./pages/HppPembelian"));
const SalesActivityPage = lazy(() => import("./pages/SalesActivityPage"));
const SalesActivityOutletDetail = lazy(() => import("./pages/SalesActivityOutletDetail"));
const PoWorkView = lazy(() => import("./pages/PoWorkView"));
const PublicCustomerApproval = lazy(() => import("./pages/PublicCustomerApproval"));
const CustomerTrackingPage = lazy(() => import("./pages/CustomerTrackingPage"));
const PublicHargaPage = lazy(() => import("./pages/PublicHargaPage"));
const DivisionTaskPage = lazy(() => import("./pages/DivisionTaskPage"));
const DivisionTaskIndex = lazy(() => import("./pages/DivisionTaskPage").then((mod) => ({ default: mod.DivisionTaskIndex })));
const KepalaProduksiKpiDetailPage = lazy(() => import("./pages/KepalaProduksiKpiDetailPage"));
const DeliverySchedulePage = lazy(() => import("./pages/DeliverySchedulePage"));
const DeliveryScheduleRouteReviewPage = lazy(() => import("./pages/DeliveryScheduleRouteReviewPage"));
const DeliveryReschedulePage = lazy(() => import("./pages/DeliveryReschedulePage"));
const DeliveryBatchPage = lazy(() => import("./pages/DeliveryBatchPage"));
const FormShowcase = lazy(() => import("./pages/FormShowcase"));
const ChatPage = lazy(() => import("./pages/ChatPage"));
const SpkDashboard = lazy(() => import("./pages/SpkDashboard"));
const SpkCreate = lazy(() => import("./pages/SpkCreate"));
const SpkDetail = lazy(() => import("./pages/SpkDetail"));
const AppShell = lazy(() => import("./components/AppShell"));
const RouteOptimizerLayout = lazy(() => import("./mobile/RouteOptimizerLayout"));
const MobileOrdersPage = lazy(() => import("./mobile/MobileOrdersPage"));
const MobileOrderDetailPage = lazy(() => import("./mobile/MobileOrderDetailPage"));
const MobileSuratJalanViewerPage = lazy(() => import("./mobile/MobileSuratJalanViewerPage"));
const MobileProductionSchedulePage = lazy(() => import("./mobile/MobileProductionSchedulePage"));
const MobileDeliverySchedulePage = lazy(() => import("./mobile/MobileDeliverySchedulePage"));
const MobileDeliveryRouteReviewPage = lazy(() => import("./mobile/MobileDeliveryRouteReviewPage"));
const MobileNotificationsPage = lazy(() => import("./mobile/MobileNotificationsPage"));
const MobileProfilePage = lazy(() => import("./mobile/MobileProfilePage"));
const MobileChatPage = lazy(() => import("./mobile/MobileChatPage"));
const MobileChatRoomPage = lazy(() => import("./mobile/MobileChatRoomPage"));
const SalesPwaLayout = lazy(() => import("./sales/SalesPwaLayout"));
const SalesPwaDashboardPage = lazy(() => import("./sales/SalesPwaDashboardPage"));
const SalesPwaOrdersPage = lazy(() => import("./sales/SalesPwaOrdersPage"));
const SalesPwaVisitsPage = lazy(() => import("./sales/SalesPwaVisitsPage"));
const SalesPwaOrderDetailPage = lazy(() => import("./sales/SalesPwaOrderDetailPage"));
const SalesPwaDocumentViewerPage = lazy(() => import("./sales/SalesPwaDocumentViewerPage"));
const SalesPwaCustomersPage = lazy(() => import("./sales/SalesPwaCustomersPage"));
const SalesPwaInvoicesPage = lazy(() => import("./sales/SalesPwaInvoicesPage"));
const SalesPwaInvoiceDetailPage = lazy(() => import("./sales/SalesPwaInvoiceDetailPage"));
const SalesPwaReceivablesPage = lazy(() => import("./sales/SalesPwaReceivablesPage"));
const SalesPwaPredictionsPage = lazy(() => import("./sales/SalesPwaPredictionsPage"));
const SalesPwaProfilePage = lazy(() => import("./sales/SalesPwaProfilePage"));

const SETTINGS_PERMS = ["users.view", "users.manage", "roles.manage", "permissions.manage", "audit.view", "customer_approval.view", "customer_approval.settings", "delivery.deadline.settings", "delivery.deadline.edit", "dashboard.production.view", "schedule.edit", "settings.work_panel.view", "settings.work_panel.manage", "settings.operator_wage.view", "settings.operator_wage.manage", "reports.gaji_operator.view", "settings.depot.view", "settings.depot.manage", "settings.hpp.view", "settings.hpp.manage", "settings.sales_accounting.view", "settings.sales_accounting.manage", "payroll_accounting.view", "payroll_accounting.manage", "settings.customer_pricing.view", "settings.customer_pricing.manage", "settings.customer_pricing.publish", "settings.public_catalog.view", "settings.public_catalog.manage", "employee_admin.settings", "payroll.settings", "settings.sales_activity.view", "settings.sales_activity.manage", "settings.route_optimizer.view", "settings.route_optimizer.manage", "settings.sales_user_mapping.view", "settings.sales_user_mapping.manage", "settings.sales_master.view", "settings.sales_master.manage", "settings.crm_daily_report.view", "settings.crm_daily_report.manage"];
const PHYSICAL_STOCK_SETTINGS_PERMS = ["current_stock.view", "current_stock.update", "permissions.manage"];
const SETTINGS_HUB_PERMS = [...SETTINGS_PERMS, "current_stock.view", "current_stock.update"];
const PhysicalStockSettingsRoute = Settings;

function SalesEternalRedirect() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading || !user) return null;
  if (!isSalesPwaRole(user)) return null;
  if (location.pathname === "/login") return null;
  if (isCustomerDesignApprovalPath(location.pathname)) return null;
  if (location.pathname === "/sales" || location.pathname.startsWith("/sales/")) return null;
  return <Navigate to="/sales" replace />;
}

function SalesPwaGate({ children }) {
  const { user } = useAuth();
  if (!isSalesPwaRole(user)) return <Navigate to="/" replace />;
  return children;
}

function Protected(props) {
  return <AuthProtected {...props} />;
}

function PublicOnly(props) {
  return <AuthPublicOnly {...props} />;
}

function PermGate({ anyOf, alsoAnyOf, children }) {
  const { hasPerm } = useAuth();
  const ok = (anyOf || []).some((p) => hasPerm(p));
  const also = !alsoAnyOf?.length || alsoAnyOf.some((p) => hasPerm(p));
  if (!ok || !also) return <Navigate to="/" replace />;
  return children;
}

function WidgetDashboardGate({ children }) {
  const { user, hasPerm } = useAuth();
  if (!canOpenWidgetDashboard(user, hasPerm)) return <Navigate to="/" replace />;
  return children;
}

function OwnerDashboardGate({ children }) {
  const { user, hasPerm } = useAuth();
  if (!canAccessOwnerDashboard(user, hasPerm)) return <Navigate to="/" replace />;
  return children;
}

function WidgetDashboardAlias() {
  const location = useLocation();
  return (
    <WidgetDashboardGate>
      <Navigate to={widgetDashboardAliasTarget(location)} replace />
    </WidgetDashboardGate>
  );
}

function WidgetDashboardFinanceAlias() {
  const location = useLocation();
  return (
    <WidgetDashboardGate>
      <Navigate to={widgetDashboardFinanceTarget(location)} replace />
    </WidgetDashboardGate>
  );
}

function NilaiPersediaanGate({ children }) {
  const { hasPerm } = useAuth();
  if (!inventoryValueRouteAllowed(hasPerm)) return <Navigate to="/" replace />;
  return children;
}

function PersediaanIndex() {
  const { hasPerm } = useAuth();
  const inv = inventoryNavVisible(hasPerm);
  if (inv.masterItems) return <Navigate to="/persediaan/master-barang-jasa" replace />;
  if (inv.branchesWarehouses) return <Navigate to="/persediaan/cabang-gudang" replace />;
  if (inv.currentStock) return <Navigate to="/persediaan/stock-saat-ini" replace />;
  if (inv.inventoryValue) return <Navigate to="/persediaan/nilai-persediaan" replace />;
  return <Navigate to="/" replace />;
}

function PembelianIndex() {
  const { hasPerm } = useAuth();
  const buy = purchasingNavVisible(hasPerm);
  if (buy.requests) return <Navigate to="/pembelian/permintaan" replace />;
  if (buy.orders) return <Navigate to="/pembelian/pesanan-pembelian" replace />;
  if (buy.invoices) return <Navigate to="/pembelian/faktur-pembelian" replace />;
  if (buy.payments) return <Navigate to="/pembelian/pembayaran-pembelian" replace />;
  if (buy.receipts) return <Navigate to="/pembelian/penerimaan-barang" replace />;
  if (buy.hpp) return <Navigate to="/pembelian/hpp-pembelian" replace />;
  return <Navigate to="/" replace />;
}

function LaporanIndex() {
  const { hasPerm } = useAuth();
  const r = reportsNavVisible(hasPerm);
  if (r.analytics) return <Navigate to="/laporan/analitik-output" replace />;
  if (r.statusPo) return <Navigate to="/laporan/status-po" replace />;
  if (r.gajiOperator) return <Navigate to="/laporan/gaji-operator" replace />;
  if (r.orderPrediction) return <Navigate to="/laporan/prediksi-order" replace />;
  return <Navigate to="/" replace />;
}

function BukuBesarIndex() {
  const { hasPerm } = useAuth();
  const v = ledgerNavVisible(hasPerm);
  if (v.accounts) return <Navigate to="/buku-besar/akun-perkiraan" replace />;
  if (v.generalJournal) return <Navigate to="/buku-besar/jurnal-umum" replace />;
  if (v.openingBalance) return <Navigate to="/buku-besar/saldo-awal" replace />;
  if (v.accountingPeriods) return <Navigate to="/buku-besar/periode-akuntansi" replace />;
  if (v.generalLedger) return <Navigate to="/buku-besar/buku-besar" replace />;
  if (v.accountsReceivable) return <Navigate to="/buku-besar/piutang-usaha" replace />;
  if (v.accountsPayable) return <Navigate to="/buku-besar/hutang-usaha" replace />;
  if (v.trialBalance) return <Navigate to="/buku-besar/neraca-saldo" replace />;
  if (v.profitLoss) return <Navigate to="/buku-besar/laba-rugi" replace />;
  if (v.balanceSheet) return <Navigate to="/buku-besar/neraca" replace />;
  return <Navigate to="/" replace />;
}

function KasBankIndex() {
  const { hasPerm } = useAuth();
  const v = cashBankNavVisible(hasPerm);
  if (v.accounts) return <Navigate to="/kas-bank/daftar" replace />;
  if (v.receipts) return <Navigate to="/kas-bank/penerimaan" replace />;
  if (v.payments) return <Navigate to="/kas-bank/pembayaran" replace />;
  if (v.transfers) return <Navigate to="/kas-bank/transfer" replace />;
  if (v.mutasi) return <Navigate to="/kas-bank/mutasi" replace />;
  if (v.reconciliation) return <Navigate to="/kas-bank/rekonsiliasi" replace />;
  if (v.reconOwner) return <Navigate to="/kas-bank/rekonsiliasi-owner" replace />;
  return <Navigate to="/" replace />;
}

function AdministrasiIndex() {
  const { hasPerm } = useAuth();
  const v = employeeAdminNavVisible(hasPerm);
  if (v.mine) return <Navigate to="/administrasi-karyawan/pengajuan-saya" replace />;
  if (v.create) return <Navigate to="/administrasi-karyawan/buat" replace />;
  if (v.approve) return <Navigate to="/administrasi-karyawan/persetujuan" replace />;
  if (v.archive) return <Navigate to="/administrasi-karyawan/arsip" replace />;
  return <Navigate to="/" replace />;
}

function HrPayrollIndex() {
  const { hasPerm } = useAuth();
  const v = hrPayrollNavVisible(hasPerm);
  if (v.employees) return <Navigate to="/hr-payroll/daftar-karyawan" replace />;
  if (v.employment) return <Navigate to="/hr-payroll/data-kepegawaian" replace />;
  if (v.payroll) return <Navigate to="/hr-payroll/penggajian" replace />;
  if (v.payslips) return <Navigate to="/hr-payroll/slip-gaji" replace />;
  if (v.components) return <Navigate to="/hr-payroll/komponen-gaji" replace />;
  if (v.history) return <Navigate to="/hr-payroll/riwayat" replace />;
  if (v.reconciliation) return <Navigate to="/hr-payroll/rekonsiliasi" replace />;
  return <Navigate to="/" replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <SalesEternalRedirect />
        <Suspense fallback={<WorkspaceChunkFallback />}>
        <Routes>
          <Route path="/login" element={<PublicOnly><Login /></PublicOnly>} />
          {/* Public — customer pages (no login required) */}
          <Route path="/customer/design-approval/:token" element={<PublicCustomerApproval />} />
          <Route path="/tracking/:token" element={<CustomerTrackingPage />} />
          <Route path="/t/:customerSlug/:poSlug/:code" element={<CustomerTrackingPage />} />
          <Route path="/harga" element={<PublicHargaPage />} />
          <Route path="/harga/:groupPublicId" element={<PublicHargaPage />} />
          <Route path="/produk/:slug" element={<PublicHargaPage />} />
          <Route element={<Protected><MobileOperationalLayout /></Protected>}>
            <Route path="/m" element={<Navigate to="/m/tasks" replace />} />
            <Route path="/m/tasks" element={<MobileTasksPage />} />
            <Route path="/m/orders" element={<MobileOrdersPage />} />
            <Route path="/m/orders/:id" element={<MobileOrderDetailPage />} />
            <Route path="/m/orders/:id/surat-jalan/:fileId" element={<MobileSuratJalanViewerPage />} />
            <Route path="/m/schedule" element={<MobileProductionSchedulePage />} />
            <Route path="/m/delivery-schedule" element={<MobileDeliverySchedulePage />} />
            <Route path="/m/delivery-schedule/review-optimasi" element={<MobileDeliveryRouteReviewPage />} />
            <Route path="/m/notifications" element={<MobileNotificationsPage />} />
            <Route path="/m/chat" element={<MobileChatPage />} />
            <Route path="/m/chat/:roomId" element={<MobileChatRoomPage />} />
            <Route path="/m/profile" element={<MobileProfilePage />} />
          </Route>
          <Route element={<Protected><SalesPwaGate><SalesPwaLayout /></SalesPwaGate></Protected>}>
            <Route path="/sales" element={<SalesPwaDashboardPage />} />
            <Route path="/sales/po" element={<SalesPwaOrdersPage />} />
            <Route path="/sales/po/:id" element={<SalesPwaOrderDetailPage />} />
            <Route path="/sales/po/:id/dokumen/:kind/:documentId" element={<SalesPwaDocumentViewerPage />} />
            <Route path="/sales/pelanggan" element={<SalesPwaCustomersPage />} />
            <Route path="/sales/invoices" element={<SalesPwaInvoicesPage />} />
            <Route path="/sales/invoices/:id" element={<SalesPwaInvoiceDetailPage />} />
            <Route path="/sales/receivables" element={<SalesPwaReceivablesPage />} />
            <Route path="/sales/visit" element={<SalesPwaVisitsPage />} />
            <Route path="/sales/prediksi" element={<SalesPwaPredictionsPage />} />
            <Route path="/sales/prediksi/:customerKey" element={<SalesPwaPredictionsPage />} />
            <Route path="/sales/profil" element={<SalesPwaProfilePage />} />
          </Route>
          {/* Optimasi Rute: same paths for desktop + PWA; adaptive shell keeps standalone in-app. */}
          <Route element={<Protected><RouteOptimizerLayout /></Protected>}>
            <Route path="/optimasi-rute/baru" element={<PermGate anyOf={[NAV_KEYS.routeOptimizer, "route_optimizer.create", "route_optimizer.manage"]}><OptimasiRuteEditor /></PermGate>} />
            <Route path="/optimasi-rute/:id" element={<PermGate anyOf={[NAV_KEYS.routeOptimizer, "route_optimizer.view", "route_optimizer.create", "route_optimizer.edit_own", "route_optimizer.optimize", "route_optimizer.manage"]}><OptimasiRuteEditor /></PermGate>} />
            <Route path="/optimasi-rute" element={<PermGate anyOf={[NAV_KEYS.routeOptimizer, "route_optimizer.view", "route_optimizer.create", "route_optimizer.edit_own", "route_optimizer.optimize", "route_optimizer.manage"]}><OptimasiRute /></PermGate>} />
          </Route>
          <Route element={<Protected><AppShell /></Protected>}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/spk/baru" element={<PermGate anyOf={SPK_ACCESS_KEYS}><SpkCreate /></PermGate>} />
            <Route path="/spk/:id/ubah" element={<PermGate anyOf={SPK_ACCESS_KEYS}><SpkCreate mode="edit" /></PermGate>} />
            <Route path="/spk/:id" element={<PermGate anyOf={SPK_ACCESS_KEYS}><SpkDetail /></PermGate>} />
            <Route path="/spk" element={<PermGate anyOf={SPK_ACCESS_KEYS}><SpkDashboard /></PermGate>} />
            <Route path="/chat" element={<PermGate anyOf={[NAV_KEYS.chat, "chat.use"]}><ChatPage /></PermGate>} />
            <Route path="/chat/:roomId" element={<PermGate anyOf={[NAV_KEYS.chat, "chat.use"]}><ChatPage /></PermGate>} />
            <Route path="/dashboard/keuangan" element={<WidgetDashboardFinanceAlias />} />
            <Route path="/dashboard/penjualan" element={<WidgetDashboardGate><WidgetDashboard /></WidgetDashboardGate>} />
            <Route path="/dashboard/widget" element={<WidgetDashboardAlias />} />
            <Route path="/dashboard/owner" element={<OwnerDashboardGate><OwnerDashboard /></OwnerDashboardGate>} />
            <Route path="/dashboard" element={<PermGate anyOf={[NAV_KEYS.dashboard]}><HoPDashboard /></PermGate>} />
            <Route path="/tasks" element={<DivisionTaskIndex />} />
            <Route path="/tasks/designer" element={<PermGate anyOf={[NAV_KEYS.tasksDesigner, "design.upload"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/kepala-produksi/prioritas/:kpi" element={<PermGate anyOf={[NAV_KEYS.tasksKepalaProduksi, "schedule.edit"]}><KepalaProduksiKpiDetailPage /></PermGate>} />
            <Route path="/tasks/kepala-produksi" element={<PermGate anyOf={[NAV_KEYS.tasksKepalaProduksi, "schedule.edit"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/screener" element={<PermGate anyOf={[NAV_KEYS.tasksScreener, "screen.update"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/helper" element={<PermGate anyOf={[NAV_KEYS.tasksHelper, "helper.prepare"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/operator" element={<PermGate anyOf={[NAV_KEYS.tasksOperator, "operator.update"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/packing" element={<PermGate anyOf={[NAV_KEYS.tasksPacking, "packing.update"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/surat-jalan" element={<PermGate anyOf={[NAV_KEYS.tasksSuratJalan, "delivery_note.manage"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/loading" element={<PermGate anyOf={[NAV_KEYS.tasksLoading, "loading.update"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/delivery" element={<PermGate anyOf={[NAV_KEYS.tasksDelivery, "delivery.update"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/produksi/jadwal-pengiriman" element={<PermGate anyOf={[NAV_KEYS.deliverySchedule, "delivery.schedule.view", "schedule.view"]}><DeliverySchedulePage /></PermGate>} />
            <Route path="/produksi/jadwal-pengiriman/review-optimasi" element={<PermGate anyOf={["route_optimizer.create", "route_optimizer.optimize", "route_optimizer.manage"]}><DeliveryScheduleRouteReviewPage /></PermGate>} />
            <Route path="/produksi/jadwal-pengiriman/buat" element={<Navigate to="/produksi/jadwal-pengiriman" replace />} />
            <Route path="/produksi/jadwal-pengiriman/batch/:batchId" element={<PermGate anyOf={[NAV_KEYS.deliverySchedule, "delivery.schedule.view", "delivery.update"]}><DeliveryBatchPage /></PermGate>} />
            <Route path="/produksi/jadwal-pengiriman-ulang" element={<PermGate anyOf={[NAV_KEYS.deliveryReschedule, "delivery.reschedule", "delivery.reschedule.set_date"]}><DeliveryReschedulePage /></PermGate>} />
            <Route path="/produksi/pengiriman-hari-ini" element={<Navigate to="/produksi/jadwal-pengiriman?status=hari_ini" replace />} />
            <Route path="/produksi/rute-saya" element={<Navigate to="/" replace />} />
            <Route path="/produksi" element={<PermGate anyOf={[NAV_KEYS.schedule]}><ProductionSchedule /></PermGate>} />
            <Route path="/analytics/operators" element={<PermGate anyOf={["dashboard.production.view"]}><AnalyticsOperators /></PermGate>} />
            <Route path="/admin/import" element={<PermGate anyOf={[NAV_KEYS.importPo]} alsoAnyOf={["po.import.view"]}><ImportPO /></PermGate>} />
            <Route path="/penjualan/data-pelanggan" element={<PermGate anyOf={[NAV_KEYS.salesCustomers, NAV_KEYS.sales, "sales.customer.view", "sales.customer.manage", "sales_order.view", "sales_order.create", "po.view"]}><DataPelanggan /></PermGate>} />
            <Route path="/penjualan/permintaan-produk/baru" element={<PermGate anyOf={["product_request.create"]}><ProductRequestForm /></PermGate>} />
            <Route path="/penjualan/permintaan-produk/:id" element={<ProductRequestForm />} />
            <Route path="/penjualan/permintaan-produk" element={<ProductRequestList />} />
            <Route path="/penjualan/pesanan-penjualan" element={<PermGate anyOf={[NAV_KEYS.salesOrder, NAV_KEYS.sales, "sales_order.view", "sales_order.create", "po.view"]}><PesananPenjualan /></PermGate>} />
            <Route path="/penjualan/pengiriman-pesanan" element={<PermGate anyOf={[NAV_KEYS.salesDeliveryOrder, NAV_KEYS.sales, "sales.delivery.view", "sales.delivery.create", "sales_order.view", "po.view"]}><PengirimanPesanan /></PermGate>} />
            <Route path="/penjualan/faktur-penjualan" element={<PermGate anyOf={[NAV_KEYS.salesInvoice, NAV_KEYS.sales, "sales.invoice.view", "sales.invoice.create", "sales_order.view", "po.view"]}><FakturPenjualan /></PermGate>} />
            <Route path="/penjualan/penerimaan-penjualan" element={<PermGate anyOf={[NAV_KEYS.salesReceipt, NAV_KEYS.sales, "sales.receipt.view", "sales.receipt.create", "sales.invoice.view", "sales_order.view", "po.view"]}><PenerimaanPenjualan /></PermGate>} />
            <Route path="/penjualan/aktivitas-sales/outlet/:id" element={<PermGate anyOf={[NAV_KEYS.salesActivity, "sales.activity.view", "sales.activity.create"]}><SalesActivityOutletDetail /></PermGate>} />
            <Route path="/penjualan/aktivitas-sales" element={<PermGate anyOf={[NAV_KEYS.salesActivity, "sales.activity.view", "sales.activity.create", "sales.outlet.create"]}><SalesActivityPage /></PermGate>} />
            <Route path="/penjualan/laporan-harian-crm/baru" element={<PermGate anyOf={["crm.daily_report.create"]}><CrmDailyReportForm /></PermGate>} />
            <Route path="/penjualan/laporan-harian-crm/:id" element={<PermGate anyOf={["crm.daily_report.view_own", "crm.daily_report.view_all", "crm.daily_report.create", "crm.daily_report.edit_own", NAV_KEYS.crmDailyReport]}><CrmDailyReportForm /></PermGate>} />
            <Route path="/penjualan/laporan-harian-crm" element={<PermGate anyOf={["crm.daily_report.view_own", "crm.daily_report.view_all", "crm.daily_report.create", "crm.daily_report.edit_own", NAV_KEYS.crmDailyReport]}><CrmDailyReportList /></PermGate>} />
            <Route path="/penjualan" element={<Navigate to="/penjualan/pesanan-penjualan" replace />} />
            <Route path="/pembelian/permintaan" element={<PermGate anyOf={["purchase_requests.view", "purchase_requests.create"]}><PermintaanPembelian /></PermGate>} />
            <Route path="/pembelian/pesanan-pembelian" element={<PermGate anyOf={["purchase_orders.view", "purchase_orders.create"]}><PesananPembelian /></PermGate>} />
            <Route path="/pembelian/uang-muka" element={<PermGate anyOf={["purchase_orders.view", "purchase_orders.create", "purchase_payments.view", "purchase_payments.create"]}><UangMukaPembelian /></PermGate>} />
            <Route path="/pembelian/faktur-pembelian" element={<PermGate anyOf={["purchase_invoices.view", "purchase_invoices.create"]}><FakturPembelian /></PermGate>} />
            <Route path="/pembelian/pembayaran-pembelian" element={<PermGate anyOf={["purchase_payments.view", "purchase_payments.create"]}><PembayaranPembelian /></PermGate>} />
            <Route path="/pembelian/penerimaan-barang" element={<PermGate anyOf={["purchase_receipts.view"]}><PenerimaanBarang /></PermGate>} />
            <Route path="/pembelian/retur" element={<PermGate anyOf={["purchase_receipts.view"]}><ReturPembelian /></PermGate>} />
            <Route path="/pembelian/hpp-pembelian" element={<PermGate anyOf={["purchase_hpp.view"]}><HppPembelian /></PermGate>} />
            <Route path="/pembelian" element={<PembelianIndex />} />
            <Route path="/persediaan/master-barang-jasa" element={<PermGate anyOf={[NAV_KEYS.masterItems]} alsoAnyOf={["master_items.view"]}><MasterBarangJasa /></PermGate>} />
            <Route path="/persediaan/master-barang-jasa/:id" element={<PermGate anyOf={[NAV_KEYS.masterItems]} alsoAnyOf={["master_items.view"]}><ProductDetail /></PermGate>} />
            <Route path="/persediaan/cabang-gudang" element={<PermGate anyOf={[NAV_KEYS.branchesWarehouses]} alsoAnyOf={["org.branches.view", "org.warehouses.view"]}><CabangGudang /></PermGate>} />
            <Route path="/persediaan/stock-saat-ini" element={<PermGate anyOf={[NAV_KEYS.currentStock]} alsoAnyOf={["current_stock.view"]}><StockSaatIni /></PermGate>} />
            <Route path="/persediaan/nilai-persediaan" element={<NilaiPersediaanGate><NilaiPersediaan /></NilaiPersediaanGate>} />
            <Route path="/persediaan/stock-opname" element={<PermGate anyOf={[NAV_KEYS.currentStock]} alsoAnyOf={["current_stock.update", "current_stock.view"]}><StockOpname /></PermGate>} />
            <Route path="/persediaan" element={<PersediaanIndex />} />
            <Route path="/buku-besar/akun-perkiraan" element={<PermGate anyOf={[NAV_KEYS.ledgerAccounts, NAV_KEYS.ledger, "ledger.accounts.view", "ledger.accounts.manage"]}><AkunPerkiraan /></PermGate>} />
            <Route path="/buku-besar/jurnal-umum" element={<PermGate anyOf={[NAV_KEYS.ledgerGeneralJournal, NAV_KEYS.ledger, "general_journal.view", "general_journal.manage", "general_journal.post", "general_journal.reverse"]}><JurnalUmum /></PermGate>} />
            <Route path="/buku-besar/saldo-awal" element={<PermGate anyOf={[NAV_KEYS.ledgerOpeningBalance, NAV_KEYS.ledger, "opening_balance.view", "opening_balance.manage", "opening_balance.post", "opening_balance.reverse"]}><SaldoAwal /></PermGate>} />
            <Route path="/buku-besar/periode-akuntansi" element={<PermGate anyOf={[NAV_KEYS.ledgerAccountingPeriods, NAV_KEYS.ledger, "accounting.period.view", "accounting.period.close", "accounting.period.reopen"]}><PeriodeAkuntansi /></PermGate>} />
            <Route path="/buku-besar/buku-besar" element={<PermGate anyOf={[NAV_KEYS.ledgerGeneralLedger, NAV_KEYS.ledger, "general_ledger.view"]}><BukuBesar /></PermGate>} />
            <Route path="/buku-besar/piutang-usaha" element={<PermGate anyOf={[NAV_KEYS.ledgerAccountsReceivable, "accounts_receivable.view"]}><PiutangUsaha /></PermGate>} />
            <Route path="/buku-besar/hutang-usaha" element={<PermGate anyOf={[NAV_KEYS.ledgerAccountsPayable, "accounts_payable.view"]}><HutangUsaha /></PermGate>} />
            <Route path="/buku-besar/neraca-saldo" element={<PermGate anyOf={[NAV_KEYS.ledgerTrialBalance, NAV_KEYS.ledger, "trial_balance.view"]}><NeracaSaldo /></PermGate>} />
            <Route path="/buku-besar/laba-rugi" element={<PermGate anyOf={[NAV_KEYS.ledgerProfitLoss, NAV_KEYS.ledger, "profit_loss.view"]}><LabaRugi /></PermGate>} />
            <Route path="/buku-besar/neraca" element={<PermGate anyOf={[NAV_KEYS.ledgerBalanceSheet, NAV_KEYS.ledger, "balance_sheet.view"]}><Neraca /></PermGate>} />
            <Route path="/buku-besar" element={<BukuBesarIndex />} />
            <Route path="/kas-bank/daftar" element={<PermGate anyOf={[NAV_KEYS.cashBankAccounts, NAV_KEYS.cashBank, "cash_bank.view", "cash_bank.manage"]}><DaftarKasBank /></PermGate>} />
            <Route path="/kas-bank/penerimaan" element={<PermGate anyOf={[NAV_KEYS.cashBankReceipts, NAV_KEYS.cashBank, "cash_bank.view", "cash_bank.manage"]}><PenerimaanKasBank /></PermGate>} />
            <Route path="/kas-bank/pembayaran" element={<PermGate anyOf={[NAV_KEYS.cashBankPayments, NAV_KEYS.cashBank, "cash_bank.view", "cash_bank.manage"]}><PembayaranKasBank /></PermGate>} />
            <Route path="/kas-bank/transfer" element={<PermGate anyOf={[NAV_KEYS.cashBankTransfers, NAV_KEYS.cashBank, "cash_bank.view", "cash_bank.manage"]}><TransferKasBank /></PermGate>} />
            <Route path="/kas-bank/mutasi" element={<PermGate anyOf={[NAV_KEYS.cashBankMutasi, NAV_KEYS.cashBank, "cash_bank.view"]}><MutasiKasBank /></PermGate>} />
            <Route path="/kas-bank/rekonsiliasi" element={<PermGate anyOf={[NAV_KEYS.cashBankReconciliation, NAV_KEYS.cashBank, "cash_bank.view", "cash_bank.reconcile"]}><RekonsiliasiKasBank /></PermGate>} />
            <Route path="/kas-bank/rekonsiliasi-owner" element={<PermGate anyOf={[NAV_KEYS.cashBankReconOwner, NAV_KEYS.cashBank, "cash_bank.view"]}><RekonsiliasiOwnerKasBank /></PermGate>} />
            <Route path="/kas-bank" element={<KasBankIndex />} />
            <Route path="/laporan/analitik-output" element={<PermGate anyOf={[NAV_KEYS.analytics]} alsoAnyOf={["schedule.view"]}><AnalitikOutput /></PermGate>} />
            <Route path="/laporan/status-po" element={<PermGate anyOf={[NAV_KEYS.statusPo]} alsoAnyOf={["schedule.view"]}><StatusPO /></PermGate>} />
            <Route path="/laporan/status-pengiriman" element={<Navigate to="/produksi/jadwal-pengiriman" replace />} />
            <Route path="/laporan/gaji-operator" element={<PermGate anyOf={[NAV_KEYS.gajiOperator]} alsoAnyOf={["reports.gaji_operator.view", "schedule.view"]}><GajiOperator /></PermGate>} />
            <Route path="/laporan/prediksi-order" element={<PermGate anyOf={[NAV_KEYS.orderPrediction]} alsoAnyOf={["reports.order_prediction.view"]}><PrediksiOrder /></PermGate>} />
            <Route path="/laporan" element={<LaporanIndex />} />
            <Route path="/administrasi-karyawan" element={<AdministrasiIndex />} />
            <Route path="/administrasi-karyawan/pengajuan-saya" element={<PermGate anyOf={[NAV_KEYS.employeeAdminMine, NAV_KEYS.employeeAdmin, "employee_admin.view_own", "employee_admin.submit"]}><EmployeeAdmin mode="mine" /></PermGate>} />
            <Route path="/administrasi-karyawan/buat" element={<PermGate anyOf={[NAV_KEYS.employeeAdminCreate, "employee_admin.submit"]}><EmployeeAdmin mode="create" /></PermGate>} />
            <Route path="/administrasi-karyawan/persetujuan" element={<PermGate anyOf={[NAV_KEYS.employeeAdminApprove, "employee_admin.approve"]}><EmployeeAdmin mode="approve" /></PermGate>} />
            <Route path="/administrasi-karyawan/arsip" element={<PermGate anyOf={[NAV_KEYS.employeeAdminArchive, "employee_admin.document_view", "employee_admin.view_all"]}><EmployeeAdmin mode="archive" /></PermGate>} />
            <Route path="/administrasi-karyawan/pengajuan/:id" element={<PermGate anyOf={[NAV_KEYS.employeeAdmin, NAV_KEYS.employeeAdminMine, NAV_KEYS.employeeAdminApprove, NAV_KEYS.employeeAdminArchive, "employee_admin.view_own", "employee_admin.submit", "employee_admin.approve", "employee_admin.view_all", "employee_admin.document_view"]}><EmployeeAdminDetail /></PermGate>} />
            <Route path="/hr-payroll" element={<HrPayrollIndex />} />
            <Route path="/hr-payroll/daftar-karyawan" element={<PermGate anyOf={[NAV_KEYS.hrPayrollEmployees, NAV_KEYS.hrPayroll, "hr.employee.view", "hr.employee.manage"]}><HrPayroll mode="employees" /></PermGate>} />
            <Route path="/hr-payroll/data-kepegawaian" element={<PermGate anyOf={[NAV_KEYS.hrPayrollEmployment, NAV_KEYS.hrPayroll, "hr.employee.view", "hr.employee.manage"]}><HrPayroll mode="employment" /></PermGate>} />
            <Route path="/hr-payroll/penggajian" element={<PermGate anyOf={[NAV_KEYS.hrPayrollPayroll, NAV_KEYS.hrPayroll, "payroll.view", "payroll.calculate", "payroll.review", "payroll.approve", "payroll.finalize"]}><HrPayroll mode="payroll" /></PermGate>} />
            <Route path="/hr-payroll/slip-gaji" element={<PermGate anyOf={[NAV_KEYS.hrPayrollPayslips, NAV_KEYS.hrPayroll, "payroll.payslip.view_own", "payroll.payslip.view_all"]}><HrPayroll mode="payslips" /></PermGate>} />
            <Route path="/hr-payroll/komponen-gaji" element={<PermGate anyOf={[NAV_KEYS.hrPayrollComponents, NAV_KEYS.hrPayroll, "payroll.settings", "payroll.view"]}><HrPayroll mode="components" /></PermGate>} />
            <Route path="/hr-payroll/riwayat" element={<PermGate anyOf={[NAV_KEYS.hrPayrollHistory, NAV_KEYS.hrPayroll, "payroll.view", "payroll.payslip.view_all"]}><HrPayroll mode="history" /></PermGate>} />
            <Route path="/hr-payroll/rekonsiliasi" element={<PermGate anyOf={[NAV_KEYS.hrPayrollReconciliation, "payroll.view"]} alsoAnyOf={["payroll_payment.view"]}><PayrollReconciliation /></PermGate>} />
            <Route path="/settings/physical-stock" element={<PermGate anyOf={[NAV_KEYS.settings]} alsoAnyOf={PHYSICAL_STOCK_SETTINGS_PERMS}><PhysicalStockSettingsRoute /></PermGate>} />
            <Route path="/settings/:tab" element={<PermGate anyOf={[NAV_KEYS.settings]} alsoAnyOf={SETTINGS_PERMS}><Settings /></PermGate>} />
            <Route path="/settings" element={<PermGate anyOf={[NAV_KEYS.settings]} alsoAnyOf={SETTINGS_HUB_PERMS}><Settings /></PermGate>} />
            <Route path="/po/:id/kerja" element={<PermGate anyOf={["work_panel.view", "operator.update", "po.view"]}><PoWorkView /></PermGate>} />
            <Route path="/po/:id" element={<PODetail />} />
            <Route path="/po/:id/surat-jalan" element={<SuratJalan />} />
          </Route>
          {/* Dev-only showcase (no auth needed for dev preview) */}
          <Route path="/dev/form-showcase" element={<FormShowcase />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
      </BrowserRouter>
    </AuthProvider>
  );
}

