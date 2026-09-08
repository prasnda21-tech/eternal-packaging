import { NavLink } from "react-router-dom";
import { Bell, CalendarDays, ClipboardList, Package, UserRound } from "lucide-react";
import { useAuth } from "../lib/auth";
import { activeMobileNav, visibleMobileNavItems } from "../lib/mobileWorkspaces";

const ICONS = {
  tasks: ClipboardList,
  orders: Package,
  schedule: CalendarDays,
  notifications: Bell,
  profile: UserRound,
};

export default function MobileBottomNav({ pathname }) {
  const { hasPerm } = useAuth();
  const active = activeMobileNav(pathname);
  const items = visibleMobileNavItems(hasPerm);
  return (
    <nav className="mop-bottom-nav" aria-label="Navigasi operasional" data-testid="mobile-bottom-nav">
      {items.map((item) => {
        const Icon = ICONS[item.id];
        return (
          <NavLink
            key={item.id}
            to={item.to}
            className={`mop-nav-btn${active === item.id ? " is-active" : ""}`}
            data-testid={`mobile-nav-${item.id}`}
            aria-current={active === item.id ? "page" : undefined}
          >
            {Icon ? <Icon size={20} aria-hidden /> : null}
            {item.lines ? (
              <span className="mop-nav-label">
                {item.lines.map((line) => <span key={line}>{line}</span>)}
              </span>
            ) : item.label}
          </NavLink>
        );
      })}
    </nav>
  );
}
