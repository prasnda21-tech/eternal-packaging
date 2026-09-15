import { resolveStageState } from "./stageState";
import {
  cupMarkTone,
  screenMarkTone,
  prodMarkTone,
  packMarkTone,
  loadMarkTone,
  kirimMarkTone,
  chipStateToRoleTone,
  deliveryRoleMarkTones,
} from "./mobileOpsStatusMark";

function printOrder(extra = {}) {
  return {
    design_status: "Approved",
    payment_bypassed: true,
    operator_splits: [{ line_idx: 0, quantity: 10000, qty_completed: 10000 }],
    helper_prep: { qty_prepared: 10000 },
    screener_info: { screen_qty: 10 },
    sample_files: [{ id: "s1" }],
    sample_approved: true,
    line_items: [{ name: "Cetak", quantity: 10000, production_process: "print" }],
    packing_info: { qty_packed: 4000, lines: [{ line_idx: 0, packed_qty: 4000 }] },
    current_stage: 12,
    ...extra,
  };
}

function toneForStage(order, idx) {
  return chipStateToRoleTone(resolveStageState(idx, order, Number(order.current_stage || 0)).state);
}

describe("mobileOpsStatusMark — task-form chips", () => {
  test("1. packing complete in task form → Pack done even when packed qty is partial", () => {
    const order = printOrder({ current_stage: 12 });
    expect(resolveStageState(11, order, 12).state).toBe("done");
    expect(packMarkTone(order)).toBe("done");
    expect(packMarkTone({ qty_packed: 4000, qty_total: 10000, current_stage: 12, ...order })).toBe("done");
  });

  test("2. loading complete → Load done", () => {
    const order = printOrder({
      current_stage: 13,
      surat_jalan_created: true,
      loading_qty_status: "completed",
      loading_confirmations: [{ id: "c1", surat_jalan_id: "sh-1", lines: [{ line_idx: 0, qty: 10000 }] }],
      loading_completed: true,
    });
    expect(resolveStageState(13, order, 13).state).toBe("done");
    expect(loadMarkTone(order)).toBe("done");
  });

  test("3. loading not complete after SJ → Load pending, not done", () => {
    const order = printOrder({
      current_stage: 12,
      surat_jalan_created: true,
      loading_qty_status: "pending",
      loading_confirmations: [],
    });
    expect(resolveStageState(13, order, 12).state).toBe("current");
    expect(loadMarkTone(order)).toBe("pending");
    expect(loadMarkTone(order)).not.toBe("done");
  });

  test("4. pengiriman current + qty_delivered 0 → Kirim pending", () => {
    const order = printOrder({
      current_stage: 13,
      surat_jalan_created: true,
      loading_qty_status: "completed",
      loading_confirmations: [{ id: "c1", lines: [{ line_idx: 0, qty: 10000 }] }],
      eligible_delivery_qty: 10000,
      qty_delivered: 0,
      terkirim_confirmed: false,
      delivery_status: "Belum Terkirim",
    });
    expect(resolveStageState(14, order, 13).state).toBe("current");
    expect(kirimMarkTone(order)).toBe("pending");
    expect(kirimMarkTone(order)).not.toBe("idle");
    expect(kirimMarkTone(order)).not.toBe("done");
  });

  test("5. pengiriman complete → Kirim done", () => {
    const order = printOrder({
      current_stage: 14,
      terkirim_confirmed: true,
      delivery_status: "Terkirim",
      qty_delivered: 10000,
    });
    expect(resolveStageState(14, order, 14).state).toBe("done");
    expect(kirimMarkTone(order)).toBe("done");
  });

  test("payload role_status wins over leftover qty fields", () => {
    expect(packMarkTone({
      qty_packed: 400,
      qty_total: 1000,
      role_status: { pack: "done" },
    })).toBe("done");
    expect(loadMarkTone({ role_status: { load: "done" } })).toBe("done");
    expect(kirimMarkTone({
      percent_delivered: 0,
      qty_delivered: 0,
      qty_total: 1000,
      role_status: { kirim: "pending" },
    })).toBe("pending");
  });

  test("Cup/Screen/Prod follow the same chip tones", () => {
    const order = printOrder();
    expect(cupMarkTone(order)).toBe(toneForStage(order, 7));
    expect(screenMarkTone(order)).toBe(toneForStage(order, 6));
    expect(prodMarkTone(order)).toBe(toneForStage(order, 10));
  });

  test("6. two SJ role_status rows do not copy Load/Kirim", () => {
    const sj1 = deliveryRoleMarkTones({
      id: "po-1",
      role_status: { cup: "done", pack: "done", load: "done", kirim: "done" },
    });
    const sj2 = deliveryRoleMarkTones({
      id: "po-1",
      role_status: { cup: "done", pack: "done", load: "pending", kirim: "idle" },
    });
    expect(sj1.load).toBe("done");
    expect(sj1.kirim).toBe("done");
    expect(sj2.load).toBe("pending");
    expect(sj2.kirim).toBe("idle");
    expect(sj1.pack).toBe("done");
    expect(sj2.pack).toBe("done");
  });
});
