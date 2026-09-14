/** Ops-queue header for /m/schedule and /m/delivery-schedule. Presentation only. */

import { Menu } from "lucide-react";
import { useAuth } from "../../lib/auth";
import { useMobileNav } from "../MobileNavContext";

function formatOpsClock(now = new Date()) {
  const date = new Intl.DateTimeFormat("id-ID", {
    timeZone: "Asia/Jakarta",
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(now);
  const time = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Jakarta",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(now);
  return { date, time };
}

function adminLabel(user) {
  const name = String(user?.name || user?.full_name || user?.email || "Admin").trim();
  const roleRaw = String(user?.role_name || user?.role || "").trim();
  const role = !roleRaw || /^admin$/i.test(roleRaw) ? "Operational Team" : roleRaw;
  const initial = (name.replace(/^[^A-Za-z0-9]+/, "")[0] || "A").toUpperCase();
  return { name, role, initial };
}

export default function OpsQueueHeader({
  title,
  subtitle = "Dari Produksi ke Pengiriman, Semua Lebih Terarah.",
}) {
  const { menuOpen, toggleMenu } = useMobileNav();
  const { user } = useAuth();
  const clock = formatOpsClock();
  const admin = adminLabel(user);
  const online = typeof navigator === "undefined" || navigator.onLine !== false;

  return (
    <header className="ops-queue-header" data-testid="mobile-topbar">
      <button
        type="button"
        className={`mop-topbar-menu${menuOpen ? " is-open" : ""}`}
        data-testid="mobile-nav-menu"
        aria-label="Menu"
        aria-expanded={menuOpen}
        aria-controls="mobile-sidebar"
        onClick={toggleMenu}
      >
        <Menu size={18} aria-hidden />
      </button>
      <div className="ops-qh-brand">
        <span className="ops-qh-mark" aria-hidden="true">E</span>
        <div className="ops-qh-brand-copy">
          <p className="ops-qh-brand-name">ETERNAL PACKAGING</p>
          <p className="ops-qh-brand-kicker">Operation on hand</p>
        </div>
      </div>
      <span className="ops-qh-divider" aria-hidden="true" />
      <div className="ops-qh-title-block">
        <h1 className="ops-qh-title" data-testid="mobile-topbar-title">{title}</h1>
        <p className="ops-qh-sub">{subtitle}</p>
      </div>
      <div className="ops-qh-clock">
        <p className="ops-qh-date">{clock.date}</p>
        <p>
          <span className="ops-qh-time">{clock.time}</span>
          <span className="ops-qh-online">
            <span className={`ops-qh-online-dot${online ? "" : " is-off"}`} aria-hidden="true" />
            {online ? "Sistem Online" : "Sistem Offline"}
          </span>
        </p>
      </div>
      <div className="ops-qh-admin">
        <span className="ops-qh-avatar" aria-hidden="true">{admin.initial}</span>
        <div>
          <p className="ops-qh-admin-name">{admin.name}</p>
          <p className="ops-qh-admin-role">{admin.role}</p>
        </div>
      </div>
    </header>
  );
}
