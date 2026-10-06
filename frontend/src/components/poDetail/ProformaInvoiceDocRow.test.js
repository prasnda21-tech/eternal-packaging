import { act } from "react";
import { createRoot } from "react-dom/client";
import ProformaInvoiceDocRow from "./ProformaInvoiceDocRow";

global.IS_REACT_ACT_ENVIRONMENT = true;

test("Lihat Proforma is a secondary document action", () => {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const onView = jest.fn();
  act(() => {
    root.render(<ProformaInvoiceDocRow invoiceNumber="INV/04/09/26/001" onView={onView} />);
  });
  const button = container.querySelector('[data-testid="btn-lihat-proforma"]');
  expect(button.textContent).toBe("Lihat Proforma");
  expect(button.className).toBe("ghost sm");
  expect(button.className).not.toMatch(/po-action-primary/);
  expect(container.textContent).toContain("Proforma Invoice");
  expect(container.querySelector('[data-testid="po-doc-proforma-number"]').textContent).toBe("INV/04/09/26/001");
  act(() => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  expect(onView).toHaveBeenCalledTimes(1);
  act(() => root.unmount());
  container.remove();
});
