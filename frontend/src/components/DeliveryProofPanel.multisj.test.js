import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import DeliveryProofPanel from "./DeliveryProofPanel";
import api from "../lib/api";

jest.mock("../lib/api", () => ({
  __esModule: true,
  fileUrl: (storagePath) => `/api/files/${storagePath}`,
  default: {
    uploadDeliveryProof: jest.fn(),
    deleteDeliveryProof: jest.fn(),
    uploadDeliveryRecipientSignature: jest.fn(),
    uploadDeliveryDriverSignature: jest.fn(),
    confirmDelivered: jest.fn(),
    clearDeliveryRecipientSignature: jest.fn(),
    clearDeliveryDriverSignature: jest.fn(),
    warehouses: jest.fn(async () => ({ items: [] })),
    deliveryStockPreflight: jest.fn(async () => ({
      r2_authority_active: false,
      stock_warnings: [],
      has_shortage_warning: false,
    })),
  },
}));

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u-1", email: "driver@eternalpackaging.com", name: "Driver Test", role: "driver" },
    hasPerm: () => true,
  }),
}));

jest.mock("./SignaturePad", () => {
  const React = require("react");
  // Controllable deferred restore (simulates delayed image.onload).
  // When __deferSigRestore is true, "ready" waits until flushDeferredSigRestores().
  if (!global.__sigRestorePending) global.__sigRestorePending = [];
  if (typeof global.__deferSigRestore !== "boolean") global.__deferSigRestore = false;
  global.flushDeferredSigRestores = () => {
    const q = global.__sigRestorePending.splice(0, global.__sigRestorePending.length);
    q.forEach((fn) => fn());
  };
  return {
    __esModule: true,
    default: ({ onChange, value, onRestoreState }) => {
      const restoreRef = React.useRef(onRestoreState);
      restoreRef.current = onRestoreState;
      React.useEffect(() => {
        let cancelled = false;
        if (value && String(value).startsWith("data:image/")) {
          restoreRef.current?.("restoring");
          const complete = () => {
            if (!cancelled) restoreRef.current?.("ready");
          };
          if (global.__deferSigRestore) {
            global.__sigRestorePending.push(complete);
          } else {
            Promise.resolve().then(complete);
          }
        } else {
          restoreRef.current?.(null);
        }
        return () => { cancelled = true; };
      }, [value]);
      return (
        <div
          data-testid="signature-pad-mock"
          data-restore-value={value ? "yes" : "no"}
        >
          <button
            type="button"
            data-testid="signature-pad-mock-sign"
            onClick={() => onChange?.(value || "data:image/png;base64,c2ln")}
          >
            sign
          </button>
          <button
            type="button"
            data-testid="signature-pad-mock-fail-restore"
            onClick={() => {
              onRestoreState?.("failed");
              onChange?.(null);
            }}
          >
            fail-restore
          </button>
          <span data-testid="signature-pad-mock-value">{value || ""}</span>
        </div>
      );
    },
  };
});

jest.mock("../lib/imageCompress", () => ({
  __esModule: true,
  compressImage: (file) => Promise.resolve({ file, compressed: false, sizeKB: 1, originalSizeKB: 1 }),
}));

jest.mock("./RescheduleDeliveryModal", () => ({
  __esModule: true,
  default: () => null,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

function jpegFile(name = "bukti.jpg") {
  return new File([new Uint8Array([0xff, 0xd8, 0xff])], name, { type: "image/jpeg" });
}

function setInputFiles(input, files) {
  Object.defineProperty(input, "files", { configurable: true, value: files });
  act(() => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function proof(id, shipmentId, storagePath = `delivery/${id}.jpg`) {
  return {
    id,
    shipment_id: shipmentId,
    storage_path: storagePath,
    original_filename: `${id}.jpg`,
    uploaded_by: "driver@eternalpackaging.com",
    uploaded_by_name: "Driver Test",
    uploaded_by_role: "driver",
    uploaded_at: "2026-09-16T00:00:00Z",
    note: "",
  };
}

function demoMultiSjPhotoOrder(overrides = {}) {
  const sj1 = {
    id: "demo-multisj-photo-001",
    number: "DEMO-MULTISJ-PHOTO-001",
    delivery_date: "2026-09-16",
    lines: [{ line_idx: 0, line_id: "LA", name: "Cup A", qty: 1000 }],
    total_qty: 1000,
  };
  const sj2 = {
    id: "demo-multisj-photo-002",
    number: "DEMO-MULTISJ-PHOTO-002",
    delivery_date: "2026-09-16",
    lines: [{ line_idx: 1, line_id: "LB", name: "Cup B", qty: 800 }],
    total_qty: 800,
  };
  const sj3 = {
    id: "demo-multisj-photo-003",
    number: "DEMO-MULTISJ-PHOTO-003",
    delivery_date: "2026-09-16",
    lines: [{ line_idx: 2, line_id: "LC", name: "Lid C", qty: 600 }],
    total_qty: 600,
  };
  return {
    id: "order-demo-multisj-photo",
    po_number: "DEMO-MULTISJ-PHOTO-PO",
    customer: "PT Multi SJ Photo",
    delivery_status: "Belum Terkirim",
    terkirim_confirmed: false,
    line_items: [
      { name: "Cup A", quantity: 1000, line_id: "LA", production_process: "print" },
      { name: "Cup B", quantity: 800, line_id: "LB", production_process: "print" },
      { name: "Lid C", quantity: 600, line_id: "LC", production_process: "non_print" },
    ],
    packing_info: {
      qty_packed: 2400,
      lines: [
        { line_idx: 0, packed_qty: 1000 },
        { line_idx: 1, packed_qty: 800 },
        { line_idx: 2, packed_qty: 600 },
      ],
    },
    shipments: [sj1, sj2, sj3],
    loading_confirmations: [
      { id: "c1", surat_jalan_id: "demo-multisj-photo-001", lines: [{ line_idx: 0, qty: 1000 }] },
      { id: "c2", surat_jalan_id: "demo-multisj-photo-002", lines: [{ line_idx: 1, qty: 800 }] },
      { id: "c3", surat_jalan_id: "demo-multisj-photo-003", lines: [{ line_idx: 2, qty: 600 }] },
    ],
    delivery_proofs: [],
    delivery_confirmations: [],
    ...overrides,
  };
}

function singleSjOrder(overrides = {}) {
  return {
    id: "order-single",
    po_number: "PO-SINGLE",
    customer: "PT Single",
    delivery_status: "Belum Terkirim",
    line_items: [{ name: "Cup", quantity: 10, line_id: "l0", production_process: "print" }],
    packing_info: { qty_packed: 10, lines: [{ line_idx: 0, packed_qty: 10 }] },
    shipments: [{ id: "s1", number: "SJ-1", lines: [{ line_idx: 0, qty: 10 }], total_qty: 10 }],
    loading_confirmations: [{ id: "c1", surat_jalan_id: "s1", lines: [{ line_idx: 0, qty: 10 }] }],
    delivery_proofs: [],
    ...overrides,
  };
}

function mount(ui) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(ui);
  });
  return {
    container,
    rerender(next) {
      act(() => {
        root.render(next);
      });
    },
    unmount() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
  });
}

function setSearch(search) {
  window.history.replaceState({}, "", `/po/order-demo-multisj-photo${search || ""}`);
}

function selectSj(container, shipmentId) {
  const select = container.querySelector('[data-testid="pengiriman-sj-select"]');
  act(() => {
    select.value = shipmentId;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

describe("DeliveryProofPanel multi-SJ desktop exact identity", () => {
  beforeAll(() => {
    if (typeof global.crypto === "undefined") {
      global.crypto = {};
    }
    if (typeof global.crypto.randomUUID !== "function") {
      global.crypto.randomUUID = () => `id-${Date.now()}-${Math.random()}`;
    }
    if (!URL.createObjectURL) URL.createObjectURL = () => "blob:mock";
    if (!URL.revokeObjectURL) URL.revokeObjectURL = () => {};
  });

  beforeEach(() => {
    jest.clearAllMocks();
    global.__deferSigRestore = false;
    global.__sigRestorePending = [];
    URL.createObjectURL = jest.fn((blob) => `blob:mock-${blob?.size || 0}-${Math.random()}`);
    URL.revokeObjectURL = jest.fn();
    global.fetch = jest.fn(() => Promise.resolve({
      ok: true,
      blob: () => Promise.resolve(new Blob(["img"], { type: "image/jpeg" })),
    }));
    setSearch("");
  });

  afterEach(() => {
    global.__deferSigRestore = false;
    global.__sigRestorePending = [];
    setSearch("");
  });

  test("source: no first-SJ fallback and no PO-level mutation lock", () => {
    const src = fs.readFileSync(path.join(__dirname, "DeliveryProofPanel.jsx"), "utf8");
    expect(src).not.toMatch(/sjOptions\[0\]/);
    expect(src).not.toMatch(/poDelivered/);
    expect(src).toMatch(/isPengirimanMutationLocked/);
    expect(src).toMatch(/proofsForShipment/);
    expect(src).toMatch(/selectedPengirimanShipmentId\(order, urlShipmentId\(\)\)/);
    expect(src).toMatch(/Tidak ada batas maksimum jumlah foto/);
    expect(src).not.toMatch(/MAX_PHOTOS/);
    expect(src).toMatch(/onSelectSj/);
    expect(src).toMatch(/selectionKey/);
  });

  test("URL shipment=002 selects SJ 002 directly without mounting SJ 001", () => {
    setSearch("?shipment=demo-multisj-photo-002");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={demoMultiSjPhotoOrder()} onUpdated={() => {}} />
    );
    const select = container.querySelector('[data-testid="pengiriman-sj-select"]');
    expect(select.value).toBe("demo-multisj-photo-002");
    expect(container.querySelector('[data-testid="pengiriman-selected-sj-id"]').getAttribute("data-shipment-id"))
      .toBe("demo-multisj-photo-002");
    expect(container.querySelector('[data-testid="pengiriman-selected-sj-id"]').textContent)
      .not.toContain("demo-multisj-photo-002");
    expect(container.querySelector('[data-testid="pengiriman-selected-sj-number"]').textContent)
      .toContain("DEMO-MULTISJ-PHOTO-002");
    expect(container.querySelector('[data-testid="pengiriman-sj-required"]')).toBeFalsy();
    unmount();
  });

  test("URL shipment=003 selects SJ 003 directly", () => {
    setSearch("?shipment=demo-multisj-photo-003");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={demoMultiSjPhotoOrder()} onUpdated={() => {}} />
    );
    expect(container.querySelector('[data-testid="pengiriman-sj-select"]').value)
      .toBe("demo-multisj-photo-003");
    expect(container.querySelector('[data-testid="pengiriman-selected-sj-id"]').getAttribute("data-shipment-id"))
      .toBe("demo-multisj-photo-003");
    expect(container.querySelector('[data-testid="pengiriman-selected-sj-id"]').textContent)
      .not.toContain("demo-multisj-photo-003");
    unmount();
  });

  test("operational dropdown and card hide shipment UUID while select value stays bound", () => {
    const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
    const sj1Id = "c992dfa4-8c67-496c-87e7-bf7eb22a0ea6";
    const sj2Id = "ee1e3067-33f2-4623-8796-161c76db6690";
    const order = {
      id: "258b4eac-d91a-4623-b206-d38c2c2e4d2f",
      po_number: "PO/2026/09/12/000002",
      customer: "ES TEH NUMANI",
      delivery_status: "Sebagian Terkirim",
      line_items: [{ name: "22 oz Datar 8 gr Berlian", quantity: 5000, line_id: "L0", production_process: "print" }],
      shipments: [
        { id: sj1Id, number: "SJ/2026/09/12/000002", delivery_date: "2026-09-16", lines: [{ line_idx: 0, line_id: "L0", name: "22 oz Datar 8 gr Berlian", qty: 1000 }], total_qty: 1000 },
        { id: sj2Id, number: "SJ/2026/09/12/000002_2", delivery_date: "2026-09-16", delivered_at: "t", delivery_status: "Terkirim", lines: [{ line_idx: 0, line_id: "L0", name: "22 oz Datar 8 gr Berlian", qty: 4000 }], total_qty: 4000 },
      ],
      loading_confirmations: [
        { id: "c1", surat_jalan_id: sj1Id, lines: [{ line_idx: 0, qty: 1000 }] },
        { id: "c2", surat_jalan_id: sj2Id, lines: [{ line_idx: 0, qty: 4000 }] },
      ],
      delivery_confirmations: [{ id: "d2", shipment_id: sj2Id, qty: 4000 }],
      delivery_proofs: [],
    };
    setSearch(`?shipment=${sj1Id}`);
    const { container, unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    const select = container.querySelector('[data-testid="pengiriman-sj-select"]');
    expect(select.value).toBe(sj1Id);
    const optionTexts = [...select.options].map((o) => o.textContent);
    expect(optionTexts.some((t) => t.includes("SJ/2026/09/12/000002"))).toBe(true);
    expect(optionTexts.every((t) => !UUID_RE.test(t))).toBe(true);
    const card = container.querySelector('[data-testid="pengiriman-selected-sj-card"]');
    expect(UUID_RE.test(card.textContent)).toBe(false);
    expect(container.querySelector('[data-testid="pengiriman-selected-sj-number"]').textContent)
      .toContain("SJ/2026/09/12/000002");
    expect(container.querySelector('[data-testid="pengiriman-selected-sj-id"]').getAttribute("data-shipment-id"))
      .toBe(sj1Id);
    expect(container.querySelector('[data-testid="pengiriman-selected-sj-qty"]').textContent).toMatch(/1\.000/);
    expect(container.querySelector('[data-testid="pengiriman-selected-ready-status"]').textContent)
      .toMatch(/Siap dikirim/i);
    unmount();
  });

  test("unknown shipment query fails closed and does not fall back to SJ 001", () => {
    setSearch("?shipment=unknown-sj");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={demoMultiSjPhotoOrder()} onUpdated={() => {}} />
    );
    expect(container.querySelector('[data-testid="pengiriman-sj-unmatched"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="pengiriman-sj-select"]').value).toBe("");
    expect(container.querySelector('[data-testid="pengiriman-selected-sj-card"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-camera-button"]').disabled).toBe(true);
    expect(container.querySelector('[data-testid="proof-gallery-button"]').disabled).toBe(true);
    unmount();
  });

  test("multi-SJ with no shipment query does not auto-select sjOptions[0]", () => {
    setSearch("");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={demoMultiSjPhotoOrder()} onUpdated={() => {}} />
    );
    expect(container.querySelector('[data-testid="pengiriman-sj-select"]').value).toBe("");
    expect(container.querySelector('[data-testid="pengiriman-sj-required"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="pengiriman-selected-sj-card"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-gallery-button"]').disabled).toBe(true);
    unmount();
  });

  test("history photos load without selecting a sibling SJ", async () => {
    const order = demoMultiSjPhotoOrder({
      delivery_proofs: [
        proof("history-a", "demo-multisj-photo-001"),
        proof("history-b", "demo-multisj-photo-002"),
      ],
      delivery_history: [
        { id: "h1", qty: 1000, proof_ids: ["history-a"] },
        { id: "h2", qty: 800, proof_ids: ["history-b"] },
      ],
    });
    setSearch("");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    await flush();
    expect(container.querySelector('[data-testid="pengiriman-sj-select"]').value).toBe("");
    expect(global.fetch).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[data-testid="delivery-history-photo-0-history-a"] img')).toBeTruthy();
    expect(container.querySelector('[data-testid="delivery-history-photo-1-history-b"] img')).toBeTruthy();
    unmount();
  });

  test("failed history photo stops spinning and reports unavailable", async () => {
    global.fetch = jest.fn(() => Promise.resolve({ ok: false }));
    const order = demoMultiSjPhotoOrder({
      delivery_proofs: [proof("history-failed", "demo-multisj-photo-001")],
      delivery_history: [{ id: "h1", qty: 1000, proof_ids: ["history-failed"] }],
    });
    const { container, unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    await flush();
    const button = container.querySelector('[data-testid="delivery-history-photo-0-history-failed"]');
    expect(button.disabled).toBe(true);
    expect(button.textContent).toMatch(/Foto tidak tersedia/i);
    expect(button.querySelector(".spin")).toBeFalsy();
    unmount();
  });

  test("history proof id without file metadata reports unavailable instead of spinning forever", async () => {
    const order = demoMultiSjPhotoOrder({
      delivery_proofs: [],
      delivery_history: [{ id: "h1", qty: 1000, proof_ids: ["history-missing"] }],
    });
    const { container, unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    await flush();
    const button = container.querySelector('[data-testid="delivery-history-photo-0-history-missing"]');
    expect(global.fetch).not.toHaveBeenCalled();
    expect(button.disabled).toBe(true);
    expect(button.textContent).toMatch(/Foto tidak tersedia/i);
    expect(button.querySelector(".spin")).toBeFalsy();
    unmount();
  });

  test("delivered SJ 001 / PO Terkirim does not disable upload on SJ 002 or SJ 003", () => {
    const order = demoMultiSjPhotoOrder({
      delivery_status: "Terkirim",
      terkirim_confirmed: true,
      shipments: [
        {
          id: "demo-multisj-photo-001",
          number: "DEMO-MULTISJ-PHOTO-001",
          delivered_at: "2026-09-16T01:00:00Z",
          delivery_status: "Terkirim",
          lines: [{ line_idx: 0, line_id: "LA", name: "Cup A", qty: 1000 }],
          total_qty: 1000,
        },
        {
          id: "demo-multisj-photo-002",
          number: "DEMO-MULTISJ-PHOTO-002",
          lines: [{ line_idx: 1, line_id: "LB", name: "Cup B", qty: 800 }],
          total_qty: 800,
        },
        {
          id: "demo-multisj-photo-003",
          number: "DEMO-MULTISJ-PHOTO-003",
          lines: [{ line_idx: 2, line_id: "LC", name: "Lid C", qty: 600 }],
          total_qty: 600,
        },
      ],
    });
    setSearch("?shipment=demo-multisj-photo-002");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    expect(container.querySelector('[data-testid="pengiriman-sj-select"]').value)
      .toBe("demo-multisj-photo-002");
    expect(container.querySelector('[data-testid="proof-gallery-button"]').disabled).toBe(false);
    expect(container.querySelector('[data-testid="proof-camera-button"]').disabled).toBe(false);
    selectSj(container, "demo-multisj-photo-003");
    expect(container.querySelector('[data-testid="pengiriman-sj-select"]').value)
      .toBe("demo-multisj-photo-003");
    expect(container.querySelector('[data-testid="proof-gallery-button"]').disabled).toBe(false);
    selectSj(container, "demo-multisj-photo-001");
    expect(container.querySelector('[data-testid="proof-gallery-button"]').disabled).toBe(true);
    unmount();
  });

  test("proofs stay exact-SJ scoped; untagged multi-SJ legacy proof is blocked", async () => {
    const order = demoMultiSjPhotoOrder({
      delivery_proofs: [
        proof("p001", "demo-multisj-photo-001"),
        proof("p002", "demo-multisj-photo-002"),
        proof("p003", "demo-multisj-photo-003"),
        { id: "legacy-untagged", original_filename: "old.jpg", storage_path: "delivery/old.jpg" },
      ],
    });
    setSearch("?shipment=demo-multisj-photo-001");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    await flush();
    expect(container.querySelector('[data-testid="proof-p001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="proof-p002"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-p003"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-legacy-untagged"]')).toBeFalsy();

    selectSj(container, "demo-multisj-photo-002");
    await flush();
    expect(container.querySelector('[data-testid="proof-p002"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="proof-p001"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-legacy-untagged"]')).toBeFalsy();

    selectSj(container, "demo-multisj-photo-003");
    await flush();
    expect(container.querySelector('[data-testid="proof-p003"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="proof-p001"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-p002"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-legacy-untagged"]')).toBeFalsy();
    unmount();
  });

  test("switching 001→002 and 002→003 clears staged files, proof blobs, and signature/confirm state", async () => {
    const order = demoMultiSjPhotoOrder({
      delivery_proofs: [
        proof("p001a", "demo-multisj-photo-001"),
        proof("p001b", "demo-multisj-photo-001"),
        proof("p002a", "demo-multisj-photo-002"),
      ],
    });
    setSearch("?shipment=demo-multisj-photo-001");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    await flush();
    expect(container.querySelector('[data-testid="proof-p001a"]')).toBeTruthy();

    expect(container.querySelectorAll('[data-testid="signature-pad-mock"]').length).toBe(2);
    signBothPads(container);
    const name = container.querySelector('[data-testid="recipient-name-input"]');
    act(() => {
      const setValue = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setValue.call(name, "Budi Penerima");
      name.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="recipient-name-input"]').value).toBe("Budi Penerima");

    setInputFiles(container.querySelector('[data-testid="proof-file-input"]'), [jpegFile("sj001.jpg")]);
    await flush();
    expect(container.querySelector('[data-testid="staged-block"]')).toBeTruthy();
    const blobCountAfter001 = URL.createObjectURL.mock.calls.length;
    expect(blobCountAfter001).toBeGreaterThan(0);

    selectSj(container, "demo-multisj-photo-002");
    await flush();
    expect(container.querySelector('[data-testid="pengiriman-sj-select"]').value)
      .toBe("demo-multisj-photo-002");
    expect(window.location.search).toContain("shipment=demo-multisj-photo-002");
    expect(container.querySelector('[data-testid="staged-block"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-p001a"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-p001b"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-p002a"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="confirm-action-modal"]')).toBeFalsy();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="recipient-name-input"]')).toBeFalsy();

    setInputFiles(container.querySelector('[data-testid="proof-file-input"]'), [jpegFile("sj002.jpg")]);
    await flush();
    expect(container.querySelector('[data-testid="staged-block"]')).toBeTruthy();

    selectSj(container, "demo-multisj-photo-003");
    await flush();
    expect(container.querySelector('[data-testid="pengiriman-sj-select"]').value)
      .toBe("demo-multisj-photo-003");
    expect(container.querySelector('[data-testid="staged-block"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-p002a"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-p001a"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-gallery-button"]').disabled).toBe(false);
    unmount();
  });

  test("selected-SJ with 6+ photos still allows more uploads and isolates siblings", () => {
    const order = demoMultiSjPhotoOrder({
      delivery_proofs: [
        proof("a1", "demo-multisj-photo-001"),
        proof("a2", "demo-multisj-photo-001"),
        proof("a3", "demo-multisj-photo-001"),
        proof("a4", "demo-multisj-photo-001"),
        proof("a5", "demo-multisj-photo-001"),
        proof("a6", "demo-multisj-photo-001"),
        proof("b1", "demo-multisj-photo-002"),
        proof("b2", "demo-multisj-photo-002"),
      ],
    });
    setSearch("?shipment=demo-multisj-photo-001");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    expect(container.querySelector('[data-testid="proof-gallery-button"]').disabled).toBe(false);
    expect(container.querySelector('[data-testid="proof-stored-count"]').textContent)
      .toContain("6 foto tersimpan");
    expect(container.querySelector('[data-testid="proof-photo-limit-hint"]').textContent)
      .toContain("Tidak ada batas maksimum jumlah foto");
    expect(container.querySelector('[data-testid="proof-photo-limit-hint"]').textContent)
      .not.toContain("Slot penuh");
    expect(container.querySelectorAll(".proof-card").length).toBe(6);

    selectSj(container, "demo-multisj-photo-002");
    expect(container.querySelector('[data-testid="proof-gallery-button"]').disabled).toBe(false);
    expect(container.querySelector('[data-testid="proof-stored-count"]').textContent)
      .toContain("2 foto tersimpan");
    expect(container.querySelector('[data-testid="proof-p-a1"]') || container.querySelector('[data-testid="proof-a1"]'))
      .toBeFalsy();
    expect(container.querySelector('[data-testid="proof-b1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="proof-b2"]')).toBeTruthy();
    expect(container.querySelectorAll(".proof-card").length).toBe(2);

    selectSj(container, "demo-multisj-photo-003");
    expect(container.querySelector('[data-testid="proof-gallery-button"]').disabled).toBe(false);
    expect(container.querySelector('[data-testid="proof-stored-count"]').textContent)
      .toContain("0 foto tersimpan");
    expect(container.querySelectorAll(".proof-card").length).toBe(0);
    unmount();
  });

  test("single-SJ flow remains convenient without a max-photo cap", async () => {
    setSearch("");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={singleSjOrder()} onUpdated={() => {}} />
    );
    await flush();
    expect(container.querySelector('[data-testid="pengiriman-sj-select"]').value).toBe("s1");
    expect(container.querySelector('[data-testid="pengiriman-sj-required"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="pengiriman-sj-unmatched"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="proof-gallery-button"]').disabled).toBe(false);
    expect(container.querySelector('[data-testid="proof-photo-limit-hint"]').textContent)
      .toContain("Tidak ada batas maksimum jumlah foto");
    expect(container.querySelector('[data-testid="proof-photo-limit-hint"]').textContent)
      .not.toContain("Batas 5 foto");
    setInputFiles(container.querySelector('[data-testid="proof-file-input"]'), [jpegFile("one.jpg")]);
    await flush();
    expect(container.querySelector('[data-testid="staged-block"]')).toBeTruthy();
    expect(api.uploadDeliveryProof).not.toHaveBeenCalled();
    unmount();
  });

  function typeRecipientName(container, value) {
    const name = container.querySelector('[data-testid="recipient-name-input"]');
    expect(name).toBeTruthy();
    act(() => {
      const setValue = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setValue.call(name, value);
      name.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }

  function signBothPads(container) {
    const signs = container.querySelectorAll('[data-testid="signature-pad-mock-sign"]');
    expect(signs.length).toBeGreaterThanOrEqual(2);
    act(() => {
      signs[0].dispatchEvent(new MouseEvent("click", { bubbles: true }));
      signs[1].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  }

  function padValues(container) {
    return [...container.querySelectorAll('[data-testid="signature-pad-mock-value"]')]
      .map((n) => n.textContent || "");
  }

  test("recipient name draft does not leak across SJ and restores per SJ id", async () => {
    const order = demoMultiSjPhotoOrder({
      delivery_proofs: [
        proof("p001a", "demo-multisj-photo-001"),
        proof("p001b", "demo-multisj-photo-001"),
        proof("p002a", "demo-multisj-photo-002"),
        proof("p002b", "demo-multisj-photo-002"),
      ],
      // Sibling POD for SJ-001 must never prefill SJ-002.
      delivery_recipient_signature: {
        id: "sig-001",
        shipment_id: "demo-multisj-photo-001",
        recipient_name: "POD Tersimpan A",
        signature_storage_path: "sig/a.png",
        recipient_signed_at: "2026-09-16T00:00:00Z",
      },
    });
    setSearch("?shipment=demo-multisj-photo-001");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    await flush();
    expect(container.querySelector('[data-testid="recipient-name-input"]').value)
      .toBe("POD Tersimpan A");

    typeRecipientName(container, "Draft Nama A");
    expect(container.querySelector('[data-testid="recipient-name-input"]').value).toBe("Draft Nama A");

    selectSj(container, "demo-multisj-photo-002");
    await flush();
    expect(container.querySelector('[data-testid="pengiriman-sj-select"]').value)
      .toBe("demo-multisj-photo-002");
    // SJ-002 has proofs so name field is visible, but must start empty (no sibling POD/draft).
    expect(container.querySelector('[data-testid="recipient-name-input"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="recipient-name-input"]').value).toBe("");
    expect(container.querySelector('[data-testid="confirm-delivered-hint"]')?.textContent || "")
      .toMatch(/Nama penerima|Tanda tangan/i);

    typeRecipientName(container, "Draft Nama B");
    selectSj(container, "demo-multisj-photo-001");
    await flush();
    expect(container.querySelector('[data-testid="recipient-name-input"]').value).toBe("Draft Nama A");

    selectSj(container, "demo-multisj-photo-002");
    await flush();
    expect(container.querySelector('[data-testid="recipient-name-input"]').value).toBe("Draft Nama B");

    selectSj(container, "demo-multisj-photo-003");
    await flush();
    // SJ-003 has 0 proofs → name input gated off; still must not carry B's draft into UI state
    // when returning after visiting empty SJ.
    expect(container.querySelector('[data-testid="recipient-name-input"]')).toBeFalsy();
    selectSj(container, "demo-multisj-photo-002");
    await flush();
    expect(container.querySelector('[data-testid="recipient-name-input"]').value).toBe("Draft Nama B");
    unmount();
  });

  test("confirm modal cancel does not submit and keeps active SJ name draft", async () => {
    api.confirmDelivered.mockResolvedValue({ order: demoMultiSjPhotoOrder() });
    const order = demoMultiSjPhotoOrder({
      delivery_proofs: [
        proof("p001a", "demo-multisj-photo-001"),
        proof("p001b", "demo-multisj-photo-001"),
      ],
    });
    setSearch("?shipment=demo-multisj-photo-001");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    await flush();
    typeRecipientName(container, "Penerima Cancel Test");
    signBothPads(container);
    await flush();
    const btn = container.querySelector('[data-testid="confirm-delivered-button"]');
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(false);
    await act(async () => { btn.click(); });
    await flush();
    expect(container.querySelector('[data-testid="confirm-action-modal"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="confirm-recipient-name"]').textContent)
      .toContain("Penerima Cancel Test");
    await act(async () => {
      container.querySelector('[data-testid="confirm-cancel"]').click();
    });
    await flush();
    expect(container.querySelector('[data-testid="confirm-action-modal"]')).toBeFalsy();
    expect(api.confirmDelivered).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="recipient-name-input"]').value)
      .toBe("Penerima Cancel Test");
    unmount();
  });

  test("confirm payload locks active shipment_id and recipient name snapshot", async () => {
    const base = demoMultiSjPhotoOrder({
      delivery_proofs: [
        proof("p001a", "demo-multisj-photo-001"),
        proof("p001b", "demo-multisj-photo-001"),
        proof("p002a", "demo-multisj-photo-002"),
        proof("p002b", "demo-multisj-photo-002"),
      ],
    });
    api.uploadDeliveryRecipientSignature.mockResolvedValue({ order: base });
    api.uploadDeliveryDriverSignature.mockResolvedValue({ order: base });
    api.confirmDelivered.mockResolvedValue({
      order: {
        ...base,
        delivery_confirmations: [{ id: "dc1", shipment_id: "demo-multisj-photo-001", recipient_name: "Nama A" }],
      },
    });
    setSearch("?shipment=demo-multisj-photo-001");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={base} onUpdated={() => {}} />
    );
    await flush();
    typeRecipientName(container, "Nama A");
    signBothPads(container);
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="confirm-delivered-button"]').click();
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="confirm-ok"]').click();
    });
    await flush();
    expect(api.confirmDelivered).toHaveBeenCalledTimes(1);
    const [, payload] = api.confirmDelivered.mock.calls[0];
    expect(payload.shipment_id).toBe("demo-multisj-photo-001");
    expect(payload.recipient_name).toBe("Nama A");
    expect(api.uploadDeliveryRecipientSignature).toHaveBeenCalledWith(
      base.id,
      expect.objectContaining({ shipmentId: "demo-multisj-photo-001", recipientName: "Nama A" }),
    );
    unmount();
  });

  test("TTD draft restores onto pad for active SJ only; clear empties draft and re-gates", async () => {
    const order = demoMultiSjPhotoOrder({
      delivery_proofs: [
        proof("p001a", "demo-multisj-photo-001"),
        proof("p001b", "demo-multisj-photo-001"),
        proof("p002a", "demo-multisj-photo-002"),
        proof("p002b", "demo-multisj-photo-002"),
      ],
    });
    setSearch("?shipment=demo-multisj-photo-001");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    await flush();
    typeRecipientName(container, "Nama A");
    signBothPads(container);
    await flush();
    const signedA = padValues(container);
    expect(signedA[0]).toMatch(/^data:image\//);
    expect(signedA[1]).toMatch(/^data:image\//);
    expect(container.querySelector('[data-testid="confirm-delivered-button"]').disabled).toBe(false);

    selectSj(container, "demo-multisj-photo-002");
    await flush();
    // B must not show A's draft value on pads.
    expect(padValues(container).every((v) => !v)).toBe(true);
    expect(container.querySelector('[data-testid="confirm-delivered-button"]').disabled).toBe(true);

    selectSj(container, "demo-multisj-photo-001");
    await flush();
    // Returning to A restores draft into pad value (visible restore path).
    const restored = padValues(container);
    expect(restored[0]).toBe(signedA[0]);
    expect(restored[1]).toBe(signedA[1]);
    expect(container.querySelector('[data-testid="confirm-delivered-button"]').disabled).toBe(false);

    await act(async () => {
      container.querySelector('[data-testid="recipient-signature-clear"]').click();
    });
    await flush();
    expect(padValues(container)[0]).toBe("");
    expect(container.querySelector('[data-testid="confirm-delivered-button"]').disabled).toBe(true);

    // Draft for A recipient TTD cleared — switching away and back stays empty for recipient pad.
    selectSj(container, "demo-multisj-photo-002");
    await flush();
    selectSj(container, "demo-multisj-photo-001");
    await flush();
    expect(padValues(container)[0]).toBe("");
    expect(padValues(container)[1]).toBe(signedA[1]); // driver draft still isolated/restored
    unmount();
  });

  test("failed TTD restore clears hidden draft and keeps confirm gated", async () => {
    const order = demoMultiSjPhotoOrder({
      delivery_proofs: [
        proof("p001a", "demo-multisj-photo-001"),
        proof("p001b", "demo-multisj-photo-001"),
        proof("p002a", "demo-multisj-photo-002"),
        proof("p002b", "demo-multisj-photo-002"),
      ],
    });
    setSearch("?shipment=demo-multisj-photo-001");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    await flush();
    typeRecipientName(container, "Nama A");
    signBothPads(container);
    await flush();
    selectSj(container, "demo-multisj-photo-002");
    await flush();
    selectSj(container, "demo-multisj-photo-001");
    await flush();
    expect(container.querySelector('[data-testid="confirm-delivered-button"]').disabled).toBe(false);

    await act(async () => {
      container.querySelectorAll('[data-testid="signature-pad-mock-fail-restore"]')[0]
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(padValues(container)[0]).toBe("");
    expect(container.querySelector('[data-testid="confirm-delivered-button"]').disabled).toBe(true);
    unmount();
  });

  test("deferred restore (delayed onload): restoring does not satisfy confirm gate until ready", async () => {
    global.__deferSigRestore = true;
    const order = demoMultiSjPhotoOrder({
      delivery_proofs: [
        proof("p001a", "demo-multisj-photo-001"),
        proof("p001b", "demo-multisj-photo-001"),
        proof("p002a", "demo-multisj-photo-002"),
        proof("p002b", "demo-multisj-photo-002"),
      ],
    });
    setSearch("?shipment=demo-multisj-photo-001");
    const { container, unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    await flush();
    typeRecipientName(container, "Nama A");
    // Sign via onChange path — ready without restore.
    global.__deferSigRestore = false;
    signBothPads(container);
    await flush();
    expect(container.querySelector('[data-testid="confirm-delivered-button"]').disabled).toBe(false);

    // Change value by leaving and returning — draft restore starts with deferred onload.
    global.__deferSigRestore = true;
    selectSj(container, "demo-multisj-photo-002");
    await flush();
    expect(container.querySelector('[data-testid="confirm-delivered-button"]').disabled).toBe(true);

    selectSj(container, "demo-multisj-photo-001");
    await flush();
    // Draft value is present but restore still pending → must NOT enable confirm.
    expect(padValues(container)[0]).toMatch(/^data:image\//);
    expect(padValues(container)[1]).toMatch(/^data:image\//);
    expect(global.__sigRestorePending.length).toBeGreaterThanOrEqual(2);
    expect(container.querySelector('[data-testid="confirm-delivered-button"]').disabled).toBe(true);
    expect(container.querySelector('[data-testid="confirm-delivered-hint"]')?.textContent || "")
      .toMatch(/Tanda tangan/i);

    await act(async () => {
      global.flushDeferredSigRestores();
    });
    await flush();
    expect(container.querySelector('[data-testid="confirm-delivered-button"]').disabled).toBe(false);

    // Failed restore while deferred mid-cycle on sibling then back stays gated after fail.
    global.__deferSigRestore = true;
    selectSj(container, "demo-multisj-photo-002");
    await flush();
    selectSj(container, "demo-multisj-photo-001");
    await flush();
    expect(container.querySelector('[data-testid="confirm-delivered-button"]').disabled).toBe(true);
    await act(async () => {
      container.querySelectorAll('[data-testid="signature-pad-mock-fail-restore"]')[0]
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
      // Drop pending ready callbacks for cancelled/failed pad — simulate onload abort.
      global.__sigRestorePending = [];
    });
    await flush();
    expect(container.querySelector('[data-testid="confirm-delivered-button"]').disabled).toBe(true);

    // Rapid SJ switch must not leave sibling restore enabling the wrong SJ.
    global.__deferSigRestore = true;
    typeRecipientName(container, "Nama A");
    global.__deferSigRestore = false;
    signBothPads(container);
    await flush();
    global.__deferSigRestore = true;
    selectSj(container, "demo-multisj-photo-002");
    await flush();
    const pendingForB = global.__sigRestorePending.length;
    selectSj(container, "demo-multisj-photo-001");
    await flush();
    // Completing stale B callbacks (if any) must not unlock A incorrectly mid-restore.
    await act(async () => {
      // Only complete current pending (A's restore).
      global.flushDeferredSigRestores();
    });
    await flush();
    expect(pendingForB).toBeGreaterThanOrEqual(0);
    expect(container.querySelector('[data-testid="pengiriman-sj-select"]').value)
      .toBe("demo-multisj-photo-001");
    expect(container.querySelector('[data-testid="confirm-delivered-button"]').disabled).toBe(false);
    unmount();
  });
});
