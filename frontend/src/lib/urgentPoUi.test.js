import {
  URGENT_DISPLAY,
  classifyUrgentDisplay,
  priorityBadgeLabel,
  validateUrgentRequestForm,
  validateUrgentDecisionForm,
  buildPreviewRequest,
  applyPreviewDecision,
  previewScenarioSeed,
  isUrgentPoUiPreviewSearch,
  formatUrgentIdDate,
  formatUrgentIdDateTime,
} from "./urgentPoUi";

describe("urgentPoUi classification", () => {
  test("pending keeps badge NORMAL even if someone mis-sets priority", () => {
    const request = { status: "pending" };
    expect(classifyUrgentDisplay({ priority: "normal", request })).toBe(URGENT_DISPLAY.PENDING_REQUEST);
    expect(priorityBadgeLabel({ priority: "normal", request })).toBe("NORMAL");
    // Pending must not be treated as approved urgent
    expect(classifyUrgentDisplay({ priority: "urgent", request })).toBe(URGENT_DISPLAY.PENDING_REQUEST);
  });

  test("legacy urgent without request stays LEGACY_URGENT", () => {
    expect(classifyUrgentDisplay({ priority: "urgent", request: null })).toBe(URGENT_DISPLAY.LEGACY_URGENT);
    expect(priorityBadgeLabel({ priority: "urgent", request: null })).toBe("URGENT");
  });

  test("approved request + urgent priority is APPROVED_URGENT", () => {
    const request = { status: "approved" };
    expect(classifyUrgentDisplay({ priority: "urgent", request })).toBe(URGENT_DISPLAY.APPROVED_URGENT);
    expect(priorityBadgeLabel({ priority: "urgent", request })).toBe("URGENT");
  });

  test("rejected keeps NORMAL badge", () => {
    const request = { status: "rejected" };
    expect(classifyUrgentDisplay({ priority: "normal", request })).toBe(URGENT_DISPLAY.NORMAL);
    expect(priorityBadgeLabel({ priority: "normal", request })).toBe("NORMAL");
  });
});

describe("urgentPoUi validation", () => {
  test("request form requires reason and target date", () => {
    const empty = validateUrgentRequestForm({});
    expect(empty.ok).toBe(false);
    expect(empty.errors.reason).toBeTruthy();
    expect(empty.errors.targetDate).toBeTruthy();

    const ok = validateUrgentRequestForm({
      reason: "Butuh cepat",
      targetDate: "2026-10-28",
      notes: "opsional",
    });
    expect(ok.ok).toBe(true);
    expect(ok.values.reason).toBe("Butuh cepat");
  });

  test("reject decision requires notes", () => {
    const missing = validateUrgentDecisionForm({ decision: "rejected", decisionNotes: "" });
    expect(missing.ok).toBe(false);
    expect(missing.errors.decisionNotes).toBeTruthy();

    const approve = validateUrgentDecisionForm({ decision: "approved", decisionNotes: "" });
    expect(approve.ok).toBe(true);
  });
});

describe("urgentPoUi preview state machine", () => {
  test("submit → pending does not imply urgent priority in seed helpers", () => {
    const req = buildPreviewRequest({
      reason: "Outlet baru",
      targetDate: "2026-10-28",
    });
    expect(req.status).toBe("pending");
    const pending = previewScenarioSeed("pending");
    expect(pending.order.priority).toBe("normal");
    expect(classifyUrgentDisplay({ priority: pending.order.priority, request: pending.request }))
      .toBe(URGENT_DISPLAY.PENDING_REQUEST);
  });

  test("approve/reject preview helpers", () => {
    const pending = previewScenarioSeed("pending").request;
    const approved = applyPreviewDecision(pending, { decision: "approved", decisionNotes: "ok" });
    expect(approved.status).toBe("approved");
    const rejected = applyPreviewDecision(pending, { decision: "rejected", decisionNotes: "penuh" });
    expect(rejected.status).toBe("rejected");
    expect(rejected.decision_notes).toBe("penuh");
  });

  test("legacy scenario has urgent priority and no request", () => {
    const legacy = previewScenarioSeed("legacy_urgent");
    expect(legacy.order.priority).toBe("urgent");
    expect(legacy.request).toBeNull();
    expect(classifyUrgentDisplay({ priority: legacy.order.priority, request: legacy.request }))
      .toBe(URGENT_DISPLAY.LEGACY_URGENT);
  });

  test("preview search helper", () => {
    expect(isUrgentPoUiPreviewSearch("?urgent_ui_preview=1")).toBe(true);
    expect(isUrgentPoUiPreviewSearch("?foo=1")).toBe(false);
  });

  test("Indonesian display date companion", () => {
    expect(formatUrgentIdDate("2026-10-28")).toBe("28 Okt 2026");
    expect(formatUrgentIdDate("")).toBe("");
    expect(formatUrgentIdDateTime("2026-10-21T04:12:00.000Z")).toMatch(/Okt 2026/);
  });
});
