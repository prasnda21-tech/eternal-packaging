import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import PurchasePaymentAjukanModal from "./PurchasePaymentAjukanModal";
import api from "../lib/api";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    listPurchasePaymentSubmitTargets: jest.fn(),
    ajukanPurchasePayment: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => {
  api.listPurchasePaymentSubmitTargets.mockResolvedValue({ users: [] });
});

function Host({ invoice }) {
  const [current, setCurrent] = useState(invoice);
  global.__setAjukanInvoice = setCurrent;
  return <PurchasePaymentAjukanModal open invoice={current} onCancel={() => {}} onSubmitted={() => {}} />;
}

function render(invoice) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => { root.render(<Host invoice={invoice} />); });
  return {
    host,
    unmount() {
      act(() => { root.unmount(); });
      host.remove();
    },
  };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const waitSearch = () => new Promise((resolve) => setTimeout(resolve, 250));

test("typed amount stays when the same invoice object is refreshed", async () => {
  const view = render({ id: "inv-1", invoice_number: "FPB-1", outstanding_amount: 80, supplier: "A" });
  await act(async () => { await flush(); });
  const input = view.host.querySelector("[data-testid='payment-ajukan-amount']");
  expect(input.value).toBe("80");
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(input, "25");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await flush();
  });
  await act(async () => {
    global.__setAjukanInvoice({ id: "inv-1", invoice_number: "FPB-1", outstanding_amount: 80, supplier: "A refreshed" });
    await flush();
  });
  expect(view.host.querySelector("[data-testid='payment-ajukan-amount']").value).toBe("25");
  expect(view.host.textContent).toContain("A refreshed");
  view.unmount();
});

test("amount resets when the outstanding balance changes", async () => {
  const view = render({ id: "inv-1", invoice_number: "FPB-1", outstanding_amount: 80 });
  await act(async () => { await flush(); });
  await act(async () => {
    global.__setAjukanInvoice({ id: "inv-1", invoice_number: "FPB-1", outstanding_amount: 40 });
    await flush();
  });
  expect(view.host.querySelector("[data-testid='payment-ajukan-amount']").value).toBe("40");
  await act(async () => { await waitSearch(); });
  expect(api.listPurchasePaymentSubmitTargets).toHaveBeenCalled();
  view.unmount();
});
