import { NavLink } from "react-router-dom";
import { Bell, ClipboardList, Package, UserRound } from "lucide-react";
import { activeMobileNav, MOBILE_NAV_ITEMS } from "../lib/mobileWorkspaces";

const ICONS = {
  tasks: ClipboardList,
  orders: Package,
  notifications: Bell,
  profile: UserRound,
};

export default function MobileBottomNav({ pathname }) {
  const active = activeMobileNav(pathname);
  return (
    <nav className="mop-bottom-nav" aria-label="Navigasi operasional" data-testid="mobile-bottom-nav">
      {MOBILE_NAV_ITEMS.map((item) => {
        const Icon = ICONS[item.id];
        return (
          <NavLink
            key={item.id}
            to={item.to}
            className={`mop-nav-btn${active === item.id ? " is-active" : ""}`}
            data-testid={`mobile-nav-${item.id}`}
            aria-current={active === item.id ? "page" : undefined}
          >
            <Icon size={20} aria-hidden />
            {item.label}
          </NavLink>
        );
      })}
    </nav>
  );
}
