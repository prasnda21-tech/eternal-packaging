import { ASSIGN_APPROVAL_DESIGN_NOTE, approvalDesignAssignEvent } from "./approvalDesignHistory";

describe("approvalDesignAssignEvent", () => {
  test("uses server assign timestamp not approve time", () => {
    const ev = approvalDesignAssignEvent({
      timeline: [
        { stage: 1, at: "2026-08-29T03:00:00+00:00", note: "Upload design: a.png" },
        { stage: 2, at: "2026-08-29T04:15:00+00:00", note: ASSIGN_APPROVAL_DESIGN_NOTE, by_name: "Sinta" },
        { stage: 2, at: "2026-08-29T06:00:00+00:00", note: "Design di-approve", by_name: "Customer: Budi" },
      ],
      customer_approval: { generated_at: "2026-08-29T04:15:00+00:00", generated_by_name: "Sinta" },
    });
    expect(ev.at).toBe("2026-08-29T04:15:00+00:00");
    expect(ev.name).toBe("Sinta");
    expect(ev.method).toBe("Link");
  });

  test("historical record without timestamp stays empty", () => {
    const ev = approvalDesignAssignEvent({
      timeline: [{ stage: 2, at: "2026-08-01T00:00:00+00:00", note: "Design di-approve", by_name: "Admin" }],
      customer_approval: { status: "approved" },
      customer_approval_history: [{ action: "generated", by_name: "Sinta" }],
    });
    expect(ev.at).toBeNull();
    expect(ev.name).toBe("Sinta");
    expect(ev.method).toBeNull();
  });

  test("falls back to customer_approval.generated_at", () => {
    const ev = approvalDesignAssignEvent({
      timeline: [],
      customer_approval: { generated_at: "2026-08-28T10:00:00+00:00", generated_by_name: "Sinta" },
    });
    expect(ev.at).toBe("2026-08-28T10:00:00+00:00");
    expect(ev.method).toBe("Link");
  });
});
