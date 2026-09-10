/** PWA-only Jadwal Produksi columns — display subset; desktop Web keeps full set. */

import { PRODUCTION_SCHEDULE_TABLE_COLS } from "./productionScheduleTable";

const MOBILE_HIDDEN_KEYS = new Set(["payment", "packing", "sj", "driver", "screen_size"]);

/**
 * PWA order:
 * … Qty Cetak → Total Qty → Persiapan Cup → Screen → Prio …
 * Screen = screen_qty only (no denominator). screen_size hidden to avoid dual "Screen".
 */
export function buildMobileProductionScheduleCols(source = PRODUCTION_SCHEDULE_TABLE_COLS) {
  const byKey = new Map(source.map((c) => [c.key, { ...c }]));
  const helper = byKey.get("helper");
  const screen = byKey.get("screen");
  if (helper) helper.label = "Persiapan Cup";
  if (screen) {
    screen.label = "Screen";
    screen.filter = "screener";
  }

  const visible = source
    .filter((c) => !MOBILE_HIDDEN_KEYS.has(c.key))
    .map((c) => byKey.get(c.key))
    .filter(Boolean);

  const withoutHelperScreen = visible.filter((c) => c.key !== "helper" && c.key !== "screen");
  const qtyIdx = withoutHelperScreen.findIndex((c) => c.key === "qty");
  const insertAt = qtyIdx >= 0 ? qtyIdx + 1 : withoutHelperScreen.length;
  const ordered = [...withoutHelperScreen];
  if (helper) ordered.splice(insertAt, 0, helper);
  if (screen) ordered.splice(insertAt + (helper ? 1 : 0), 0, screen);
  return ordered;
}

export const MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS = buildMobileProductionScheduleCols();

export const MOBILE_PRODUCTION_ABSENT_KEYS = ["payment", "packing", "sj", "driver", "screen_size"];
