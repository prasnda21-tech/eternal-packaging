import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./lib/auth";
import AppShell from "./components/AppShell";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import PODetail from "./pages/PODetail";
import HoPDashboard from "./pages/HoPDashboard";
import ProductionSchedule from "./pages/ProductionSchedule";
import SuratJalan from "./pages/SuratJalan";
import AnalyticsOperators from "./pages/AnalyticsOperators";
import ImportPO from "./pages/ImportPO";
import PesananPenjualan from "./pages/PesananPenjualan";
import PengirimanPesanan from "./pages/PengirimanPesanan";
import FakturPenjualan from "./pages/FakturPenjualan";
import PenerimaanPenjualan from "./pages/PenerimaanPenjualan";
import Settings from "./pages/Settings";
import MasterBarangJasa from "./pages/MasterBarangJasa";
import StockSaatIni from "./pages/StockSaatIni";
import PenerimaanBarang from "./pages/PenerimaanBarang";
import PermintaanPembelian from "./pages/PermintaanPembelian";
import PesananPembelian from "./pages/PesananPembelian";
import FakturPembelian from "./pages/FakturPembelian";
import PembayaranPembelian from "./pages/PembayaranPembelian";
import AnalitikOutput from "./pages/AnalitikOutput";
import StatusPengiriman from "./pages/StatusPengiriman";
import StatusPO from "./pages/StatusPO";
import GajiOperator from "./pages/GajiOperator";
import PrediksiOrder from "./pages/PrediksiOrder";
import SalesActivityPage from "./pages/SalesActivityPage";
import SalesActivityOutletDetail from "./pages/SalesActivityOutletDetail";
import PoWorkView from "./pages/PoWorkView";
import PublicCustomerApproval from "./pages/PublicCustomerApproval";
import CustomerTrackingPage from "./pages/CustomerTrackingPage";
import { NAV_KEYS, employeeAdminNavVisible, hrPayrollNavVisible, inventoryNavVisible, purchasingNavVisible, reportsNavVisible } from "./lib/navAccess";
import DivisionTaskPage, { DivisionTaskIndex } from "./pages/DivisionTaskPage";
import DeliverySchedulePage from "./pages/DeliverySchedulePage";
import DeliveryReschedulePage from "./pages/DeliveryReschedulePage";
import DeliveryBatchPage from "./pages/DeliveryBatchPage";
import EmployeeAdmin from "./pages/EmployeeAdmin";
import EmployeeAdminDetail from "./pages/EmployeeAdminDetail";
import HrPayroll from "./pages/HrPayroll";
import FormShowcase from "./pages/FormShowcase";
import OptimasiRute from "./pages/OptimasiRute";
import OptimasiRuteEditor from "./pages/OptimasiRuteEditor";
import "@/App.css";
import "@/ui-system.css";

const SETTINGS_PERMS = ["users.view", "users.manage", "roles.manage", "permissions.manage", "audit.view", "customer_approval.view", "customer_approval.settings", "delivery.deadline.settings", "delivery.deadline.edit", "dashboard.production.view", "schedule.edit", "settings.work_panel.view", "settings.work_panel.manage", "settings.operator_wage.view", "settings.operator_wage.manage", "reports.gaji_operator.view", "settings.depot.view", "settings.depot.manage", "settings.hpp.view", "settings.hpp.manage", "settings.customer_pricing.view", "settings.customer_pricing.manage", "settings.customer_pricing.publish", "employee_admin.settings", "payroll.settings", "settings.sales_activity.view", "settings.sales_activity.manage", "settings.route_optimizer.view", "settings.route_optimizer.manage"];

function Protected({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="loading">Memuat...</div>;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function PublicOnly({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="loading">Memuat...</div>;
  if (user) return <Navigate to="/" replace />;
  return children;
}

function PermGate({ anyOf, alsoAnyOf, children }) {
  const { hasPerm } = useAuth();
  const ok = (anyOf || []).some((p) => hasPerm(p));
  const also = !alsoAnyOf?.length || alsoAnyOf.some((p) => hasPerm(p));
  if (!ok || !also) return <Navigate to="/" replace />;
  return children;
}

function PersediaanIndex() {
  const { hasPerm } = useAuth();
  const inv = inventoryNavVisible(hasPerm);
  if (inv.masterItems) return <Navigate to="/persediaan/master-barang-jasa" replace />;
  if (inv.currentStock) return <Navigate to="/persediaan/stock-saat-ini" replace />;
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
  return <Navigate to="/" replace />;
}

function LaporanIndex() {
  const { hasPerm } = useAuth();
  const r = reportsNavVisible(hasPerm);
  if (r.analytics) return <Navigate to="/laporan/analitik-output" replace />;
  if (r.statusPo) return <Navigate to="/laporan/status-po" replace />;
  if (r.shipmentStatus) return <Navigate to="/laporan/status-pengiriman" replace />;
  if (r.gajiOperator) return <Navigate to="/laporan/gaji-operator" replace />;
  if (r.orderPrediction) return <Navigate to="/laporan/prediksi-order" replace />;
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
  return <Navigate to="/" replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<PublicOnly><Login /></PublicOnly>} />
          {/* Public — customer pages (no login required) */}
          <Route path="/customer/design-approval/:token" element={<PublicCustomerApproval />} />
          <Route path="/tracking/:token" element={<CustomerTrackingPage />} />
          <Route element={<Protected><AppShell /></Protected>}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/dashboard" element={<PermGate anyOf={[NAV_KEYS.dashboard]}><HoPDashboard /></PermGate>} />
            <Route path="/tasks" element={<DivisionTaskIndex />} />
            <Route path="/tasks/designer" element={<PermGate anyOf={[NAV_KEYS.tasksDesigner, "design.upload"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/kepala-produksi" element={<PermGate anyOf={[NAV_KEYS.tasksKepalaProduksi, "schedule.edit"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/screener" element={<PermGate anyOf={[NAV_KEYS.tasksScreener, "screen.update"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/helper" element={<PermGate anyOf={[NAV_KEYS.tasksHelper, "helper.prepare"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/operator" element={<PermGate anyOf={[NAV_KEYS.tasksOperator, "operator.update"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/packing" element={<PermGate anyOf={[NAV_KEYS.tasksPacking, "packing.update"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/surat-jalan" element={<PermGate anyOf={[NAV_KEYS.tasksSuratJalan, "delivery_note.manage"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/loading" element={<PermGate anyOf={[NAV_KEYS.tasksLoading, "loading.update"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/tasks/delivery" element={<PermGate anyOf={[NAV_KEYS.tasksDelivery, "delivery.update"]}><DivisionTaskPage /></PermGate>} />
            <Route path="/produksi/jadwal-pengiriman" element={<PermGate anyOf={[NAV_KEYS.deliverySchedule, "delivery.schedule.view", "schedule.view"]}><DeliverySchedulePage /></PermGate>} />
            <Route path="/produksi/jadwal-pengiriman/buat" element={<Navigate to="/produksi/jadwal-pengiriman" replace />} />
            <Route path="/produksi/jadwal-pengiriman/batch/:batchId" element={<PermGate anyOf={[NAV_KEYS.deliverySchedule, "delivery.schedule.view", "delivery.update"]}><DeliveryBatchPage /></PermGate>} />
            <Route path="/produksi/jadwal-pengiriman-ulang" element={<PermGate anyOf={[NAV_KEYS.deliveryReschedule, "delivery.reschedule", "delivery.reschedule.set_date"]}><DeliveryReschedulePage /></PermGate>} />
            <Route path="/produksi/pengiriman-hari-ini" element={<PermGate anyOf={[NAV_KEYS.statusPo]} alsoAnyOf={["schedule.view"]}><StatusPO /></PermGate>} />
            <Route path="/produksi/rute-saya" element={<Navigate to="/" replace />} />
            <Route path="/optimasi-rute/baru" element={<PermGate anyOf={[NAV_KEYS.routeOptimizer, "route_optimizer.create", "route_optimizer.manage"]}><OptimasiRuteEditor /></PermGate>} />
            <Route path="/optimasi-rute/:id" element={<PermGate anyOf={[NAV_KEYS.routeOptimizer, "route_optimizer.view", "route_optimizer.create", "route_optimizer.edit_own", "route_optimizer.optimize", "route_optimizer.manage"]}><OptimasiRuteEditor /></PermGate>} />
            <Route path="/optimasi-rute" element={<PermGate anyOf={[NAV_KEYS.routeOptimizer, "route_optimizer.view", "route_optimizer.create", "route_optimizer.edit_own", "route_optimizer.optimize", "route_optimizer.manage"]}><OptimasiRute /></PermGate>} />
            <Route path="/produksi" element={<PermGate anyOf={[NAV_KEYS.schedule]}><ProductionSchedule /></PermGate>} />
            <Route path="/analytics/operators" element={<PermGate anyOf={["dashboard.production.view"]}><AnalyticsOperators /></PermGate>} />
            <Route path="/admin/import" element={<PermGate anyOf={[NAV_KEYS.importPo]} alsoAnyOf={["po.import.view"]}><ImportPO /></PermGate>} />
            <Route path="/penjualan/pesanan-penjualan" element={<PermGate anyOf={[NAV_KEYS.salesOrder, NAV_KEYS.sales, "sales_order.view", "sales_order.create", "po.view"]}><PesananPenjualan /></PermGate>} />
            <Route path="/penjualan/pengiriman-pesanan" element={<PermGate anyOf={[NAV_KEYS.salesDeliveryOrder, NAV_KEYS.sales, "sales.delivery.view", "sales.delivery.create", "sales_order.view", "po.view"]}><PengirimanPesanan /></PermGate>} />
            <Route path="/penjualan/faktur-penjualan" element={<PermGate anyOf={[NAV_KEYS.salesInvoice, NAV_KEYS.sales, "sales.invoice.view", "sales.invoice.create", "sales_order.view", "po.view"]}><FakturPenjualan /></PermGate>} />
            <Route path="/penjualan/penerimaan-penjualan" element={<PermGate anyOf={[NAV_KEYS.salesReceipt, NAV_KEYS.sales, "sales.receipt.view", "sales.receipt.create", "sales.invoice.view", "sales_order.view", "po.view"]}><PenerimaanPenjualan /></PermGate>} />
            <Route path="/penjualan/aktivitas-sales/outlet/:id" element={<PermGate anyOf={[NAV_KEYS.salesActivity, "sales.activity.view", "sales.activity.create"]}><SalesActivityOutletDetail /></PermGate>} />
            <Route path="/penjualan/aktivitas-sales" element={<PermGate anyOf={[NAV_KEYS.salesActivity, "sales.activity.view", "sales.activity.create", "sales.outlet.create"]}><SalesActivityPage /></PermGate>} />
            <Route path="/penjualan" element={<Navigate to="/penjualan/pesanan-penjualan" replace />} />
            <Route path="/pembelian/permintaan" element={<PermGate anyOf={[NAV_KEYS.purchaseRequests, NAV_KEYS.purchasing, "purchase_requests.view", "purchase_requests.create"]}><PermintaanPembelian /></PermGate>} />
            <Route path="/pembelian/pesanan-pembelian" element={<PermGate anyOf={[NAV_KEYS.purchaseOrders, NAV_KEYS.purchasing, "purchase_orders.view", "purchase_orders.create"]}><PesananPembelian /></PermGate>} />
            <Route path="/pembelian/faktur-pembelian" element={<PermGate anyOf={[NAV_KEYS.purchaseInvoices, NAV_KEYS.purchasing, "purchase_invoices.view", "purchase_invoices.create"]}><FakturPembelian /></PermGate>} />
            <Route path="/pembelian/pembayaran-pembelian" element={<PermGate anyOf={[NAV_KEYS.purchasePayments, NAV_KEYS.purchasing, "purchase_payments.view", "purchase_payments.create"]}><PembayaranPembelian /></PermGate>} />
            <Route path="/pembelian/penerimaan-barang" element={<PermGate anyOf={[NAV_KEYS.purchaseReceipts]} alsoAnyOf={["purchase_receipts.view"]}><PenerimaanBarang /></PermGate>} />
            <Route path="/pembelian" element={<PembelianIndex />} />
            <Route path="/persediaan/master-barang-jasa" element={<PermGate anyOf={[NAV_KEYS.masterItems]} alsoAnyOf={["master_items.view"]}><MasterBarangJasa /></PermGate>} />
            <Route path="/persediaan/stock-saat-ini" element={<PermGate anyOf={[NAV_KEYS.currentStock]} alsoAnyOf={["current_stock.view"]}><StockSaatIni /></PermGate>} />
            <Route path="/persediaan" element={<PersediaanIndex />} />
            <Route path="/laporan/analitik-output" element={<PermGate anyOf={[NAV_KEYS.analytics]} alsoAnyOf={["schedule.view"]}><AnalitikOutput /></PermGate>} />
            <Route path="/laporan/status-po" element={<PermGate anyOf={[NAV_KEYS.statusPo]} alsoAnyOf={["schedule.view"]}><StatusPO /></PermGate>} />
            <Route path="/laporan/status-pengiriman" element={<PermGate anyOf={[NAV_KEYS.shipmentStatus]} alsoAnyOf={["schedule.view"]}><StatusPengiriman /></PermGate>} />
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
            <Route path="/settings" element={<PermGate anyOf={[NAV_KEYS.settings]} alsoAnyOf={SETTINGS_PERMS}><Settings /></PermGate>} />
            <Route path="/po/:id/kerja" element={<PermGate anyOf={["work_panel.view", "operator.update", "po.view"]}><PoWorkView /></PermGate>} />
            <Route path="/po/:id" element={<PODetail />} />
            <Route path="/po/:id/surat-jalan" element={<SuratJalan />} />
          </Route>
          {/* Dev-only showcase (no auth needed for dev preview) */}
          <Route path="/dev/form-showcase" element={<FormShowcase />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
