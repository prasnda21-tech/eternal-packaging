import { act } from "react";
import { createRoot } from "react-dom/client";
import ProductionSchedulePriorityBadges from "./ProductionSchedulePriorityBadges";
import { productionSchedulePriorityIndicators } from "../lib/productionSchedulePriority";

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("ProductionSchedulePriorityBadges parity", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("schedule row and task card with the same fields render the same badges", () => {
    const scheduleRow = {
      po_number: "PO-1001",
      urgent: true,
      delivery_deadline_status: "h1",
    };
    const task = {
      task_id: "operator:po-1:0:s1",
      order_number: "PO-1001",
      urgent: true,
      delivery_deadline_status: "h1",
    };
    expect(productionSchedulePriorityIndicators(scheduleRow)).toEqual(
      productionSchedulePriorityIndicators(task),
    );
    act(() => {
      root.render(<ProductionSchedulePriorityBadges row={task} testIdPrefix="card" />);
    });
    expect(container.querySelector('[data-testid="card-urgent"]').textContent).toBe("URGENT");
    expect(container.querySelector('[data-testid="card-h1"]').textContent).toBe("H-1");
  });
});
