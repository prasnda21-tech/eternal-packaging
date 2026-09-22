import { desktopNotificationPath } from "./floatingNotificationBell";
import { moduleFromPath, UI_MODULES } from "./uiModule";
import { NAV_KEYS, NAV_MENU_FALLBACK } from "./navAccess";
import {
  deadlineState,
  milestoneProgress,
  overallProgress,
  previewOverall,
  visibleSpkTabs,
  weightTotal,
} from "./spk";

describe("SPK progress and navigation", () => {
  test("quantity, checklist, and approval progress stay capped", () => {
    expect(milestoneProgress({ type: "QUANTITY", target_value: 10, actual_value: 6 })).toBe(60);
    expect(milestoneProgress({ type: "QUANTITY", target_value: 5, actual_value: 9 })).toBe(100);
    expect(milestoneProgress({ type: "CHECKLIST", status: "PENDING" })).toBe(0);
    expect(milestoneProgress({ type: "CHECKLIST", status: "COMPLETED" })).toBe(100);
    expect(milestoneProgress({ type: "APPROVAL", status: "PENDING" })).toBe(0);
    expect(milestoneProgress({ type: "APPROVAL", status: "COMPLETED" })).toBe(100);
    const rows = [
      { type: "QUANTITY", weight: 25, target_value: 10, actual_value: 6, status: "IN_PROGRESS" },
      { type: "QUANTITY", weight: 25, target_value: 5, actual_value: 3, status: "IN_PROGRESS" },
      { type: "QUANTITY", weight: 25, target_value: 100, actual_value: 42, status: "IN_PROGRESS" },
      { type: "CHECKLIST", weight: 25, status: "PENDING" },
    ];
    expect(overallProgress(rows)).toBe(40.5);
    expect(overallProgress([{ type: "QUANTITY", weight: 100, target_value: 1, actual_value: 8, status: "COMPLETED" }])).toBe(100);
  });

  test("preview moves overall without a manual percent field", () => {
    const rows = [
      { id: "a", type: "QUANTITY", weight: 50, target_value: 10, actual_value: 0, status: "PENDING" },
      { id: "b", type: "CHECKLIST", weight: 50, status: "PENDING" },
    ];
    const preview = previewOverall(rows, "a", { actual_value: 5, type: "QUANTITY", target_value: 10, weight: 50 });
    expect(preview.before).toBe(0);
    expect(preview.after).toBe(25);
    expect(preview.milestone).toBe(50);
    expect(weightTotal([{ weight: 40 }, { weight: 60 }])).toBe(100);
  });

  test("deadline state is separate from workflow status", () => {
    expect(deadlineState("COMPLETED", "2026-09-01", "2026-09-08")).toBeNull();
    expect(deadlineState("IN_PROGRESS", "2026-09-11", "2026-09-07")).toBe("ON_TRACK");
    expect(deadlineState("IN_PROGRESS", "2026-09-10", "2026-09-07")).toBe("DUE_SOON");
    expect(deadlineState("IN_PROGRESS", "2026-09-06", "2026-09-07")).toBe("OVERDUE");
  });

  test("tabs follow permission and Semua SPK stays hidden", () => {
    const staff = (key) => key === "spk.view_own" || key === "spk.update_progress" || key === "nav.spk";
    expect(visibleSpkTabs(staff).map((tab) => tab.id)).toEqual(["mine", "overdue", "verification"]);
    const head = (key) => staff(key) || key === "spk.view_division";
    expect(visibleSpkTabs(head).map((tab) => tab.id)).toContain("division");
    expect(visibleSpkTabs(head).map((tab) => tab.id)).not.toContain("all");
    const director = (key) => key === "spk.view_all" || key === "nav.spk";
    expect(visibleSpkTabs(director).map((tab) => tab.id)).toContain("all");
  });

  test("route and menu are registered", () => {
    expect(NAV_KEYS.spk).toBe("nav.spk");
    expect(NAV_MENU_FALLBACK.find((item) => item.key === NAV_KEYS.spk).label).toBe("SPK Antar Divisi");
    expect(moduleFromPath("/spk")).toBe(UI_MODULES.spk);
    expect(moduleFromPath("/spk/baru")).toBe(UI_MODULES.spk);
    expect(desktopNotificationPath({ event_type: "spk_assigned", order_id: "abc", deep_link: "/spk/abc" })).toBe("/spk/abc");
    expect(desktopNotificationPath({ event_type: "customer_design_approved", order_id: "po1", deep_link: "/m/orders/po1" })).toBe("");
  });
});
