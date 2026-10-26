import { mergeDpLayout } from "./deliveryPlanTableLayout";

describe("mergeDpLayout enforceColMinWidth (Schedule hotfix)", () => {
  const defs = [
    { key: "effective_delivery_date", minWidth: 148, width: 148 },
    { key: "order_number", minWidth: 220, width: 220 },
    { key: "customer_name", minWidth: 140, width: 140 },
  ];

  test("without enforce flag, narrow saved widths stay (Plan behavior)", () => {
    const next = mergeDpLayout(defs, {
      order: ["effective_delivery_date", "order_number", "customer_name"],
      hidden: [],
      widths: { effective_delivery_date: 100, order_number: 110, customer_name: 140 },
    });
    expect(next.widths.order_number).toBe(110);
    expect(next.widths.effective_delivery_date).toBe(100);
  });

  test("with enforce flag, lifts widths below column minWidth and keeps valid wider widths / order", () => {
    const next = mergeDpLayout(defs, {
      order: ["customer_name", "order_number", "effective_delivery_date"],
      hidden: [],
      widths: {
        effective_delivery_date: 100,
        order_number: 110,
        customer_name: 200,
      },
    }, { enforceColMinWidth: true });
    expect(next.order).toEqual(["customer_name", "order_number", "effective_delivery_date"]);
    expect(next.widths.effective_delivery_date).toBe(148);
    expect(next.widths.order_number).toBe(220);
    expect(next.widths.customer_name).toBe(200);
  });
});
