import {
  excludeFromSelection,
  parseReviewOrderIds,
  selectAllEligible,
  sourceTypeLabel,
  summarizeSelection,
  toggleId,
} from "./deliveryRouteBridge";
import {
  canOptimizeFromDeliverySchedule,
  mobileScheduleReviewHref,
  optimizationStatusMeta,
  OPT_STATUS,
} from "./mobileDeliverySchedule";

describe("deliveryRouteBridge helpers", () => {
  test("parseReviewOrderIds preserves exact filtered ids", () => {
    expect(parseReviewOrderIds("ids=a,b,c")).toEqual(["a", "b", "c"]);
    expect(parseReviewOrderIds("?ids=a,a,b")).toEqual(["a", "b"]);
  });

  test("toggle / select all / exclude are selection-only", () => {
    expect(toggleId(["a"], "b")).toEqual(["a", "b"]);
    expect(toggleId(["a", "b"], "a")).toEqual(["b"]);
    const rows = [
      { sales_order_id: "a", eligible: true },
      { sales_order_id: "b", eligible: false },
      { sales_order_id: "c", eligible: true },
    ];
    expect(selectAllEligible(rows)).toEqual(["a", "c"]);
    expect(excludeFromSelection(["a", "c"], ["a"])).toEqual(["c"]);
  });

  test("summarizeSelection groups by driver id", () => {
    const rows = [
      { sales_order_id: "a", eligible: true, driver_user_id: "d1", driver_name: "A" },
      { sales_order_id: "b", eligible: true, driver_user_id: "d1", driver_name: "A" },
      { sales_order_id: "c", eligible: true, driver_user_id: "d2", driver_name: "B" },
      { sales_order_id: "x", eligible: false, driver_user_id: null },
    ];
    const summary = summarizeSelection(rows, ["a", "b", "c", "x"]);
    expect(summary.selected).toBe(4);
    expect(summary.eligible).toBe(3);
    expect(summary.ineligible).toBe(1);
    expect(summary.drivers).toBe(2);
    expect(summary.routes_to_create).toBe(2);
  });

  test("sourceTypeLabel", () => {
    expect(sourceTypeLabel("delivery_schedule")).toBe("Jadwal Pengiriman");
    expect(sourceTypeLabel("manual")).toBe("Manual");
  });
});

describe("mobile delivery schedule opt helpers", () => {
  test("permission visibility uses route_optimizer perms", () => {
    expect(canOptimizeFromDeliverySchedule((p) => p === "route_optimizer.create")).toBe(true);
    expect(canOptimizeFromDeliverySchedule((p) => p === "delivery.schedule.view")).toBe(false);
  });

  test("review href encodes current filtered ids", () => {
    expect(mobileScheduleReviewHref(["a", "b"])).toContain("ids=a%2Cb");
  });

  test("optimizationStatusMeta", () => {
    expect(optimizationStatusMeta({}).code).toBe(OPT_STATUS.NONE);
    expect(optimizationStatusMeta({ optimization_status: OPT_STATUS.OPTIMIZED, route_id: "iro-1" }).routeId).toBe("iro-1");
  });
});
