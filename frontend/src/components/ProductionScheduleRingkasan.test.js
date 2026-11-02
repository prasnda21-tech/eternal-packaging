import { act } from "react";
import { createRoot } from "react-dom/client";
import ProductionScheduleRingkasan, { ProductionScheduleSearchCluster } from "./ProductionScheduleRingkasan";

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("ProductionScheduleSearchCluster", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
  });

  test("search icon and Enter invoke onSearch with current value", async () => {
    const onSearch = jest.fn();
    const onChange = jest.fn();
    await act(async () => {
      root.render(
        <ProductionScheduleSearchCluster
          value="Kopi"
          onChange={onChange}
          onSearch={onSearch}
          inputTestId="prod-search"
          searchButtonTestId="prod-search-btn"
        />,
      );
    });
    await act(async () => {
      container.querySelector('[data-testid="prod-search-btn"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    expect(onSearch).toHaveBeenCalledWith("Kopi");

    onSearch.mockClear();
    await act(async () => {
      const input = container.querySelector('[data-testid="prod-search"]');
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(onSearch).toHaveBeenCalledWith("Kopi");
  });
});

describe("ProductionScheduleRingkasan", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
  });

  test("shows basis note for filtered PO metrics", async () => {
    await act(async () => {
      root.render(
        <ProductionScheduleRingkasan
          metrics={{
            jumlahPo: 2,
            totalQtyPesanan: 10,
            qtySudahDicetak: 1,
            sisaQtyCetak: 9,
            poSelesaiCetak: 0,
            poBelumSelesaiCetak: 1,
            operatorCount: 1,
          }}
          testidPrefix="prod"
        />,
      );
    });
    await act(async () => {
      container.querySelector('[data-testid="prod-ringkasan"]').click();
    });
    expect(container.querySelector('[data-testid="prod-ringkasan-basis"]')?.textContent)
      .toMatch(/PO terfilter/i);
  });
});
