import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import SalesPwaVisitsPage from "./SalesPwaVisitsPage";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesPwaVisits: jest.fn(),
    createSalesPwaVisit: jest.fn(),
    uploadSalesPwaVisitPhoto: jest.fn(),
  },
}));
global.IS_REACT_ACT_ENVIRONMENT = true;

test("renders a compact calendar and opens visit drawer", async () => {
  api.salesPwaVisits.mockResolvedValue({
    counts: { owned_outlets: 2, visit_month: 1, overdue: 1 },
    reminders: [{ id: "r1", outlet: "Kopi Baru", date: "2026-09-20", overdue: true }],
    outlets: [{ id: "o1", name: "Kopi Baru", pic_name: "Budi", ownership: { next_follow_up_at: "2026-09-20T09:00:00+07:00" } }],
    visit_config: { photo_required: false, next_follow_up_required: false },
  });
  const node = document.createElement("div");
  document.body.appendChild(node);
  const root = createRoot(node);
  await act(async () => {
    root.render(<SalesPwaVisitsPage />);
    await Promise.resolve();
  });
  expect(node.querySelector('[aria-label="Kalender reminder visit"]')).toBeTruthy();
  const button = Array.from(node.querySelectorAll("button")).find((item) => item.textContent.includes("Visit"));
  act(() => button.click());
  expect(document.body.textContent).toContain("CATAT VISIT");
  expect(document.body.textContent).toContain("Kopi Baru");
  act(() => root.unmount());
  node.remove();
});
