import { Download, LayoutDashboard, LogOut, Package, Settings as SettingsIcon, Table } from "lucide-react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLES } from "../lib/constants";
import BrandMark from "./BrandMark";

const SETTINGS_PERMS = ["users.view", "users.manage", "roles.manage", "permissions.manage", "audit.view"];

export default function AppShell() {
  const { user, hasPerm, logout } = useAuth();
  const navigate = useNavigate();
  // Go-Live: indikator koneksi backend/MongoDB berbasis health probe. TIDAK
  // menampilkan "Terhubung" kalau probe gagal.
  const [health, setHealth] = useState({ ok: null, checkedAt: null });

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const r = await api.systemHealth();
        if (!cancelled) setHealth({ ok: !!r?.mongo, checkedAt: r?.checked_at || null });
      } catch (_) {
        if (!cancelled) setHealth({ ok: false, checkedAt: null });
      }
    };
    check();
    const t = setInterval(check, 30000); // Reuse existing polling cadence (30s)
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const doLogout = async () => {
    await logout();
    navigate("/login");
  };

  const canOpenSettings = SETTINGS_PERMS.some((p) => hasPerm(p));

  const healthLabel = health.ok === true
    ? "Operasional · MongoDB Terhubung"
    : health.ok === false
      ? "Backend tidak terhubung"
      : "Mengecek koneksi…";
  const healthDotStyle = health.ok === false
    ? { background: "#c9302c" }
    : health.ok === true
      ? undefined
      : { background: "#c9a227" };

  const todayLabel = new Date().toLocaleDateString("id-ID", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  });

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <Link to="/" className="brand">
          <BrandMark compact onDark />
        </Link>
        <nav className="app-sidebar-nav">
          <NavLink to="/" end className={({ isActive }) => "top-nav-link" + (isActive ? " active" : "")} data-testid="top-nav-jobs">
            <Package size={16} /> Pekerjaan
          </NavLink>
          {hasPerm("dashboard.production.view") && (
            <NavLink to="/dashboard" className={({ isActive }) => "top-nav-link" + (isActive ? " active" : "")} data-testid="top-nav-dashboard">
              <LayoutDashboard size={16} /> Dashboard
            </NavLink>
          )}
          {hasPerm("schedule.view") && (
            <NavLink to="/produksi" className={({ isActive }) => "top-nav-link" + (isActive ? " active" : "")} data-testid="top-nav-produksi">
              <Table size={16} /> Jadwal Produksi
            </NavLink>
          )}
          {hasPerm("po.import.view") && (
            <NavLink to="/admin/import" className={({ isActive }) => "top-nav-link" + (isActive ? " active" : "")} data-testid="top-nav-import">
              <Download size={16} /> Import PO
            </NavLink>
          )}
          {canOpenSettings && (
            <NavLink to="/settings" className={({ isActive }) => "top-nav-link" + (isActive ? " active" : "")} data-testid="top-nav-settings">
              <SettingsIcon size={16} /> Pengaturan
            </NavLink>
          )}
        </nav>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <span className="topbar-date">{todayLabel}</span>
          <div className="sync" data-testid="sync-status" title={health.checkedAt ? `Cek terakhir: ${health.checkedAt}` : undefined}>
            <span className="live-dot" style={healthDotStyle} /> {healthLabel}
          </div>
          <div className="profile">
            <div className="profile-meta">
              <strong className="profile-name">{user?.name || user?.email}</strong>
              <span data-testid="current-user-role">{ROLES[user?.role] || user?.role}</span>
            </div>
            <button onClick={doLogout} aria-label="Keluar" data-testid="logout-button">
              <LogOut size={16} />
            </button>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
