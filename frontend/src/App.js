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
import Settings from "./pages/Settings";
import MasterBarangJasa from "./pages/MasterBarangJasa";
import StockSaatIni from "./pages/StockSaatIni";
import PenerimaanBarang from "./pages/PenerimaanBarang";
import AnalitikOutput from "./pages/AnalitikOutput";
import StatusPengiriman from "./pages/StatusPengiriman";
import StatusPO from "./pages/StatusPO";
import GajiOperator from "./pages/GajiOperator";
import PoWorkView from "./pages/PoWorkView";
import PublicCustomerApproval from "./pages/PublicCustomerApproval";
import CustomerTrackingPage from "./pages/CustomerTrackingPage";
import { NAV_KEYS, inventoryNavVisible, purchasingNavVisible, reportsNavVisible } from "./lib/navAccess";
import DivisionTaskPage, { DivisionTaskIndex } from "./pages/DivisionTaskPage";
import DeliverySchedulePage from "./pages/DeliverySchedulePage";
import DeliveryReschedulePage from "./pages/DeliveryReschedulePage";
import CreateDeliverySchedulePage from "./pages/CreateDeliverySchedulePage";
import DeliveryBatchPage from "./pages/DeliveryBatchPage";
import MyRoutePage from "./pages/MyRoutePage";
import "@/App.css";

const SETTINGS_PERMS = ["users.view", "users.manage", "roles.manage", "permissions.manage", "audit.view", "customer_approval.view", "customer_approval.settings", "delivery.deadline.settings", "delivery.deadline.edit", "dashboard.production.view", "schedule.edit", "settings.work_panel.view", "settings.work_panel.manage", "settings.operator_wage.view", "settings.operator_wage.manage", "reports.gaji_operator.view", "settings.depot.view", "settings.depot.manage"];

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
            <Route path="/produksi/jadwal-pengiriman/buat" element={<PermGate anyOf={["delivery.schedule.create"]}><CreateDeliverySchedulePage /></PermGate>} />
            <Route path="/produksi/jadwal-pengiriman/batch/:batchId" element={<PermGate anyOf={[NAV_KEYS.deliverySchedule, NAV_KEYS.driverRoute, "delivery.schedule.view", "delivery.update"]}><DeliveryBatchPage /></PermGate>} />
            <Route path="/produksi/jadwal-pengiriman-ulang" element={<PermGate anyOf={[NAV_KEYS.deliveryReschedule, "delivery.reschedule", "delivery.reschedule.set_date"]}><DeliveryReschedulePage /></PermGate>} />
            <Route path="/produksi/rute-saya" element={<PermGate anyOf={[NAV_KEYS.driverRoute, "delivery.update"]}><MyRoutePage /></PermGate>} />
            <Route path="/produksi" element={<PermGate anyOf={[NAV_KEYS.schedule]}><ProductionSchedule /></PermGate>} />
            <Route path="/analytics/operators" element={<PermGate anyOf={["dashboard.production.view"]}><AnalyticsOperators /></PermGate>} />
            <Route path="/admin/import" element={<PermGate anyOf={[NAV_KEYS.importPo]} alsoAnyOf={["po.import.view"]}><ImportPO /></PermGate>} />
            <Route path="/pembelian/penerimaan-barang" element={<PermGate anyOf={[NAV_KEYS.purchaseReceipts]} alsoAnyOf={["purchase_receipts.view"]}><PenerimaanBarang /></PermGate>} />
            <Route path="/pembelian" element={<PembelianIndex />} />
            <Route path="/persediaan/master-barang-jasa" element={<PermGate anyOf={[NAV_KEYS.masterItems]} alsoAnyOf={["master_items.view"]}><MasterBarangJasa /></PermGate>} />
            <Route path="/persediaan/stock-saat-ini" element={<PermGate anyOf={[NAV_KEYS.currentStock]} alsoAnyOf={["current_stock.view"]}><StockSaatIni /></PermGate>} />
            <Route path="/persediaan" element={<PersediaanIndex />} />
            <Route path="/laporan/analitik-output" element={<PermGate anyOf={[NAV_KEYS.analytics]} alsoAnyOf={["schedule.view"]}><AnalitikOutput /></PermGate>} />
            <Route path="/laporan/status-po" element={<PermGate anyOf={[NAV_KEYS.statusPo]} alsoAnyOf={["schedule.view"]}><StatusPO /></PermGate>} />
            <Route path="/laporan/status-pengiriman" element={<PermGate anyOf={[NAV_KEYS.shipmentStatus]} alsoAnyOf={["schedule.view"]}><StatusPengiriman /></PermGate>} />
            <Route path="/laporan/gaji-operator" element={<PermGate anyOf={[NAV_KEYS.gajiOperator]} alsoAnyOf={["reports.gaji_operator.view", "schedule.view"]}><GajiOperator /></PermGate>} />
            <Route path="/laporan" element={<LaporanIndex />} />
            <Route path="/settings" element={<PermGate anyOf={[NAV_KEYS.settings]} alsoAnyOf={SETTINGS_PERMS}><Settings /></PermGate>} />
            <Route path="/po/:id/kerja" element={<PermGate anyOf={["work_panel.view", "operator.update", "po.view"]}><PoWorkView /></PermGate>} />
            <Route path="/po/:id" element={<PODetail />} />
            <Route path="/po/:id/surat-jalan" element={<SuratJalan />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
