import {
  buildBulkAssignConfirm,
  driverDisplayLabel,
  excludeFromSelection,
  parseReviewOrderIds,
  rowsNeedingDriverChange,
  selectAllEligible,
  selectAllReviewRows,
  sourceTypeLabel,
  summarizeSelection,
  toggleId,
} from "./deliveryRouteBridge";
import {
  canAssignDeliveryDriver,
  canOptimizeFromDeliverySchedule,
  MOBILE_DELIVERY_ASSIGN_DRIVER_PERM,
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
    expect(selectAllReviewRows(rows)).toEqual(["a", "b", "c"]);
    expect(excludeFromSelection(["a", "c"], ["a"])).toEqual(["c"]);
  });

  test("selectAllReviewRows keeps ineligible ids for bulk driver selection", () => {
    const rows = [
      { sales_order_id: "x", eligible: false },
      { sales_order_id: "y", eligible: false },
    ];
    expect(selectAllEligible(rows)).toEqual([]);
    expect(selectAllReviewRows(rows)).toEqual(["x", "y"]);
    const summary = summarizeSelection(rows, selectAllReviewRows(rows));
    expect(summary.selected).toBe(2);
    expect(summary.eligible).toBe(0);
    expect(summary.ineligible).toBe(2);
  });

  test("summarizeSelection Driver counts persisted assignments; route groups stay eligible-only", () => {
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
    // Driver:N = selected rows with persisted driver (a,b,c), not unique eligible groups.
    expect(summary.drivers).toBe(3);
    expect(summary.routes_to_create).toBe(2);
    expect(summary.groups).toHaveLength(2);
  });

  test("summarizeSelection Driver count after save with ineligible rows (regression)", () => {
    // CASE 1: none assigned
    expect(
      summarizeSelection(
        [
          { sales_order_id: "a", eligible: false, driver_user_id: null },
          { sales_order_id: "b", eligible: false, driver_user_id: "" },
        ],
        ["a", "b"],
      ).drivers,
    ).toBe(0);

    // CASE 2: one persisted
    expect(
      summarizeSelection(
        [
          { sales_order_id: "a", eligible: false, driver_user_id: "drv-budi" },
          { sales_order_id: "b", eligible: false, driver_user_id: null },
        ],
        ["a", "b"],
      ).drivers,
    ).toBe(1);

    // CASE 3: both persisted but still ineligible (e.g. already in active valid route)
    const both = summarizeSelection(
      [
        {
          sales_order_id: "a",
          eligible: false,
          driver_user_id: "drv-budi",
          driver_name: "Budi Driver",
          reason: "Already in active valid route",
        },
        {
          sales_order_id: "b",
          eligible: false,
          driver_user_id: "drv-budi",
          driver_name: "Budi Driver",
          reason: "Already in active valid route",
        },
      ],
      ["a", "b"],
    );
    expect(both.selected).toBe(2);
    expect(both.eligible).toBe(0);
    expect(both.ineligible).toBe(2);
    expect(both.drivers).toBe(2);
    expect(both.routes_to_create).toBe(0);
    expect(both.groups).toEqual([]);
  });

  test("driverDisplayLabel shows Belum Ada Driver when empty", () => {
    expect(driverDisplayLabel({})).toBe("Belum Ada Driver");
    expect(driverDisplayLabel({ driver_name: "Only Name" })).toBe("Belum Ada Driver");
    expect(driverDisplayLabel({ driver_user_id: "u1", driver_name: "Nanda" })).toBe("Nanda");
  });

  test("rowsNeedingDriverChange uses exact selected ids only", () => {
    const rows = [
      { sales_order_id: "a", driver_user_id: null },
      { sales_order_id: "b", driver_user_id: "d1" },
      { sales_order_id: "c", driver_user_id: "d2" },
    ];
    const needing = rowsNeedingDriverChange(rows, ["a", "b"], "d1");
    expect(needing.map((r) => r.sales_order_id)).toEqual(["a"]);
    const confirm = buildBulkAssignConfirm(rows, ["a", "b", "c"], { id: "d1", name: "Driver A" });
    expect(confirm.selectedCount).toBe(3);
    expect(confirm.changeCount).toBe(2);
    expect(confirm.message).toContain("3 pengiriman dipilih");
    expect(confirm.message).toContain("Driver A");
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

  test("assign driver UI gated by delivery.location.manage only", () => {
    expect(MOBILE_DELIVERY_ASSIGN_DRIVER_PERM).toBe("delivery.location.manage");
    expect(canAssignDeliveryDriver((p) => p === "delivery.location.manage")).toBe(true);
    expect(canAssignDeliveryDriver((p) => p === "loading.update")).toBe(false);
    expect(canAssignDeliveryDriver((p) => p === "route_optimizer.create")).toBe(false);
  });

  test("review href encodes current filtered ids", () => {
    expect(mobileScheduleReviewHref(["a", "b"])).toContain("ids=a%2Cb");
  });

  test("optimizationStatusMeta", () => {
    expect(optimizationStatusMeta({}).code).toBe(OPT_STATUS.NONE);
    expect(optimizationStatusMeta({ optimization_status: OPT_STATUS.OPTIMIZED, route_id: "iro-1" }).routeId).toBe("iro-1");
  });
});
