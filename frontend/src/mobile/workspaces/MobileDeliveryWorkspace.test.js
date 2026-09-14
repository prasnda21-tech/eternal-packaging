import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import api from "../../lib/api";
import { MIN_DELIVERY_PROOFS } from "../../lib/deliveryProof";
import MobileDeliveryWorkspace from "./MobileDeliveryWorkspace";

jest.mock("../../lib/api", () => ({
  __esModule: true,
  default: {
    uploadDeliveryProof: jest.fn(),
    deleteDeliveryProof: jest.fn(),
    uploadDeliveryRecipientSignature: jest.fn(),
    uploadDeliveryDriverSignature: jest.fn(),
    confirmDelivered: jest.fn(),
  },
  fileUrl: (p) => p,
}));

jest.mock("../../lib/imageCompress", () => ({
  __esModule: true,
  compressImage: (file) => Promise.resolve({ file, compressed: false, sizeKB: 1 }),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

function jpegFile(name = "gallery.jpg") {
  return new File([new Uint8Array([0xff, 0xd8, 0xff])], name, { type: "image/jpeg" });
}

function heicFile() {
  return new File([new Uint8Array([1, 2, 3])], "photo.heic", { type: "image/heic" });
}

function setInputFile(input, file) {
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

const baseOrder = {
  id: "order-delivery-1",
  po_number: "PO-DEL-1",
  customer: "PT ABC",
  address: "Jl. Tegal Melati No. 1",
  delivery_status: "Belum Terkirim",
  eligible_delivery_qty: 10,
  delivery_proofs: [],
  line_items: [{ name: "Cup", quantity: 10, line_id: "l0" }],
  packing_info: { qty_packed: 10, lines: [{ line_idx: 0, packed_qty: 10 }] },
  shipments: [{ id: "s1", number: "SJ-1", lines: [{ line_idx: 0, qty: 10 }] }],
  loading_confirmations: [{ id: "c1", surat_jalan_id: "s1", lines: [{ line_idx: 0, qty: 10 }] }],
};

const unreadOrder = {
  ...baseOrder,
  packing_info: { qty_packed: 10, lines: [{ line_idx: 0, packed_qty: 10 }] },
  loading_confirmations: [],
};

const partialReadyOrder = {
  ...baseOrder,
  line_items: [{ name: "Cup", quantity: 10000, line_id: "l0" }],
  packing_info: { qty_packed: 3000, lines: [{ line_idx: 0, packed_qty: 3000 }] },
  shipments: [{ id: "s1", number: "SJ-1", lines: [{ line_idx: 0, qty: 3000 }] }],
  loading_confirmations: [{ id: "c1", surat_jalan_id: "s1", lines: [{ line_idx: 0, qty: 2000 }] }],
  eligible_delivery_qty: 2000,
};

const cupLidReadyOrder = {
  ...baseOrder,
  line_items: [
    { name: "Cup", quantity: 5000, line_id: "l0", production_process: "print" },
    { name: "LID", quantity: 5000, line_id: "l1", production_process: "non_print" },
  ],
  packing_info: { qty_packed: 5000, lines: [{ line_idx: 0, packed_qty: 5000 }] },
  shipments: [{
    id: "s1",
    number: "SJ-1",
    lines: [{ line_idx: 0, qty: 5000 }, { line_idx: 1, qty: 5000 }],
    total_qty: 10000,
  }],
  loading_confirmations: [{
    id: "c1",
    surat_jalan_id: "s1",
    lines: [{ line_idx: 0, qty: 5000 }, { line_idx: 1, qty: 5000 }],
  }],
  eligible_delivery_qty: 10000,
};

describe("PWA Pengiriman gallery picker", () => {
  let container;
  let root;
  const deliverySrc = fs.readFileSync(path.join(__dirname, "MobileDeliveryWorkspace.jsx"), "utf8");
  const loadingSrc = fs.readFileSync(path.join(__dirname, "MobileLoadingWorkspace.jsx"), "utf8");
  const packingSrc = fs.readFileSync(path.join(__dirname, "MobilePackingWorkspace.jsx"), "utf8");

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    URL.createObjectURL = jest.fn(() => "blob:delivery-preview");
    URL.revokeObjectURL = jest.fn();
    api.uploadDeliveryProof.mockReset();
    api.deleteDeliveryProof.mockReset();
    api.confirmDelivered.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("authorized editor sees Tambah Foto plus camera and gallery choices", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={() => {}} />);
    });
    expect(container.textContent).toContain("Tambah Foto");
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeTruthy();
    act(() => container.querySelector('[data-testid="mobile-add-photo"]').click());
    expect(container.querySelector('[data-testid="mobile-photo-picker-sheet"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-photo-camera"]').textContent).toMatch(/Ambil Foto/);
    expect(container.querySelector('[data-testid="mobile-photo-gallery"]').textContent).toMatch(/Pilih dari Galeri/);
  });

  test("batch photo write is blocked when Loading for this delivery is insufficient", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={unreadOrder} canEdit onUpdated={() => {}} />);
    });
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-proof-batch-readiness-hint"]')?.textContent || "").toMatch(/Loading/i);
  });

  test("partial Loading still blocks photos until SJ lines are fully loaded", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={partialReadyOrder} canEdit onUpdated={() => {}} />);
    });
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-proof-batch-readiness-hint"]')?.textContent || "").toMatch(/Loading/i);
  });

  test("cup + LID full Loading enables photos even when Packing excludes LID", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={cupLidReadyOrder} canEdit onUpdated={() => {}} />);
    });
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-proof-batch-readiness-hint"]')).toBeFalsy();
  });

  test("gallery input accepts images and does not force camera capture", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={() => {}} />);
    });
    const gallery = container.querySelector('[data-testid="mobile-delivery-gallery-input"]');
    const camera = container.querySelector('[data-testid="mobile-delivery-camera-input"]');
    expect(gallery.getAttribute("accept")).toBe("image/*");
    expect(gallery.hasAttribute("capture")).toBe(false);
    expect(gallery.getAttribute("capture")).toBeNull();
    expect(camera.getAttribute("accept")).toBe("image/*");
    expect(camera.getAttribute("capture")).toBe("environment");
  });

  test("read-only user does not get upload controls", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={{
        ...baseOrder,
        delivery_proofs: [{ id: "p1", storage_path: "proofs/p1.jpg" }],
      }} canEdit={false} onUpdated={() => {}} />);
    });
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-photo-gallery"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-proof-replace-p1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-proof-delete-p1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-proof-view-p1"]')).toBeTruthy();
  });

  test("completed delivery stays immutable", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={{
        ...baseOrder,
        delivery_status: "Terkirim",
        terkirim_confirmed: true,
        delivery_proofs: [{ id: "p1", storage_path: "proofs/p1.jpg" }, { id: "p2", storage_path: "proofs/p2.jpg" }],
      }} canEdit onUpdated={() => {}} />);
    });
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-proof-delete-p1"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-recipient-name"]').disabled).toBe(true);
  });

  test("selected gallery image enters preview/upload flow and counts after upload", async () => {
    const onUpdated = jest.fn();
    api.uploadDeliveryProof.mockResolvedValue({
      order: {
        ...baseOrder,
        delivery_proofs: [{ id: "p1", storage_path: "proofs/p1.jpg" }],
      },
    });
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={onUpdated} />);
    });
    const gallery = container.querySelector('[data-testid="mobile-delivery-gallery-input"]');
    await act(async () => { setInputFile(gallery, jpegFile()); });
    await flush();
    expect(api.uploadDeliveryProof.mock.calls[0][0]).toBe("order-delivery-1");
    expect(api.uploadDeliveryProof.mock.calls[0][4]).toBe("s1");
    expect(onUpdated).toHaveBeenCalled();
    expect(onUpdated.mock.calls[0][0].delivery_proofs).toHaveLength(1);
  });

  test("failed upload does not count and offers retry", async () => {
    api.uploadDeliveryProof.mockRejectedValue({ response: { status: 502, data: { detail: "storage" } } });
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={() => {}} />);
    });
    const gallery = container.querySelector('[data-testid="mobile-delivery-gallery-input"]');
    await act(async () => { setInputFile(gallery, jpegFile()); });
    await flush();
    expect(container.textContent).toMatch(/Gagal mengunggah foto/);
    expect(container.querySelector('[data-testid="mobile-confirm-delivered"]').disabled).toBe(true);
    expect(container.querySelector('[data-testid^="mobile-proof-retry-"]')).toBeTruthy();
  });

  test("upload in progress blocks confirm when evidence is incomplete", async () => {
    let resolveUpload;
    api.uploadDeliveryProof.mockImplementation(() => new Promise((resolve) => { resolveUpload = resolve; }));
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={() => {}} />);
    });
    const gallery = container.querySelector('[data-testid="mobile-delivery-gallery-input"]');
    await act(async () => { setInputFile(gallery, jpegFile()); });
    await flush();
    expect(container.textContent).toMatch(/Mengunggah/);
    expect(container.querySelector('[data-testid="mobile-confirm-delivered"]').disabled).toBe(true);
    await act(async () => {
      resolveUpload({ order: { ...baseOrder, delivery_proofs: [{ id: "p1", storage_path: "x" }] } });
    });
    await flush();
  });

  test("mixed camera and gallery uploads share the same order id", async () => {
    api.uploadDeliveryProof
      .mockResolvedValueOnce({ order: { ...baseOrder, delivery_proofs: [{ id: "p1", storage_path: "a" }] } })
      .mockResolvedValueOnce({ order: { ...baseOrder, delivery_proofs: [{ id: "p1", storage_path: "a" }, { id: "p2", storage_path: "b" }] } });
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={() => {}} />);
    });
    await act(async () => {
      setInputFile(container.querySelector('[data-testid="mobile-delivery-camera-input"]'), jpegFile("cam.jpg"));
    });
    await flush();
    await act(async () => {
      root.render(<MobileDeliveryWorkspace order={{
        ...baseOrder,
        delivery_proofs: [{ id: "p1", storage_path: "a" }],
      }} canEdit onUpdated={() => {}} />);
    });
    await act(async () => {
      setInputFile(container.querySelector('[data-testid="mobile-delivery-gallery-input"]'), jpegFile("gal.jpg"));
    });
    await flush();
    expect(api.uploadDeliveryProof.mock.calls[0][0]).toBe("order-delivery-1");
    expect(api.uploadDeliveryProof.mock.calls[1][0]).toBe("order-delivery-1");
    expect(MIN_DELIVERY_PROOFS).toBe(2);
  });

  test("gallery selection preserves recipient name and signature state", async () => {
    api.uploadDeliveryProof.mockResolvedValue({ order: { ...baseOrder, delivery_proofs: [{ id: "p1" }] } });
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={() => {}} />);
    });
    const name = container.querySelector('[data-testid="mobile-recipient-name"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(name, "Budi Penerima");
      name.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(name.value).toBe("Budi Penerima");
    await act(async () => {
      setInputFile(container.querySelector('[data-testid="mobile-delivery-gallery-input"]'), jpegFile());
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-recipient-name"]').value).toBe("Budi Penerima");
    expect(container.textContent).toContain("TTD PENERIMA");
    expect(container.textContent).toContain("TTD DRIVER");
  });

  test("HEIC is rejected with the supported-format message", async () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={() => {}} />);
    });
    await act(async () => {
      setInputFile(container.querySelector('[data-testid="mobile-delivery-gallery-input"]'), heicFile());
    });
    await flush();
    expect(api.uploadDeliveryProof).not.toHaveBeenCalled();
    expect(container.textContent).toMatch(/Format foto belum didukung/);
  });

  test("source keeps existing camera capture and does not use role === driver", () => {
    expect(deliverySrc).toMatch(/data-testid="mobile-delivery-camera-input"/);
    expect(deliverySrc).toMatch(/capture="environment"/);
    expect(deliverySrc).toMatch(/data-testid="mobile-delivery-gallery-input"/);
    expect(deliverySrc).toMatch(/accept=\{DELIVERY_PROOF_ACCEPT\}/);
    expect(deliverySrc).toMatch(/hasPerm|canEdit/);
    expect(deliverySrc).not.toMatch(/role\s*===\s*["']driver["']/);
    expect(deliverySrc).toMatch(/uploadDeliveryProof\(order\.id/);
    expect(deliverySrc).toMatch(/data-testid="mobile-pengiriman-sj-select"/);
    expect(deliverySrc).toMatch(/shipment_id: activeShipment/);
    expect(loadingSrc).not.toMatch(/Pilih dari Galeri/);
    expect(packingSrc).not.toMatch(/Pilih dari Galeri/);
    expect(loadingSrc).not.toMatch(/mobile-delivery-gallery-input/);
  });

  test("Pengiriman selector isolates SJ1 from SJ2", () => {
    const multi = {
      ...baseOrder,
      line_items: [{ name: "Line A", quantity: 2000, line_id: "LA", production_process: "print" }],
      packing_info: { qty_packed: 2000, lines: [{ line_idx: 0, packed_qty: 2000 }] },
      shipments: [
        { id: "sj-1", number: "SJ/001", delivery_date: "2026-09-10", lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }], total_qty: 1000 },
        { id: "sj-2", number: "SJ/001_2", delivery_date: "2026-09-12", lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }], total_qty: 1000 },
      ],
      loading_confirmations: [{ id: "c1", surat_jalan_id: "sj-1", lines: [{ line_idx: 0, qty: 1000 }] }],
    };
    act(() => {
      root.render(<MobileDeliveryWorkspace order={multi} canEdit onUpdated={() => {}} />);
    });
    const select = container.querySelector('[data-testid="mobile-pengiriman-sj-select"]');
    expect(select).toBeTruthy();
    expect(select.querySelectorAll("option")).toHaveLength(2);
    act(() => {
      select.value = "sj-1";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("SJ/001");
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-status"]').textContent).toMatch(/Siap dikirim/);
    act(() => {
      select.value = "sj-2";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("SJ/001_2");
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-status"]').textContent).toMatch(/Belum siap/);
  });
});
