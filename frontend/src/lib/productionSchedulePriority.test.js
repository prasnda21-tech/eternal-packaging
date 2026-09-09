import {
  isProductionScheduleH1,
  isProductionScheduleUrgent,
  productionSchedulePriorityIndicators,
  productionSchedulePriorityRank,
} from "./productionSchedulePriority";

describe("production schedule priority semantics", () => {
  test("URGENT comes from the schedule urgent/priority flag, not deadline status", () => {
    expect(isProductionScheduleUrgent({ urgent: true, delivery_deadline_status: "normal" })).toBe(true);
    expect(isProductionScheduleUrgent({ priority: "urgent", delivery_deadline_status: "h1" })).toBe(true);
    expect(isProductionScheduleUrgent({ urgent: false, priority: "urgent" })).toBe(false);
    expect(isProductionScheduleUrgent({ delivery_deadline_status: "h1" })).toBe(false);
    expect(isProductionScheduleUrgent({ delivery_deadline_status: "overdue" })).toBe(false);
    expect(isProductionScheduleUrgent({ delivery_deadline_status: "h6" })).toBe(false);
  });

  test("H-1 is only delivery_deadline_status === h1", () => {
    expect(isProductionScheduleH1({ delivery_deadline_status: "h1" })).toBe(true);
    expect(isProductionScheduleH1({ delivery_deadline_status: "h6" })).toBe(false);
    expect(isProductionScheduleH1({ delivery_deadline_status: "overdue" })).toBe(false);
    expect(isProductionScheduleH1({ delivery_deadline_status: "terkirim" })).toBe(false);
    expect(isProductionScheduleH1({ delivery_deadline_status: "normal" })).toBe(false);
    expect(isProductionScheduleH1({ urgent: true })).toBe(false);
  });

  test("CASE 1 — URGENT badge", () => {
    expect(productionSchedulePriorityIndicators({
      urgent: true,
      delivery_deadline_status: "normal",
    })).toEqual([{ key: "urgent", label: "URGENT" }]);
  });

  test("CASE 2 — H-1 badge", () => {
    expect(productionSchedulePriorityIndicators({
      urgent: false,
      delivery_deadline_status: "h1",
    })).toEqual([{ key: "h1", label: "H-1" }]);
  });

  test("CASE 3 — normal has no urgency badge", () => {
    expect(productionSchedulePriorityIndicators({
      urgent: false,
      priority: "normal",
      delivery_deadline_status: "normal",
    })).toEqual([]);
  });

  test("CASE 4 — same PO/date payload classifies the same on schedule row and task card", () => {
    const scheduleRow = {
      id: "po-1",
      po_number: "PO-1001",
      urgent: true,
      delivery_deadline_status: "h1",
      delivery_deadline_effective: "2026-09-10T03:00:00+00:00",
    };
    const taskCard = {
      task_id: "helper:po-1:0:-",
      order_id: "po-1",
      order_number: "PO-1001",
      urgent: true,
      delivery_deadline_status: "h1",
      delivery_deadline: "2026-09-10T03:00:00+00:00",
    };
    expect(productionSchedulePriorityIndicators(scheduleRow)).toEqual(
      productionSchedulePriorityIndicators(taskCard),
    );
    expect(productionSchedulePriorityIndicators(scheduleRow)).toEqual([
      { key: "urgent", label: "URGENT" },
      { key: "h1", label: "H-1" },
    ]);
  });

  test("CASE 5 — terkirim/completed deadline status is not H-1; URGENT still follows the flag", () => {
    expect(productionSchedulePriorityIndicators({
      urgent: false,
      delivery_deadline_status: "terkirim",
    })).toEqual([]);
    expect(productionSchedulePriorityIndicators({
      urgent: true,
      delivery_deadline_status: "terkirim",
    })).toEqual([{ key: "urgent", label: "URGENT" }]);
  });

  test("CASE 6 — same-name lines stay independent by payload, not item name", () => {
    const line0 = { task_id: "helper:po-4:0:-", item: "Cup 16oz", urgent: true, delivery_deadline_status: "normal" };
    const line2 = { task_id: "helper:po-4:2:-", item: "Cup 16oz", urgent: false, delivery_deadline_status: "h1" };
    expect(productionSchedulePriorityIndicators(line0)).toEqual([{ key: "urgent", label: "URGENT" }]);
    expect(productionSchedulePriorityIndicators(line2)).toEqual([{ key: "h1", label: "H-1" }]);
  });

  test("rank is URGENT then H-1 then normal", () => {
    expect(productionSchedulePriorityRank({ urgent: true, delivery_deadline_status: "h1" })).toBe(0);
    expect(productionSchedulePriorityRank({ urgent: true, delivery_deadline_status: "normal" })).toBe(0);
    expect(productionSchedulePriorityRank({ urgent: false, delivery_deadline_status: "h1" })).toBe(1);
    expect(productionSchedulePriorityRank({ urgent: false, delivery_deadline_status: "normal" })).toBe(2);
  });
});
