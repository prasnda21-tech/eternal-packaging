import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import {
  clearCashBankWorkspaceTabs,
  closeCashBankTab,
  loadCashBankWorkspaceTabs,
  openOrActivateCashBankTab,
  saveCashBankWorkspaceTabs,
} from "../lib/cashBankWorkspace";

/**
 * Manages Kas & Bank workspace tabs: open on route, no duplicates, close + navigate,
 * sessionStorage restore per user.
 */
export function useCashBankWorkspaceTabs() {
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [tabs, setTabs] = useState([]);
  const hydratedUser = useRef(null);
  const pathRef = useRef(location.pathname);
  pathRef.current = location.pathname;

  // Restore when user identity becomes available / switches
  useEffect(() => {
    const uid = user?.id || user?.email || user?.sub || null;
    if (!uid) {
      setTabs([]);
      hydratedUser.current = null;
      return;
    }
    if (hydratedUser.current === uid) return;
    hydratedUser.current = uid;
    const restored = loadCashBankWorkspaceTabs(user);
    setTabs(openOrActivateCashBankTab(restored, pathRef.current).tabs);
  }, [user]);

  // Opening a Kas & Bank menu route opens/activates the tab (no duplicates)
  useEffect(() => {
    if (!user || !hydratedUser.current) return;
    setTabs((cur) => {
      const { tabs: next, activated } = openOrActivateCashBankTab(cur, location.pathname);
      if (!activated) return cur;
      const same = next.length === cur.length && next.every((t, i) => t.id === cur[i]?.id);
      return same ? cur : next;
    });
  }, [location.pathname, user]);

  // Persist whenever tabs change for a logged-in user
  useEffect(() => {
    if (!user || !hydratedUser.current) return;
    saveCashBankWorkspaceTabs(user, tabs);
  }, [tabs, user]);

  const activateTab = useCallback((tab) => {
    if (!tab?.path) return;
    if (location.pathname !== tab.path) navigate(tab.path);
  }, [location.pathname, navigate]);

  const closeTab = useCallback((tabId) => {
    setTabs((cur) => {
      const { tabs: next, navigateTo } = closeCashBankTab(cur, tabId, location.pathname);
      if (navigateTo && navigateTo !== location.pathname) {
        queueMicrotask(() => navigate(navigateTo));
      }
      return next;
    });
  }, [location.pathname, navigate]);

  return {
    tabs,
    activePath: location.pathname,
    activateTab,
    closeTab,
    visible: tabs.length > 0,
  };
}

export { clearCashBankWorkspaceTabs };
