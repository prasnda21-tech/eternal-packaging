import { Suspense, useEffect } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { markMobileOpsShell } from "../lib/mobileOpsShell";
import WorkspaceChunkFallback from "../components/WorkspaceChunkFallback";
import { MobileNavProvider } from "./MobileNavContext";
import MobileSidebar from "./MobileSidebar";
import "./mobile.css";
import "./opsQueue.css";

export default function MobileOperationalLayout() {
  const { pathname } = useLocation();
  const opsWide = pathname.startsWith("/m/schedule") || pathname.startsWith("/m/delivery-schedule");

  useEffect(() => {
    markMobileOpsShell();
  }, []);

  return (
    <MobileNavProvider>
      <div
        className={`mop-root mop-root--sidebar${opsWide ? " mop-root--ops-wide" : ""}`}
        data-testid="mobile-operational-root"
      >
        <div className={`mop-shell mop-shell--sidebar${opsWide ? " mop-shell--ops-wide" : ""}`}>
          <Suspense fallback={<WorkspaceChunkFallback />}>
            <Outlet />
          </Suspense>
          <MobileSidebar pathname={pathname} />
        </div>
      </div>
    </MobileNavProvider>
  );
}
