/** Route and legacy-hash selection for the 20-widget dashboard. */

import { TABS, widgetAllowed } from "./widgetDashboardCatalog";

const LEGACY_TABS = new Set(["penjualan", "keuangan"]);

export function legacyHashTab(hash) {
  const id = String(hash || "").replace(/^#/, "").split("&")[0];
  return LEGACY_TABS.has(id) ? id : "";
}

/** Alias /dashboard/widget always lands on the primary route. Never points back at itself. */
export function widgetDashboardAliasTarget(location = {}) {
  return {
    pathname: "/dashboard/penjualan",
    search: location.search || "",
    hash: location.hash || "",
  };
}

export function widgetDashboardFinanceTarget(location = {}) {
  return {
    pathname: "/dashboard/penjualan",
    search: location.search || "",
    hash: "#keuangan",
  };
}

function panelHasAllowed(panel, user, hasPerm) {
  return panel.widgets.some((id) => widgetAllowed(id, user, hasPerm));
}

export function firstAllowedWidgetView(user, hasPerm) {
  for (const tab of TABS) {
    for (const panel of tab.panels) {
      const widgetId = panel.widgets.find((id) => widgetAllowed(id, user, hasPerm));
      if (widgetId) return { tab, panel, widgetId };
    }
  }
  return null;
}

/**
 * Explicit ?tab= stays put, including a forbidden panel.
 * A bare menu URL or legacy hash falls through to the first allowed panel
 * when the requested panel has no widget the user may open.
 * The URL is not rewritten here, so refresh and back/forward keep the address.
 */
export function resolveWidgetLocation({ search = "", hash = "", user, hasPerm } = {}) {
  const params = new URLSearchParams(String(search || "").replace(/^\?/, ""));
  const explicitTab = params.has("tab");
  const requestedId = explicitTab ? params.get("tab") : legacyHashTab(hash);
  let tab = TABS.find((item) => item.id === requestedId) || TABS[0];
  let panel = tab.panels.find((item) => item.id === params.get("panel")) || tab.panels[0];
  const requestedWidget = params.get("widget");
  let widgetId = panel.widgets.includes(requestedWidget) ? requestedWidget : panel.widgets[0];
  if (!params.has("widget")) {
    const allowedWidget = panel.widgets.find((id) => widgetAllowed(id, user, hasPerm));
    if (allowedWidget) widgetId = allowedWidget;
  }

  if (!explicitTab && !panelHasAllowed(panel, user, hasPerm)) {
    const fallback = firstAllowedWidgetView(user, hasPerm);
    if (fallback) {
      tab = fallback.tab;
      panel = fallback.panel;
      widgetId = fallback.widgetId;
    }
  }

  return { tab, panel, widgetId, explicitTab };
}
