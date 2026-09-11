import {
  buildBulkAssignConfirm,
  driverDisplayLabel,
  eligibilityReasonText,
  eligibilityStatusLabel,
  excludeFromSelection,
  hasCanonicalDeliveryCoordinates,
  locationSetupStatusLabel,
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

  test("locationSetupStatusLabel mirrors canonical lat/lng readiness", () => {
    // CASE 2 — location missing (no coords)
    expect(hasCanonicalDeliveryCoordinates({})).toBe(false);
    expect(locationSetupStatusLabel({})).toBe("Belum Diatur");
    expect(locationSetupStatusLabel({ lat: null, lng: null, address: "Jl Ada Alamat" })).toBe("Belum Diatur");
    expect(locationSetupStatusLabel({ formatted_address: "Jl Hanya Alamat", customer_address: "Jakarta" })).toBe("Belum Diatur");
    expect(locationSetupStatusLabel({ lat: 0, lng: 0 })).toBe("Belum Diatur");
    expect(locationSetupStatusLabel({ lat: "abc", lng: "106.8" })).toBe("Belum Diatur");
    expect(locationSetupStatusLabel({ lat: 91, lng: 106.8 })).toBe("Belum Diatur");
    expect(locationSetupStatusLabel({ lat: -6.2, lng: 181 })).toBe("Belum Diatur");

    // CASE 3 — location set
    expect(hasCanonicalDeliveryCoordinates({ lat: -6.2, lng: 106.8 })).toBe(true);
    expect(locationSetupStatusLabel({ lat: -6.2, lng: 106.8 })).toBe("Sudah Diatur");
    expect(locationSetupStatusLabel({ planning_snapshot: { lat: -6.2, lng: 106.8 } })).toBe("Sudah Diatur");
  });

  test("eligibility presentation uses authoritative reason; location status independent", () => {
    // CASE 1 — active valid route
    const activeRoute = {
      eligible: false,
      reason: "Already in active valid route",
      reasons: ["Already in active valid route"],
      lat: -6.2,
      lng: 106.8,
      driver_user_id: "drv-budi",
      driver_name: "Budi Driver",
    };
    expect(eligibilityStatusLabel(activeRoute)).toBe("Tidak Eligible");
    expect(eligibilityReasonText(activeRoute)).toBe("Already in active valid route");
    expect(locationSetupStatusLabel(activeRoute)).toBe("Sudah Diatur");

    // CASE 4 — location set + active route (both visible, eligibility unchanged)
    expect(locationSetupStatusLabel(activeRoute)).toBe("Sudah Diatur");
    expect(activeRoute.eligible).toBe(false);

    // CASE 2/location reason — only when authoritative reason says so
    const noCoords = {
      eligible: false,
      reasons: ["Belum ada driver", "Koordinat belum tersedia"],
      lat: null,
      lng: null,
    };
    expect(locationSetupStatusLabel(noCoords)).toBe("Belum Diatur");
    expect(eligibilityReasonText(noCoords)).toBe("Belum ada driver; Koordinat belum tersedia");

    // CASE 5 — other authoritative reason preserved
    const delivered = {
      eligible: false,
      reasons: ["Sudah terkirim"],
      lat: -6.2,
      lng: 106.8,
    };
    expect(eligibilityReasonText(delivered)).toBe("Sudah terkirim");
    expect(locationSetupStatusLabel(delivered)).toBe("Sudah Diatur");

    // CASE 6 — driver assignment does not change location/eligibility presentation inputs
    const before = { ...activeRoute, sales_order_id: "a" };
    const afterDriver = { ...before, driver_user_id: "drv-new", driver_name: "New" };
    expect(locationSetupStatusLabel(afterDriver)).toBe(locationSetupStatusLabel(before));
    expect(eligibilityReasonText(afterDriver)).toBe(eligibilityReasonText(before));
    expect(eligibilityStatusLabel(afterDriver)).toBe(eligibilityStatusLabel(before));
    const summary = summarizeSelection([afterDriver], ["a"]);
    expect(summary.eligible).toBe(0);
    expect(summary.ineligible).toBe(1);
    expect(summary.drivers).toBe(1);
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
