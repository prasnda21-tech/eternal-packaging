import { CalendarDays, ClipboardList, Package, Route, Truck, UserRound, X } from "lucide-react";
import { useEffect } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { activeMobileNav, visibleMobileNavItems } from "../lib/mobileWorkspaces";
import { useMobileNav } from "./MobileNavContext";

const ICONS = {
  tasks: ClipboardList,
  orders: Package,
  schedule: CalendarDays,
  deliverySchedule: Truck,
  optimasiRute: Route,
  profile: UserRound,
};

export default function MobileSidebar({ pathname }) {
  const { hasPerm } = useAuth();
  const { menuOpen, closeMenu } = useMobileNav();
  const active = activeMobileNav(pathname);
  const items = visibleMobileNavItems(hasPerm);

  useEffect(() => {
    closeMenu();
  }, [pathname, closeMenu]);

  if (!menuOpen) return null;

  return (
    <div
      className="mop-sidebar-backdrop"
      data-testid="mobile-sidebar-backdrop"
      onClick={closeMenu}
    >
      <aside
        className="mop-sidebar"
        role="dialog"
        aria-modal="true"
        aria-label="Menu navigasi"
        data-testid="mobile-sidebar"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="mop-sidebar-head">
          <h2>Menu</h2>
          <button
            type="button"
            className="mop-menu-close"
            aria-label="Tutup menu"
            data-testid="mobile-sidebar-close"
            onClick={closeMenu}
          >
            <X size={18} aria-hidden />
          </button>
        </header>
        <nav className="mop-sidebar-list" aria-label="Navigasi operasional" data-testid="mobile-sidebar-list">
          {items.map((item) => {
            const Icon = ICONS[item.id];
            const isActive = active === item.id;
            return (
              <NavLink
                key={item.id}
                to={item.to}
                className={`mop-sidebar-item${isActive ? " is-active" : ""}`}
                data-testid={`mobile-nav-${item.id}`}
                aria-current={isActive ? "page" : undefined}
                onClick={closeMenu}
              >
                {Icon ? <Icon size={18} aria-hidden /> : null}
                <span className="mop-sidebar-item-label">{item.label}</span>
              </NavLink>
            );
          })}
        </nav>
      </aside>
    </div>
  );
}
