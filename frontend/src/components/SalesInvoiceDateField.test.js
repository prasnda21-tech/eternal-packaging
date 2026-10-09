import { act } from "react";
import { createRoot } from "react-dom/client";
import SalesInvoiceDateField from "./SalesInvoiceDateField";

global.IS_REACT_ACT_ENVIRONMENT = true;

function setup(initial = "2026-10-09") {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const ref = { current: null };
  const state = { iso: initial, saved: null, message: "" };
  const render = () => {
    act(() => {
      root.render(
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const read = ref.current.read();
            state.saved = read.ok ? read.iso : null;
            state.message = read.message;
          }}
        >
          <SalesInvoiceDateField
            ref={ref}
            valueIso={state.iso}
            onChange={(next) => { state.iso = next; render(); }}
            testId="invoice-date"
          />
          <button type="submit">Simpan</button>
        </form>
      );
    });
  };
  render();
  return {
    host,
    state,
    text: () => host.querySelector("[data-testid='invoice-date']"),
    calendar: () => host.querySelector("[data-testid='invoice-date-calendar']"),
    save: () => act(() => { host.querySelector("button").click(); }),
    type(value) {
      const input = host.querySelector("[data-testid='invoice-date']");
      act(() => {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        setter.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    },
    pick(value) {
      const input = host.querySelector("[data-testid='invoice-date-calendar']");
      act(() => {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        setter.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    },
    load(iso) {
      state.iso = iso;
      render();
    },
    unmount() {
      act(() => root.unmount());
      host.remove();
    },
  };
}

test("clearing a valid date blocks save", () => {
  const view = setup();
  view.type("");
  expect(view.state.iso).toBe("");
  view.save();
  expect(view.state.saved).toBeNull();
  expect(view.state.message).toMatch(/wajib/i);
  view.unmount();
});

test("invalid text blocks save and stays visible", () => {
  const view = setup();
  view.type("31/02/2026");
  expect(view.text().value).toBe("31/02/2026");
  expect(view.state.iso).toBe("");
  view.save();
  expect(view.state.saved).toBeNull();
  expect(view.text().value).toBe("31/02/2026");
  view.unmount();
});

test("corrected text saves as canonical ISO", () => {
  const view = setup();
  view.type("31/02/2026");
  view.type("09/10/2026");
  view.save();
  expect(view.state.saved).toBe("2026-10-09");
  expect(view.text().value).toBe("09/10/2026");
  view.unmount();
});

test("calendar selection updates visible DD/MM/YYYY and calendar clear blocks save", () => {
  const view = setup("2026-01-15");
  view.pick("2026-10-09");
  expect(view.text().value).toBe("09/10/2026");
  expect(view.calendar().value).toBe("2026-10-09");
  view.pick("");
  expect(view.state.iso).toBe("");
  expect(view.text().value).toBe("");
  view.save();
  expect(view.state.saved).toBeNull();
  view.unmount();
});

test("opening another invoice resets stale error and input", () => {
  const view = setup("2026-10-09");
  view.type("31/02/2026");
  expect(view.host.querySelector(".fp-invoice-date-error")).not.toBeNull();
  view.load("2026-08-15");
  expect(view.text().value).toBe("15/08/2026");
  expect(view.calendar().value).toBe("2026-08-15");
  expect(view.host.querySelector(".fp-invoice-date-error")).toBeNull();
  view.save();
  expect(view.state.saved).toBe("2026-08-15");
  view.unmount();
});

test("a new invoice value replaces a stale invalid draft", () => {
  const view = setup("2026-10-09");
  view.type("31/02/2026");
  expect(view.text().value).toBe("31/02/2026");
  view.pick("2026-03-01");
  expect(view.text().value).toBe("01/03/2026");
  expect(view.calendar().value).toBe("2026-03-01");
  expect(view.host.querySelector(".fp-invoice-date-error")).toBeNull();
  view.save();
  expect(view.state.saved).toBe("2026-03-01");
  view.unmount();
});
