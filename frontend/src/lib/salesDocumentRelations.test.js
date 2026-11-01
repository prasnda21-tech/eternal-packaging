import {
  financialStatusLabel,
  fulfillmentRecoveryUi,
  isPartialFailedOneshot,
  normalizeSourceMode,
  partialFailedSources,
  paymentStatusLabel,
  poQtySummary,
  sharedProofSummary,
  sliceLoadingLabel,
  slicePodLabel,
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
});
