import {
  buildSjSourceAllocationRows,
  financialStatusLabel,
  formatPcs,
  fulfillmentRecoveryUi,
  isPartialFailedOneshot,
  normalizeSourceMode,
  partialFailedSources,
  paymentStatusLabel,
  PO_RELATION_QTY_KPI,
  poQtySummary,
  salesDeliveryDocumentStatusLabel,
  sharedProofSummary,
  sliceLoadingLabel,
  slicePodLabel,
  sjDocumentAllocationStatusLabel,
  sjFulfillmentStatusLabels,
  sjTotalQty,
  sortRelationEvents,
} from "./salesDocumentRelations";

describe("salesDocumentRelations Stage 4", () => {
  it("T10 legacy source_mode defaults to po", () => {
    expect(normalizeSourceMode({})).toBe("po");
    expect(normalizeSourceMode({ source_sj_ids: ["sj-1"] })).toBe("sj");
    expect(normalizeSourceMode({ source_mode: "SJ" })).toBe("sj");
  });

  it("T11 draft is not labeled lunas", () => {
    expect(financialStatusLabel("draft")).toBe("Draft");
    expect(paymentStatusLabel("BELUM DIBAYAR")).toBe("Belum dibayar");
    expect(paymentStatusLabel("LUNAS")).toBe("Lunas");
  });

  it("T6 loading complete is not POD complete", () => {
    expect(sliceLoadingLabel({ loading_complete: true })).toBe("Loading selesai");
    expect(slicePodLabel({ loading_complete: true, delivered: false })).toBe("Belum POD");
  });

  it("T7/T8 partial failed gates oneshot UI helpers", () => {
    expect(
      isPartialFailedOneshot({
        fulfillment_last_status: "partial_failed",
        oneshot_loading_available: false,
      }),
    ).toBe(true);
    expect(isPartialFailedOneshot({ fulfillment_commit_pending: true })).toBe(true);
    const pf = partialFailedSources({
      sources: [
        { source_po_id: "a", po_number: "PO-A", loading_complete: true, delivered: true, has_mirror: true },
        { source_po_id: "b", po_number: "PO-B", loading_complete: true, delivered: false, has_mirror: true },
      ],
    });
    expect(pf.ok.map((s) => s.label)).toEqual(["PO-A"]);
    expect(pf.pending[0].state).toBe("menunggu_pod");
  });

  it("T9 shared proof summary without duplication fields", () => {
    const proof = sharedProofSummary({
      shared_pod_proof: {
        id: "proof-1",
        recipient_name: "Budi",
        photo_count: 2,
        has_signature: true,
      },
    });
    expect(proof).toEqual({
      reference: "proof-1",
      recipient_name: "Budi",
      photo_count: 2,
      has_signature: true,
    });
  });

  it("T12 history sorts by real timestamps", () => {
    const sorted = sortRelationEvents([
      { at: "2026-01-03T00:00:00Z", kind: "c" },
      { at: "2026-01-01T00:00:00Z", kind: "a" },
      { at: "2026-01-02T00:00:00Z", kind: "b" },
    ]);
    expect(sorted.map((e) => e.kind)).toEqual(["a", "b", "c"]);
  });

  it("T4 qty summary mapping", () => {
    expect(
      poQtySummary({
        qty_summary: {
          ordered: 10,
          shipped: 4,
          loaded: 4,
          delivered: 2,
          invoiced: 3,
          remaining_delivery: 6,
          remaining_invoiceable: 7,
        },
      }),
    ).toMatchObject({ ordered: 10, shipped: 4, invoiced: 3 });
  });

  it("T20 Stage 5B recovery UI gates", () => {
    const partial = fulfillmentRecoveryUi({
      recovery_status: "partial",
      resume_loading_available: true,
      complete_sources: ["po-a"],
      pending_sources: ["po-b"],
      reason: "Sebagian",
    });
    expect(partial.resumeAvailable).toBe(true);
    expect(partial.resumeKind).toBe("loading");
    expect(partial.manual).toBe(false);

    const review = fulfillmentRecoveryUi({
      fulfillment_manual_review_required: true,
      recovery_status: "manual_review_required",
    });
    expect(review.manual).toBe(true);
    expect(review.resumeAvailable).toBe(false);

    const running = fulfillmentRecoveryUi({
      fulfillment_commit_pending: true,
      recovery_status: "in_progress",
    });
    expect(running.inProgress).toBe(true);
  });

  it("T1/T2 multi-PO SJ qty uses canonical allocations not primary PO ordered", () => {
    const delivery = {
      total_qty: 5000,
      is_multi_source: true,
      source_po_ids: ["po-a", "po-b"],
      po_id: "po-a",
      line_items: [
        { line_id: "a0", source_po_id: "po-a", source_po_number: "PO/A", name: "Cup PET 16 oz", qty: 3000 },
        { line_id: "b0", source_po_id: "po-b", source_po_number: "PO/B", name: "Cup PET 16 oz", qty: 2000 },
      ],
    };
    expect(sjTotalQty(delivery)).toBe(5000);
    const rows = buildSjSourceAllocationRows({
      delivery,
      sliceInfo: {
        sources: [
          { source_po_id: "po-a", loading_qty: 0, delivered_qty: 0, loading_complete: false, delivered: false },
          { source_po_id: "po-b", loading_qty: 0, delivered_qty: 0, loading_complete: false, delivered: false },
        ],
      },
    });
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.allocated_qty)).toEqual([3000, 2000]);
    expect(rows[0].product).toBe(rows[1].product);
    expect(rows[0].source_po_id).not.toBe(rows[1].source_po_id);
  });

  it("T3/T4 SJ existence is not delivered; loading and POD stay separate", () => {
    const labels = sjFulfillmentStatusLabels(
      { total_qty: 5000, source_po_ids: ["a", "b"], is_multi_source: true },
      {
        loading_complete: true,
        pod_complete: false,
        complete: false,
        loading_qty: 5000,
        delivered_qty: 0,
        sources: [
          { source_po_id: "a", loading_complete: true, delivered: false, loading_qty: 3000, delivered_qty: 0 },
          { source_po_id: "b", loading_complete: true, delivered: false, loading_qty: 2000, delivered_qty: 0 },
        ],
      },
      null,
    );
    expect(labels.documentLabel).toBe("Loading selesai");
    expect(labels.loadingLabel).toBe("Loading selesai");
    expect(labels.podLabel).toBe("Belum POD");
    expect(labels.deliveredQty).toBe(0);
    expect(labels.totalQty).toBe(5000);
    expect(labels.sourcePoCount).toBe(2);
  });

  it("T8 legacy single-PO source row stays one row", () => {
    const rows = buildSjSourceAllocationRows({
      delivery: {
        po_id: "po-1",
        po_number: "PO/1",
        total_qty: 1000,
        line_items: [{ line_id: "l0", name: "Item", qty: 1000 }],
      },
      sliceInfo: { sources: [] },
    });
    expect(rows).toHaveLength(1);
    expect(rows[0].allocated_qty).toBe(1000);
  });

  it("T10 partial failure document label stays accurate", () => {
    const labels = sjFulfillmentStatusLabels(
      { total_qty: 5000, source_po_ids: ["a", "b"] },
      { complete: false, loading_complete: false, sources: [] },
      { fulfillment_last_status: "partial_failed", reason: "fence" },
    );
    expect(labels.documentLabel).toBe("Fulfillment pending");
  });

  it("T6 SELESAI allocation is not POD selesai", () => {
    expect(sjDocumentAllocationStatusLabel("SELESAI")).toBe("Alokasi lengkap");
    expect(sjDocumentAllocationStatusLabel({ status: "SELESAI", is_cancelled: false })).toBe("Alokasi lengkap");
    expect(salesDeliveryDocumentStatusLabel("SELESAI")).toBe("Alokasi lengkap");
    expect(salesDeliveryDocumentStatusLabel("SEBAGIAN")).toBe("Alokasi sebagian");
    expect(slicePodLabel({ status: "SELESAI", loading_complete: false, delivered: false })).toBe("Belum POD");
    expect(sliceLoadingLabel({ status: "SELESAI", loading_complete: false })).toBe("Belum loading");
  });

  it("T7/T8 SJ created ≠ loading ≠ received", () => {
    expect(sjDocumentAllocationStatusLabel("active")).toBe("SJ dibuat");
    expect(sliceLoadingLabel({ loading_complete: true, delivered: false })).toBe("Loading selesai");
    expect(slicePodLabel({ loading_complete: true, delivered: false })).toBe("Belum POD");
    expect(slicePodLabel({ delivered: true })).toBe("POD selesai");
  });

  it("T9 finalized invoice is not lunas", () => {
    expect(financialStatusLabel("finalized")).toBe("Finalized");
    expect(paymentStatusLabel("BELUM DIBAYAR")).toBe("Belum dibayar");
  });

  it("T3 formatPcs uses Indonesian thousands + pcs", () => {
    expect(formatPcs(3000)).toBe("3.000 pcs");
    expect(formatPcs(5000)).toBe("5.000 pcs");
  });

  it("Relasi KPI remaining_delivery label is Belum Masuk SJ not Sisa Kirim", () => {
    const remaining = PO_RELATION_QTY_KPI.find((k) => k.key === "remaining_delivery");
    expect(remaining.label).toBe("Belum Masuk SJ");
    expect(PO_RELATION_QTY_KPI.some((k) => k.label === "Sisa Kirim")).toBe(false);
    expect(poQtySummary({ qty_summary: { remaining_delivery: 0 } }).remaining_delivery).toBe(0);
  });
});
