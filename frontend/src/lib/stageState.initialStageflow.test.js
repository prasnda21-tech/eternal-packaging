import { STAGES } from "./constants";
import { resolveStageState } from "./stageState";
import {
  getStagePresentation,
  stageBarClassForTone,
  resolveOperationalFocus,
} from "./poDetailPresentation";

const LATER_INDEXES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14];

function newPoFixture(overrides = {}) {
  return {
    current_stage: 0,
    requires_printing: true,
    design_files: [],
    design_status: "Waiting",
    design_bypassed: false,
    payment_files: [],
    payment_status: "Waiting",
    payment_bypassed: false,
    operator_splits: [],
    screener_info: null,
    helper_prep: null,
    sample_files: [],
    sample_approved: false,
    packing_info: {},
    surat_jalan_created: false,
    surat_jalan_status_semantic: "pending",
    loading_completed: false,
    loading_qty_status: "pending",
    loading_verification_status: {
      code: "waiting_verification",
      label: "Menunggu Verifikasi",
      surat_jalan_id: "",
    },
    terkirim_confirmed: false,
    delivery_status: "Belum Terkirim",
    eligible_delivery_qty: 0,
    qty_delivered: 0,
    ...overrides,
  };
}

function rail(order) {
  const current = Number(order.current_stage || 0);
  return STAGES.map((name, idx) => {
    const raw = resolveStageState(idx, order, current);
    const pres = getStagePresentation(idx, order, current);
    return {
      idx,
      name,
      state: raw.state,
      label: raw.label,
      tone: pres.tone,
      cls: stageBarClassForTone(pres.tone),
    };
  });
}

function expectPendingOrange(row) {
  expect(row.state).toBe("pending");
  expect(row.tone).toBe("upcoming");
  expect(row.cls).toBe("waiting");
}

describe("Detail PO stage-flow initial / progression visual states", () => {
  test("CASE 1 — new PO: only Order Masuk is current; later stages pending orange", () => {
    const order = newPoFixture();
    const rows = rail(order);

    expect(rows.map((r) => r.name)).toEqual(STAGES);
    expect(rows[0]).toMatchObject({
      name: "Order Masuk",
      state: "current",
      tone: "active",
      cls: "current",
    });

    for (const idx of LATER_INDEXES) {
      expectPendingOrange(rows[idx]);
    }

    expect(rows[1].name).toBe("Design");
    expect(rows[3].name).toBe("Payment");
    expect(rows[13].name).toBe("Loading");
    expect(rows[1].state).not.toBe("current");
    expect(rows[3].state).not.toBe("current");
    expect(rows[13].state).not.toBe("current");
    expect(rows[13].label).not.toMatch(/verifikasi/i);

    const focus = resolveOperationalFocus(order);
    expect(focus.stageIndex).toBe(0);
    expect(focus.stageName).toBe("Order Masuk");
    expect(focus.tone).toBe("active");
  });

  test("CASE 2 — Design started: Order Masuk completed, Design current, Payment+later pending", () => {
    const order = newPoFixture({ current_stage: 1 });
    const rows = rail(order);

    expect(rows[0]).toMatchObject({ state: "done", tone: "completed", cls: "done" });
    expect(rows[1]).toMatchObject({
      name: "Design",
      state: "current",
      label: "Belum dikerjakan",
      tone: "active",
      cls: "current",
    });
    for (const idx of LATER_INDEXES.filter((i) => i !== 1)) {
      expectPendingOrange(rows[idx]);
    }
    expect(rows[3].state).toBe("pending");
    expect(rows[13].state).toBe("pending");
  });

  test("CASE 3 — Payment current: earlier completed by evidence, later untouched pending", () => {
    const order = newPoFixture({
      current_stage: 3,
      design_files: [{ name: "art.pdf", uploaded_at: "2026-09-01T00:00:00Z", url: "https://files/art.pdf" }],
      design_status: "Approved",
    });
    const rows = rail(order);

    expect(rows[0].state).toBe("done");
    expect(rows[1]).toMatchObject({ state: "done", tone: "completed" });
    expect(rows[2]).toMatchObject({ state: "done", tone: "completed" });
    expect(rows[3]).toMatchObject({
      name: "Payment",
      state: "current",
      label: "Belum dikerjakan",
      tone: "active",
      cls: "current",
    });
    for (const idx of [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]) {
      expectPendingOrange(rows[idx]);
    }
  });

  test("CASE 4 — Loading current: earlier completed as supported, Pengiriman pending", () => {
    const order = newPoFixture({
      current_stage: 13,
      design_files: [{ name: "art.pdf", uploaded_at: "2026-09-01T00:00:00Z", url: "https://files/art.pdf" }],
      design_status: "Approved",
      payment_files: [{ name: "pay.png" }],
      payment_status: "Approved",
      operator_splits: [{ operator_name: "Op", quantity: 100, qty_completed: 100 }],
      screener_info: { screen_qty: 10 },
      helper_prep: { qty_prepared: 10 },
      sample_files: [{ name: "s.jpg" }],
      sample_approved: true,
      print_status: "SELESAI CETAK",
      print_completed_qty: 100,
      print_qty_target: 100,
      packing_info: { qty_packed: 100, lines: [{ line_idx: 0, packed_qty: 100 }] },
      surat_jalan_created: true,
      surat_jalan_status_semantic: "completed",
      loading_qty_status: "pending",
      loading_verification_status: {
        code: "ready",
        label: "Siap Diverifikasi",
        surat_jalan_id: "sj-1",
      },
    });
    const rows = rail(order);

    expect(rows[0].state).toBe("done");
    expect(rows[1].state).toBe("done");
    expect(rows[3].state).toBe("done");
    expect(rows[12].state).toBe("done");
    expect(rows[13]).toMatchObject({
      name: "Loading",
      state: "current",
      tone: "active",
      cls: "current",
    });
    expectPendingOrange(rows[14]);
    expect(rows[14].name).toBe("Pengiriman");
  });

  test("CASE 5 — completed delivery continues to render completed stages", () => {
    const order = newPoFixture({
      current_stage: 14,
      design_files: [{ name: "art.pdf", uploaded_at: "2026-09-01T00:00:00Z", url: "https://files/art.pdf" }],
      design_status: "Approved",
      payment_files: [{ name: "pay.png" }],
      payment_status: "Approved",
      operator_splits: [{ operator_name: "Op", quantity: 100, qty_completed: 100 }],
      screener_info: { screen_qty: 10 },
      helper_prep: { qty_prepared: 10 },
      sample_files: [{ name: "s.jpg" }],
      sample_approved: true,
      print_status: "SELESAI CETAK",
      print_completed_qty: 100,
      print_qty_target: 100,
      packing_info: { qty_packed: 100, lines: [{ line_idx: 0, packed_qty: 100 }] },
      surat_jalan_created: true,
      surat_jalan_status_semantic: "completed",
      loading_completed: true,
      loading_qty_status: "completed",
      loading_confirmations: [{ id: "c1" }],
      loading_verification_status: {
        code: "complete",
        label: "Loading Selesai",
        surat_jalan_id: "sj-1",
      },
      terkirim_confirmed: true,
      delivery_status: "Terkirim",
      qty_delivered: 100,
      eligible_delivery_qty: 100,
    });
    const rows = rail(order);

    expect(rows[0].state).toBe("done");
    expect(rows[1].state).toBe("done");
    expect(rows[3].state).toBe("done");
    expect(rows[12].state).toBe("done");
    expect(rows[13]).toMatchObject({ state: "done", tone: "completed", cls: "done" });
    expect(rows[14]).toMatchObject({
      name: "Pengiriman",
      state: "done",
      tone: "completed",
      cls: "done",
    });
  });

  test("default loading_verification_status does not activate Loading on a new PO", () => {
    const loading = resolveStageState(13, newPoFixture(), 0);
    expect(loading.state).toBe("pending");
    expect(loading.label).toBe("Menunggu Surat Jalan");
  });
});
