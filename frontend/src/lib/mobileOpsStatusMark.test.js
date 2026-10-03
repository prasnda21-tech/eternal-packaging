import {
  cupMarkTone,
  screenMarkTone,
  prodMarkTone,
  packMarkTone,
  loadMarkTone,
  kirimMarkTone,
  qtyMarkTone,
  prodCompletedQty,
  deliveryRoleMarkTones,
} from "./mobileOpsStatusMark";

describe("mobileOpsStatusMark — 3-state persisted evidence", () => {
  test("qty tones stay idle when both sides missing", () => {
    expect(qtyMarkTone(null, null)).toBe("idle");
    expect(qtyMarkTone(0, 0)).toBe("idle");
  });

  test("1. Cup: no progress gray, partial amber, complete green", () => {
    expect(cupMarkTone({})).toBe("idle");
    expect(cupMarkTone({ helper_required: 100, helper_prepared: 0 })).toBe("idle");
    expect(cupMarkTone({ helper_required: 100, helper_prepared: 20 })).toBe("pending");
    expect(cupMarkTone({ helper_required: 100, helper_prepared: 100 })).toBe("done");
  });

  test("2. Screen: empty gray, saved qty green; no invented amber", () => {
    expect(screenMarkTone({})).toBe("idle");
    expect(screenMarkTone({ current_stage: 8 })).toBe("idle");
    expect(screenMarkTone({ screen_qty: 10 })).toBe("done");
    expect(screenMarkTone({ screen_qty: 1, current_stage: 6 })).toBe("done");
  });

  test("3. Cetak: 0 / partial / complete vs print_qty_total", () => {
    expect(prodMarkTone({})).toBe("idle");
    expect(prodMarkTone({
      print_qty_total: 50,
      operators: [{ split_id: "s1", line_printable: true, qty_completed: 0 }],
    })).toBe("idle");
    expect(prodMarkTone({
      print_qty_total: 50,
      operators: [{ split_id: "s1", line_printable: true, qty_completed: 10 }],
    })).toBe("pending");
    expect(prodMarkTone({
      print_qty_total: 50,
      operators: [{ split_id: "s1", line_printable: true, qty_completed: 50 }],
    })).toBe("done");
  });

  test("Cetak maps delivery qty_printed_operator when operators are absent", () => {
    expect(prodCompletedQty({
      print_qty_total: 1000,
      qty_printed_operator: 400,
    })).toBe(400);
    expect(prodMarkTone({
      print_qty_total: 1000,
      qty_printed_operator: 400,
    })).toBe("pending");
    expect(prodMarkTone({
      print_qty_total: 1000,
      qty_printed_operator: 1000,
    })).toBe("done");
  });

  test("4. Pack: Belum gray, Berjalan amber, Sudah green", () => {
    expect(packMarkTone({ packing_status: "Belum" })).toBe("idle");
    expect(packMarkTone({ packing_status: "Berjalan", packing_qty: 4000, qty_total: 10000 })).toBe("pending");
    expect(packMarkTone({ packing_status: "Sudah" })).toBe("done");
    expect(packMarkTone({ qty_packed: 0, qty_total: 1000 })).toBe("idle");
    expect(packMarkTone({ qty_packed: 400, qty_total: 1000 })).toBe("pending");
    expect(packMarkTone({ qty_packed: 1000, qty_total: 1000 })).toBe("done");
    expect(packMarkTone({
      packing_status: "Berjalan",
      role_status: { pack: "done" },
      current_stage: 12,
    })).toBe("pending");
  });

  test("5. Load: 0 / partial / complete per-SJ from qty_loaded vs SJ need", () => {
    const need = { qty_load_need: 5000, sj_item_lines: [{ qty: 5000 }] };
    expect(loadMarkTone({})).toBe("idle");
    expect(loadMarkTone({ ...need, qty_loaded: 0 })).toBe("idle");
    expect(loadMarkTone({ ...need, qty_loaded: 2000 })).toBe("pending");
    expect(loadMarkTone({ ...need, qty_loaded: 5000 })).toBe("done");
    expect(loadMarkTone({
      qty_loaded: 5000,
      qty_load_need: 5000,
      role_status: { load: "idle" },
      current_stage: 12,
    })).toBe("done");
  });

  test("6. Kirim: 0 / partial / complete per-SJ from qty_delivered_sj; ignores PO percent", () => {
    expect(kirimMarkTone({})).toBe("idle");
    expect(kirimMarkTone({
      percent_delivered: 100,
      qty_delivered: 1000,
      qty_total: 1000,
    })).toBe("idle");
    expect(kirimMarkTone({ qty_delivered_sj: 0, qty_sj: 5000 })).toBe("idle");
    expect(kirimMarkTone({ qty_delivered_sj: 2000, qty_sj: 5000 })).toBe("pending");
    expect(kirimMarkTone({ qty_delivered_sj: 5000, qty_sj: 5000 })).toBe("done");
  });

  test("7. two SJ rows isolate Load and Kirim", () => {
    const sj1 = deliveryRoleMarkTones({
      id: "po-1",
      shipment_id: "sh-1",
      packing_status: "Sudah",
      qty_loaded: 5000,
      qty_load_need: 5000,
      qty_delivered_sj: 5000,
      qty_sj: 5000,
      percent_delivered: 50,
    });
    const sj2 = deliveryRoleMarkTones({
      id: "po-1",
      shipment_id: "sh-2",
      packing_status: "Sudah",
      qty_loaded: 2000,
      qty_load_need: 5000,
      qty_delivered_sj: 0,
      qty_sj: 5000,
      percent_delivered: 50,
    });
    expect(sj1.load).toBe("done");
    expect(sj2.load).toBe("pending");
    expect(sj1.kirim).toBe("done");
    expect(sj2.kirim).toBe("idle");
    expect(sj1.pack).toBe("done");
    expect(sj2.pack).toBe("done");
  });

  test("delivery payload without cup/screen fields stays gray while packing is active", () => {
    const deliveryOnly = {
      print_qty_total: 1000,
      qty_printed_operator: 1000,
      qty_packed: 400,
      qty_total: 1000,
      qty_delivered: 0,
      percent_delivered: 0,
    };
    expect(deliveryRoleMarkTones(deliveryOnly, false)).toEqual({
      cup: "idle",
      screen: "idle",
      prod: "done",
      pack: "pending",
      load: "idle",
      kirim: "idle",
    });
  });

  test("production loading or failure is not the same mark as belum selesai", () => {
    const row = {
      qty_loaded: 2500,
      qty_load_need: 5000,
      qty_delivered_sj: 1000,
      qty_sj: 5000,
      helper_prepared: 0,
      helper_required: 5000,
      print_qty_total: 5000,
      print_completed_qty: 0,
      packing_status: "Belum",
    };
    for (const state of ["loading", "failed"]) {
      const tones = deliveryRoleMarkTones(row, false, state);
      expect(tones.cup).toBe("unavailable");
      expect(tones.screen).toBe("unavailable");
      expect(tones.prod).toBe("unavailable");
      expect(tones.pack).toBe("unavailable");
      expect(tones.load).toBe("pending");
      expect(tones.kirim).toBe("pending");
    }
    expect(deliveryRoleMarkTones(row, false, "ready").cup).toBe("idle");
    expect(deliveryRoleMarkTones(row, false, "ready").load).toBe("pending");
  });

  test("does not fabricate earlier roles from later packing or kirim", () => {
    const packedAndShipped = {
      qty_packed: 1000,
      qty_total: 1000,
      qty_delivered_sj: 400,
      qty_sj: 1000,
    };
    const tones = deliveryRoleMarkTones(packedAndShipped, false);
    expect(tones.cup).toBe("idle");
    expect(tones.screen).toBe("idle");
    expect(tones.prod).toBe("idle");
    expect(tones.pack).toBe("done");
    expect(tones.load).toBe("idle");
    expect(tones.kirim).toBe("pending");
  });
});
