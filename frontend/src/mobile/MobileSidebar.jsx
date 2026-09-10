import { Bell, CalendarDays, ClipboardList, MessageCircle, Package, Truck, UserRound, X } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../lib/auth";
import api from "../lib/api";
import { activeMobileNav, visibleMobileNavItems } from "../lib/mobileWorkspaces";
import { useMobileNav } from "./MobileNavContext";

const ICONS = {
  tasks: ClipboardList,
  orders: Package,
  schedule: CalendarDays,
  deliverySchedule: Truck,
  chat: MessageCircle,
  notifications: Bell,
  profile: UserRound,
};

export default function MobileSidebar({ pathname }) {
  const { hasPerm } = useAuth();
  const { menuOpen, closeMenu } = useMobileNav();
  const active = activeMobileNav(pathname);
  const items = visibleMobileNavItems(hasPerm);
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await api.chatUnreadSummary();
        if (!cancelled) setUnread(Number(res?.total_unread || 0));
      } catch {
        if (!cancelled) setUnread(0);
      }
    };
    load();
    const t = setInterval(load, 30000);
    return () => { cancelled = true; clearInterval(t); };
  }, [pathname]);

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
                {item.id === "chat" && unread > 0 ? (
                  <span className="mop-menu-item-badge" data-testid="mobile-menu-chat-unread">
                    {unread > 99 ? "99+" : unread}
                  </span>
                ) : null}
              </NavLink>
            );
          })}
        </nav>
      </aside>
    </div>
  );
}
