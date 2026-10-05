import { act } from "react";
import { createRoot } from "react-dom/client";
import MobilePrintWorkspace from "./MobilePrintWorkspace";
import api from "../../lib/api";

jest.mock("../../lib/api", () => ({
  __esModule: true,
  fileUrl: (storagePath) => `/api/files/${storagePath}`,
  default: {
    updateOperatorProgress: jest.fn(),
    fetchStoredFileBlob: jest.fn(),
    getOrderAttachment: jest.fn(),
  },
}));

jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: jest.fn(),
}));

const HELPER_REASON = "Persiapan Helper harus diisi sebelum pekerjaan Operator.";
const SAMPLE_REASON = "Cetak Sample harus disetujui sebelum Qty Selesai Cetak dapat diisi.";
const EMPTY_MSG = "Tidak ada alokasi cetak untuk akun ini.";
const NEUTRAL_MSG = "Input hasil cetak belum tersedia.";
const OLD_FALLBACK = "Cetak menunggu persiapan dan sampel.";

const USER_A = { id: "user-a", email: "operator.a@x.com" };
const USER_B = { id: "user-b", email: "operator.b@x.com" };
const USER_C = { id: "user-c", email: "operator.c@x.com" };

let mockUser = USER_A;
const mockHasPerm = jest.fn(() => false);

jest.mock("../../lib/auth", () => ({
  useAuth: () => ({ user: mockUser, hasPerm: (perm) => mockHasPerm(perm) }),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

function idQty(n) {
  return Number(n).toLocaleString("id-ID");
}

function designFiles() {
  return [{
    name: "desain.pdf",
    content_type: "application/pdf",
    storage_path: "designs/a.pdf",
  }];
}

function split({ id, user, lineIdx, quantity, done = 0 }) {
  return {
    id,
    operator_email: user.email,
    operator_user_id: user.id,
    operator_name: user.email,
    line_idx: lineIdx,
    quantity,
    qty_completed: done,
    progress_entries: [],
  };
}

function printOrder({ splits, lineItems, workflowReady = { print_qty: true }, gateReason = null, printQtyTarget = 2000 }) {
  return {
    id: "po-print-1",
    po_number: "PO-PRINT-1",
    print_qty_target: printQtyTarget,
    workflow_ready: workflowReady,
    print_qty_gate_reason: gateReason,
    design_status: "Waiting",
    design_files: designFiles(),
    line_items: lineItems || [
      { name: "Wrap Paper", quantity: 2000, item_id: "item-0", production_process: "print" },
    ],
    operator_splits: splits,
  };
}

function metrics(container) {
  return Object.fromEntries([...container.querySelectorAll(".mop-metric")].map((node) => [
    node.querySelector("span").textContent,
    node.querySelector("strong").textContent,
  ]));
}

function lineFigures(container) {
  return [...container.querySelectorAll(".mop-line-block p.mop-card-meta")].map((node) => (
    node.children[node.children.length - 1].textContent
  ));
}

function saveButtons(container) {
  return [...container.querySelectorAll("button")].filter((node) => node.textContent === "Simpan Hasil Cetak");
}

describe("Mobile print panel allocation empty state and gate copy", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockUser = USER_A;
    mockHasPerm.mockReset();
    mockHasPerm.mockReturnValue(false);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function renderPanel(order, canEdit = true) {
    act(() => {
      root.render(<MobilePrintWorkspace order={order} canEdit={canEdit} onUpdated={() => {}} />);
    });
  }

  function expectDesignAvailable() {
    const button = container.querySelector('[data-testid="mobile-lihat-desain"]');
    expect(container.querySelector('[data-testid="mobile-design-file"]')).not.toBeNull();
    expect(button).not.toBeNull();
    expect(button.textContent).toBe("Lihat Desain");
    expect(button.disabled).toBe(false);
  }

  function expectNoResultForm() {
    expect(container.querySelector('[data-testid="mobile-status-badge"]')).toBeNull();
    expect(container.querySelector('[data-testid="mobile-metrics"]')).toBeNull();
    expect(container.querySelector('[aria-label="Progress cetak"]')).toBeNull();
    expect(container.textContent).not.toMatch(/Target Cetak|Sudah Cetak|Belum Mulai|Proses|Selesai/);
    expect(saveButtons(container)).toHaveLength(0);
    expect(container.querySelector("input")).toBeNull();
    expect(container.querySelector('[data-testid="mobile-print-gate"]')).toBeNull();
    expect(container.textContent).not.toContain(HELPER_REASON);
    expect(container.textContent).not.toContain(SAMPLE_REASON);
    expect(container.textContent).not.toContain(NEUTRAL_MSG);
    expect(container.textContent).not.toContain(OLD_FALLBACK);
  }

  test("allocation 1200 with result 400 shows 1200 / 400 / 800", () => {
    renderPanel(printOrder({
      splits: [
        split({ id: "sp-a", user: USER_A, lineIdx: 0, quantity: 1200, done: 400 }),
        split({ id: "sp-b", user: USER_B, lineIdx: 0, quantity: 800, done: 0 }),
      ],
    }));

    expect(metrics(container)).toEqual({
      "Target Cetak": idQty(1200),
      "Sudah Cetak": idQty(400),
      Sisa: idQty(800),
    });
    expect(container.querySelector('[data-testid="mobile-status-badge"]').textContent).toBe("Proses");
    expect(lineFigures(container)).toEqual([`${idQty(400)} / ${idQty(1200)}`]);
    expect(container.textContent).not.toContain(idQty(2000));
    expect(container.querySelector('[data-testid="mobile-print-empty"]')).toBeNull();
    expect(container.querySelector('[data-testid="mobile-print-gate"]')).toBeNull();
    expect(saveButtons(container)[0].disabled).toBe(false);
    expectDesignAvailable();
  });

  test("another operator sees only that account allocation and result", () => {
    mockUser = USER_B;
    renderPanel(printOrder({
      splits: [
        split({ id: "sp-a", user: USER_A, lineIdx: 0, quantity: 1200, done: 400 }),
        split({ id: "sp-b", user: USER_B, lineIdx: 0, quantity: 800, done: 0 }),
      ],
    }));

    expect(metrics(container)).toEqual({
      "Target Cetak": idQty(800),
      "Sudah Cetak": idQty(0),
      Sisa: idQty(800),
    });
    expect(lineFigures(container)).toEqual([`${idQty(0)} / ${idQty(800)}`]);
    expect(container.textContent).not.toContain(idQty(1200));
    expect(container.textContent).not.toContain(idQty(2000));
    expect(container.querySelector('[data-testid="mobile-print-empty"]')).toBeNull();
    expectDesignAvailable();
  });

  test("no allocation shows one message and hides numbers, status, progress, and form", () => {
    mockUser = USER_C;
    renderPanel(printOrder({
      splits: [
        split({ id: "sp-a", user: USER_A, lineIdx: 0, quantity: 1200, done: 400 }),
      ],
      printQtyTarget: 2000,
    }));

    const empty = container.querySelectorAll('[data-testid="mobile-print-empty"]');
    expect(empty).toHaveLength(1);
    expect(empty[0].textContent).toBe(EMPTY_MSG);
    expect(container.querySelectorAll(".mop-empty")).toHaveLength(1);
    expectNoResultForm();
    expectDesignAvailable();
  });

  test("no allocation with a locked gate still shows only the empty allocation message", () => {
    mockUser = USER_C;
    renderPanel(printOrder({
      splits: [split({ id: "sp-a", user: USER_A, lineIdx: 0, quantity: 1200, done: 0 })],
      workflowReady: { print_qty: false },
      gateReason: HELPER_REASON,
    }));

    expect(container.querySelectorAll('[data-testid="mobile-print-empty"]')).toHaveLength(1);
    expect(container.textContent).toContain(EMPTY_MSG);
    expectNoResultForm();
    expectDesignAvailable();
  });

  test("allocated print stays locked and shows the helper reason from the API", () => {
    renderPanel(printOrder({
      splits: [split({ id: "sp-a", user: USER_A, lineIdx: 0, quantity: 1200, done: 0 })],
      workflowReady: { print_qty: false },
      gateReason: HELPER_REASON,
    }));

    expect(metrics(container)["Target Cetak"]).toBe(idQty(1200));
    expect(container.querySelector('[data-testid="mobile-print-gate"]').textContent).toBe(HELPER_REASON);
    expect(container.textContent).not.toContain(OLD_FALLBACK);
    expect(container.textContent).not.toContain(EMPTY_MSG);
    expect(saveButtons(container)[0].disabled).toBe(true);
    expect(container.querySelector("input").disabled).toBe(true);
    expectDesignAvailable();
  });

  test("allocated print stays locked and shows the sample reason from the API", () => {
    renderPanel(printOrder({
      splits: [split({ id: "sp-a", user: USER_A, lineIdx: 0, quantity: 1200, done: 0 })],
      workflowReady: { print_qty: false },
      gateReason: SAMPLE_REASON,
    }));

    expect(container.querySelector('[data-testid="mobile-print-gate"]').textContent).toBe(SAMPLE_REASON);
    expect(container.textContent).not.toContain(HELPER_REASON);
    expect(container.textContent).not.toContain(OLD_FALLBACK);
    expect(saveButtons(container)[0].disabled).toBe(true);
    expect(container.querySelector("input").disabled).toBe(true);
    expectDesignAvailable();
  });

  test("locked print without a reason uses the neutral message and keeps input locked", () => {
    renderPanel(printOrder({
      splits: [split({ id: "sp-a", user: USER_A, lineIdx: 0, quantity: 1200, done: 0 })],
      workflowReady: { print_qty: false },
      gateReason: "",
    }));

    expect(container.querySelector('[data-testid="mobile-print-gate"]').textContent).toBe(NEUTRAL_MSG);
    expect(container.textContent).not.toContain(OLD_FALLBACK);
    expect(container.textContent).not.toContain(HELPER_REASON);
    expect(container.textContent).not.toContain(SAMPLE_REASON);
    expect(saveButtons(container)[0].disabled).toBe(true);
    expect(container.querySelector("input").disabled).toBe(true);
    expectDesignAvailable();
  });

  test("two items keep each allocation figure and do not use PO qty", () => {
    renderPanel(printOrder({
      printQtyTarget: 2500,
      lineItems: [
        { name: "Item A", quantity: 2000, item_id: "item-0", production_process: "print" },
        { name: "Item B", quantity: 500, item_id: "item-1", production_process: "print" },
      ],
      splits: [
        split({ id: "sp-a0", user: USER_A, lineIdx: 0, quantity: 1500, done: 100 }),
        split({ id: "sp-a1", user: USER_A, lineIdx: 1, quantity: 200, done: 0 }),
        split({ id: "sp-b0", user: USER_B, lineIdx: 0, quantity: 500, done: 0 }),
        split({ id: "sp-b1", user: USER_B, lineIdx: 1, quantity: 300, done: 50 }),
      ],
    }));

    expect(metrics(container)).toEqual({
      "Target Cetak": idQty(1700),
      "Sudah Cetak": idQty(100),
      Sisa: idQty(1600),
    });
    expect(lineFigures(container)).toEqual([
      `${idQty(100)} / ${idQty(1500)}`,
      `${idQty(0)} / ${idQty(200)}`,
    ]);
    expect(container.textContent).not.toContain(idQty(2500));
    expect(container.textContent).not.toContain(`${idQty(0)} / ${idQty(500)}`);
    expect(container.textContent).not.toContain(`${idQty(50)} / ${idQty(300)}`);
    expect(container.querySelector('[data-testid="mobile-print-empty"]')).toBeNull();
    expectDesignAvailable();
  });

  test("retry sends the same work date and action id", async () => {
    const order = printOrder({
      splits: [split({ id: "sp-a", user: USER_A, lineIdx: 0, quantity: 1200, done: 0 })],
    });
    api.updateOperatorProgress
      .mockRejectedValueOnce({ response: { data: { detail: "gagal jaringan" } } })
      .mockResolvedValueOnce({ order });
    renderPanel(order);
    const date = container.querySelector('[data-testid="mobile-print-date-0"]');
    const qty = container.querySelector('input[aria-label="Qty"]');
    const setValue = (el, value) => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(el, value);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    };
    await act(async () => {
      setValue(date, "2026-09-10");
      setValue(qty, "10");
    });
    const button = saveButtons(container)[0];
    await act(async () => { button.click(); });
    await act(async () => { button.click(); });
    expect(api.updateOperatorProgress).toHaveBeenCalledTimes(2);
    const first = api.updateOperatorProgress.mock.calls[0][3];
    const second = api.updateOperatorProgress.mock.calls[1][3];
    expect(first.work_date).toBe("2026-09-10");
    expect(second.work_date).toBe("2026-09-10");
    expect(second.action_id).toBe(first.action_id);
    expect(first.qty).toBe(10);
    expect(second.qty).toBe(10);
  });
});
