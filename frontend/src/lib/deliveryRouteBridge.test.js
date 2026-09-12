import {
  buildBulkAssignConfirm,
  driverDisplayLabel,
  eligibilityReasonText,
  eligibilityStatusLabel,
  excludeFromSelection,
  hasCanonicalDeliveryCoordinates,
  locationSetupSortValue,
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
  desktopScheduleReviewHref,
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

  test("locationSetupSortValue ranks Belum below Sudah from canonical helper", () => {
    expect(locationSetupSortValue({ lat: null, lng: null, address: "Jl Ada" })).toBe(0);
    expect(locationSetupSortValue({ lat: 0, lng: 0 })).toBe(0);
    expect(locationSetupSortValue({ lat: -6.2, lng: 106.8 })).toBe(1);
    expect(locationSetupSortValue({ planning_snapshot: { lat: -6.2, lng: 106.8 } })).toBe(1);
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
      reasons: ["Surat Jalan belum siap.", "Koordinat belum tersedia"],
      lat: null,
      lng: null,
    };
    expect(locationSetupStatusLabel(noCoords)).toBe("Belum Diatur");
    expect(eligibilityReasonText(noCoords)).toBe("Surat Jalan belum siap.; Koordinat belum tersedia");

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
    expect(mobileScheduleReviewHref(["a", "b"])).toMatch(/^\/m\/delivery-schedule\/review-optimasi/);
    expect(desktopScheduleReviewHref(["a", "b"])).toContain("ids=a%2Cb");
    expect(desktopScheduleReviewHref(["ord-a", "ord-b"])).toBe(
      "/produksi/jadwal-pengiriman/review-optimasi?ids=ord-a%2Cord-b",
    );
    expect(desktopScheduleReviewHref(["a"])).not.toMatch(/\/m\//);
  });

  test("optimizationStatusMeta", () => {
    expect(optimizationStatusMeta({}).code).toBe(OPT_STATUS.NONE);
    expect(optimizationStatusMeta({ optimization_status: OPT_STATUS.OPTIMIZED, route_id: "iro-1" }).routeId).toBe("iro-1");
  });
});


describe("route opt eligibility fixtures (1/2 fleet create)", () => {
  const fixtures = require("./deliveryRouteOptEligibilityFixtures");
  const sharedSrc = require("fs").readFileSync(
    require("path").join(__dirname, "../components/DeliveryRouteReviewContent.jsx"),
    "utf8",
  );

  test("preview is hard-blocked outside development", () => {
    const prev = process.env.NODE_ENV;
    process.env.NODE_ENV = "production";
    expect(fixtures.isRouteOptEligibilityPreviewEnabled("previewRouteOptEligibility=1")).toBe(false);
    process.env.NODE_ENV = "development";
    expect(fixtures.isRouteOptEligibilityPreviewEnabled("previewRouteOptEligibility=1")).toBe(true);
    expect(fixtures.isRouteOptEligibilityPreviewEnabled("")).toBe(false);
    expect(fixtures.isRouteOptEligibilityPreviewEnabled("ids=a,b")).toBe(false);
    process.env.NODE_ENV = prev;
  });

  test("activation does not require ids and wins before allowed/empty-ID handling in shared review", () => {
    const prev = process.env.NODE_ENV;
    process.env.NODE_ENV = "development";
    expect(fixtures.isRouteOptEligibilityPreviewEnabled("previewRouteOptEligibility=1")).toBe(true);
    process.env.NODE_ENV = prev;

    const loadIdx = sharedSrc.indexOf("const load = useCallback");
    const fixtureIdx = sharedSrc.indexOf("isRouteOptEligibilityPreviewEnabled(idsQuery)", loadIdx);
    const allowedIdx = sharedSrc.indexOf("if (!allowed) return;", loadIdx);
    const apiIdx = sharedSrc.indexOf("reviewScheduleRouteOptimize", loadIdx);
    expect(fixtureIdx).toBeGreaterThan(loadIdx);
    expect(allowedIdx).toBeGreaterThan(fixtureIdx);
    expect(apiIdx).toBeGreaterThan(allowedIdx);
    expect(sharedSrc).toMatch(/DATA DUMMY — DEVELOPMENT PREVIEW/);
    expect(sharedSrc).toMatch(/Optimasi Rute/);
    expect(sharedSrc).not.toMatch(/Simpan 2 Rute/);
    expect(sharedSrc).not.toMatch(/createFleetPairFromSchedule/);
    expect(sharedSrc).not.toMatch(/optimizeFromSchedule\(/);
    expect(sharedSrc).toMatch(/createFleetsFromSchedule/);
    expect(sharedSrc).not.toMatch(/mds-review-fleet-driver-select/);
  });

  test("fixture covers four clusters, urgents, ineligibles, and local 1/2-fleet preview", () => {
    const review = fixtures.getRouteOptEligibilityPreviewReview();
    expect(review.rows.length).toBeGreaterThanOrEqual(20);
    const eligible = review.rows.filter((r) => r.eligible);
    const urgent = eligible.filter((r) => r.urgent);
    expect(eligible.length).toBeGreaterThanOrEqual(16);
    expect(urgent.length).toBeGreaterThanOrEqual(5);
    expect(review.rows.some((r) => !r.eligible && /Surat Jalan/i.test(r.reason || ""))).toBe(true);
    expect(review.rows.some((r) => !r.eligible && /Koordinat/i.test((r.reasons || []).join(" ")))).toBe(true);

    const one = fixtures.buildRouteOptEligibilityCreateFleets({
      orderIds: eligible.map((r) => r.sales_order_id),
      fleetCount: 1,
    });
    expect(one.created).toBe(true);
    expect(one.persisted).toBe(false);
    expect(one.fleet_count).toBe(1);
    expect(one.routes).toHaveLength(1);
    expect(one.schedule_input_fingerprint).toBeTruthy();
    expect(one.created).toBe(true);
    expect(one.persisted).toBe(false);
    expect(one.routes[0].label).toMatch(/Armada 1/);
    expect(new Set(one.routes[0].order_ids).size).toBe(eligible.length);


    const two = fixtures.buildRouteOptEligibilityCreateFleets({
      orderIds: eligible.map((r) => r.sales_order_id),
      fleetCount: 2,
    });
    expect(two.created).toBe(true);
    expect(two.persisted).toBe(false);
    expect(two.fleet_count).toBe(2);
    expect(two.routes).toHaveLength(2);
    const a1 = new Set(two.routes[0].order_ids);
    const a2 = new Set(two.routes[1].order_ids);
    expect(a1.size + a2.size).toBe(eligible.length);
    expect([...a1].some((id) => a2.has(id))).toBe(false);
    for (const panel of two.routes) {
      expect(panel.label).toMatch(/Armada [12]/);
      expect(panel.route_id).toBeTruthy();
      expect(panel.order_ids.length).toBeGreaterThan(0);
    }

    expect(() => fixtures.buildRouteOptEligibilityPreviewFleetPairResult()).toThrow(/retired|previewFleets/i);
  });
});
