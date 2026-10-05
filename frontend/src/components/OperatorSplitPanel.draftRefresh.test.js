import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import OperatorSplitPanel from "./OperatorSplitPanel";
import api from "../lib/api";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    listOperators: jest.fn(() => Promise.resolve({ operators: [] })),
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

beforeEach(() => {
  api.listOperators.mockResolvedValue({ operators: [] });
});

function orderWith(quantity) {
  return {
    id: "po-draft",
    requires_printing: true,
    print_qty_total: 100,
    operator_assignment_cap: 100,
    design_status: "Approved",
    payment_status: "Approved",
    sample_approved: true,
    workflow_ready: { penjadwalan: true, print_qty: true },
    line_items: [{ id: "L1", name: "Cup", quantity: 100, production_process: "print" }],
    operator_splits: [{
      id: "s1",
      operator_email: "op@x.com",
      operator_name: "Op",
      quantity,
      line_idx: 0,
      work_date: "2026-10-08",
      screen_size: "large",
      qty_completed: 0,
    }],
  };
}

function Host({ order }) {
  const [current, setCurrent] = useState(order);
  global.__setSplitOrder = setCurrent;
  return <OperatorSplitPanel order={current} canEdit onUpdated={() => {}} />;
}

test("a refreshed order object keeps an edited allocation", async () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(<Host order={orderWith(10)} />); });
  const input = host.querySelector("[data-testid='split-quantity-input-0']");
  expect(input).toBeTruthy();
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(input, "40");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(host.querySelector("[data-testid='split-quantity-input-0']").value).toBe("40");
  await act(async () => { global.__setSplitOrder(orderWith(10)); });
  expect(host.querySelector("[data-testid='split-quantity-input-0']").value).toBe("40");
  await act(async () => { root.unmount(); });
  host.remove();
  expect(api.listOperators).toHaveBeenCalled();
});
