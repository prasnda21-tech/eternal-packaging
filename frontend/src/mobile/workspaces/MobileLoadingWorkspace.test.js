import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import api from "../../lib/api";
import MobileTaskModal from "../components/MobileTaskModal";
import MobileLoadingWorkspace from "./MobileLoadingWorkspace";
import { SJ_LINE_UNAVAILABLE_NAME } from "../../lib/loadingConfirm";

const mockHasPerm = jest.fn(() => true);
const mockSetParams = jest.fn();
let mockSearch = "";

jest.mock("../../lib/auth", () => ({
  useAuth: () => ({ hasPerm: (perm) => mockHasPerm(perm) }),
}));

jest.mock("react-router-dom", () => ({
  useSearchParams: () => [new URLSearchParams(mockSearch), mockSetParams],
}), { virtual: true });

jest.mock("../../lib/api", () => ({
  __esModule: true,
  default: {
    confirmLoadingMatch: jest.fn(),
    reportLoadingDiscrepancy: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const ITEM = "22 oz Datar 8 gr Berlian";
const sj1Id = "c992dfa4-8c67-496c-87e7-bf7eb22a0ea6";
const sj2Id = "ee1e3067-33f2-4623-8796-161c76db6690";
const sj3Id = "26c9e4f0-8616-4c06-ab68-c11c4bbca3c5";

function numaniOrder() {
  return {
    id: "po-numani",
    po_number: "PO-NUMANI",
    customer_name: "ES TEH NUMANI",
    total_quantity: 5000,
    line_items: [{ name: ITEM, quantity: 5000, line_id: "L0", production_process: "print" }],
    packing_info: { lines: [{ line_idx: 0, packed_qty: 5000 }] },
    shipments: [
      { id: sj1Id, number: "SJ/2026/09/12/000002", lines: [{ line_idx: 0, line_id: "L0", name: ITEM, qty: 1000, unit: "pcs" }] },
      { id: sj2Id, number: "SJ/2026/09/12/000002_2", lines: [{ line_idx: 0, line_id: "L0", name: ITEM, qty: 4000, unit: "pcs" }] },
      {
        id: sj3Id,
        number: "SJ/2026/09/12/000002_3",
        status: "superseded",
        cancelled: true,
        superseded_by_shipment_id: sj2Id,
        lines: [{ line_idx: 0, line_id: "L0", name: ITEM, qty: 1000 }],
      },
    ],
    surat_jalan_files: [
      { id: sj1Id, shipment_id: sj1Id, sj_number: "SJ/2026/09/12/000002", name: "1.pdf", lines: [{ line_idx: 0, line_id: "L0", name: ITEM, qty: 1000 }] },
      { id: sj2Id, shipment_id: sj2Id, sj_number: "SJ/2026/09/12/000002_2", name: "2.pdf", lines: [{ line_idx: 0, line_id: "L0", name: ITEM, qty: 4000 }] },
      { id: sj3Id, shipment_id: sj3Id, sj_number: "SJ/2026/09/12/000002_3", name: "3.pdf", status: "superseded", cancelled: true },
    ],
  };
}

function incompleteOrder(extra = {}) {
  return {
    id: "po-load-1",
    po_number: "PO-100",
    customer_name: "MATCHA IN",
    line_items: [{ name: "MATCHA IN", quantity: 4000, line_id: "L0", production_process: "print" }],
    packing_info: { lines: [{ line_idx: 0, packed_qty: 4000 }] },
    shipment_lines: [{ line_idx: 0, packed: 4000, loaded: 4000, remaining_loading: 0 }],
    shipments: [{
      id: "sh-1",
      number: "SJ-2000",
      lines: [{ line_idx: 0, line_id: "L0", name: "MATCHA IN", qty: 2000, unit: "pcs" }],
    }],
    surat_jalan_files: [{
      id: "sj-1",
      shipment_id: "sh-1",
      sj_number: "SJ-2000",
      name: "sj.pdf",
      lines: [{ line_idx: 0, line_id: "L0", name: "MATCHA IN", qty: 2000 }],
    }],
    ...extra,
  };
}

function completeOrder() {
  const order = incompleteOrder();
  return {
    ...order,
    loading_confirmations: [{ id: "c1", surat_jalan_id: "sj-1", lines: [{ line_idx: 0, qty: 2000 }] }],
  };
}

describe("Mobile Loading popup SJ lines", () => {
  let container;
  let root;
  const src = fs.readFileSync(path.join(__dirname, "MobileLoadingWorkspace.jsx"), "utf8");

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    mockHasPerm.mockImplementation(() => true);
    mockSearch = "";
    mockSetParams.mockReset();
    api.confirmLoadingMatch.mockReset();
    api.reportLoadingDiscrepancy.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function renderPopup(order, shipmentId) {
    const onClose = jest.fn();
    act(() => {
      root.render(
        <MobileTaskModal
          open
          onClose={onClose}
          title="Loading"
          roleId="loading"
          context={{
            customer: order.customer_name || order.customer,
            po: order.po_number,
            sj: order.surat_jalan_files?.find((f) => (f.shipment_id || f.id) === shipmentId)?.sj_number
              || order.shipments?.find((s) => s.id === shipmentId)?.number
              || "",
          }}
          showClose
          labelledBy="mobile-task-modal-title"
        >
          <MobileLoadingWorkspace
            order={order}
            canEdit
            shipmentId={shipmentId}
            onUpdated={() => {}}
            onRequestClose={onClose}
          />
        </MobileTaskModal>,
      );
    });
    return onClose;
  }

  test("source keeps existing confirm/discrepancy APIs and drops packed/loaded/remaining cards", () => {
    expect(src).toMatch(/confirmLoadingMatch/);
    expect(src).toMatch(/reportLoadingDiscrepancy/);
    expect(src).toMatch(/surat_jalan_id: activeSj\.id/);
    expect(src).toMatch(/selectedSjDisplayLines/);
    expect(src).not.toMatch(/Packed Qty/);
    expect(src).not.toMatch(/Loaded Qty/);
    expect(src).not.toMatch(/Remaining Qty/);
    expect(src).not.toMatch(/MobileMetricStrip/);
    expect(src).not.toMatch(/getOrder\(/);
  });

  test("8/13/16/17. incomplete Loading shows SJ qty, actions, and Buka Surat Jalan without UUID", () => {
    renderPopup(incompleteOrder(), "sh-1");
    expect(container.textContent).toContain("Barang Sesuai Surat Jalan");
    expect(container.querySelector('[data-testid="mobile-loading-sj-line-name-0"]').textContent).toBe("MATCHA IN");
    expect(container.querySelector('[data-testid="mobile-loading-sj-line-qty-0"]').textContent).toBe("2.000 pcs");
    expect(container.querySelector('[data-testid="mobile-loading-sj-total"]').textContent).toMatch(/2\.000 pcs/);
    expect(container.textContent).not.toMatch(/Packed Qty|Loaded Qty|Remaining Qty/);
    expect(container.textContent).not.toContain("4.000");
    expect(container.querySelector('[data-testid="mobile-loading-confirm-match"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-loading-open-discrepancy"]')).toBeTruthy();
    const openSj = [...container.querySelectorAll("a")].find((a) => a.textContent.includes("Buka Surat Jalan"));
    expect(openSj).toBeTruthy();
    expect(openSj.getAttribute("href")).toMatch(/surat-jalan\/files/);
    expect(container.querySelector('[data-testid="mobile-task-modal-context"]').textContent).not.toMatch(UUID_RE);
    expect(container.querySelector('[data-testid="mobile-loading-sj-lines"]').textContent).not.toMatch(UUID_RE);
  });

  test("15/18. completed Loading stays read-only with same Qty SJ and Tutup", () => {
    const onClose = renderPopup(completeOrder(), "sh-1");
    expect(container.querySelector('[data-testid="mobile-loading-status-value"]').textContent).toBe("Loading Selesai");
    expect(container.querySelector('[data-testid="mobile-loading-verify-value"]').textContent).toBe("Selesai");
    expect(container.querySelector('[data-testid="mobile-loading-sj-line-qty-0"]').textContent).toBe("2.000 pcs");
    expect(container.querySelector('[data-testid="mobile-loading-confirm-match"]')).toBeFalsy();
    expect(container.querySelector("input[type='number']")).toBeFalsy();
    const tutup = container.querySelector('[data-testid="mobile-task-modal-footer-close"]');
    expect(tutup).toBeTruthy();
    expect(tutup.textContent).toBe("Tutup");
    act(() => tutup.click());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test("multi-item SJ renders each line and totals selected qty", () => {
    const order = incompleteOrder({
      line_items: [
        { name: "Cup", quantity: 1000, line_id: "L0" },
        { name: "Lid", quantity: 500, line_id: "L1" },
      ],
      shipments: [{
        id: "sh-1",
        number: "SJ-MULTI",
        lines: [
          { line_idx: 0, line_id: "L0", name: "Cup", qty: 1000 },
          { line_idx: 1, line_id: "L1", name: "Lid", qty: 500 },
        ],
      }],
      surat_jalan_files: [{
        id: "sj-1",
        shipment_id: "sh-1",
        sj_number: "SJ-MULTI",
        lines: [
          { line_idx: 0, name: "Cup", qty: 1000 },
          { line_idx: 1, name: "Lid", qty: 500 },
        ],
      }],
    });
    renderPopup(order, "sh-1");
    expect(container.querySelector('[data-testid="mobile-loading-sj-line-name-0"]').textContent).toBe("Cup");
    expect(container.querySelector('[data-testid="mobile-loading-sj-line-name-1"]').textContent).toBe("Lid");
    expect(container.querySelector('[data-testid="mobile-loading-sj-line-qty-0"]').textContent).toBe("1.000 pcs");
    expect(container.querySelector('[data-testid="mobile-loading-sj-line-qty-1"]').textContent).toBe("500 pcs");
    expect(container.querySelector('[data-testid="mobile-loading-sj-total"]').textContent).toMatch(/1\.500 pcs/);
  });

  test("ES TEH NUMANI SJ1 vs SJ2 isolate qty and hide superseded SJ3", () => {
    const order = numaniOrder();
    renderPopup(order, sj1Id);
    expect(container.querySelector('[data-testid="mobile-loading-sj-line-name-0"]').textContent).toBe(ITEM);
    expect(container.querySelector('[data-testid="mobile-loading-sj-line-qty-0"]').textContent).toBe("1.000 pcs");
    expect(container.textContent).not.toContain("4.000");
    expect(container.textContent).not.toContain("5.000");
    expect(container.textContent).not.toMatch(UUID_RE);

    renderPopup(order, sj2Id);
    expect(container.querySelector('[data-testid="mobile-loading-sj-line-qty-0"]').textContent).toBe("4.000 pcs");
    expect(container.textContent).not.toContain("1.000");
    expect(container.textContent).not.toContain("5.000");
    expect(container.textContent).not.toContain("SJ/2026/09/12/000002_3");
  });

  test("legacy PO empty names show Item tidak tersedia", () => {
    const order = {
      id: "old-po",
      po_number: "PO-OLD",
      customer_name: "Legacy",
      line_items: [{ quantity: 1000 }],
      packing_info: { lines: [{ line_idx: 0, packed_qty: 1000 }] },
      surat_jalan_files: [{
        id: "old",
        shipment_id: "old",
        sj_number: "SJ-OLD",
        lines: [{ qty: 250 }],
      }],
    };
    expect(() => renderPopup(order, "old")).not.toThrow();
    expect(container.querySelector('[data-testid="mobile-loading-sj-line-name-0"]').textContent).toBe(SJ_LINE_UNAVAILABLE_NAME);
    expect(container.querySelector('[data-testid="mobile-loading-sj-line-qty-0"]').textContent).toBe("250 pcs");
  });

  test("Qty SJ is not an editable input", () => {
    renderPopup(incompleteOrder(), "sh-1");
    const qty = container.querySelector('[data-testid="mobile-loading-sj-line-qty-0"]');
    expect(qty.querySelector("input")).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-loading-sj-lines"] input')).toBeFalsy();
  });

  test("confirm match still posts once to the selected SJ", async () => {
    api.confirmLoadingMatch.mockResolvedValue({ order: completeOrder() });
    renderPopup(incompleteOrder(), "sh-1");
    await act(async () => {
      container.querySelector('[data-testid="mobile-loading-confirm-match"]').click();
    });
    expect(api.confirmLoadingMatch).toHaveBeenCalledTimes(1);
    expect(api.confirmLoadingMatch.mock.calls[0][1].surat_jalan_id).toBe("sj-1");
  });
});
