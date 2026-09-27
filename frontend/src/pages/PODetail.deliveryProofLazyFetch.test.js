import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import DeliveryProofPanel from "../components/DeliveryProofPanel";
import PoStageSection from "../components/poDetail/PoStageSection";
import { PoStageAccordionProvider, usePoStageAccordion } from "../components/poDetail/PoStageAccordionContext";
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
  },
}));

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u-1", email: "driver@x.com", name: "Driver Test", role: "driver" },
    hasPerm: () => true,
  }),
}));

jest.mock("../components/SignaturePad", () => ({
  __esModule: true,
  default: ({ onChange }) => (
    <button
      type="button"
      data-testid="signature-pad-mock"
      onClick={() => onChange("data:image/png;base64,c2ln")}
    >
      sign
    </button>
  ),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

function PengirimanDeliveryProofPanel({ order, onUpdated }) {
  const { open } = usePoStageAccordion(14);
  return <DeliveryProofPanel order={order} onUpdated={onUpdated} isOpen={open} />;
}

function proof(id, storagePath) {
  return {
    id,
    storage_path: storagePath,
    original_filename: `${id}.jpg`,
    uploaded_by: "driver@x.com",
    uploaded_by_name: "Driver Test",
    uploaded_by_role: "driver",
    uploaded_at: "2026-09-13T00:00:00Z",
    note: "",
  };
}

function baseOrder(overrides = {}) {
  return {
    id: "po-1",
    po_number: "PO-1",
    current_stage: 10,
    delivery_status: "Belum Terkirim",
    delivery_proofs: [
      proof("p1", "delivery/p1.jpg"),
      proof("p2", "delivery/p2.jpg"),
    ],
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

async function waitForFetchCount(n, { timeoutMs = 1500 } = {}) {
  const start = Date.now();
  while (global.fetch.mock.calls.length < n && Date.now() - start < timeoutMs) {
    await flush();
  }
  expect(global.fetch.mock.calls.length).toBe(n);
}

function accordionUi(order, onUpdated = () => {}) {
  return (
    <PoStageAccordionProvider>
      <PoStageSection stageIndex={14} order={order} title="Pengiriman">
        <PengirimanDeliveryProofPanel order={order} onUpdated={onUpdated} />
      </PoStageSection>
    </PoStageAccordionProvider>
  );
}

function fileFetchUrls() {
  return global.fetch.mock.calls.map((c) => c[0]);
}

describe("PODetail delivery proof lazy blob fetch", () => {
  beforeAll(() => {
    if (!URL.createObjectURL) URL.createObjectURL = () => "blob:mock";
    if (!URL.revokeObjectURL) URL.revokeObjectURL = () => {};
  });

  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn((url) => Promise.resolve({
      ok: true,
      blob: () => Promise.resolve(new Blob(["img"], { type: "image/jpeg" })),
    }));
  });

  test("source: panel stays mounted; only blob effect is gated by accordion open", () => {
    const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
    const proofSrc = fs.readFileSync(
      path.join(__dirname, "../components/DeliveryProofPanel.jsx"),
      "utf8"
    );
    expect(detail).toMatch(/function PengirimanDeliveryProofPanel/);
    expect(detail).toMatch(/usePoStageAccordion\(14\)/);
    expect(detail).toMatch(/isOpen=\{open\}/);
    expect(detail).toMatch(/<PengirimanDeliveryProofPanel order=\{order\} onUpdated=\{setOrder\} \/>/);
    const slot = detail.slice(detail.indexOf("workflow-slot-14"));
    expect(slot).toMatch(/PengirimanDeliveryProofPanel/);
    expect(slot).not.toMatch(/\{open\s*&&\s*<DeliveryProofPanel/);
    expect(proofSrc).toMatch(/isOpen = true/);
    expect(proofSrc).toMatch(/if \(!isOpen\) return;/);
    // Identity key, not proof count: a same-length file replacement must refetch.
    expect(proofSrc).toMatch(/\[isOpen, proofLoadKey, selectionKey\]/);
    expect(proofSrc).toMatch(/fetch\(fileUrl\(p\.storage_path\), \{ credentials: "include" \}\)/);
    expect(proofSrc).toMatch(/api\.uploadDeliveryProof/);
    expect(proofSrc).toMatch(/canConfirmDelivered\(proofs\)/);
    expect(proofSrc).not.toMatch(/canConfirmDelivered\(proofBlobs/);
    expect(proofSrc).not.toMatch(/current_stage/);
    expect(proofSrc).not.toMatch(/getWorkflowReady/);
  });

  test("CASE A collapsed: panel mounted, 0 proof blob GETs (LOCAL / TEST HARNESS ONLY)", async () => {
    const order = baseOrder();
    const { container, unmount } = mount(accordionUi(order));
    await flush();
    expect(container.querySelector('[data-testid="delivery-proof-panel"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="po-stage-toggle-14"]').getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector(".po-stage-section-body").hidden).toBe(true);
    expect(global.fetch.mock.calls.length).toBe(0);
    unmount();
  });

  test("CASE B first open: N proof blob GETs at fileUrl paths (LOCAL / TEST HARNESS ONLY)", async () => {
    const order = baseOrder();
    const { container, unmount } = mount(accordionUi(order));
    await flush();
    act(() => {
      container.querySelector('[data-testid="po-stage-toggle-14"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await waitForFetchCount(2);
    expect(fileFetchUrls()).toEqual([
      "/api/files/delivery/p1.jpg",
      "/api/files/delivery/p2.jpg",
    ]);
    expect(global.fetch.mock.calls.every((c) => c[1]?.credentials === "include")).toBe(true);
    unmount();
  });

  test("CASE C close and reopen without proof changes: 0 duplicate GETs (LOCAL / TEST HARNESS ONLY)", async () => {
    const order = baseOrder();
    const { container, unmount } = mount(accordionUi(order));
    const toggle = () => {
      container.querySelector('[data-testid="po-stage-toggle-14"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    };
    act(toggle);
    await waitForFetchCount(2);
    act(toggle);
    await flush();
    expect(container.querySelector(".po-stage-section-body").hidden).toBe(true);
    expect(container.querySelector('[data-testid="delivery-proof-panel"]')).toBeTruthy();
    act(toggle);
    await flush();
    expect(container.querySelector(".po-stage-section-body").hidden).toBe(false);
    expect(global.fetch.mock.calls.length).toBe(2);
    unmount();
  });

  test("CASE D proof list change loads only the new required proof (LOCAL / TEST HARNESS ONLY)", async () => {
    const order = baseOrder();
    const { container, rerender, unmount } = mount(accordionUi(order));
    act(() => {
      container.querySelector('[data-testid="po-stage-toggle-14"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await waitForFetchCount(2);
    const next = baseOrder({
      delivery_proofs: [
        ...order.delivery_proofs,
        proof("p3", "delivery/p3.jpg"),
      ],
    });
    rerender(accordionUi(next));
    await waitForFetchCount(3);
    expect(fileFetchUrls()[2]).toBe("/api/files/delivery/p3.jpg");
    unmount();
  });

  test("same proof count with a replaced file still fetches the new blob", async () => {
    const order = baseOrder();
    const { container, rerender, unmount } = mount(accordionUi(order));
    act(() => {
      container.querySelector('[data-testid="po-stage-toggle-14"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await waitForFetchCount(2);
    const next = baseOrder({
      delivery_proofs: [
        proof("p1", "delivery/p1.jpg"),
        proof("p2b", "delivery/p2b.jpg"),
      ],
    });
    rerender(accordionUi(next));
    await waitForFetchCount(3);
    expect(fileFetchUrls()[2]).toBe("/api/files/delivery/p2b.jpg");
    unmount();
  });

  test("proof list change while collapsed still loads on first open", async () => {
    const order = baseOrder();
    const { rerender, unmount, container } = mount(accordionUi(order));
    await flush();
    expect(global.fetch.mock.calls.length).toBe(0);
    const next = baseOrder({
      delivery_proofs: [
        ...order.delivery_proofs,
        proof("p3", "delivery/p3.jpg"),
      ],
    });
    rerender(accordionUi(next));
    await flush();
    expect(global.fetch.mock.calls.length).toBe(0);
    act(() => {
      container.querySelector('[data-testid="po-stage-toggle-14"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await waitForFetchCount(3);
    unmount();
  });

  test("editable local state survives collapse; current_stage change does not fetch", async () => {
    const order = baseOrder({ current_stage: 5 });
    const { container, rerender, unmount } = mount(accordionUi(order));
    await flush();
    const name = container.querySelector('[data-testid="recipient-name-input"]');
    expect(name).toBeTruthy();
    act(() => {
      const setValue = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setValue.call(name, "Budi Penerima");
      name.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const pads = container.querySelectorAll('[data-testid="signature-pad-mock"]');
    act(() => {
      pads[0].dispatchEvent(new MouseEvent("click", { bubbles: true }));
      pads[1].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    rerender(accordionUi(baseOrder({ current_stage: 14 })));
    await flush();
    expect(global.fetch.mock.calls.length).toBe(0);
    expect(container.querySelector('[data-testid="recipient-name-input"]').value).toBe("Budi Penerima");
    expect(container.querySelector('[data-testid="delivery-proof-panel"]')).toBeTruthy();
    expect(api.uploadDeliveryProof).not.toHaveBeenCalled();
    expect(api.confirmDelivered).not.toHaveBeenCalled();
    unmount();
  });

  test("without isOpen (Surat Jalan / role work) existing proofs still fetch", async () => {
    const order = baseOrder();
    const { unmount } = mount(
      <DeliveryProofPanel order={order} onUpdated={() => {}} />
    );
    await waitForFetchCount(2);
    expect(fileFetchUrls()).toEqual([
      "/api/files/delivery/p1.jpg",
      "/api/files/delivery/p2.jpg",
    ]);
    unmount();
  });
});
