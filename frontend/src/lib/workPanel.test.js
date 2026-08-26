import {
  canSubmitDivision,
  deliveryPartialPercent,
  helperWorkModel,
  loadingWorkModel,
  operatorLineModels,
  operatorMyAssignmentModels,
  operatorStatusLabel,
  packingWorkModel,
  permittedDivisionIds,
  schedulePoHref,
  screenerWorkModel,
  taskPoHref,
} from "./workPanel";
import { sumEntryQty } from "./workflowEntries";

const order = {
  id: "po1",
  po_number: "PO-12345",
  print_qty_target: 10000,
  print_completed_qty: 7000,
  line_items: [
    { name: "Item A", quantity: 6000, item_id: "a", production_process: "print" },
    { name: "Item B", quantity: 4000, item_id: "b", production_process: "print" },
    { name: "Non", quantity: 2000, item_id: "c", production_process: "non_print" },
  ],
  operator_splits: [
    {
      id: "s1",
      line_idx: 0,
      operator_email: "budi@x.com",
      operator_name: "Budi",
      quantity: 6000,
      qty_completed: 4000,
      progress_entries: [
        { id: "e1", qty: 4000, created_at: "2026-08-26T08:15:00+07:00", created_by_name: "Budi", action_id: "a1" },
      ],
    },
    {
      id: "s2",
      line_idx: 1,
      operator_email: "budi@x.com",
      operator_name: "Budi",
      quantity: 4000,
      qty_completed: 3000,
      progress_entries: [
        { id: "e2", qty: 1000, created_at: "2026-08-26T09:00:00+07:00", created_by_name: "Budi" },
        { id: "e3", qty: 2000, created_at: "2026-08-26T10:20:00+07:00", created_by_name: "Budi" },
      ],
    },
  ],
};

describe("operator work panel models", () => {
  test("does not mix print lines and ignores non-print", () => {
    const lines = operatorLineModels(order);
    expect(lines).toHaveLength(2);
    expect(lines[0].name).toBe("Item A");
    expect(lines[0].target).toBe(6000);
    expect(lines[0].done).toBe(4000);
    expect(lines[0].remaining).toBe(2000);
    expect(lines[1].done).toBe(3000);
    expect(lines[1].remaining).toBe(1000);
    expect(sumEntryQty(lines[1].entries)).toBe(3000);
  });

  test("legacy qty_completed is visible when no entries", () => {
    const legacy = {
      ...order,
      operator_splits: [{
        id: "s1", line_idx: 0, quantity: 6000, qty_completed: 5000, operator_name: "Budi",
        updated_at: "2026-08-26T08:15:00+07:00",
      }],
      line_items: [{ name: "Item A", quantity: 10000, production_process: "print" }],
    };
    const line = operatorLineModels(legacy)[0];
    expect(line.done).toBe(5000);
    expect(line.remaining).toBe(5000);
    expect(line.entries[0].qty).toBe(5000);
    expect(line.entries[0].legacy).toBe(true);
  });

  test("status proses for partial", () => {
    expect(operatorStatusLabel(5000, 10000)).toBe("Proses");
    expect(operatorStatusLabel(0, 10000)).toBe("Belum");
    expect(operatorStatusLabel(10000, 10000)).toBe("Selesai");
  });

  test("click PO uses work panel for operator.submit perm", () => {
    expect(schedulePoHref("abc", (p) => p === "operator.update")).toBe("/po/abc/kerja?divisi=operator");
    expect(schedulePoHref("abc", () => false)).toBe("/po/abc");
  });

  test("tugas saya without full PO perm goes to own work panel", () => {
    expect(taskPoHref("abc", (p) => p === "operator.update", "operator_work")).toBe("/po/abc/kerja?divisi=operator");
    expect(taskPoHref("abc", (p) => p === "po.view", "operator_work")).toBe("/po/abc");
    expect(taskPoHref("abc", (p) => p === "helper.prepare", "helper")).toBe("/po/abc/kerja?divisi=helper");
    expect(taskPoHref("abc", (p) => p === "design.upload", "design_upload")).toBe("/po/abc/kerja?divisi=designer");
    expect(taskPoHref("abc", (p) => p === "schedule.edit", "penjadwalan")).toBe("/po/abc/kerja?divisi=kepala_produksi");
  });

  test("cannot submit without division perm", () => {
    const div = { id: "operator", active: true, implemented: true, submit_perm: "operator.update" };
    expect(canSubmitDivision(() => false, div)).toBe(false);
    expect(canSubmitDivision((p) => p === "operator.update", div)).toBe(true);
    expect(canSubmitDivision((p) => p === "operator.update", { ...div, active: false })).toBe(false);
  });
});

describe("click PO routes by permitted work submit", () => {
  test("screener perm focuses screener panel", () => {
    expect(schedulePoHref("abc", (p) => p === "screen.update")).toBe("/po/abc/kerja?divisi=screener");
  });

  test("packing perm focuses packing panel", () => {
    expect(schedulePoHref("abc", (p) => p === "packing.update")).toBe("/po/abc/kerja?divisi=packing");
  });

  test("helper with packing perm lands on helper first, both permitted", () => {
    const has = (p) => p === "helper.prepare" || p === "packing.update";
    expect(schedulePoHref("abc", has)).toBe("/po/abc/kerja?divisi=helper");
    expect(permittedDivisionIds(has)).toEqual(["helper", "packing"]);
  });

  test("does not hardcode role names for routing", () => {
    const src = require("fs").readFileSync(require("path").join(__dirname, "workPanel.js"), "utf8");
    expect(src).not.toMatch(/role\s*===\s*["']operator["']/);
    expect(src).not.toMatch(/role\s*===\s*["']helper["']/);
  });
});

describe("screener / helper / packing / loading aggregates", () => {
  test("screener 5000 + 2000 = 7000", () => {
    const model = screenerWorkModel({
      print_qty_total: 10000,
      line_items: [{ name: "A", quantity: 10000, production_process: "print" }],
      screener_info: {
        screen_qty: 7000,
        entries: [
          { qty: 5000, created_by_name: "Andi", created_at: "2026-08-26T08:00:00+07:00" },
          { qty: 2000, created_by_name: "Andi", created_at: "2026-08-26T09:15:00+07:00" },
        ],
      },
    });
    expect(model.headerDone).toBe(7000);
    expect(model.lines[0].entries).toHaveLength(2);
    expect(sumEntryQty(model.lines[0].entries)).toBe(7000);
  });

  test("helper prepared 4000 + 2000 = 6000 and does not mix lines", () => {
    const model = helperWorkModel({
      line_items: [
        { name: "A", quantity: 8000, production_process: "print" },
        { name: "B", quantity: 3000, production_process: "print" },
      ],
      helper_prep: {
        items: [
          { line_idx: 0, qty_prepared: 6000 },
          { line_idx: 1, qty_prepared: 1000 },
        ],
        entries: [
          { line_idx: 0, qty: 4000 },
          { line_idx: 0, qty: 2000 },
          { line_idx: 1, qty: 1000 },
        ],
      },
    });
    expect(model.lines[0].done).toBe(6000);
    expect(model.lines[1].done).toBe(1000);
    expect(sumEntryQty(model.lines[0].entries)).toBe(6000);
  });

  test("helper target follows active schedule not line qty", () => {
    const model = helperWorkModel({
      print_qty_total: 10000,
      line_items: [{ name: "A", quantity: 10000, production_process: "print" }],
      operator_splits: [
        { line_idx: 0, quantity: 6000, operator_email: "a@x.com" },
        { line_idx: 0, quantity: 4000, operator_email: "b@x.com" },
      ],
      helper_prep: {
        items: [{ line_idx: 0, qty_prepared: 2000 }],
        entries: [{ line_idx: 0, qty: 2000 }],
      },
    });
    expect(model.headerTarget).toBe(10000);
    expect(model.headerDone).toBe(2000);
    expect(model.headerRemain).toBe(8000);
    expect(model.lines[0].target).toBe(10000);
  });

  test("helper over-prepared keeps actual and remaining 0", () => {
    const model = helperWorkModel({
      line_items: [{ name: "A", quantity: 10000, production_process: "print" }],
      operator_splits: [{ line_idx: 0, quantity: 6000, operator_email: "a@x.com" }],
      helper_prep: {
        items: [{ line_idx: 0, qty_prepared: 8000 }],
        entries: [{ line_idx: 0, qty: 8000 }],
      },
    });
    expect(model.headerTarget).toBe(6000);
    expect(model.headerDone).toBe(8000);
    expect(model.headerRemain).toBe(0);
    expect(model.overPrepared).toBe(2000);
    expect(model.lines[0].entries).toHaveLength(1);
  });

  test("packing 4000 + 3000 = 7000 partial", () => {
    const model = packingWorkModel({
      line_items: [{ name: "A", quantity: 10000, production_process: "print" }],
      packing_lines: [{
        line_idx: 0, name: "A", available: 10000, packed: 7000, remaining: 3000,
      }],
      packing_info: {
        events: [
          { line_idx: 0, qty: 4000 },
          { line_idx: 0, qty: 3000 },
        ],
      },
    });
    expect(model.lines[0].done).toBe(7000);
    expect(model.lines[0].remaining).toBe(3000);
    expect(sumEntryQty(model.lines[0].entries)).toBe(7000);
    expect(model.lines[0].entries).toHaveLength(2);
  });

  test("loading lines stay independent 2000+1500 = 3500", () => {
    const model = loadingWorkModel({
      surat_jalan_created: true,
      shipment_lines: [
        { line_idx: 0, name: "A", packed: 6000, loaded: 3500, loading_cap: 6000, available_loading: 2500 },
        { line_idx: 1, name: "B", packed: 2000, loaded: 0, loading_cap: 2000, available_loading: 2000 },
      ],
      loading_info: {
        events: [
          { line_idx: 0, qty: 2000 },
          { line_idx: 0, qty: 1500 },
        ],
      },
    });
    expect(model.lines[0].done).toBe(3500);
    expect(sumEntryQty(model.lines[0].entries)).toBe(3500);
    expect(model.lines[1].done).toBe(0);
    expect(model.lines[1].entries).toHaveLength(0);
  });

  test("partial delivery does not mark whole PO 100%", () => {
    expect(deliveryPartialPercent({ qty_delivered: 5000, total_quantity: 10000 })).toBe(50);
    expect(deliveryPartialPercent({ qty_delivered: 5000, total_quantity: 10000 })).not.toBe(100);
  });

  test("legacy packing snapshot is one history row not duplicated", () => {
    const model = packingWorkModel({
      packing_lines: [{ line_idx: 0, name: "A", available: 10000, packed: 7000, remaining: 3000 }],
      packing_info: { updated_at: "2026-08-01T08:00:00+07:00", updated_by_name: "Lama" },
    });
    expect(model.lines[0].entries).toHaveLength(1);
    expect(model.lines[0].entries[0].legacy).toBe(true);
    expect(model.lines[0].entries[0].qty).toBe(7000);
  });

  test("Budi assignment is 4000 not Andi 1000 or line 5000", () => {
    const rows = operatorMyAssignmentModels({
      line_items: [{ name: "Cup A", quantity: 5000, production_process: "print" }],
      operator_splits: [
        { id: "b", line_idx: 0, operator_email: "budi@x.com", quantity: 4000, qty_completed: 1500 },
        { id: "a", line_idx: 0, operator_email: "andi@x.com", quantity: 1000, qty_completed: 0 },
      ],
    }, { email: "budi@x.com" });
    expect(rows).toHaveLength(1);
    expect(rows[0].target).toBe(4000);
    expect(rows[0].done).toBe(1500);
    expect(rows[0].remaining).toBe(2500);
  });
});
