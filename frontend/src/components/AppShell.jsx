import { Download, LayoutDashboard, LogOut, Package, Settings as SettingsIcon, Table } from "lucide-react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLES } from "../lib/constants";

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

  return (
    <div className="app-shell">
      <header className="topbar">
        <Link to="/" className="brand">
          <span>EP</span>
          <strong>Eternal Packaging</strong>
        </Link>
        <nav className="top-nav">
          <NavLink to="/" end className={({ isActive }) => "top-nav-link" + (isActive ? " active" : "")} data-testid="top-nav-jobs">
            <Package size={15} /> Pekerjaan
          </NavLink>
          {hasPerm("dashboard.production.view") && (
            <NavLink to="/dashboard" className={({ isActive }) => "top-nav-link" + (isActive ? " active" : "")} data-testid="top-nav-dashboard">
              <LayoutDashboard size={15} /> Dashboard
            </NavLink>
          )}
          {hasPerm("schedule.view") && (
            <NavLink to="/produksi" className={({ isActive }) => "top-nav-link" + (isActive ? " active" : "")} data-testid="top-nav-produksi">
              <Table size={15} /> Jadwal Produksi
            </NavLink>
          )}
          {hasPerm("po.import.view") && (
            <NavLink to="/admin/import" className={({ isActive }) => "top-nav-link" + (isActive ? " active" : "")} data-testid="top-nav-import">
              <Download size={15} /> Import PO
            </NavLink>
          )}
          {canOpenSettings && (
            <NavLink to="/settings" className={({ isActive }) => "top-nav-link" + (isActive ? " active" : "")} data-testid="top-nav-settings">
              <SettingsIcon size={15} /> Pengaturan
            </NavLink>
          )}
        </nav>
        <div className="sync" data-testid="sync-status" title={health.checkedAt ? `Cek terakhir: ${health.checkedAt}` : undefined}>
          <span className="live-dot" style={healthDotStyle} /> {healthLabel}
        </div>
        <div className="profile">
          <span data-testid="current-user-role">{ROLES[user?.role] || user?.role}</span>
          <button onClick={doLogout} aria-label="Keluar" data-testid="logout-button">
            <LogOut size={18} />
          </button>
        </div>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
