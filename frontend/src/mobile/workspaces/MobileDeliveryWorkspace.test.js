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

jest.mock("../../components/SignaturePad", () => ({
  __esModule: true,
  default: () => <div data-testid="mobile-sig-pad-stub" />,
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

function typeInput(el, value) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
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

const signedReadyOrder = {
  ...baseOrder,
  delivery_proofs: [
    { id: "p1", shipment_id: "s1", storage_path: "a.jpg" },
    { id: "p2", shipment_id: "s1", storage_path: "b.jpg" },
  ],
  delivery_recipient_signature: {
    id: "r1",
    signature_storage_path: "r.png",
    recipient_signed_at: "t",
    recipient_name: "",
  },
  delivery_driver_signature: {
    id: "d1",
    signature_storage_path: "d.png",
    driver_signed_at: "t",
  },
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
    api.uploadDeliveryRecipientSignature.mockReset();
    api.uploadDeliveryDriverSignature.mockReset();
    api.confirmDelivered.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const openPhoto = () => {
    act(() => container.querySelector('[data-testid="mobile-delivery-photo-action"]').click());
  };
  const openRecipient = () => {
    act(() => container.querySelector('[data-testid="mobile-delivery-recipient-sign-action"]').click());
  };

  test("authorized editor sees Tambah Foto plus camera and gallery choices", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={() => {}} />);
    });
    openPhoto();
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
    openPhoto();
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
    openPhoto();
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
    expect(container.querySelector('[data-testid="mobile-confirm-delivered"]').disabled).toBe(true);
    expect(container.querySelector('[data-testid="mobile-delivery-driver-sign-action"]').disabled).toBe(true);
    expect(container.querySelector('[data-testid="mobile-delivery-recipient-sign-action"]').disabled).toBe(true);
    openPhoto();
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-proof-delete-p1"]')).toBeFalsy();
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
    openPhoto();
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
    openRecipient();
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
    expect(container.textContent).toContain("TTD Penerima");
    expect(container.textContent).toContain("TTD Pengirim");
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
    expect(deliverySrc).toMatch(/shipment_id: selectedShipmentKey/);
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
    expect(select.value).toBe("");
    expect(select.querySelectorAll("option").length).toBeGreaterThanOrEqual(2);
    expect([...select.options].some((o) => o.value === "")).toBe(true);
    act(() => {
      select.value = "sj-1";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("SJ/001");
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-status"]').textContent).toMatch(/Siap Kirim/);
    act(() => {
      select.value = "sj-2";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("SJ/001_2");
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-status"]').textContent).toMatch(/Belum siap/);
  });

  test("SJ visible text hides UUID while select value keeps shipment id", () => {
    const uuid = "3ab43353-5705-476b-97fd-f3beb1b1314c";
    const uuid2 = "7c91e2a0-4b18-4d6f-9c22-0e8f1a2b3c4d";
    const order = {
      ...baseOrder,
      shipments: [
        { id: uuid, number: "SJ/2026/09/07/000031 1", delivery_date: "2026-09-15", lines: [{ line_idx: 0, qty: 10 }], total_qty: 10 },
        { id: uuid2, number: "SJ/2026/09/07/000031 2", delivery_date: "2026-09-15", lines: [{ line_idx: 0, qty: 10 }], total_qty: 10 },
      ],
      loading_confirmations: [{ id: "c1", surat_jalan_id: uuid, lines: [{ line_idx: 0, qty: 10 }] }],
    };
    act(() => {
      root.render(<MobileDeliveryWorkspace order={order} canEdit onUpdated={() => {}} />);
    });
    const select = container.querySelector('[data-testid="mobile-pengiriman-sj-select"]');
    expect(select.value).toBe("");
    expect(select.disabled).toBe(false);
    expect(select.textContent).toContain("SJ/2026/09/07/000031 1");
    expect(select.textContent).not.toContain(uuid);
    expect(select.textContent).not.toContain(uuid2);
    expect(container.textContent).not.toContain(uuid);
    expect(container.textContent).toMatch(/SJ\/2026\/09\/07\/000031 1/);
  });

  test("explicit shipmentId always wins over default multi-SJ selection", () => {
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
      root.render(<MobileDeliveryWorkspace order={multi} canEdit onUpdated={() => {}} shipmentId="sj-2" />);
    });
    const select = container.querySelector('[data-testid="mobile-pengiriman-sj-select"]');
    expect(select.value).toBe("sj-2");
    expect(select.disabled).toBe(true);
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("SJ/001_2");
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-status"]').textContent).toMatch(/Belum siap/);
    expect(container.querySelector('[data-testid="mobile-delivery-error"]')).toBeFalsy();
  });

  test("unmatched explicit shipmentId does not open another SJ", () => {
    const multi = {
      ...baseOrder,
      shipments: [
        { id: "sj-1", number: "SJ/001", delivery_date: "2026-09-10", lines: [{ line_idx: 0, qty: 10 }], total_qty: 10 },
        { id: "sj-2", number: "SJ/001_2", delivery_date: "2026-09-12", lines: [{ line_idx: 0, qty: 10 }], total_qty: 10 },
      ],
      loading_confirmations: [{ id: "c1", surat_jalan_id: "sj-1", lines: [{ line_idx: 0, qty: 10 }] }],
    };
    act(() => {
      root.render(<MobileDeliveryWorkspace order={multi} canEdit onUpdated={() => {}} shipmentId="missing-sj" />);
    });
    const select = container.querySelector('[data-testid="mobile-pengiriman-sj-select"]');
    expect(select.value).toBe("");
    expect(select.disabled).toBe(true);
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]')?.textContent || "").not.toContain("SJ/001");
    expect(container.querySelector('[data-testid="mobile-delivery-error"]').textContent).toMatch(/Surat Jalan tidak dapat ditentukan/);
    expect(container.querySelector('[data-testid="mobile-confirm-delivered"]').disabled).toBe(true);
  });

  test("explicit unmatched id still fails closed on a single-SJ order", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={() => {}} shipmentId="missing-sj" />);
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]')?.textContent || "").not.toBe("SJ-1");
    expect(container.querySelector('[data-testid="mobile-delivery-error"]').textContent).toMatch(/Surat Jalan tidak dapat ditentukan/);
    expect(container.querySelector('[data-testid="mobile-confirm-delivered"]').disabled).toBe(true);
  });

  test("Surat Jalan button opens modal and close returns to delivery actions", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={() => {}} />);
    });
    expect(container.querySelector('[data-testid="mobile-sj-detail-modal"]')).toBeFalsy();
    act(() => container.querySelector('[data-testid="mobile-delivery-sj-action"]').click());
    expect(container.querySelector('[data-testid="mobile-sj-detail-modal"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-sj-detail-number"]').textContent).toContain("SJ-1");
    expect(container.querySelector('[data-testid="mobile-ws-delivery"]')).toBeTruthy();
    act(() => container.querySelector('[data-testid="mobile-sj-detail-close"]').click());
    expect(container.querySelector('[data-testid="mobile-sj-detail-modal"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-delivery-sj-action"]')).toBeTruthy();
  });

  test("TTD Pengirim and TTD Penerima open the existing signature interaction", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={() => {}} />);
    });
    act(() => container.querySelector('[data-testid="mobile-delivery-driver-sign-action"]').click());
    expect(container.querySelector('[data-testid="mobile-signature-modal"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-signature-modal"] h2').textContent).toBe("Tanda Tangan Pengirim");
    act(() => container.querySelector('[data-testid="mobile-sig-cancel"]').click());
    act(() => container.querySelector('[data-testid="mobile-delivery-recipient-sign-action"]').click());
    expect(container.querySelector('[data-testid="mobile-signature-modal"] h2').textContent).toBe("Tanda Tangan Penerima");
    expect(container.querySelector('[data-testid="mobile-recipient-name-modal"]')).toBeTruthy();
  });

  test("Nama Penerima is visible on the confirmation screen and gates Konfirmasi Terkirim", async () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={signedReadyOrder} canEdit onUpdated={() => {}} />);
    });
    const name = container.querySelector('[data-testid="mobile-recipient-name"]');
    expect(name).toBeTruthy();
    expect(container.textContent).toContain("Nama Penerima");
    expect(name.value).toBe("");
    expect(container.querySelector('[data-testid="mobile-confirm-block-reason"]').textContent).toMatch(/Nama penerima wajib/);

    const named = {
      ...signedReadyOrder,
      delivery_recipient_signature: {
        id: "r2",
        signature_storage_path: "r.png",
        recipient_signed_at: "t",
        recipient_name: "Budi Penerima",
      },
    };
    act(() => {
      root.render(<MobileDeliveryWorkspace order={named} canEdit onUpdated={() => {}} />);
    });
    expect(container.querySelector('[data-testid="mobile-recipient-name"]').value).toBe("Budi Penerima");
    expect(container.querySelector('[data-testid="mobile-confirm-delivered"]').disabled).toBe(false);
    expect(container.querySelector('[data-testid="mobile-confirm-block-reason"]')).toBeFalsy();
  });

  test("empty or whitespace Nama Penerima rejects confirm without an API call", async () => {
    api.confirmDelivered.mockResolvedValue({ order: { ...signedReadyOrder, delivery_status: "Terkirim" } });
    act(() => {
      root.render(<MobileDeliveryWorkspace order={signedReadyOrder} canEdit onUpdated={() => {}} />);
    });
    expect(container.querySelector('[data-testid="mobile-confirm-block-reason"]').textContent).toMatch(/Nama penerima wajib/);
    await act(async () => {
      container.querySelector('[data-testid="mobile-confirm-delivered"]').click();
    });
    expect(api.confirmDelivered).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mobile-delivery-error"]').textContent).toMatch(/Nama penerima wajib/);

    await act(async () => {
      typeInput(container.querySelector('[data-testid="mobile-recipient-name"]'), "   ");
    });
    await act(async () => {
      container.querySelector('[data-testid="mobile-confirm-delivered"]').click();
    });
    expect(api.confirmDelivered).not.toHaveBeenCalled();
    expect(container.textContent).toMatch(/Nama penerima wajib/);
  });

  test("typing a valid name clears the error and confirm sends trimmed recipient_name immediately", async () => {
    const delivered = {
      ...signedReadyOrder,
      delivery_status: "Terkirim",
      terkirim_confirmed: true,
      delivery_recipient_signature: {
        ...signedReadyOrder.delivery_recipient_signature,
        recipient_name: "me time",
      },
    };
    api.confirmDelivered.mockResolvedValue({ order: delivered });
    const onUpdated = jest.fn();
    act(() => {
      root.render(<MobileDeliveryWorkspace order={signedReadyOrder} canEdit onUpdated={onUpdated} />);
    });
    expect(container.querySelector('[data-testid="mobile-confirm-block-reason"]').textContent).toMatch(/Nama penerima wajib/);

    await act(async () => {
      typeInput(container.querySelector('[data-testid="mobile-recipient-name"]'), "  me time  ");
    });
    expect(container.querySelector('[data-testid="mobile-recipient-name"]').value).toBe("  me time  ");
    expect(container.querySelector('[data-testid="mobile-confirm-block-reason"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-delivery-error"]')).toBeFalsy();

    await act(async () => {
      container.querySelector('[data-testid="mobile-confirm-delivered"]').click();
    });
    await flush();
    expect(api.uploadDeliveryRecipientSignature).not.toHaveBeenCalled();
    expect(api.confirmDelivered).toHaveBeenCalledTimes(1);
    expect(api.confirmDelivered).toHaveBeenCalledWith("order-delivery-1", expect.objectContaining({
      shipment_id: "s1",
      recipient_name: "me time",
    }));
    expect(onUpdated).toHaveBeenCalledWith(delivered);
  });

  test("confirm payload uses the live input value even if React state is stale", async () => {
    api.confirmDelivered.mockResolvedValue({
      order: { ...signedReadyOrder, delivery_status: "Terkirim", terkirim_confirmed: true },
    });
    act(() => {
      root.render(<MobileDeliveryWorkspace order={signedReadyOrder} canEdit onUpdated={() => {}} />);
    });
    const name = container.querySelector('[data-testid="mobile-recipient-name"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(name, "me time");
    await act(async () => {
      container.querySelector('[data-testid="mobile-confirm-delivered"]').click();
    });
    await flush();
    expect(api.confirmDelivered).toHaveBeenCalledWith("order-delivery-1", expect.objectContaining({
      recipient_name: "me time",
    }));
  });

  test("API failure other than recipient name is shown as-is", async () => {
    api.confirmDelivered.mockRejectedValue({
      response: { status: 400, data: { detail: "Qty Loading untuk pengiriman ini belum mencukupi." } },
    });
    act(() => {
      root.render(<MobileDeliveryWorkspace order={{
        ...signedReadyOrder,
        delivery_recipient_signature: {
          ...signedReadyOrder.delivery_recipient_signature,
          recipient_name: "Budi Penerima",
        },
      }} canEdit onUpdated={() => {}} />);
    });
    await act(async () => {
      container.querySelector('[data-testid="mobile-confirm-delivered"]').click();
    });
    await flush();
    expect(container.querySelector('[data-testid="mobile-delivery-error"]').textContent).toBe(
      "Qty Loading untuk pengiriman ini belum mencukupi.",
    );
    expect(container.querySelector('[data-testid="mobile-delivery-error"]').textContent).not.toMatch(/Nama penerima wajib/);
  });

  test("double click while submitting sends a single confirm request", async () => {
    let resolveConfirm;
    api.confirmDelivered.mockImplementation(() => new Promise((resolve) => { resolveConfirm = resolve; }));
    act(() => {
      root.render(<MobileDeliveryWorkspace order={{
        ...signedReadyOrder,
        delivery_recipient_signature: {
          ...signedReadyOrder.delivery_recipient_signature,
          recipient_name: "Budi Penerima",
        },
      }} canEdit onUpdated={() => {}} />);
    });
    const btn = container.querySelector('[data-testid="mobile-confirm-delivered"]');
    await act(async () => { btn.click(); });
    await act(async () => { btn.click(); });
    expect(api.confirmDelivered).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveConfirm({ order: { ...signedReadyOrder, delivery_status: "Terkirim" } });
    });
    await flush();
  });

  test("legacy PO missing recipient_name field confirms after typing a live name", async () => {
    const legacy = {
      ...signedReadyOrder,
      delivery_recipient_signature: {
        id: "r-legacy",
        signature_storage_path: "r.png",
        recipient_signed_at: "t",
      },
    };
    const delivered = {
      ...legacy,
      delivery_status: "Terkirim",
      terkirim_confirmed: true,
      delivery_recipient_signature: {
        ...legacy.delivery_recipient_signature,
        recipient_name: "me time",
      },
    };
    api.confirmDelivered.mockResolvedValue({ order: delivered });
    act(() => {
      root.render(<MobileDeliveryWorkspace order={legacy} canEdit onUpdated={() => {}} />);
    });
    expect(legacy.delivery_recipient_signature.recipient_name).toBeUndefined();
    expect(container.querySelector('[data-testid="mobile-recipient-name"]').value).toBe("");
    expect(container.querySelector('[data-testid="mobile-confirm-block-reason"]').textContent).toMatch(/Nama penerima wajib/);
    await act(async () => {
      typeInput(container.querySelector('[data-testid="mobile-recipient-name"]'), "me time");
    });
    await act(async () => {
      container.querySelector('[data-testid="mobile-confirm-delivered"]').click();
    });
    await flush();
    expect(api.confirmDelivered).toHaveBeenCalledTimes(1);
    expect(api.confirmDelivered).toHaveBeenCalledWith("order-delivery-1", expect.objectContaining({
      shipment_id: "s1",
      recipient_name: "me time",
    }));
  });

  test("legacy PO that already has recipient_name still confirms", async () => {
    const named = {
      ...signedReadyOrder,
      delivery_recipient_signature: {
        ...signedReadyOrder.delivery_recipient_signature,
        recipient_name: "Budi Penerima",
      },
    };
    api.confirmDelivered.mockResolvedValue({ order: { ...named, delivery_status: "Terkirim" } });
    act(() => {
      root.render(<MobileDeliveryWorkspace order={named} canEdit onUpdated={() => {}} />);
    });
    await act(async () => {
      container.querySelector('[data-testid="mobile-confirm-delivered"]').click();
    });
    await flush();
    expect(api.confirmDelivered).toHaveBeenCalledTimes(1);
    expect(api.confirmDelivered).toHaveBeenCalledWith("order-delivery-1", expect.objectContaining({
      recipient_name: "Budi Penerima",
    }));
  });

  test("legacy PO already Terkirim does not send a second confirm", async () => {
    const done = {
      ...signedReadyOrder,
      delivery_status: "Terkirim",
      terkirim_confirmed: true,
      shipments: [{
        ...signedReadyOrder.shipments[0],
        delivery_status: "Terkirim",
        delivered_at: "2026-09-01T00:00:00+00:00",
        total_qty: 10,
      }],
      delivery_recipient_signature: {
        ...signedReadyOrder.delivery_recipient_signature,
        recipient_name: "Budi Penerima",
        frozen: true,
      },
    };
    act(() => {
      root.render(<MobileDeliveryWorkspace order={done} canEdit onUpdated={() => {}} />);
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-status"]').textContent).toBe("Terkirim");
    expect(container.querySelector('[data-testid="mobile-confirm-delivered"]').disabled).toBe(true);
    await act(async () => {
      container.querySelector('[data-testid="mobile-confirm-delivered"]').click();
    });
    expect(api.confirmDelivered).not.toHaveBeenCalled();
  });

  test("Foto Bukti reuses existing camera/gallery action", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={() => {}} />);
    });
    openPhoto();
    expect(container.querySelector('[data-testid="mobile-delivery-photo-modal"]')).toBeTruthy();
    act(() => container.querySelector('[data-testid="mobile-add-photo"]').click());
    expect(container.querySelector('[data-testid="mobile-photo-camera"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-photo-gallery"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-delivery-camera-input"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-delivery-gallery-input"]')).toBeTruthy();
  });

  test("Konfirmasi Terkirim reuses existing handler and shipment id", async () => {
    const ready = {
      ...baseOrder,
      delivery_proofs: [
        { id: "p1", shipment_id: "s1", storage_path: "a.jpg" },
        { id: "p2", shipment_id: "s1", storage_path: "b.jpg" },
      ],
      delivery_recipient_signature: {
        id: "r1",
        signature_storage_path: "r.png",
        recipient_signed_at: "t",
        recipient_name: "Budi",
      },
      delivery_driver_signature: {
        id: "d1",
        signature_storage_path: "d.png",
        driver_signed_at: "t",
      },
    };
    api.confirmDelivered.mockResolvedValue({ order: { ...ready, delivery_status: "Terkirim", terkirim_confirmed: true } });
    const onUpdated = jest.fn();
    act(() => {
      root.render(<MobileDeliveryWorkspace order={ready} canEdit onUpdated={onUpdated} />);
    });
    expect(container.querySelector('[data-testid="mobile-confirm-delivered"]').disabled).toBe(false);
    await act(async () => {
      container.querySelector('[data-testid="mobile-confirm-delivered"]').click();
    });
    await flush();
    expect(api.confirmDelivered).toHaveBeenCalledWith("order-delivery-1", expect.objectContaining({
      shipment_id: "s1",
      recipient_name: "Budi",
    }));
    expect(onUpdated).toHaveBeenCalled();
  });

  test("new UI has no manual Qty Terkirim input", () => {
    act(() => {
      root.render(<MobileDeliveryWorkspace order={baseOrder} canEdit onUpdated={() => {}} />);
    });
    expect(container.textContent).not.toMatch(/Qty Terkirim/i);
    expect(container.querySelector('input[name="qty_delivered"]')).toBeFalsy();
    expect(deliverySrc).not.toMatch(/Qty Terkirim/);
    expect(deliverySrc).toMatch(/confirmDeliveredBlockReason/);
    expect(deliverySrc).toMatch(/api\.confirmDelivered\(latest\.id/);
  });

  test("already-delivered SJ keeps completed/disabled confirmation", () => {
    const done = {
      ...baseOrder,
      shipments: [{
        id: "s1",
        number: "SJ-1",
        delivered_at: "2026-09-10T10:00:00+07:00",
        delivery_status: "Terkirim",
        lines: [{ line_idx: 0, qty: 10 }],
        total_qty: 10,
      }],
      delivery_status: "Terkirim",
      terkirim_confirmed: true,
      delivery_proofs: [
        { id: "p1", shipment_id: "s1", storage_path: "a.jpg" },
        { id: "p2", shipment_id: "s1", storage_path: "b.jpg" },
      ],
    };
    act(() => {
      root.render(<MobileDeliveryWorkspace order={done} canEdit onUpdated={() => {}} />);
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-status"]').textContent).toBe("Terkirim");
    expect(container.querySelector('[data-testid="mobile-confirm-delivered"]').disabled).toBe(true);
  });

  const multiSjPhotoOrder = (overrides = {}) => ({
    id: "order-demo-multisj-photo",
    po_number: "DEMO-MULTISJ-PHOTO-PO",
    customer: "PT Multi SJ Photo",
    address: "Jl. Preview Multi SJ",
    delivery_status: "Belum Terkirim",
    terkirim_confirmed: false,
    eligible_delivery_qty: 2400,
    line_items: [
      { name: "Cup A", quantity: 1000, line_id: "LA", production_process: "print" },
      { name: "Cup B", quantity: 800, line_id: "LB", production_process: "print" },
      { name: "Lid C", quantity: 600, line_id: "LC", production_process: "non_print" },
    ],
    packing_info: {
      qty_packed: 1800,
      lines: [{ line_idx: 0, packed_qty: 1000 }, { line_idx: 1, packed_qty: 800 }],
    },
    shipments: [
      {
        id: "demo-multisj-photo-001",
        number: "DEMO-MULTISJ-PHOTO-001",
        delivery_date: "2026-09-16",
        lines: [{ line_idx: 0, line_id: "LA", name: "Cup A", qty: 1000 }],
        total_qty: 1000,
      },
      {
        id: "demo-multisj-photo-002",
        number: "DEMO-MULTISJ-PHOTO-002",
        delivery_date: "2026-09-16",
        lines: [{ line_idx: 1, line_id: "LB", name: "Cup B", qty: 800 }],
        total_qty: 800,
      },
      {
        id: "demo-multisj-photo-003",
        number: "DEMO-MULTISJ-PHOTO-003",
        delivery_date: "2026-09-16",
        lines: [{ line_idx: 2, line_id: "LC", name: "Lid C", qty: 600 }],
        total_qty: 600,
      },
    ],
    loading_confirmations: [
      { id: "c1", surat_jalan_id: "demo-multisj-photo-001", lines: [{ line_idx: 0, qty: 1000 }] },
      { id: "c2", surat_jalan_id: "demo-multisj-photo-002", lines: [{ line_idx: 1, qty: 800 }] },
      { id: "c3", surat_jalan_id: "demo-multisj-photo-003", lines: [{ line_idx: 2, qty: 600 }] },
    ],
    delivery_proofs: [],
    ...overrides,
  });

  test("SJ 001/002/003 can each upload after the previous SJ and keep proofs isolated", async () => {
    const order = multiSjPhotoOrder();
    const onUpdated = jest.fn();
    api.uploadDeliveryProof.mockImplementation((id, file, _note, _progress, shipmentId) => {
      const proof = {
        id: `p-${shipmentId}-${file.name}`,
        shipment_id: shipmentId,
        storage_path: `${shipmentId}/${file.name}`,
      };
      const latest = onUpdated.mock.calls.length
        ? onUpdated.mock.calls[onUpdated.mock.calls.length - 1][0]
        : order;
      return Promise.resolve({
        order: { ...latest, delivery_proofs: [...(latest.delivery_proofs || []), proof] },
      });
    });

    act(() => {
      root.render(<MobileDeliveryWorkspace order={order} canEdit onUpdated={onUpdated} shipmentId="demo-multisj-photo-001" />);
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("DEMO-MULTISJ-PHOTO-001");
    openPhoto();
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeTruthy();
    await act(async () => {
      setInputFile(container.querySelector('[data-testid="mobile-delivery-gallery-input"]'), jpegFile("sj-001.png"));
    });
    await flush();
    expect(api.uploadDeliveryProof.mock.calls[0][4]).toBe("demo-multisj-photo-001");

    const after001 = {
      ...order,
      delivery_proofs: [{ id: "p-001", shipment_id: "demo-multisj-photo-001", storage_path: "001.png" }],
    };
    act(() => {
      root.render(<MobileDeliveryWorkspace order={after001} canEdit onUpdated={onUpdated} shipmentId="demo-multisj-photo-002" />);
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("DEMO-MULTISJ-PHOTO-002");
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).not.toContain("DEMO-MULTISJ-PHOTO-001");
    expect(container.querySelector('[data-testid="mobile-delivery-proof-p-001"]')).toBeFalsy();
    openPhoto();
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeTruthy();
    await act(async () => {
      setInputFile(container.querySelector('[data-testid="mobile-delivery-gallery-input"]'), jpegFile("sj-002.png"));
    });
    await flush();
    expect(api.uploadDeliveryProof.mock.calls[1][4]).toBe("demo-multisj-photo-002");

    const after002 = {
      ...after001,
      delivery_proofs: [
        { id: "p-001", shipment_id: "demo-multisj-photo-001", storage_path: "001.png" },
        { id: "p-002", shipment_id: "demo-multisj-photo-002", storage_path: "002.png" },
      ],
    };
    act(() => {
      root.render(<MobileDeliveryWorkspace order={after002} canEdit onUpdated={onUpdated} shipmentId="demo-multisj-photo-003" />);
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("DEMO-MULTISJ-PHOTO-003");
    expect(container.querySelector('[data-testid="mobile-delivery-proof-p-001"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-delivery-proof-p-002"]')).toBeFalsy();
    openPhoto();
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeTruthy();
    await act(async () => {
      setInputFile(container.querySelector('[data-testid="mobile-delivery-gallery-input"]'), jpegFile("sj-003.png"));
    });
    await flush();
    expect(api.uploadDeliveryProof.mock.calls[2][4]).toBe("demo-multisj-photo-003");
  });

  test("delivered SJ 001 does not disable upload on SJ 002 or SJ 003 even if PO is Terkirim", () => {
    const order = multiSjPhotoOrder({
      delivery_status: "Terkirim",
      terkirim_confirmed: true,
      qty_delivered: 1000,
      shipments: [
        {
          id: "demo-multisj-photo-001",
          number: "DEMO-MULTISJ-PHOTO-001",
          delivery_date: "2026-09-16",
          delivered_at: "t1",
          delivery_status: "Terkirim",
          lines: [{ line_idx: 0, line_id: "LA", name: "Cup A", qty: 1000 }],
          total_qty: 1000,
        },
        {
          id: "demo-multisj-photo-002",
          number: "DEMO-MULTISJ-PHOTO-002",
          delivery_date: "2026-09-16",
          lines: [{ line_idx: 1, line_id: "LB", name: "Cup B", qty: 800 }],
          total_qty: 800,
        },
        {
          id: "demo-multisj-photo-003",
          number: "DEMO-MULTISJ-PHOTO-003",
          delivery_date: "2026-09-16",
          lines: [{ line_idx: 2, line_id: "LC", name: "Lid C", qty: 600 }],
          total_qty: 600,
        },
      ],
      delivery_proofs: [
        { id: "p-001a", shipment_id: "demo-multisj-photo-001", storage_path: "001a.png" },
        { id: "p-001b", shipment_id: "demo-multisj-photo-001", storage_path: "001b.png" },
      ],
    });
    act(() => {
      root.render(<MobileDeliveryWorkspace order={order} canEdit onUpdated={() => {}} shipmentId="demo-multisj-photo-002" />);
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("DEMO-MULTISJ-PHOTO-002");
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-status"]').textContent).not.toBe("Terkirim");
    expect(container.querySelector('[data-testid="mobile-delivery-proof-p-001a"]')).toBeFalsy();
    openPhoto();
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-delivery-gallery-input"]')).toBeTruthy();

    act(() => {
      root.render(<MobileDeliveryWorkspace order={order} canEdit onUpdated={() => {}} shipmentId="demo-multisj-photo-003" />);
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("DEMO-MULTISJ-PHOTO-003");
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-status"]').textContent).not.toBe("Terkirim");
    openPhoto();
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeTruthy();
  });

  test("untagged legacy proof does not appear as sibling SJ proof or disable upload", () => {
    const order = multiSjPhotoOrder({
      delivery_proofs: [{ id: "legacy-untagged", storage_path: "legacy.png" }],
    });
    act(() => {
      root.render(<MobileDeliveryWorkspace order={order} canEdit onUpdated={() => {}} shipmentId="demo-multisj-photo-002" />);
    });
    openPhoto();
    expect(container.querySelector('[data-testid="mobile-delivery-proof-legacy-untagged"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeTruthy();
    act(() => {
      root.render(<MobileDeliveryWorkspace order={order} canEdit onUpdated={() => {}} shipmentId="demo-multisj-photo-003" />);
    });
    openPhoto();
    expect(container.querySelector('[data-testid="mobile-delivery-proof-legacy-untagged"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeTruthy();
  });

  test("switching 001 → 002 → 003 changes exact selected shipment with no stale proof", () => {
    const order = multiSjPhotoOrder({
      delivery_proofs: [
        { id: "p-001", shipment_id: "demo-multisj-photo-001", storage_path: "001.png" },
        { id: "p-002", shipment_id: "demo-multisj-photo-002", storage_path: "002.png" },
        { id: "p-003", shipment_id: "demo-multisj-photo-003", storage_path: "003.png" },
      ],
    });
    act(() => {
      root.render(<MobileDeliveryWorkspace order={order} canEdit onUpdated={() => {}} shipmentId="demo-multisj-photo-001" />);
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-sj-select"]').value).toBe("demo-multisj-photo-001");
    openPhoto();
    expect(container.querySelector('[data-testid="mobile-delivery-proof-p-001"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-delivery-proof-p-002"]')).toBeFalsy();

    act(() => {
      root.render(<MobileDeliveryWorkspace order={order} canEdit onUpdated={() => {}} shipmentId="demo-multisj-photo-002" />);
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-sj-select"]').value).toBe("demo-multisj-photo-002");
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("DEMO-MULTISJ-PHOTO-002");
    openPhoto();
    expect(container.querySelector('[data-testid="mobile-delivery-proof-p-001"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-delivery-proof-p-002"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="mobile-delivery-proof-p-003"]')).toBeFalsy();

    act(() => {
      root.render(<MobileDeliveryWorkspace order={order} canEdit onUpdated={() => {}} shipmentId="demo-multisj-photo-003" />);
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-sj-select"]').value).toBe("demo-multisj-photo-003");
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]').textContent).toContain("DEMO-MULTISJ-PHOTO-003");
    openPhoto();
    expect(container.querySelector('[data-testid="mobile-delivery-proof-p-001"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-delivery-proof-p-002"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-delivery-proof-p-003"]')).toBeTruthy();
  });

  test("stale and unknown shipment ids fail closed with no first-SJ fallback", () => {
    const order = multiSjPhotoOrder();
    act(() => {
      root.render(<MobileDeliveryWorkspace order={order} canEdit onUpdated={() => {}} shipmentId="stale-sj-old" />);
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-sj-select"]').value).toBe("");
    expect(container.querySelector('[data-testid="mobile-delivery-error"]').textContent).toMatch(/Surat Jalan tidak dapat ditentukan/);
    expect(container.querySelector('[data-testid="mobile-add-photo"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="mobile-confirm-delivered"]').disabled).toBe(true);

    act(() => {
      root.render(<MobileDeliveryWorkspace order={order} canEdit onUpdated={() => {}} shipmentId="unknown-sj" />);
    });
    expect(container.querySelector('[data-testid="mobile-pengiriman-sj-select"]').value).toBe("");
    expect(container.querySelector('[data-testid="mobile-pengiriman-selected-sj-number"]')?.textContent || "").not.toContain("DEMO-MULTISJ-PHOTO-001");
    expect(container.querySelector('[data-testid="mobile-delivery-error"]').textContent).toMatch(/Surat Jalan tidak dapat ditentukan/);
  });
});
