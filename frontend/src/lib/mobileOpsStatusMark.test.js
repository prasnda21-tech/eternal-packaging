import {
  cupMarkTone,
  screenMarkTone,
  prodMarkTone,
  packMarkTone,
  loadMarkTone,
  kirimMarkTone,
  qtyMarkTone,
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

  test("pack and kirim use delivery qty fields; load stays idle", () => {
    expect(loadMarkTone()).toBe("idle");
    expect(packMarkTone({ qty_packed: 0, qty_total: 1000 })).toBe("idle");
    expect(packMarkTone({ qty_packed: 400, qty_total: 1000 })).toBe("pending");
    expect(packMarkTone({ qty_packed: 1000, qty_total: 1000 })).toBe("done");
    expect(kirimMarkTone({ percent_delivered: 0, qty_delivered: 0, qty_total: 1000 })).toBe("idle");
    expect(kirimMarkTone({ percent_delivered: 40, qty_delivered: 400, qty_total: 1000 })).toBe("pending");
    expect(kirimMarkTone({ percent_delivered: 100, qty_delivered: 1000, qty_total: 1000 })).toBe("done");
  });
});
