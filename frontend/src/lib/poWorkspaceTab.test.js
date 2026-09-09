import {
  compactPoWorkspaceLabel,
  hydratePoDetailStoredTab,
  poDetailOrderIdFromPath,
  poDetailWorkspaceLabel,
  poDetailWorkspaceTab,
  poDetailWorkspaceTitle,
  shouldUpgradePoDetailTabLabel,
} from "./poWorkspaceTab";

describe("poWorkspaceTab", () => {
  test("CASE 1 — customer label wins over PO number", () => {
    expect(poDetailWorkspaceLabel({
      customer: "WELCOME TO PLEASURE",
      po_number: "PO/2026/09/09/000018",
    }, "ord-a")).toBe("WELCOME TO PLEASURE");
    expect(poDetailWorkspaceTab("/po/ord-a", {
      customer: "WELCOME TO PLEASURE",
      po_number: "PO/2026/09/09/000018",
    }).label).toBe("WELCOME TO PLEASURE");
  });

  test("CASE 2 — another customer label", () => {
    expect(poDetailWorkspaceTab("/po/ord-b", {
      customer: "PT ABC",
      po_number: "PO/2026/09/09/000017",
    }).label).toBe("PT ABC");
  });

  test("CASE 3 — missing customer falls back to compact PO", () => {
    expect(poDetailWorkspaceLabel({ po_number: "PO/2026/09/09/000018" }, "ord-a")).toBe("PO 000018");
    expect(compactPoWorkspaceLabel("PO/2026/09/09/000018", "ord-a")).toBe("PO 000018");
    expect(compactPoWorkspaceLabel("TASK-SCREEN-SIZE-01", "ord-a")).toBe("TASK-SCREEN-SIZE-01");
  });

  test("CASE 4 — missing customer + PO number falls back to order_id", () => {
    expect(poDetailWorkspaceLabel({}, "ord-a")).toBe("PO ord-a");
    expect(compactPoWorkspaceLabel("", "ord-a")).toBe("PO ord-a");
    expect(poDetailWorkspaceTab("/po/ord-a", null).label).toBe("PO ord-a");
  });

  test("CASE 5 — same customer different order_id stay distinct tabs", () => {
    const a = poDetailWorkspaceTab("/po/order-a", { customer: "WELCOME TO PLEASURE" });
    const b = poDetailWorkspaceTab("/po/order-b", { customer: "WELCOME TO PLEASURE" });
    expect(a.id).toBe("po-detail:order-a");
    expect(b.id).toBe("po-detail:order-b");
    expect(a.id).not.toBe(b.id);
    expect(a.label).toBe("WELCOME TO PLEASURE");
    expect(b.label).toBe("WELCOME TO PLEASURE");
    expect(a.path).toBe("/po/order-a");
    expect(b.path).toBe("/po/order-b");
  });

  test("identity is order_id from /po/:id only", () => {
    expect(poDetailOrderIdFromPath("/po/ord-a")).toBe("ord-a");
    expect(poDetailOrderIdFromPath("/po/ord-a?x=1")).toBe("ord-a");
    expect(poDetailOrderIdFromPath("/po/ord-a/kerja")).toBe(null);
    expect(poDetailWorkspaceTab("/po/ord-a").id).toBe("po-detail:ord-a");
    expect(poDetailWorkspaceTab("/produksi")).toBe(null);
  });

  test("CASE 7 — null React Router location.state does not throw (workspace tab return)", () => {
    expect(() => poDetailWorkspaceTab("/po/ord-a", null)).not.toThrow();
    const tab = poDetailWorkspaceTab("/po/ord-a", null);
    expect(tab.id).toBe("po-detail:ord-a");
    expect(tab.path).toBe("/po/ord-a");
    expect(tab.label).toBe("PO ord-a");
    expect(poDetailWorkspaceTab("/po/ord-a", { po_number: "PO/2026/09/09/000018" }).label).toBe("PO 000018");
  });

  test("optional tooltip title includes customer and full PO number", () => {
    expect(poDetailWorkspaceTitle({
      customer: "WELCOME TO PLEASURE",
      po_number: "PO/2026/09/09/000018",
    }, "ord-a")).toBe("WELCOME TO PLEASURE — PO/2026/09/09/000018");
  });

  test("CASE 9 — hydrate restores stored customer label without catalog id", () => {
    const hydrated = hydratePoDetailStoredTab({
      id: "po-detail:ord-a",
      path: "/po/ord-a",
      label: "WELCOME TO PLEASURE",
      title: "WELCOME TO PLEASURE — PO/2026/09/09/000018",
      labelPriority: 2,
    });
    expect(hydrated.id).toBe("po-detail:ord-a");
    expect(hydrated.label).toBe("WELCOME TO PLEASURE");
    expect(hydrated.title).toBe("WELCOME TO PLEASURE — PO/2026/09/09/000018");
    expect(hydrated.labelPriority).toBe(2);
    expect(hydrated.dynamic).toBe(true);
    expect(hydratePoDetailStoredTab("po-detail:ord-b").path).toBe("/po/ord-b");
    expect(hydratePoDetailStoredTab("dashboard")).toBe(null);
  });

  test("null meta must not upgrade existing customer label", () => {
    const existing = poDetailWorkspaceTab("/po/ord-a", {
      customer: "WELCOME TO PLEASURE",
      po_number: "PO/2026/09/09/000018",
    });
    const entry = poDetailWorkspaceTab("/po/ord-a", null);
    expect(shouldUpgradePoDetailTabLabel(existing, entry, null)).toBe(false);
    expect(shouldUpgradePoDetailTabLabel(existing, entry, undefined)).toBe(false);
  });

  test("po_number-only meta must not downgrade customer label", () => {
    const existing = poDetailWorkspaceTab("/po/ord-a", {
      customer: "WELCOME TO PLEASURE",
      po_number: "PO/2026/09/09/000018",
    });
    const entry = poDetailWorkspaceTab("/po/ord-a", { po_number: "PO/2026/09/09/000018" });
    expect(shouldUpgradePoDetailTabLabel(existing, entry, { po_number: "PO/2026/09/09/000018" })).toBe(false);
  });
});
