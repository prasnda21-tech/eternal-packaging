import { addWidget, boxesOverlap, defaultLayout, layoutOverlaps, placeWidget, removeWidget } from "./financeWidgetLayout";

const allow = (...keys) => (key) => keys.includes(key);

describe("finance widget layout", () => {
  test("default layout for a full reader does not overlap", () => {
    const widgets = defaultLayout(allow(
      "dashboard.finance.view",
      "profit_loss.view",
      "purchase_invoices.view",
      "cash_bank.reconcile",
      "dashboard.sales.view",
    ));
    expect(widgets.map((item) => item.widget_id)).toEqual([
      "cash", "sales", "purchases", "profit", "expense", "trend", "products", "sellers", "recon",
    ]);
    expect(layoutOverlaps(widgets)).toBe(false);
    expect(widgets.find((item) => item.widget_id === "cash").w).toBe(3);
    expect(widgets.find((item) => item.widget_id === "cash").h).toBe(2);
    expect(widgets.find((item) => item.widget_id === "expense").w).toBe(4);
    expect(widgets.find((item) => item.widget_id === "expense").y).toBe(2);
    expect(widgets.find((item) => item.widget_id === "sellers").w).toBe(6);
  });

  test("sales permission does not place finance cards", () => {
    const widgets = defaultLayout(allow("dashboard.sales.view"));
    expect(widgets.map((item) => item.widget_id)).toEqual(["sales", "trend", "products", "sellers"]);
    expect(layoutOverlaps(widgets)).toBe(false);
  });

  test("move pushes the card underneath instead of overlapping", () => {
    const start = addWidget(addWidget([], "cash"), "expense");
    const moved = placeWidget(start, "cash", { x: 4, y: 0, w: 4, h: 3 });
    expect(layoutOverlaps(moved)).toBe(false);
    expect(boxesOverlap(moved[0], moved[1])).toBe(false);
    expect(moved.find((item) => item.widget_id === "cash").x).toBe(4);
    expect(moved.find((item) => item.widget_id === "expense").y).toBeGreaterThan(0);
  });

  test("resize stays inside the widget limits and the grid", () => {
    const start = addWidget([], "trend");
    const sized = placeWidget(start, "trend", { x: 10, y: 1, w: 20, h: 1 });
    const trend = sized[0];
    expect(trend.w).toBeLessThanOrEqual(12);
    expect(trend.h).toBeGreaterThanOrEqual(3);
    expect(trend.x + trend.w).toBeLessThanOrEqual(12);
  });

  test("remove leaves the other card", () => {
    const start = addWidget(addWidget([], "cash"), "expense");
    expect(removeWidget(start, "cash").map((item) => item.widget_id)).toEqual(["expense"]);
  });
});
