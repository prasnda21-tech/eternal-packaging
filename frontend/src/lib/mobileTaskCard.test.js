import {
  TASK_DEADLINE_EMPTY,
  TASK_OPERATOR_EMPTY,
  TASK_PRODUCT_EMPTY,
  TASK_QTY_EMPTY,
  TASK_WORK_DATE_EMPTY,
  formatTaskCardDate,
  sortOperationalTasks,
  taskDeadlineLabel,
  taskHasPcsQty,
  taskOperatorLabel,
  taskProductLabel,
  taskScopeQtyLabel,
  taskWorkDateLabel,
} from "./mobileTaskCard";

describe("formatTaskCardDate", () => {
  test("2026-09-08 → 08/09/26", () => {
    expect(formatTaskCardDate("2026-09-08")).toBe("08/09/26");
  });

  test("2026-08-31 → 31/08/26", () => {
    expect(formatTaskCardDate("2026-08-31")).toBe("31/08/26");
  });

  test("null and empty do not render Invalid Date", () => {
    expect(formatTaskCardDate(null)).toBe("");
    expect(formatTaskCardDate(undefined)).toBe("");
    expect(formatTaskCardDate("")).toBe("");
    expect(formatTaskCardDate("not-a-date")).toBe("");
    expect(taskWorkDateLabel({ work_date: null })).toBe(TASK_WORK_DATE_EMPTY);
    expect(taskDeadlineLabel({ delivery_deadline: null })).toBe(TASK_DEADLINE_EMPTY);
  });

  test("date-only YYYY-MM-DD does not timezone-shift", () => {
    expect(formatTaskCardDate("2026-09-08")).toBe("08/09/26");
    expect(formatTaskCardDate("2026-09-08T00:00:00Z")).toBe("08/09/26");
  });
});

describe("task product and scope qty labels", () => {
  test("uses canonical item text", () => {
    expect(taskProductLabel({ item: "Standing Pouch 250g" })).toBe("Standing Pouch 250g");
    expect(taskProductLabel({ item: "" })).toBe(TASK_PRODUCT_EMPTY);
  });

  test("formats Indonesian thousands with pcs", () => {
    expect(taskScopeQtyLabel({ target_qty: 1000, required_input_type: "qty_cetak" })).toBe("1.000 pcs");
    expect(taskScopeQtyLabel({ target_qty: 2500, division: "helper" })).toBe("2.500 pcs");
    expect(taskScopeQtyLabel({ target_qty: 12500, required_input_type: "qty_packing" })).toBe("12.500 pcs");
  });

  test("Cetak uses split target_qty only", () => {
    expect(taskScopeQtyLabel({
      division: "operator",
      required_input_type: "qty_cetak",
      target_qty: 1000,
      completed_qty: 200,
    })).toBe("1.000 pcs");
  });

  test("photo/file tasks are not treated as pcs", () => {
    expect(taskHasPcsQty({ division: "terkirim", required_input_type: "upload_foto", target_qty: 2 })).toBe(false);
    expect(taskScopeQtyLabel({ division: "terkirim", required_input_type: "upload_foto", target_qty: 2 })).toBe(TASK_QTY_EMPTY);
    expect(taskScopeQtyLabel({ division: "surat_jalan", required_input_type: "upload_sj", target_qty: null })).toBe(TASK_QTY_EMPTY);
    expect(taskScopeQtyLabel({ division: "loading", required_input_type: "qty_loading", target_qty: null })).toBe(TASK_QTY_EMPTY);
  });
});

describe("task operator label", () => {
  test("uses canonical operator_names", () => {
    expect(taskOperatorLabel({ operator_names: ["Fauzan Maaruf"] })).toBe("Fauzan Maaruf");
  });

  test("missing operator uses Belum ditentukan", () => {
    expect(taskOperatorLabel({})).toBe(TASK_OPERATOR_EMPTY);
    expect(taskOperatorLabel({ operator_names: [], assigned_user_name: "" })).toBe(TASK_OPERATOR_EMPTY);
  });
});

describe("sortOperationalTasks", () => {
  test("scheduled earliest work_date first, unscheduled last", () => {
    const tasks = [
      { task_id: "A", work_date: "2026-09-10", order_number: "PO-A" },
      { task_id: "B", work_date: null, order_number: "PO-B" },
      { task_id: "C", work_date: "2026-09-08", order_number: "PO-C" },
      { task_id: "D", work_date: "2026-09-09", order_number: "PO-D" },
    ];
    const original = tasks.map((t) => t.task_id);
    const sorted = sortOperationalTasks(tasks);
    expect(sorted.map((t) => t.task_id)).toEqual(["C", "D", "A", "B"]);
    expect(tasks.map((t) => t.task_id)).toEqual(original);
    expect(sorted).toHaveLength(4);
  });

  test("same work_date uses earlier delivery deadline", () => {
    const sorted = sortOperationalTasks([
      { task_id: "late", work_date: "2026-09-08", delivery_deadline: "2026-09-12" },
      { task_id: "early", work_date: "2026-09-08", delivery_deadline: "2026-09-09" },
    ]);
    expect(sorted.map((t) => t.task_id)).toEqual(["early", "late"]);
  });

  test("same work_date and deadline uses stable task identity", () => {
    const sorted = sortOperationalTasks([
      { task_id: "z-task", work_date: "2026-09-08", delivery_deadline: "2026-09-09" },
      { task_id: "a-task", work_date: "2026-09-08", delivery_deadline: "2026-09-09" },
    ]);
    expect(sorted.map((t) => t.task_id)).toEqual(["a-task", "z-task"]);
  });

  test("unscheduled sorts by delivery deadline then identity", () => {
    const sorted = sortOperationalTasks([
      { task_id: "b", work_date: null, delivery_deadline: "2026-09-20" },
      { task_id: "a", work_date: "", delivery_deadline: "2026-09-10" },
    ]);
    expect(sorted.map((t) => t.task_id)).toEqual(["a", "b"]);
  });
});
