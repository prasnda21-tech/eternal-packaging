import { Bell, CalendarDays, ClipboardList, MessageCircle, Package, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../lib/auth";
import api from "../lib/api";
import { activeMobileNav, visibleMobileNavItems } from "../lib/mobileWorkspaces";

const ICONS = {
  tasks: ClipboardList,
  orders: Package,
  schedule: CalendarDays,
  chat: MessageCircle,
  notifications: Bell,
  profile: UserRound,
};

export default function MobileBottomNav({ pathname }) {
  const { hasPerm } = useAuth();
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
            {item.id === "chat" && unread > 0 ? (
              <span className="mop-nav-unread" data-testid="mobile-chat-unread">{unread > 99 ? "99+" : unread}</span>
            ) : null}
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
