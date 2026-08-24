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
import PublicCustomerApproval from "./pages/PublicCustomerApproval";
import "@/App.css";

const SETTINGS_PERMS = ["users.view", "users.manage", "roles.manage", "permissions.manage", "audit.view", "customer_approval.view", "customer_approval.settings", "delivery.deadline.settings", "delivery.deadline.edit", "dashboard.production.view", "schedule.edit"];

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

function PermGate({ anyOf, children }) {
  const { hasPerm } = useAuth();
  const ok = (anyOf || []).some((p) => hasPerm(p));
  if (!ok) return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<PublicOnly><Login /></PublicOnly>} />
          {/* Public — customer approval link (no login required) */}
          <Route path="/customer/design-approval/:token" element={<PublicCustomerApproval />} />
          <Route element={<Protected><AppShell /></Protected>}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/dashboard" element={<PermGate anyOf={["dashboard.production.view"]}><HoPDashboard /></PermGate>} />
            <Route path="/produksi" element={<PermGate anyOf={["schedule.view"]}><ProductionSchedule /></PermGate>} />
            <Route path="/analytics/operators" element={<PermGate anyOf={["dashboard.production.view"]}><AnalyticsOperators /></PermGate>} />
            <Route path="/admin/import" element={<PermGate anyOf={["po.import.view"]}><ImportPO /></PermGate>} />
            <Route path="/settings" element={<PermGate anyOf={SETTINGS_PERMS}><Settings /></PermGate>} />
            <Route path="/po/:id" element={<PODetail />} />
            <Route path="/po/:id/surat-jalan" element={<SuratJalan />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
