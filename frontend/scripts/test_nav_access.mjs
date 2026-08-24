/**
 * Sidebar nav visibility helpers.
 * Run from frontend/: node scripts/test_nav_access.mjs
 */
import { NAV_KEYS, reportsNavVisible, salesNavVisible } from "../src/lib/navAccess.js";

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function hasPermFrom(set) {
  return (key) => set.has(key);
}

{
  const hasPerm = hasPermFrom(new Set([NAV_KEYS.dashboard, NAV_KEYS.schedule]));
  const sales = salesNavVisible(hasPerm);
  const reports = reportsNavVisible(hasPerm);
  assert(!sales.parent && !sales.importPo, "ROLE A: penjualan hidden");
  assert(!reports.parent && !reports.analytics, "ROLE A: laporan hidden");
}

{
  const hasPerm = hasPermFrom(new Set([NAV_KEYS.reports, NAV_KEYS.analytics]));
  const reports = reportsNavVisible(hasPerm);
  assert(reports.parent && reports.analytics, "ROLE B: laporan + analitik");
}

{
  const hasPerm = hasPermFrom(new Set([NAV_KEYS.reports]));
  const reports = reportsNavVisible(hasPerm);
  assert(!reports.analytics, "ROLE C: analitik hidden");
  assert(!reports.parent, "ROLE C: empty laporan parent hidden");
}

{
  const hasPerm = hasPermFrom(new Set([NAV_KEYS.sales]));
  const sales = salesNavVisible(hasPerm);
  assert(!sales.importPo, "sales without import po hides child");
  assert(!sales.parent, "empty penjualan parent hidden");
}

{
  const hasPerm = hasPermFrom(new Set([NAV_KEYS.sales, NAV_KEYS.importPo]));
  const sales = salesNavVisible(hasPerm);
  assert(sales.parent && sales.importPo, "sales + import po visible");
}

console.log("nav access helpers OK");
