/** The same desktop menu resolver for the live shell and role preview.
 * hasPerm must use server-effective permissions, including runtime filters.
 */
import {
  NAV_KEYS, TASK_NAV_ORDER, dashboardMenuItems, salesMenuItems, productionMenuItems,
  taskMenuItems, purchasingMenuItems, inventoryMenuItems, cashBankMenuItems,
  ledgerMenuItems, employeeAdminMenuItems, hrPayrollMenuItems, reportsMenuItems,
  settingsMenuItems,
} from "./navAccess";
import { buildDesktopSidebar } from "./sidebarPresentation";
import { canSeeSpkNav } from "./spk";
import { canAccessRoutePage } from "./independentRoute";

export function resolveDesktopSidebar(hasPerm, user) {
  return buildDesktopSidebar({
    dashboardItems: dashboardMenuItems(hasPerm, user),
    salesItems: salesMenuItems(hasPerm),
    productionItems: productionMenuItems(hasPerm),
    taskItems: [...taskMenuItems(hasPerm)].sort(
      (a, b) => TASK_NAV_ORDER.indexOf(a.key) - TASK_NAV_ORDER.indexOf(b.key),
    ),
    showRouteOptimizer: canAccessRoutePage(hasPerm),
    purchasingItems: purchasingMenuItems(hasPerm),
    inventoryItems: inventoryMenuItems(hasPerm),
    cashBankItems: cashBankMenuItems(hasPerm),
    ledgerItems: ledgerMenuItems(hasPerm),
    employeeAdminItems: employeeAdminMenuItems(hasPerm),
    hrPayrollItems: hrPayrollMenuItems(hasPerm),
    reportItems: reportsMenuItems(hasPerm),
    settingsItems: settingsMenuItems(hasPerm),
    showSpk: canSeeSpkNav(hasPerm),
    showChat: hasPerm(NAV_KEYS.chat) || hasPerm("chat.use"),
  });
}

export function roleSidebarPreview(roles) {
  const columns = (roles || []).filter((role) => role.status !== "inactive").map((role) => {
    // Never fall back to raw grants: they have not passed production filters.
    const known = Array.isArray(role.sidebar_permissions);
    const permissions = new Set(role.sidebar_permissions || []);
    const sidebar = known ? resolveDesktopSidebar((key) => permissions.has(key), { role: role.role }) : [];
    const leaves = sidebar.flatMap((rail) => rail.to
      ? [{ to: rail.to, label: rail.label, group: rail.label }]
      : (rail.sections || []).flatMap((section) => section.items.map((item) => ({
          ...item, group: rail.label,
        }))));
    return { ...role, known, leaves, paths: new Set(leaves.map((item) => item.to)) };
  });
  const byPath = new Map();
  columns.forEach((column) => column.leaves.forEach((leaf) => {
    if (!byPath.has(leaf.to)) byPath.set(leaf.to, leaf);
  }));
  return { columns, rows: [...byPath.values()] };
}
