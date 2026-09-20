import { act } from "react";
import { createRoot } from "react-dom/client";
import StatusPO from "./StatusPO";
import { STATUS_PO_WORKFLOW_STAGES } from "../lib/statusPoWorkflow";
import api from "../lib/api";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={typeof to === "string" ? to : to?.pathname || "#"} {...props}>{children}</a>,
  useSearchParams: () => [new URLSearchParams(""), jest.fn()],
  useLocation: () => ({ pathname: "/laporan/status-po", search: "", state: null }),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u-1", role: "admin", name: "Admin", email: "a@x.com" },
    hasPerm: () => true,
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    statusPo: jest.fn(),
    bulkCancel: jest.fn(),
    bulkDelete: jest.fn(),
    listOrders: jest.fn(),
    reminders: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const rows = [
  {
    id: "pack",
    po_number: "PO-PACK",
    order_date: "2026-09-01",
    customer: "Pack Co",
    item_lines: ["Cup 16oz", "Lid 98 mm", "Add On Warna"],
    status: "Packing",
    percent_delivered: 0,
    current_stage: 11,
    workflow: {
      order: "completed", design: "pending", cetak: "completed", packing: "active", kirim: "pending",
    },
  },
  {
    id: "done",
    po_number: "PO-DONE",
    order_date: "2026-08-01",
    customer: "Done Co",
    item_lines: ["Finished Pack"],
    status: "Terkirim",
    percent_delivered: 100,
    current_stage: 14,
    workflow: Object.fromEntries([
      "order", "design", "appr_design", "payment", "appr_payment",
      "jadwal", "screen", "helper", "operator", "sample",
      "cetak", "packing", "sj", "loading", "kirim",
    ].map((k) => [k, "completed"])),
  },
  {
    id: "cancel",
    po_number: "PO-CXL",
    order_date: "2026-08-02",
    customer: "Cancel Co",
    item_lines: ["Cancelled Item"],
    status: "Cancelled",
    percent_delivered: 0,
    cancelled: true,
    current_stage: 2,
  },
];

describe("Status PO workflow matrix UI", () => {
  let container;
  let root;

  beforeEach(() => {
    window.localStorage.clear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.statusPo.mockReset();
    api.statusPo.mockResolvedValue({ rows, total: rows.length });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("renders compact operational headers and 15-stage progress", async () => {
    await act(async () => { root.render(<StatusPO />); });
    await flush();
    expect(container.querySelector('[data-testid="status-po-th-status"]').textContent).toContain("Status Saat Ini");
    expect(container.querySelector('[data-testid="status-po-th-progress"]').textContent).toContain("Progress (15 Tahap)");
    expect(container.querySelector('[data-testid="status-po-th-items"]').textContent).toContain("Item / Produk");
    expect(container.querySelector('[data-testid="status-po-th-age"]').textContent).toContain("Umur");
    expect(container.querySelector('[data-testid="status-po-subtitle"]')).toBeNull();
    expect(container.textContent).not.toContain("Pantau status pesanan dari semua cabang secara real-time");
    expect(container.querySelector('[data-testid="status-po-density"]')).toBeNull();
    expect(container.textContent).not.toMatch(/Tampilan:\s*(Rapat|Longgar)/);
    expect(container.querySelector('[data-testid="status-po-heading-count"]').textContent).toContain("3 PO");
    expect(container.querySelector('[data-testid="status-po-filter-po"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-filter-customer"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-filter-status"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-filter-date-from"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-filter-date-to"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-filter-reset"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="status-po-refresh"]')).toBeTruthy();
    expect(container.querySelectorAll('[data-testid="status-po-progress-PO-PACK"] .spo-seg')).toHaveLength(STATUS_PO_WORKFLOW_STAGES.length);
    expect(STATUS_PO_WORKFLOW_STAGES).toHaveLength(15);
  });

  test("current strip, complete/active/pending/cancelled segments, progress", async () => {
    await act(async () => { root.render(<StatusPO />); });
    await flush();
    expect(container.querySelector('[data-testid="status-po-status-PO-PACK"] .spo-status-strip').textContent).toContain("Packing");
    expect(container.querySelector('[data-testid="status-po-wf-PO-PACK-cetak"]').getAttribute("data-state")).toBe("completed");
    expect(container.querySelector('[data-testid="status-po-wf-PO-PACK-packing"]').getAttribute("data-state")).toBe("active");
    expect(container.querySelector('[data-testid="status-po-wf-PO-PACK-design"]').getAttribute("data-state")).toBe("pending");
    expect(container.querySelector('[data-testid="status-po-wf-PO-PACK-kirim"]').getAttribute("data-state")).toBe("pending");
    expect(container.querySelector('[data-testid="status-po-wf-PO-CXL-order"]').getAttribute("data-state")).toBe("cancelled");
    expect(container.querySelector('[data-testid="status-po-pct-PO-DONE"]').textContent).toContain("100%");
    const items = container.querySelector('[data-testid="status-po-items-PO-PACK"]');
    expect(items.textContent).toContain("Cup 16oz");
    expect(items.textContent).toContain("+2 lainnya");
    expect(items.getAttribute("title")).toContain("Lid 98 mm");
    expect(items.getAttribute("title")).toContain("Add On Warna");
    expect(items.textContent).not.toContain("1000");
    expect(items.textContent).not.toContain("qty");
    expect(container.querySelectorAll('[data-testid="status-po-row-PO-PACK"]')).toHaveLength(1);
  });

  test("compact table stays a single full-width table", async () => {
    await act(async () => { root.render(<StatusPO />); });
    await flush();
    const table = container.querySelector('[data-testid="status-po-matrix"]');
    expect(table).toBeTruthy();
    expect(table.querySelectorAll("thead tr")).toHaveLength(1);
    expect(Number.parseInt(table.style.minWidth, 10)).toBeGreaterThan(900);
  });

  test("resizing a header keeps min width and aligned colgroup", async () => {
    await act(async () => { root.render(<StatusPO />); });
    await flush();
    const handle = container.querySelector('[data-testid="status-po-resize-po"]');
    await act(async () => {
      handle.dispatchEvent(new MouseEvent("mousedown", { clientX: 200, bubbles: true }));
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 40, bubbles: true }));
      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });
    const stored = JSON.parse(window.localStorage.getItem("status-po-col-widths-v3"));
    expect(stored.po).toBeGreaterThanOrEqual(132);
    const cols = container.querySelectorAll("colgroup col");
    expect(cols.length).toBe(10);
  });

  test("legend stays compact with existing meanings", async () => {
    await act(async () => { root.render(<StatusPO />); });
    await flush();
    const legend = container.querySelector('[data-testid="status-po-legend"]').textContent;
    expect(legend).toContain("Selesai");
    expect(legend).toContain("Tahap Aktif");
    expect(legend).toContain("Dilewati (data lama)");
    expect(legend).not.toContain("Dalam Proses");
    expect(legend).toContain("Belum Dimulai");
    expect(legend).toContain("Tertahan / Bermasalah");
    expect(container.querySelector('[data-testid="status-po-view-label"]')).toBeNull();
    expect(container.querySelector(".spo-heading-accent")).toBeNull();
  });

  test("row selection still works beside the matrix", async () => {
    await act(async () => { root.render(<StatusPO />); });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="status-po-check-PO-PACK"]').click();
    });
    expect(container.querySelector('[data-testid="status-po-selected-count"]').textContent).toContain("1");
  });
});
