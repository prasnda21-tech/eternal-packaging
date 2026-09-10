import {
  MOBILE_WORKSPACES,
  activeMobileNav,
  loadingAvailability,
  mobileTaskHref,
  mobileWorkStatus,
  packingMetrics,
  permittedMobileWorkspaces,
  poCardPresentation,
  selectMobileWorkspace,
  taskSummaryFromList,
  visibleMobileNavItems,
} from "./mobileWorkspaces";
import { canSubmitConfirmDelivered } from "./deliveryProof";

const permSet = (...keys) => (p) => keys.includes(p);

describe("mobile workspace permissions", () => {
  test("helper.prepare / packing.update / loading.update are independent", () => {
    expect(permittedMobileWorkspaces(permSet("helper.prepare")).map((w) => w.id)).toEqual(["prep"]);
    expect(permittedMobileWorkspaces(permSet("packing.update")).map((w) => w.id)).toEqual(["packing"]);
    expect(permittedMobileWorkspaces(permSet("loading.update")).map((w) => w.id)).toEqual(["loading"]);
    expect(permittedMobileWorkspaces(permSet("helper.prepare", "packing.update", "loading.update")).map((w) => w.id))
      .toEqual(["prep", "packing", "loading"]);
  });

  test("screener / operator / delivery permissions", () => {
    expect(permittedMobileWorkspaces(permSet("screen.update")).map((w) => w.id)).toEqual(["screener"]);
    expect(permittedMobileWorkspaces(permSet("operator.update")).map((w) => w.id)).toEqual(["print"]);
    expect(permittedMobileWorkspaces(permSet("operator.sample_upload")).map((w) => w.id)).toEqual(["sample"]);
    expect(permittedMobileWorkspaces(permSet("operator.update", "operator.sample_upload")).map((w) => w.id))
      .toEqual(["print", "sample"]);
    expect(permittedMobileWorkspaces(permSet("delivery.update")).map((w) => w.id)).toEqual(["delivery"]);
  });

  test("unauthorized workspace query does not grant action", () => {
    const helper = permSet("helper.prepare", "packing.update");
    const result = selectMobileWorkspace(helper, "delivery");
    expect(result.unauthorized).toBe(true);
    expect(result.selected.id).toBe("prep");
    expect(result.permitted.some((w) => w.id === "delivery")).toBe(false);
  });

  test("deterministic first permitted workspace", () => {
    const driver = permSet("loading.update", "delivery.update");
    expect(selectMobileWorkspace(driver, null).selected.id).toBe("loading");
    expect(selectMobileWorkspace(driver, "delivery").selected.id).toBe("delivery");
    expect(selectMobileWorkspace(permSet(), "packing").selected).toBe(null);
  });

  test("catalog has no Semua workspace", () => {
    expect(MOBILE_WORKSPACES.some((w) => /semua/i.test(w.label) || w.id === "all")).toBe(false);
  });
});

describe("operator / packing / loading presentation", () => {
  test("operator status 0 / partial / done", () => {
    expect(mobileWorkStatus(0, 100).label).toBe("Belum Mulai");
    expect(mobileWorkStatus(40, 100).label).toBe("Proses");
    expect(mobileWorkStatus(100, 100).label).toBe("Selesai");
    expect(mobileWorkStatus(120, 100).label).toBe("Selesai");
  });

  test("packing metrics preserve line_idx identity", () => {
    const metrics = packingMetrics({}, [
      { line_idx: 0, name: "Cup A", packed: 10, remaining: 5, qty_po: 15, print_completed: 15 },
      { line_idx: 1, name: "Cup A", packed: 2, remaining: 8, qty_po: 10, print_completed: 10 },
    ]);
    expect(metrics.lines).toHaveLength(2);
    expect(metrics.lines[0].line_idx).toBe(0);
    expect(metrics.lines[1].line_idx).toBe(1);
    expect(metrics.lines[0].name).toBe("Cup A");
    expect(metrics.lines[1].name).toBe("Cup A");
    expect(metrics.packed).toBe(12);
  });

  test("loading blocked without packing or SJ", () => {
    const noPack = loadingAvailability({}, [{ line_idx: 0, packed: 0, loaded: 0, available: 0 }], [{ id: "sj1" }]);
    expect(noPack.blocked).toBe(true);
    expect(noPack.reason).toMatch(/Packing/i);

    const noSj = loadingAvailability({}, [{ line_idx: 0, packed: 10, loaded: 0, available: 10 }], []);
    expect(noSj.blocked).toBe(true);
    expect(noSj.reason).toMatch(/Surat Jalan/i);

    const ok = loadingAvailability({ surat_jalan_created: true }, [{ line_idx: 0, packed: 10, loaded: 2, available: 8 }], [{ id: "sj1" }]);
    expect(ok.blocked).toBe(false);
    expect(ok.packed).toBe(10);
    expect(ok.loaded).toBe(2);
    expect(ok.remaining).toBe(8);
  });
});

describe("mobile cards and nav", () => {
  test("PO cards do not merge same-name lines", () => {
    const card = poCardPresentation({
      line_items: [
        { name: "Cup A", quantity: 100 },
        { name: "Cup A", quantity: 50 },
      ],
    });
    expect(card.lines).toHaveLength(2);
    expect(card.lines[0].line_idx).toBe(0);
    expect(card.lines[1].line_idx).toBe(1);
    expect(card.qty).toBe(null);
    expect(card.product).toMatch(/\+1 line/);
  });

  test("task href is presentational workspace only", () => {
    expect(mobileTaskHref("abc", "packing")).toBe("/m/orders/abc?ws=packing");
  });

  test("bottom nav active state", () => {
    expect(activeMobileNav("/m/tasks")).toBe("tasks");
    expect(activeMobileNav("/m/orders/123?ws=print")).toBe("orders");
    expect(activeMobileNav("/m/schedule")).toBe("schedule");
    expect(activeMobileNav("/m/delivery-schedule")).toBe("deliverySchedule");
    expect(activeMobileNav("/m/notifications")).toBe("notifications");
    expect(activeMobileNav("/m/profile")).toBe("profile");
    expect(activeMobileNav("/po/123")).toBe(null);
  });

  test("Jadwal Produksi and Jadwal Pengiriman nav are permission gated, not role-name gated", () => {
    expect(visibleMobileNavItems((p) => p === "schedule.view").map((i) => i.id))
      .toEqual(["tasks", "orders", "schedule", "deliverySchedule", "notifications", "profile"]);
    expect(visibleMobileNavItems((p) => p === "nav.production_schedule").map((i) => i.id))
      .toEqual(["tasks", "orders", "schedule", "notifications", "profile"]);
    expect(visibleMobileNavItems((p) => p === "nav.delivery_schedule").map((i) => i.id))
      .toEqual(["tasks", "orders", "deliverySchedule", "notifications", "profile"]);
    expect(visibleMobileNavItems((p) => p === "operator.update").map((i) => i.id))
      .toEqual(["tasks", "orders", "notifications", "profile"]);
    expect(visibleMobileNavItems((p) => p === "admin").map((i) => i.id))
      .toEqual(["tasks", "orders", "notifications", "profile"]);
    expect(visibleMobileNavItems((p) => p === "driver").map((i) => i.id))
      .toEqual(["tasks", "orders", "notifications", "profile"]);
  });

  test("task counts come from loaded list only", () => {
    const summary = taskSummaryFromList([
      { status: "belum" },
      { status: "sebagian" },
      { status: "selesai" },
    ]);
    expect(summary.hariIni).toBe(3);
    expect(summary.aktif).toBe(2);
    expect(summary.proses).toBe(1);
    expect(summary.menunggu).toBe(1);
  });
});

describe("delivery CTA requirements stay authoritative", () => {
  test("photos + recipient + both signatures required", () => {
    const proofs = [{ id: "1" }, { id: "2" }];
    expect(canSubmitConfirmDelivered(proofs, "Budi", null, null, null, null)).toBe(false);
    expect(canSubmitConfirmDelivered(proofs, "Budi", "data:image/png;base64,aa", null, null, null)).toBe(false);
    expect(canSubmitConfirmDelivered(
      proofs,
      "Budi",
      "data:image/png;base64,aa",
      null,
      "data:image/png;base64,bb",
      null,
    )).toBe(true);
  });
});
