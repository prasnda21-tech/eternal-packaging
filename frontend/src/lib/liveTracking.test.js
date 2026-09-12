import { STAGES } from "./constants";
import { liveTrackingRows } from "./liveTracking";
import { resolveTimelineChip } from "./timelineChip";

describe("Live Tracking shares Alur Pekerjaan stages", () => {
  test("exactly 15 stages in the same order and labels", () => {
    const rows = liveTrackingRows({ current_stage: 0 });
    expect(rows.map((r) => r.stage)).toEqual(STAGES);
    expect(rows).toHaveLength(15);
    expect(rows[0].stage).toBe("Order Masuk");
    expect(rows[2].stage).toBe("Approval Design");
    expect(rows[12].stage).toBe("Surat Jalan");
    expect(rows[13].stage).toBe("Loading");
    expect(rows[14].stage).toBe("Pengiriman");
  });

  test("each stage status matches Timeline chip", () => {
    const order = {
      current_stage: 13,
      requires_printing: false,
      work_date: "2026-01-15",
      design_bypassed: true,
      payment_bypassed: true,
      payment_status: "Approved",
      surat_jalan_created: true,
      surat_jalan_status_semantic: "completed",
      loading_qty_status: "completed",
      loading_completed: true,
      loading_confirmations: [{ id: "c1", lines: [{ line_idx: 0, qty: 1000 }] }],
      packing_info: { qty_packed: 1000 },
      eligible_delivery_qty: 1000,
      terkirim_confirmed: false,
      delivery_status: "Belum Terkirim",
    };
    const rows = liveTrackingRows(order);
    rows.forEach((row) => {
      const chip = resolveTimelineChip(row.idx, order, 13);
      expect(row.label).toBe(chip.label);
      expect(row.state).toBe(chip.state);
    });
    expect(rows[11].label).toBe("Selesai");
    expect(rows[12].label).toBe("Selesai");
    expect(rows[13].label).toBe("Loading Selesai");
    expect(rows[14].label).toBe("Menunggu bukti pengiriman");
  });

  test("partial Surat Jalan matches Timeline", () => {
    const order = {
      current_stage: 13,
      surat_jalan_created: true,
      surat_jalan_status_semantic: "partial",
      loading_qty_status: "partial",
      loading_confirmations: [{ id: "c1" }],
    };
    const rows = liveTrackingRows(order);
    expect(rows[12].label).toBe(resolveTimelineChip(12, order, 13).label);
    expect(rows[12].label).toMatch(/parsial/i);
  });
});
