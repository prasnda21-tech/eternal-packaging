import {
  compactPoWorkspaceLabel,
  hydratePoDetailStoredTab,
  poDetailOrderIdFromPath,
  poDetailWorkspaceTab,
} from "./poWorkspaceTab";

describe("poWorkspaceTab", () => {
  test("CASE 9 — compact slash serial and full fallback", () => {
    expect(compactPoWorkspaceLabel("PO/2026/09/09/000018", "ord-a")).toBe("PO 000018");
    expect(compactPoWorkspaceLabel("TASK-SCREEN-SIZE-01", "ord-a")).toBe("TASK-SCREEN-SIZE-01");
    expect(compactPoWorkspaceLabel("", "ord-a")).toBe("PO ord-a");
  });

  test("identity is order_id from /po/:id only", () => {
    expect(poDetailOrderIdFromPath("/po/ord-a")).toBe("ord-a");
    expect(poDetailOrderIdFromPath("/po/ord-a?x=1")).toBe("ord-a");
    expect(poDetailOrderIdFromPath("/po/ord-a/kerja")).toBe(null);
    expect(poDetailWorkspaceTab("/po/ord-a").id).toBe("po-detail:ord-a");
    expect(poDetailWorkspaceTab("/produksi")).toBe(null);
  });

  test("hydrate restores stored dynamic tab without catalog id", () => {
    const hydrated = hydratePoDetailStoredTab({
      id: "po-detail:ord-a",
      path: "/po/ord-a",
      label: "PO 000018",
    });
    expect(hydrated.id).toBe("po-detail:ord-a");
    expect(hydrated.label).toBe("PO 000018");
    expect(hydrated.dynamic).toBe(true);
    expect(hydratePoDetailStoredTab("po-detail:ord-b").path).toBe("/po/ord-b");
    expect(hydratePoDetailStoredTab("dashboard")).toBe(null);
  });
});
