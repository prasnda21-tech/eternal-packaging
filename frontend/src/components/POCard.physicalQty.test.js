/**
 * @jest-environment jsdom
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import POCard from "./POCard";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={typeof to === "string" ? to : "#"} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: () => true }),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("POCard physical qty (Order Masuk)", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("IDER: shows packing_required_total 1000 not total_quantity 3000", async () => {
    await act(async () => {
      root.render(
        <POCard
          order={{
            id: "o1",
            po_number: "PO/2026/09/09/000007",
            outlet: "IDER COFFEE",
            item_count: 3,
            current_stage: 0,
            total_quantity: 3000,
            packing_required_total: 1000,
            operator_splits: [],
          }}
        />,
      );
    });
    const el = container.querySelector('[data-testid="order-physical-qty-PO/2026/09/09/000007"]');
    expect(el).toBeTruthy();
    expect(el.textContent).toMatch(/1\.000 pcs/);
    expect(el.textContent).not.toMatch(/3\.000/);
  });

  test("zero physical remains 0 (not missing)", async () => {
    await act(async () => {
      root.render(
        <POCard
          order={{
            id: "o0",
            po_number: "PO-ZERO",
            outlet: "X",
            item_count: 2,
            current_stage: 0,
            total_quantity: 2000,
            packing_required_total: 0,
            operator_splits: [],
          }}
        />,
      );
    });
    const el = container.querySelector('[data-testid="order-physical-qty-PO-ZERO"]');
    expect(el.textContent).toMatch(/0 pcs/);
    expect(el.textContent).not.toMatch(/tidak tersedia/);
  });

  test("missing packing_required_total does not fall back to total_quantity", async () => {
    await act(async () => {
      root.render(
        <POCard
          order={{
            id: "o2",
            po_number: "PO-MISS",
            outlet: "X",
            item_count: 2,
            current_stage: 0,
            total_quantity: 3000,
            operator_splits: [],
          }}
        />,
      );
    });
    const el = container.querySelector('[data-testid="order-physical-qty-PO-MISS"]');
    expect(el).toBeTruthy();
    expect(el.textContent).toMatch(/Qty fisik tidak tersedia/);
    expect(el.textContent).not.toMatch(/3\.000/);
  });
});
