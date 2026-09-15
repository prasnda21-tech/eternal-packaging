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

describe("mobileOpsStatusMark", () => {
  test("qty tones stay idle when both sides missing", () => {
    expect(qtyMarkTone(null, null)).toBe("idle");
    expect(qtyMarkTone(0, 0)).toBe("idle");
  });

  test("cup uses helper_prepared vs helper_required", () => {
    expect(cupMarkTone({})).toBe("idle");
    expect(cupMarkTone({ helper_required: 100, helper_prepared: 20 })).toBe("pending");
    expect(cupMarkTone({ helper_required: 100, helper_prepared: 100 })).toBe("done");
  });

  test("screen uses existing screenerFilled", () => {
    expect(screenMarkTone({ screen_qty: 10, current_stage: 8 })).toBe("done");
    expect(screenMarkTone({ current_stage: 8 })).toBe("pending");
  });

  test("prod uses printable completed vs print_qty_total", () => {
    expect(prodMarkTone({})).toBe("idle");
    expect(prodMarkTone({
      print_qty_total: 50,
      operators: [{ split_id: "s1", line_printable: true, qty_completed: 10 }],
    })).toBe("pending");
    expect(prodMarkTone({
      print_qty_total: 50,
      operators: [{ split_id: "s1", line_printable: true, qty_completed: 50 }],
    })).toBe("done");
  });

  test("prod maps delivery qty_printed_operator when operators are absent", () => {
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
    expect(prodMarkTone({
      print_qty_total: 1000,
      print_completed_qty: 250,
    })).toBe("pending");
  });

  test("prod prefers operators over delivery totals when splits exist", () => {
    expect(prodCompletedQty({
      print_qty_total: 1000,
      qty_printed_operator: 1000,
      operators: [{ split_id: "s1", line_printable: true, qty_completed: 100 }],
    })).toBe(100);
  });

  test("pack and kirim use delivery qty fields; load stays idle", () => {
    expect(loadMarkTone()).toBe("idle");
    expect(packMarkTone({ qty_packed: 0, qty_total: 1000 })).toBe("idle");
    expect(packMarkTone({ qty_packed: 400, qty_total: 1000 })).toBe("pending");
    expect(packMarkTone({ qty_packed: 1000, qty_total: 1000 })).toBe("done");
    expect(kirimMarkTone({ percent_delivered: 0, qty_delivered: 0, qty_total: 1000 })).toBe("idle");
    expect(kirimMarkTone({ percent_delivered: 40, qty_delivered: 400, qty_total: 1000 })).toBe("pending");
    expect(kirimMarkTone({ percent_delivered: 100, qty_delivered: 1000, qty_total: 1000 })).toBe("done");
  });

  test("delivery payload without cup/screen fields looks skipped while packing is active", () => {
    const deliveryOnly = {
      print_qty_total: 1000,
      qty_printed_operator: 1000,
      qty_packed: 400,
      qty_total: 1000,
      qty_delivered: 0,
      percent_delivered: 0,
    };
    const beforeOverlay = deliveryRoleMarkTones(deliveryOnly, false);
    expect(beforeOverlay).toEqual({
      cup: "idle",
      screen: "idle",
      prod: "done",
      pack: "pending",
      load: "idle",
      kirim: "idle",
    });
  });

  test("does not fabricate earlier roles from later packing or kirim", () => {
    const packedAndShipped = {
      qty_packed: 1000,
      qty_total: 1000,
      percent_delivered: 40,
      qty_delivered: 400,
    };
    const tones = deliveryRoleMarkTones(packedAndShipped, false);
    expect(tones.cup).toBe("idle");
    expect(tones.screen).toBe("idle");
    expect(tones.prod).toBe("idle");
    expect(tones.pack).toBe("done");
    expect(tones.load).toBe("idle");
    expect(tones.kirim).toBe("pending");
  });

  test("1. fully progressed print line stays truthful per source fields", () => {
    const row = {
      helper_required: 1000,
      helper_prepared: 1000,
      screen_qty: 1000,
      current_stage: 12,
      print_qty_total: 1000,
      operators: [{ split_id: "s1", line_printable: true, qty_completed: 1000 }],
      qty_packed: 1000,
      qty_total: 1000,
      percent_delivered: 100,
      qty_delivered: 1000,
    };
    expect(deliveryRoleMarkTones(row, true)).toEqual({
      cup: "done",
      screen: "done",
      prod: "done",
      pack: "done",
      load: "idle",
      kirim: "done",
    });
  });

  test("2. genuinely incomplete upstream cup/screen/prod stay incomplete", () => {
    const row = {
      helper_required: 1000,
      helper_prepared: 200,
      screen_qty: "",
      current_stage: 8,
      print_qty_total: 1000,
      operators: [{ split_id: "s1", line_printable: true, qty_completed: 100 }],
      qty_packed: 0,
      qty_total: 1000,
    };
    expect(deliveryRoleMarkTones(row, true)).toEqual({
      cup: "pending",
      screen: "pending",
      prod: "pending",
      pack: "idle",
      load: "idle",
      kirim: "idle",
    });
  });

  test("3. packing active after production completion does not backfill load/kirim", () => {
    const row = {
      helper_required: 500,
      helper_prepared: 500,
      screen_qty: 10,
      print_qty_total: 500,
      qty_printed_operator: 500,
      qty_packed: 200,
      qty_total: 500,
      percent_delivered: 0,
    };
    const tones = deliveryRoleMarkTones(row, true);
    expect(tones.cup).toBe("done");
    expect(tones.screen).toBe("done");
    expect(tones.prod).toBe("done");
    expect(tones.pack).toBe("pending");
    expect(tones.load).toBe("idle");
    expect(tones.kirim).toBe("idle");
  });

  test("4. packing complete does not mark loading done", () => {
    const tones = deliveryRoleMarkTones({
      qty_packed: 800,
      qty_total: 800,
      print_qty_total: 800,
      qty_printed_operator: 800,
    }, true);
    expect(tones.pack).toBe("done");
    expect(tones.load).toBe("idle");
  });

  test("5. pengiriman active after packing does not fabricate loading", () => {
    const tones = deliveryRoleMarkTones({
      qty_packed: 800,
      qty_total: 800,
      percent_delivered: 50,
      qty_delivered: 400,
    }, false);
    expect(tones.pack).toBe("done");
    expect(tones.load).toBe("idle");
    expect(tones.kirim).toBe("pending");
  });

  test("9. non_print skips cup/prod without marking them complete", () => {
    const tones = deliveryRoleMarkTones({
      helper_required: null,
      print_qty_total: 0,
      qty_packed: 200,
      qty_total: 300,
    }, true);
    expect(tones.cup).toBe("idle");
    expect(tones.prod).toBe("idle");
    expect(tones.pack).toBe("pending");
  });

  test("10. cancelled production row does not show open screener work", () => {
    expect(deliveryRoleMarkTones({
      cancelled: true,
      current_stage: 8,
    }, true).screen).toBe("idle");
  });
});
