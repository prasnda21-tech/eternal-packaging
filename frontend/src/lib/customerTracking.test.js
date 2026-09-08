import {
  approvalActionPath,
  compactItemSummary,
  currentHeadline,
  documentRows,
  documentTiles,
  hasApprovalCta,
  itemLineLabel,
  railTone,
  RAIL_DISPLAY_ORDER,
  stageBlurb,
  stageShortLabel,
  trackingDisplayLabel,
  trackingStatusClass,
  trackingStatusLabel,
  workflowRailModel,
} from "./customerTracking";

describe("customer tracking status labels", () => {
  test("maps the three public statuses only", () => {
    expect(trackingStatusLabel("completed")).toBe("Selesai");
    expect(trackingStatusLabel("in_progress")).toBe("Proses");
    expect(trackingStatusLabel("waiting")).toBe("Menunggu");
    expect(trackingStatusLabel("bypass")).toBe("Menunggu");
    expect(trackingStatusLabel("error")).toBe("Menunggu");
  });

  test("prefers Alur Pekerjaan label from payload", () => {
    expect(trackingDisplayLabel({ status: "in_progress", label: "Menunggu bukti pengiriman" }))
      .toBe("Menunggu bukti pengiriman");
    expect(trackingDisplayLabel({ status: "completed" })).toBe("Selesai");
  });

  test("badge classes stay compact", () => {
    expect(trackingStatusClass("completed")).toMatch(/is-done/);
    expect(trackingStatusClass("in_progress")).toMatch(/is-run/);
    expect(trackingStatusClass("waiting")).toMatch(/is-wait/);
  });

  test("approval CTA hidden without existing path", () => {
    expect(hasApprovalCta(undefined)).toBe(false);
    expect(hasApprovalCta({ status: "active" })).toBe(false);
    expect(hasApprovalCta({ status: "approved", path: "/customer/design-approval/tok" })).toBe(false);
  });

  test("approval CTA uses existing approval page path only", () => {
    const path = "/customer/design-approval/tok-public-aaaaaaaaaaaaaaaa";
    expect(approvalActionPath({ status: "active", path })).toBe(path);
    expect(hasApprovalCta({ status: "active", path })).toBe(true);
    expect(approvalActionPath({ status: "active", path: "https://evil.example/x" })).toBe("");
  });

  test("completed and revision statuses stay existing", () => {
    expect(currentHeadline([{ stage: "Approval Design", status: "in_progress" }], { status: "active" }))
      .toBe("Sedang Approval Design");
    expect(stageBlurb("Approval Design", { label: "Menunggu approval" }, { status: "approved" }))
      .toBe("Design disetujui");
    expect(stageBlurb("Approval Design", { label: "Menunggu approval" }, undefined))
      .toBe("Menunggu link approval");
  });

  test("documents omit fake links and keep same-name items distinct", () => {
    const rows = documentRows({
      documents: {
        design_approved: [],
        surat_jalan: [{ id: "sj1", name: "SJ-1", preview_url: "/api/public/tracking/t/surat-jalan/sj1" }],
      },
      delivery_proofs: [],
    });
    expect(rows.some((r) => r.kind === "pdf" && r.available && r.href.includes("/surat-jalan/"))).toBe(true);
    expect(documentRows({ documents: { design_approved: [], surat_jalan: [] } })).toEqual([]);
    expect(itemLineLabel({ name: "Kaos", color: "Merah" })).toBe("Kaos · Merah");
    expect(itemLineLabel({ name: "Kaos" })).toBe("Kaos");
  });

  test("compact summary does not merge same-name lines", () => {
    expect(compactItemSummary([{ name: "Kaos", quantity: 10, unit: "pcs" }])).toEqual({
      item: "Kaos",
      qty: "10 pcs",
    });
    expect(compactItemSummary([
      { name: "Kaos", quantity: 10 },
      { name: "Kaos", quantity: 5 },
    ])).toEqual({ item: "2 item", qty: "2 baris" });
    expect(stageShortLabel("Approval Design")).toBe("Approval Design");
    expect(stageShortLabel("Persiapan Helper")).toBe("Persiapan");
  });

  test("customer-facing rail is sequential Screener then Persiapan", () => {
    const stages = [
      "Order Masuk", "Design", "Approval Design", "Payment", "Approval Payment",
      "Penjadwalan", "Screener", "Persiapan Helper", "Operator", "Konfirmasi Sample",
      "Cetak", "Packing", "Surat Jalan", "Loading", "Pengiriman",
    ];
    const workflow = stages.map((stage, i) => ({
      stage,
      status: i < 2 ? "completed" : i === 2 ? "in_progress" : "waiting",
    }));
    const rail = workflowRailModel(workflow);
    expect(rail.branched).toBe(false);
    expect(rail.displayOrder).toEqual(RAIL_DISPLAY_ORDER);
    expect(rail.displayOrder.indexOf("Screener")).toBeLessThan(rail.displayOrder.indexOf("Persiapan Helper"));
    expect(rail.displayOrder.indexOf("Persiapan Helper")).toBeLessThan(rail.displayOrder.indexOf("Operator"));
    expect(rail.nodes.map((n) => n.stage)).toEqual(RAIL_DISPLAY_ORDER);
    expect(rail.nodes[6].x).toBeGreaterThan(rail.nodes[7].x);
    expect(rail.nodes[5].x).toBe(90);
    expect(rail.nodes[9].x).toBe(10);
    expect(rail.nodes.find((n) => n.stage === "Design").status).toBe("completed");
    expect(rail.nodes.find((n) => n.stage === "Approval Design").status).toBe("in_progress");
    expect(rail.segments.some((s) => /Q620|toFork|diamond/.test(s.d))).toBe(false);
    expect(railTone("completed")).toBe("done");
    expect(railTone("in_progress")).toBe("run");
    expect(railTone("waiting")).toBe("wait");
  });

  test("document tiles keep payment unavailable and disable empty hrefs", () => {
    const tiles = documentTiles({
      documents: {
        design_approved: [{ preview_url: "/api/public/tracking/t/design/1", content_type: "image/png" }],
        surat_jalan: [],
      },
      delivery_proofs: [],
    });
    expect(tiles).toHaveLength(4);
    expect(tiles.find((t) => t.key === "design").available).toBe(true);
    expect(tiles.find((t) => t.key === "payment")).toMatchObject({ available: false, href: "", status: "Belum tersedia" });
    expect(tiles.find((t) => t.key === "sj").available).toBe(false);
    expect(tiles.find((t) => t.key === "ship").available).toBe(false);
  });
});
