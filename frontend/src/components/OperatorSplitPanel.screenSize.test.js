import { act } from "react";
import { createRoot } from "react-dom/client";
import OperatorSplitPanel from "./OperatorSplitPanel";
import api from "../lib/api";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    listOperators: jest.fn(),
    assignOperators: jest.fn(),
    cancelOperatorSplit: jest.fn(),
    updateOperatorProgress: jest.fn(),
  },
}));

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { email: "prod@x.com", name: "Prod", role: "head_production" },
    hasPerm: () => true,
  }),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const change = (el, value) => {
  el.value = value;
  el.dispatchEvent(new Event("change", { bubbles: true }));
};

const order = {
  id: "po-screen-1",
  requires_printing: true,
  print_qty_total: 8000,
  operator_assignment_cap: 8000,
  design_status: "Approved",
  payment_status: "Approved",
  sample_approved: true,
  workflow_ready: { penjadwalan: true, print_qty: true },
  line_items: [
    { id: "L1", name: "Cup 16oz", quantity: 5000, production_process: "print", screen_size: "large" },
    { id: "L2", name: "Cup 16oz", quantity: 3000, production_process: "print", screen_size: "small" },
  ],
  operator_splits: [
    { id: "a", operator_email: "a@x.com", operator_name: "A", quantity: 4000, qty_completed: 0, line_idx: 0, work_date: "2026-09-10", screen_size: "large" },
    { id: "b", operator_email: "b@x.com", operator_name: "B", quantity: 1000, qty_completed: 0, line_idx: 0, work_date: "2026-09-10", screen_size: "large" },
    { id: "c", operator_email: "c@x.com", operator_name: "C", quantity: 3000, qty_completed: 0, line_idx: 1, work_date: "2026-09-11", screen_size: "small" },
  ],
};

const screenOptions = (select) => Array.from(select.querySelectorAll("option")).map((o) => ({
  value: o.value,
  text: o.textContent.trim(),
}));

describe("OperatorSplitPanel screen size", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.listOperators.mockResolvedValue({
      operators: [
        { email: "a@x.com", name: "A" },
        { email: "b@x.com", name: "B" },
        { email: "c@x.com", name: "C" },
      ],
    });
    api.assignOperators.mockResolvedValue({ order });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const flush = async () => {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  };

  const renderPanel = async (props = {}) => {
    await act(async () => {
      root.render(<OperatorSplitPanel order={order} canEdit onUpdated={() => {}} mode="schedule" {...props} />);
    });
    await flush();
  };

  test("CASE 1: fresh allocation row shows SCREEN selector with Pilih Screen", async () => {
    const fresh = {
      ...order,
      operator_splits: [],
      line_items: [{ id: "L1", name: "Cup", quantity: 1000, production_process: "print" }],
      print_qty_total: 1000,
      operator_assignment_cap: 1000,
    };
    await renderPanel({ order: fresh });
    expect(container.querySelector('[data-testid="split-screen-size-0"]')).toBeNull();
    await act(async () => {
      container.querySelector('[data-testid="split-add-row"]').click();
    });
    await flush();
    const sel = container.querySelector('[data-testid="split-screen-size-0"]');
    expect(sel).toBeTruthy();
    expect(sel.value).toBe("");
    expect(sel.querySelector('option[value=""]').textContent).toBe("Pilih Screen");
    const row = container.querySelector('[data-testid="split-assign-row-0"]');
    expect(row.querySelector(".split-assign-barang")).toBeTruthy();
    expect(row.querySelector(".split-assign-operator")).toBeTruthy();
    expect(row.querySelector(".qty-control")).toBeTruthy();
    expect(row.querySelector(".split-assign-screen")).toBeTruthy();
    expect(row.querySelector(".split-assign-date")).toBeTruthy();
  });

  test("CASE 2: options are exactly Screen Besar / Screen Kecil", async () => {
    await renderPanel();
    const sel = container.querySelector('[data-testid="split-screen-size-0"]');
    expect(screenOptions(sel)).toEqual([
      { value: "", text: "Pilih Screen" },
      { value: "large", text: "Screen Besar" },
      { value: "small", text: "Screen Kecil" },
    ]);
  });

  test("CASE 3: saved screen_size=large preloads Screen Besar", async () => {
    await renderPanel();
    expect(container.querySelector('[data-testid="split-screen-size-0"]').value).toBe("large");
  });

  test("CASE 4: saved screen_size=small preloads Screen Kecil", async () => {
    await renderPanel();
    expect(container.querySelector('[data-testid="split-screen-size-2"]').value).toBe("small");
  });

  test("CASE 5: two same-name lines remain independent", async () => {
    await renderPanel();
    expect(container.querySelector('[data-testid="split-screen-size-0"]').value).toBe("large");
    expect(container.querySelector('[data-testid="split-screen-size-1"]').value).toBe("large");
    expect(container.querySelector('[data-testid="split-screen-size-2"]').value).toBe("small");
    await act(async () => {
      change(container.querySelector('[data-testid="split-screen-size-2"]'), "large");
    });
    await flush();
    expect(container.querySelector('[data-testid="split-screen-size-0"]').value).toBe("large");
    expect(container.querySelector('[data-testid="split-screen-size-1"]').value).toBe("large");
    expect(container.querySelector('[data-testid="split-screen-size-2"]').value).toBe("large");
    await act(async () => {
      change(container.querySelector('[data-testid="split-screen-size-0"]'), "small");
    });
    await flush();
    expect(container.querySelector('[data-testid="split-screen-size-0"]').value).toBe("small");
    expect(container.querySelector('[data-testid="split-screen-size-1"]').value).toBe("large");
    expect(container.querySelector('[data-testid="split-screen-size-2"]').value).toBe("large");
  });

  test("CASE 6: two operator rows on the same line stay independent", async () => {
    await renderPanel();
    await act(async () => {
      change(container.querySelector('[data-testid="split-screen-size-1"]'), "small");
    });
    await flush();
    expect(container.querySelector('[data-testid="split-screen-size-0"]').value).toBe("large");
    expect(container.querySelector('[data-testid="split-screen-size-1"]').value).toBe("small");
    expect(container.querySelector('[data-testid="split-screen-size-2"]').value).toBe("small");
  });

  test("CASE 7: READY / editable panel still renders selector", async () => {
    const readyOrder = {
      ...order,
      operator_splits: [],
      print_qty_total: 8000,
      operator_assignment_cap: 8000,
      workflow_ready: { penjadwalan: true, print_qty: false },
    };
    await renderPanel({ order: readyOrder, canEdit: true, mode: "schedule" });
    expect(container.querySelector('[data-testid="atur-pembagian-card"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="split-add-row"]').click();
    });
    await flush();
    const sel = container.querySelector('[data-testid="split-screen-size-0"]');
    expect(sel).toBeTruthy();
    expect(sel.disabled).toBe(false);
    expect(container.textContent).toMatch(/READY|Sisa:/);
  });

  test("preloads Screen Besar / Screen Kecil per exact line, same-name stays separate", async () => {
    await renderPanel();
    expect(container.querySelector('[data-testid="split-screen-size-0"]').value).toBe("large");
    expect(container.querySelector('[data-testid="split-screen-size-1"]').value).toBe("large");
    expect(container.querySelector('[data-testid="split-screen-size-2"]').value).toBe("small");
  });

  test("legacy missing screen_size shows Pilih Screen", async () => {
    const legacy = {
      ...order,
      line_items: [{ id: "L1", name: "Cup", quantity: 1000, production_process: "print" }],
      operator_splits: [{ id: "z", operator_email: "a@x.com", operator_name: "A", quantity: 1000, qty_completed: 0, line_idx: 0 }],
      print_qty_total: 1000,
      operator_assignment_cap: 1000,
    };
    await renderPanel({ order: legacy });
    expect(container.querySelector('[data-testid="split-screen-size-0"]').value).toBe("");
  });

  test("Deni and Djoko keep independent screens through edit, add/remove, save, and reopen", async () => {
    const deniOrder = {
      ...order,
      print_qty_total: 7000,
      operator_assignment_cap: 7000,
      line_items: [
        { id: "L1", name: "Cup 16oz", quantity: 7000, production_process: "print", screen_size: "large" },
      ],
      operator_splits: [
        { id: "deni", operator_email: "deni@x.com", operator_name: "Deni", quantity: 4000, qty_completed: 0, line_idx: 0, work_date: "2026-10-02", screen_size: "large" },
        { id: "djoko", operator_email: "djoko@x.com", operator_name: "Djoko", quantity: 3000, qty_completed: 0, line_idx: 0, work_date: "2026-10-03", screen_size: "small" },
      ],
    };
    api.listOperators.mockResolvedValue({
      operators: [
        { email: "deni@x.com", name: "Deni" },
        { email: "djoko@x.com", name: "Djoko" },
      ],
    });
    await renderPanel({ order: deniOrder });

    const screen = (i) => container.querySelector(`[data-testid="split-screen-size-${i}"]`);
    const field = (id) => container.querySelector(`[data-testid="${id}"]`).value;
    expect(screen(0).value).toBe("large");
    expect(screen(1).value).toBe("small");

    await act(async () => { change(screen(0), "small"); });
    await flush();
    expect(screen(0).value).toBe("small");
    expect(screen(1).value).toBe("small");
    expect(field("split-operator-select-1")).toBe("djoko@x.com");
    expect(field("split-quantity-input-1")).toBe("3000");
    expect(field("split-work-date-input-1")).toBe("2026-10-03");

    await act(async () => { change(screen(0), "large"); });
    await flush();
    expect(screen(0).value).toBe("large");
    expect(screen(1).value).toBe("small");

    await act(async () => { change(screen(1), "large"); });
    await flush();
    expect(screen(0).value).toBe("large");
    expect(screen(1).value).toBe("large");
    expect(field("split-operator-select-0")).toBe("deni@x.com");
    expect(field("split-quantity-input-0")).toBe("4000");
    expect(field("split-work-date-input-0")).toBe("2026-10-02");

    await act(async () => { change(screen(1), "small"); });
    await flush();
    expect(screen(0).value).toBe("large");
    expect(screen(1).value).toBe("small");

    await act(async () => {
      container.querySelector('[data-testid="split-add-row"]').click();
    });
    await flush();
    expect(screen(0).value).toBe("large");
    expect(screen(1).value).toBe("small");
    expect(screen(2).value).toBe("");
    expect(field("split-operator-select-0")).toBe("deni@x.com");
    expect(field("split-operator-select-1")).toBe("djoko@x.com");

    await act(async () => {
      container.querySelector('[data-testid="split-remove-2"]').click();
    });
    await flush();
    expect(screen(0).value).toBe("large");
    expect(screen(1).value).toBe("small");
    expect(screen(2)).toBeNull();
    expect(field("split-quantity-input-0")).toBe("4000");
    expect(field("split-quantity-input-1")).toBe("3000");
    expect(field("split-work-date-input-0")).toBe("2026-10-02");
    expect(field("split-work-date-input-1")).toBe("2026-10-03");

    await act(async () => {
      container.querySelector('[data-testid="split-save"]').click();
    });
    await flush();
    const payload = api.assignOperators.mock.calls.at(-1)[1];
    expect(payload.map((row) => row.screen_size)).toEqual(["large", "small"]);
    expect(payload.map((row) => row.quantity)).toEqual([4000, 3000]);
    expect(payload.map((row) => row.work_date)).toEqual(["2026-10-02", "2026-10-03"]);
    expect(payload.map((row) => row.operator_email)).toEqual(["deni@x.com", "djoko@x.com"]);

    const reopened = {
      ...deniOrder,
      line_items: [
        { id: "L1", name: "Cup 16oz", quantity: 7000, production_process: "print", screen_size: "large" },
      ],
      operator_splits: payload.map((row) => ({ ...row, qty_completed: 0 })),
    };
    await renderPanel({ order: reopened });
    expect(container.querySelector('[data-testid="split-screen-size-0"]').value).toBe("large");
    expect(container.querySelector('[data-testid="split-screen-size-1"]').value).toBe("small");
    expect(container.querySelector('[data-testid="split-quantity-input-0"]').value).toBe("4000");
    expect(container.querySelector('[data-testid="split-quantity-input-1"]').value).toBe("3000");
    expect(container.querySelector('[data-testid="split-operator-select-0"]').value).toBe("deni@x.com");
    expect(container.querySelector('[data-testid="split-operator-select-1"]').value).toBe("djoko@x.com");
    expect(container.querySelector('[data-testid="split-work-date-input-0"]').value).toBe("2026-10-02");
    expect(container.querySelector('[data-testid="split-work-date-input-1"]').value).toBe("2026-10-03");
  });

  test("Simpan pembagian sends screen_size on each split", async () => {
    await renderPanel();
    await act(async () => {
      container.querySelector('[data-testid="split-save"]').click();
    });
    await flush();
    expect(api.assignOperators).toHaveBeenCalled();
    const payload = api.assignOperators.mock.calls[0][1];
    expect(payload[0].screen_size).toBe("large");
    expect(payload[1].screen_size).toBe("large");
    expect(payload[2].screen_size).toBe("small");
    expect(payload[0].line_idx).toBe(0);
    expect(payload[2].line_idx).toBe(1);
  });
});
