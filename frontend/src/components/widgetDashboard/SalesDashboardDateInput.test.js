import { act } from "react";
import { createRoot } from "react-dom/client";
import SalesDashboardDateInput from "./SalesDashboardDateInput";

global.IS_REACT_ACT_ENVIRONMENT = true;

function setup(initial = "2026-03-01", ariaLabel = "Tanggal mulai") {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const state = { iso: initial };
  const render = () => {
    act(() => {
      root.render(
        <SalesDashboardDateInput
          valueIso={state.iso}
          onChange={(next) => { state.iso = next; render(); }}
          ariaLabel={ariaLabel}
          testId="sales-period-start"
        />
      );
    });
  };
  render();
  return {
    host,
    state,
    text: () => host.querySelector("[data-testid='sales-period-start']"),
    calendar: () => host.querySelector("[data-testid='sales-period-start-calendar']"),
    trigger: () => host.querySelector("[data-testid='sales-period-start-trigger']"),
    type(value) {
      const input = host.querySelector("[data-testid='sales-period-start']");
      act(() => {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        setter.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    },
    blur() {
      const input = host.querySelector("[data-testid='sales-period-start']");
      act(() => { input.dispatchEvent(new Event("blur", { bubbles: true })); });
    },
    pick(value) {
      const input = host.querySelector("[data-testid='sales-period-start-calendar']");
      act(() => {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        setter.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    },
    unmount() {
      act(() => root.unmount());
      host.remove();
    },
  };
}

test("shows one visible dd/mm/yyyy value with an accessible start label", () => {
  const view = setup("2026-03-01", "Tanggal mulai");
  expect(view.text().value).toBe("01/03/2026");
  expect(view.text().type).toBe("text");
  expect(view.host.textContent).toMatch(/Tanggal mulai/);
  expect(view.trigger()).not.toBeNull();
  expect(view.calendar().getAttribute("aria-hidden")).toBe("true");
  expect(view.calendar().className).toMatch(/sd-date-calendar/);
  expect(view.calendar().tabIndex).toBe(-1);
  expect(view.state.iso).toBe("2026-03-01");
  expect(view.host.querySelectorAll("input[type='text']")).toHaveLength(1);
  expect(view.host.querySelectorAll("input[type='date']")).toHaveLength(1);
  view.unmount();
});

test("rejects impossible calendar days and keeps the previous ISO", () => {
  const view = setup("2026-03-01");
  view.type("31/04/2026");
  view.blur();
  expect(view.text().value).toBe("31/04/2026");
  expect(view.state.iso).toBe("2026-03-01");
  expect(view.host.textContent).toMatch(/DD\/MM\/YYYY/);
  expect(view.host.textContent).not.toMatch(/Invalid Date/);

  view.type("29/02/2026");
  view.blur();
  expect(view.state.iso).toBe("2026-03-01");

  view.type("29/02/2028");
  expect(view.state.iso).toBe("2028-02-29");
  expect(view.text().value).toBe("29/02/2028");
  view.unmount();
});

test("partial typing does not clear the request date", () => {
  const view = setup("2026-03-31");
  view.type("01/0");
  expect(view.state.iso).toBe("2026-03-31");
  expect(view.calendar().value).toBe("2026-03-31");
  view.unmount();
});

test("calendar selection publishes YYYY-MM-DD and displays dd/mm/yyyy", () => {
  const view = setup("2026-03-01");
  view.pick("2026-03-31");
  expect(view.state.iso).toBe("2026-03-31");
  expect(view.text().value).toBe("31/03/2026");
  expect(view.calendar().value).toBe("2026-03-31");
  view.unmount();
});

test("calendar trigger opens the hidden native picker", () => {
  const view = setup("2026-03-01");
  let opened = false;
  view.calendar().showPicker = () => { opened = true; };
  act(() => { view.trigger().click(); });
  expect(opened).toBe(true);
  view.unmount();
});
