import { act } from "react";
import { createRoot } from "react-dom/client";
import { LineRouteEditor } from "./ProductionRoutePanel";
import api from "../lib/api";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: { setLineItemRoute: jest.fn() },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

function mount(ui) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => root.render(ui));
  return {
    container,
    rerender(next) {
      act(() => root.render(next));
    },
    unmount() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

function orderWith(process) {
  return {
    id: "order-route-1",
    line_items: [{ name: "Cup 12oz", item_code: "C12", quantity: 500, production_process: process }],
  };
}

describe("LineRouteEditor dropdown", () => {
  beforeEach(() => {
    api.setLineItemRoute.mockReset();
  });

  test("renders the server process and reverts when the save fails", async () => {
    api.setLineItemRoute.mockRejectedValueOnce({ response: { data: { detail: "gagal route" } } });
    const view = mount(
      <LineRouteEditor order={orderWith("non_print")} lineIdx={0} canEdit onUpdated={jest.fn()} />
    );
    const select = view.container.querySelector('[data-testid="line-route-select-0"]');
    expect(select.value).toBe("non_print");
    expect(select.querySelector('option[value="print"]').textContent).toBe("Cetak");
    expect(select.querySelector('option[value="non_print"]').textContent).toBe("Tidak Cetak");
    await act(async () => {
      select.value = "print";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(api.setLineItemRoute).toHaveBeenCalledWith("order-route-1", [
      { line_idx: 0, production_process: "print" },
    ]);
    expect(view.container.querySelector('[data-testid="line-route-select-0"]').value).toBe("non_print");
    expect(view.container.querySelector('[data-testid="route-error-0"]').textContent).toMatch(/gagal route/);
    view.unmount();
  });

  test("commits the order returned by the server", async () => {
    const next = orderWith("print");
    api.setLineItemRoute.mockResolvedValueOnce({ order: next });
    const onUpdated = jest.fn();
    const view = mount(
      <LineRouteEditor order={orderWith("non_print")} lineIdx={0} canEdit onUpdated={onUpdated} />
    );
    const select = view.container.querySelector('[data-testid="line-route-select-0"]');
    await act(async () => {
      select.value = "non_print";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(api.setLineItemRoute).not.toHaveBeenCalled();
    await act(async () => {
      select.value = "print";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(onUpdated).toHaveBeenCalledWith(next);
    view.rerender(<LineRouteEditor order={next} lineIdx={0} canEdit onUpdated={onUpdated} />);
    expect(view.container.querySelector('[data-testid="line-route-select-0"]').value).toBe("print");
    view.unmount();
  });

  test("disables the dropdown without production.route.edit", () => {
    const view = mount(
      <LineRouteEditor order={orderWith("print")} lineIdx={0} canEdit={false} onUpdated={jest.fn()} />
    );
    const select = view.container.querySelector('[data-testid="line-route-select-0"]');
    expect(select.disabled).toBe(true);
    expect(select.value).toBe("print");
    view.unmount();
  });
});
