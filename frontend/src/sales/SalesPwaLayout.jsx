import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { ClipboardList, LayoutDashboard, MapPin, MoreHorizontal, TrendingUp, UserRound, Users, X } from "lucide-react";
import { activeSalesPwaNav, SALES_PWA_MORE_NAV, SALES_PWA_NAV } from "../lib/salesPwa";
import "./salesPwa.css";

const ICONS = {
  dashboard: LayoutDashboard,
  orders: ClipboardList,
  visits: MapPin,
  more: MoreHorizontal,
  customers: Users,
  predictions: TrendingUp,
  profile: UserRound,
};

export default function SalesPwaLayout() {
  const location = useLocation();
  const active = activeSalesPwaNav(location.pathname);
  const [moreOpen, setMoreOpen] = useState(false);

  useEffect(() => {
    setMoreOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    const link = document.querySelector('link[rel="manifest"]');
    const prev = link?.getAttribute("href");
    if (link) link.setAttribute("href", `${process.env.PUBLIC_URL || ""}/sales-manifest.json`);
    document.title = "PO Saya — Sales Eternal";
    return () => {
      if (link && prev) link.setAttribute("href", prev);
    };
  }, []);

  return (
    <div className="spwa-shell" data-testid="sales-pwa-shell">
      <Outlet />
      <nav className="spwa-nav" data-testid="sales-pwa-nav" aria-label="PWA Sales">
        {SALES_PWA_NAV.map((item) => {
          const Icon = ICONS[item.id];
          if (item.id === "more") {
            return (
              <button
                key={item.id}
                type="button"
                className={moreOpen || SALES_PWA_MORE_NAV.some((entry) => entry.id === active) ? "is-on" : ""}
                onClick={() => setMoreOpen(true)}
                data-testid="sales-nav-more"
              >
                <Icon size={18} strokeWidth={1.8} />
                {item.label}
              </button>
            );
          }
          return (
            <NavLink
              key={item.id}
              to={item.to}
              end={item.id === "orders"}
              className={active === item.id ? "is-on" : ""}
              data-testid={`sales-nav-${item.id}`}
            >
              <Icon size={18} strokeWidth={1.8} />
              {item.label}
            </NavLink>
          );
        })}
      </nav>
      {moreOpen && (
        <div className="spwa-drawer-backdrop" role="presentation" onClick={() => setMoreOpen(false)}>
          <aside
            className="spwa-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Menu lainnya"
            data-testid="sales-more-drawer"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="spwa-drawer-head">
              <strong>Menu lainnya</strong>
              <button type="button" aria-label="Tutup" onClick={() => setMoreOpen(false)}><X size={20} /></button>
            </div>
            <div className="spwa-drawer-links">
              {SALES_PWA_MORE_NAV.map((item) => {
                const Icon = ICONS[item.id];
                return (
                  <NavLink key={item.id} to={item.to} className={active === item.id ? "is-on" : ""}>
                    <Icon size={18} /> <span>{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
