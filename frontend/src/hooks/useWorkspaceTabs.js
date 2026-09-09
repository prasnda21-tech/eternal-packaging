import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

/**
 * Shared workspace tabs: open on route, no duplicates, close + navigate,
 * sessionStorage restore per user. Same lifecycle as Kas & Bank.
 */
export function useWorkspaceTabs({ load, save, openOrActivate, closeTab: closeTabFn }) {
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [tabs, setTabs] = useState([]);
  const hydratedUser = useRef(null);
  const pathRef = useRef(location.pathname);
  pathRef.current = location.pathname;
  const stateRef = useRef(location.state);
  stateRef.current = location.state;

  useEffect(() => {
    const uid = user?.id || user?.email || user?.sub || null;
    if (!uid) {
      setTabs([]);
      hydratedUser.current = null;
      return;
    }
    if (hydratedUser.current === uid) return;
    hydratedUser.current = uid;
    const restored = load(user);
    setTabs(openOrActivate(restored, pathRef.current, stateRef.current).tabs);
  }, [user, load, openOrActivate]);

  useEffect(() => {
    if (!user || !hydratedUser.current) return;
    setTabs((cur) => {
      const { tabs: next, activated } = openOrActivate(cur, location.pathname, location.state);
      if (!activated) return cur;
      const same = next.length === cur.length && next.every((t, i) => (
        t.id === cur[i]?.id && t.path === cur[i]?.path && t.label === cur[i]?.label
      ));
      return same ? cur : next;
    });
  }, [location.pathname, location.state, user, openOrActivate]);

  useEffect(() => {
    if (!user || !hydratedUser.current) return;
    save(user, tabs);
  }, [tabs, user, save]);

  const activateTab = useCallback((tab) => {
    if (!tab?.path) return;
    if (location.pathname !== tab.path) navigate(tab.path);
  }, [location.pathname, navigate]);

  const closeTab = useCallback((tabId) => {
    setTabs((cur) => {
      const { tabs: next, navigateTo } = closeTabFn(cur, tabId, location.pathname);
      if (navigateTo && navigateTo !== location.pathname) {
        queueMicrotask(() => navigate(navigateTo));
      }
      return next;
    });
  }, [location.pathname, navigate, closeTabFn]);

  return {
    tabs,
    activePath: location.pathname,
    activateTab,
    closeTab,
    visible: tabs.length > 0,
  };
}
