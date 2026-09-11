import { useEffect } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { markMobileOpsShell } from "../lib/mobileOpsShell";
import { MobileNavProvider } from "./MobileNavContext";
import MobileSidebar from "./MobileSidebar";
import "./mobile.css";

export default function MobileOperationalLayout() {
  const { pathname } = useLocation();

  useEffect(() => {
    markMobileOpsShell();
  }, []);

  return (
    <MobileNavProvider>
      <div className="mop-root mop-root--sidebar" data-testid="mobile-operational-root">
        <div className="mop-shell mop-shell--sidebar">
          <Outlet />
          <MobileSidebar pathname={pathname} />
        </div>
      </div>
    </MobileNavProvider>
  );
}
