import { useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { ClipboardList, TrendingUp, UserRound, Users } from "lucide-react";
import { activeSalesPwaNav, SALES_PWA_NAV } from "../lib/salesPwa";
import "./salesPwa.css";

const ICONS = {
  orders: ClipboardList,
  customers: Users,
  predictions: TrendingUp,
  profile: UserRound,
};

export default function SalesPwaLayout() {
  const location = useLocation();
  const active = activeSalesPwaNav(location.pathname);

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
    </div>
  );
}
