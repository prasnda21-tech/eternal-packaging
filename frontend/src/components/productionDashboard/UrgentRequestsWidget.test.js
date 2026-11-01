import { act } from "react";
import { createRoot } from "react-dom/client";
import UrgentRequestsWidget, {
  urgentStatusBadgeClass,
  urgentStatusLabel,
  URGENT_REQUEST_FILTERS,
} from "./UrgentRequestsWidget";

jest.mock("react-router-dom", () => {
  const React = require("react");
  return {
    Link: ({ to, children, ...props }) => React.createElement("a", { href: to, ...props }, children),
  };
}, { virtual: true });

global.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

test("status labels and badge classes follow monitoring contract", () => {
  expect(urgentStatusLabel("pending")).toBe("Menunggu");
  expect(urgentStatusLabel("approving")).toBe("Sedang Diproses");
  expect(urgentStatusLabel("approved")).toBe("Disetujui");
  expect(urgentStatusLabel("rejected")).toBe("Ditolak");
  expect(urgentStatusLabel("cancelled")).toBe("Dibatalkan");
  expect(urgentStatusBadgeClass("pending")).toContain("is-urgent-pending");
  expect(urgentStatusBadgeClass("approving")).toContain("is-urgent-approving");
  expect(URGENT_REQUEST_FILTERS[0].key).toBe("active");
});

test("loads rows, opens read-only drawer without approve actions", async () => {
  const fetchList = jest.fn().mockResolvedValue({
    status: "complete",
    total: 1,
    page: 1,
    page_size: 10,
    can_open_po: true,
    items: [{
      id: "ur-1",
      order_id: "ord-1",
      po_number: "PO-1001",
      customer_name: "Kopi Nusantara",
      branch_name: "Yogyakarta",
      requested_by_name: "Budi",
      reason: "Event mendadak",
      target_completion_date: "2026-11-05",
      status: "pending",
      requested_at: "2026-11-01T10:00:00+07:00",
      decided_at: "",
      history: [{ action: "submitted", at: "2026-11-01T10:00:00+07:00", by_name: "Budi", note: "Event mendadak" }],
    }],
  });

  await act(async () => {
    root.render(<UrgentRequestsWidget model={{ status: "ready" }} fetchList={fetchList} />);
  });
  await act(async () => { await Promise.resolve(); });

  expect(fetchList).toHaveBeenCalledWith(expect.objectContaining({ status: "active", page: 1 }));
  expect(host.querySelector('[data-testid="produksi-urgent-table"]')).toBeTruthy();
  expect(host.textContent).toContain("PO-1001");
  expect(host.querySelector('[data-testid="produksi-urgent-status"]').textContent).toBe("Menunggu");

  await act(async () => {
    host.querySelector('[data-testid="produksi-urgent-view"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  expect(host.querySelector('[data-testid="produksi-urgent-drawer"]')).toBeTruthy();
  expect(host.textContent).toContain("Detail Pengajuan Urgent");
  expect(host.textContent).toContain("Buka Detail PO");
  expect(host.querySelector('[data-testid="produksi-urgent-drawer"]')?.textContent || "").not.toMatch(
    /Setujui Pengajuan|Tolak Pengajuan|Batalkan Pengajuan|decision-card/i,
  );
  expect(host.querySelector('[data-testid="produksi-urgent-drawer"] button[data-testid*="approve"]')).toBeNull();
  expect(host.querySelector('[data-testid="produksi-urgent-drawer"] button[data-testid*="reject"]')).toBeNull();
  expect(host.querySelector('[data-testid="produksi-urgent-open-po"]').getAttribute("href")).toBe("/po/ord-1");
});

test("search clear appears only when query has text and clears without approve actions", async () => {
  const fetchList = jest.fn().mockResolvedValue({
    status: "complete",
    total: 0,
    page: 1,
    page_size: 10,
    can_open_po: true,
    items: [],
  });

  await act(async () => {
    root.render(<UrgentRequestsWidget model={{ status: "ready" }} fetchList={fetchList} />);
  });
  await act(async () => { await Promise.resolve(); });

  expect(host.querySelector('[data-testid="produksi-urgent-search-clear"]')).toBeNull();
  const input = host.querySelector('[data-testid="produksi-urgent-search"]');
  await act(async () => {
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    nativeInputValueSetter.call(input, "Nusantara");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(host.querySelector('[data-testid="produksi-urgent-search-clear"]')).toBeTruthy();
  await act(async () => {
    host.querySelector('[data-testid="produksi-urgent-search-clear"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  expect(input.value).toBe("");
  expect(host.querySelector('[data-testid="produksi-urgent-search-clear"]')).toBeNull();
});

test("forbidden empty and error states render", async () => {
  await act(async () => {
    root.render(<UrgentRequestsWidget model={{ status: "forbidden" }} fetchList={jest.fn()} />);
  });
  expect(host.querySelector('[data-testid="produksi-urgent-forbidden"]')).toBeTruthy();

  const emptyFetch = jest.fn().mockResolvedValue({ status: "empty", total: 0, items: [], can_open_po: false });
  await act(async () => {
    root.render(<UrgentRequestsWidget model={{ status: "ready" }} fetchList={emptyFetch} />);
  });
  await act(async () => { await Promise.resolve(); });
  expect(host.querySelector('[data-testid="produksi-urgent-empty"]')).toBeTruthy();

  const err = Object.assign(new Error("boom"), { response: { status: 500 } });
  const badFetch = jest.fn().mockRejectedValue(err);
  await act(async () => {
    root.render(<UrgentRequestsWidget model={{ status: "ready" }} fetchList={badFetch} />);
  });
  await act(async () => { await Promise.resolve(); });
  expect(host.querySelector('[data-testid="produksi-urgent-error"]')).toBeTruthy();
});
