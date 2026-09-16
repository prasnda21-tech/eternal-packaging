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

  test("renders two-level headers in workflow order", async () => {
    await act(async () => { root.render(<StatusPO />); });
    await flush();
    expect(container.querySelector('[data-testid="status-po-group-header"]').textContent).toContain("DATA PO");
    expect(container.querySelector('[data-testid="status-po-group-workflow"]').textContent).toContain("PROGRESS WORKFLOW");
    expect(container.querySelector('[data-testid="status-po-group-summary"]').textContent).toContain("RINGKASAN");
    expect(container.querySelector('[data-testid="status-po-th-status"]').textContent).toContain("Status Saat Ini");
    const headers = STATUS_PO_WORKFLOW_STAGES.map((s) => (
      container.querySelector(`[data-testid="status-po-th-wf-${s.key}"]`).textContent.trim()
    ));
    expect(headers).toEqual(STATUS_PO_WORKFLOW_STAGES.map((s) => s.header));
  });

  test("current badge, complete check, active ring, pending dash, cancelled x, progress", async () => {
    await act(async () => { root.render(<StatusPO />); });
    await flush();
    expect(container.querySelector('[data-testid="status-po-status-PO-PACK"] .spo-badge').textContent).toBe("Packing");
    expect(container.querySelector('[data-testid="status-po-wf-PO-PACK-cetak"]').getAttribute("data-state")).toBe("completed");
    expect(container.querySelector('[data-testid="status-po-wf-PO-PACK-packing"]').getAttribute("data-state")).toBe("active");
    expect(container.querySelector('[data-testid="status-po-wf-PO-PACK-design"]').getAttribute("data-state")).toBe("pending");
    expect(container.querySelector('[data-testid="status-po-wf-PO-PACK-kirim"]').getAttribute("data-state")).toBe("pending");
    expect(container.querySelector('[data-testid="status-po-wf-PO-CXL-order"]').getAttribute("data-state")).toBe("cancelled");
    expect(container.querySelector('[data-testid="status-po-progress-PO-DONE"]').textContent).toContain("100%");
    const items = container.querySelector('[data-testid="status-po-items-PO-PACK"]');
    expect(items.textContent).toContain("Cup 16oz");
    expect(items.textContent).toContain("Lid 98 mm");
    expect(items.textContent).toContain("Add On Warna");
    expect(items.textContent).not.toContain("1000");
    expect(items.textContent).not.toContain("qty");
    expect(container.querySelectorAll('[data-testid="status-po-row-PO-PACK"]')).toHaveLength(1);
  });

  test("horizontal matrix structure stays a single wide table", async () => {
    await act(async () => { root.render(<StatusPO />); });
    await flush();
    const table = container.querySelector('[data-testid="status-po-matrix"]');
    expect(table).toBeTruthy();
    expect(table.querySelectorAll("thead tr")).toHaveLength(2);
    expect(Number.parseInt(table.style.minWidth, 10)).toBeGreaterThan(1100);
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
    const stored = JSON.parse(window.localStorage.getItem("status-po-col-widths-v1"));
    expect(stored.po).toBeGreaterThanOrEqual(175);
    const cols = container.querySelectorAll("colgroup col");
    expect(cols.length).toBeGreaterThan(18);
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
