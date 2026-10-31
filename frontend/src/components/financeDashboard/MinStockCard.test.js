import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react-dom/test-utils";
import MinStockCard from "./MinStockCard";

function render(model) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => {
    root.render(<MinStockCard model={model} />);
  });
  return { host, root, unmount: () => act(() => root.unmount()) };
}

describe("MinStockCard", () => {
  test("shows verified violations and unevaluable separately", () => {
    const { host, unmount } = render({
      status: "incomplete",
      violation_count: 2,
      unevaluable_count: 3,
      detail: "Sebagian data belum bisa dinilai",
      items: [
        {
          master_item_id: "m1",
          warehouse_id: "w1",
          item_name: "Box A",
          warehouse_code: "WH1",
          stock_dapat_dijual: 1,
          minimum_configured: true,
          minimum_qty: 5,
        },
      ],
    });
    expect(host.querySelector("[data-testid='min-stock-violation-count']").textContent).toContain("2");
    expect(host.querySelector("[data-testid='min-stock-unevaluable-count']").textContent).toContain("3");
    expect(host.querySelector("[data-testid='min-stock-incomplete-note']")).toBeTruthy();
    unmount();
  });

  test("empty with unevaluable is not presented as all-safe zero alone", () => {
    const { host, unmount } = render({
      status: "empty",
      violation_count: 0,
      unevaluable_count: 4,
      items: [],
    });
    expect(host.querySelector("[data-testid='min-stock-unevaluable-count']").textContent).toContain("4");
    expect(host.textContent).not.toMatch(/semua aman/i);
    unmount();
  });
});
