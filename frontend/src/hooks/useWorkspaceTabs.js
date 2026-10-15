import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { registerWorkspaceTabEnricher } from "../lib/workspaceTabEnrichment";
import {
  normalizeWorkspaceSearch,
  workspaceTabHref,
} from "../lib/workspaceTabs";

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
  const searchRef = useRef(location.search);
  searchRef.current = location.search;
  const stateRef = useRef(location.state);
  stateRef.current = location.state;
  const openOrActivateRef = useRef(openOrActivate);
  openOrActivateRef.current = openOrActivate;

  // Layout phase so /po/:id is in workspace before PODetail child effects run.
  useLayoutEffect(() => {
    const uid = user?.id || user?.email || user?.sub || null;
    if (!uid) {
      setTabs([]);
      hydratedUser.current = null;
      return;
    }
    if (hydratedUser.current === uid) return;
    hydratedUser.current = uid;
    const restored = load(user);
    setTabs(openOrActivate(restored, pathRef.current, stateRef.current, searchRef.current).tabs);
  }, [user, load, openOrActivate]);

  useLayoutEffect(() => {
    if (!user || !hydratedUser.current) return;
    setTabs((cur) => {
      const { tabs: next, activated } = openOrActivate(
        cur,
        location.pathname,
        location.state,
        location.search,
      );
      if (!activated) return cur;
      const same = next.length === cur.length && next.every((t, i) => (
        t.id === cur[i]?.id
        && t.path === cur[i]?.path
        && t.label === cur[i]?.label
        && normalizeWorkspaceSearch(t.search) === normalizeWorkspaceSearch(cur[i]?.search)
      ));
      return same ? cur : next;
    });
  }, [location.pathname, location.search, location.state, user, openOrActivate]);

  useEffect(() => {
    if (!user || !hydratedUser.current) return;
    // Never persist [] — that wipe + remount restores only catalog tabs
    // (Dashboard / Pesanan Penjualan) and drops po-detail:<order_id>.
    if (!tabs.length) return;
    save(user, tabs);
  }, [tabs, user, save]);

  // In-place existence + label enrichment (no navigate/replace).
  // Layout: register before paint so queued PODetail pings flush after hydrate.
  // Do not pass search — enrichers must not wipe rememberSearch query state.
  useLayoutEffect(() => {
    return registerWorkspaceTabEnricher((pathname, meta) => {
      if (!hydratedUser.current) return;
      setTabs((cur) => {
        const { tabs: next, activated } = openOrActivateRef.current(cur, pathname, meta);
        if (!activated) return cur;
        const same = next.length === cur.length && next.every((t, i) => (
          t.id === cur[i]?.id && t.path === cur[i]?.path && t.label === cur[i]?.label
          && t.title === cur[i]?.title && t.labelPriority === cur[i]?.labelPriority
          && normalizeWorkspaceSearch(t.search) === normalizeWorkspaceSearch(cur[i]?.search)
        ));
        return same ? cur : next;
      });
    });
  }, []);

  const activateTab = useCallback((tab) => {
    if (!tab?.path) return;
    const target = workspaceTabHref(tab);
    const current = `${location.pathname}${normalizeWorkspaceSearch(location.search)}`;
    if (current !== target) navigate(target);
  }, [location.pathname, location.search, navigate]);

  const closeTab = useCallback((tabId) => {
    setTabs((cur) => {
      const { tabs: next, navigateTo } = closeTabFn(cur, tabId, location.pathname);
      const current = `${location.pathname}${normalizeWorkspaceSearch(location.search)}`;
      if (navigateTo && navigateTo !== current) {
        queueMicrotask(() => navigate(navigateTo));
      }
      return next;
    });
  }, [location.pathname, location.search, navigate, closeTabFn]);

  return {
    tabs,
    activePath: location.pathname,
    activateTab,
    closeTab,
    visible: tabs.length > 0,
  };
}
