import { Outlet, useLocation } from "react-router-dom";
import MobileBottomNav from "./MobileBottomNav";
import "./mobile.css";

export default function MobileOperationalLayout() {
  const { pathname } = useLocation();
  return (
    <div className="mop-root" data-testid="mobile-operational-root">
      <div className="mop-shell">
        <Outlet />
        <MobileBottomNav pathname={pathname} />
      </div>
    </div>
  );
}
