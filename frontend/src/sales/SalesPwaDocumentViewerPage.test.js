import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import SalesPwaDocumentViewerPage from "./SalesPwaDocumentViewerPage";

const mockNavigate = jest.fn();
let mockLocationState = { salesDocumentFrom: "/sales/po/order-1" };
let mockParams = { id: "order-1", kind: "sj", documentId: "sj-1" };

jest.mock("react-router-dom", () => ({
  useLocation: () => ({ state: mockLocationState }),
  useNavigate: () => mockNavigate,
  useParams: () => mockParams,
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: { salesPwaDocumentFile: jest.fn() },
}));

jest.mock("./SalesPwaPdfViewer", () => ({
  __esModule: true,
  default: ({ label, mode, onError }) => (
    <div data-testid="sales-pdf-viewer" data-mode={mode}>
      <button type="button" data-testid="trigger-pdf-error" onClick={() => onError("PDF rusak atau tidak dapat dibaca.")}>
        rusak
      </button>
      {label}
    </div>
  ),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

describe("SalesPwaDocumentViewerPage", () => {
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;

  beforeEach(() => {
    mockLocationState = { salesDocumentFrom: "/sales/po/order-1" };
    mockParams = { id: "order-1", kind: "sj", documentId: "sj-1" };
    mockNavigate.mockReset();
    URL.createObjectURL = jest.fn(() => "blob:owned-sj");
    URL.revokeObjectURL = jest.fn();
    api.salesPwaDocumentFile.mockResolvedValue({
      blob: new Blob(["pdf"], { type: "application/pdf" }),
      contentType: "application/pdf",
    });
  });

  afterEach(() => {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
    jest.restoreAllMocks();
  });

  test("renders the authenticated SJ with PDF.js and returns through PWA history", async () => {
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => root.render(<SalesPwaDocumentViewerPage />));
    await flush();

    expect(api.salesPwaDocumentFile).toHaveBeenCalledWith(
      "order-1",
      "sj",
      "sj-1",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    const viewer = container.querySelector('[data-testid="sales-pdf-viewer"]');
    expect(viewer).not.toBeNull();
    expect(viewer.getAttribute("data-mode")).toBe("windowed");
    expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector('a[target="_blank"]')).toBeNull();

    act(() => container.querySelector('[data-testid="sales-document-back"]').click());
    expect(mockNavigate).toHaveBeenCalledWith(-1);

    act(() => root.unmount());
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:owned-sj");
  });

  test("guards a direct viewer URL so Android Back falls back to the exact PO", async () => {
    mockLocationState = null;
    const pushState = jest.spyOn(window.history, "pushState");
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => root.render(<SalesPwaDocumentViewerPage />));
    await flush();

    expect(pushState).toHaveBeenCalled();
    act(() => window.dispatchEvent(new PopStateEvent("popstate")));
    expect(mockNavigate).toHaveBeenCalledWith("/sales/po/order-1", { replace: true });

    act(() => root.unmount());
  });

  test("retries a missing document and keeps a non-PDF proof on the existing path", async () => {
    api.salesPwaDocumentFile.mockRejectedValueOnce({ response: { status: 404 } });
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => root.render(<SalesPwaDocumentViewerPage />));
    await flush();

    expect(container.textContent).toContain("Dokumen tidak ditemukan.");
    expect(container.querySelector('[data-testid="sales-document-open-original"]')).toBeNull();

    api.salesPwaDocumentFile.mockResolvedValue({
      blob: new Blob(["pdf"], { type: "application/pdf" }),
      contentType: "application/pdf",
      disposition: 'inline; filename="SJ-100.pdf"',
    });
    act(() => container.querySelector('[data-testid="sales-document-retry"]').click());
    await flush();
    expect(container.querySelector("h1").textContent).toBe("SJ-100.pdf");
    expect(container.querySelector('[data-testid="sales-pdf-viewer"]')).not.toBeNull();

    mockParams = { id: "order-1", kind: "proof", documentId: "proof-1" };
    api.salesPwaDocumentFile.mockResolvedValue({
      blob: new Blob(["img"], { type: "image/jpeg" }),
      contentType: "image/jpeg",
      disposition: "",
    });
    await act(async () => root.render(<SalesPwaDocumentViewerPage />));
    await flush();
    expect(container.querySelector("img.spwa-viewer-image")).not.toBeNull();
    expect(container.querySelector('[data-testid="sales-pdf-viewer"]')).toBeNull();

    act(() => root.unmount());
  });

  test("offers the same original file when preview fails and ignores a stale document", async () => {
    let resolveFirst;
    api.salesPwaDocumentFile.mockImplementation((id, kind, documentId) => {
      if (documentId === "sj-1") {
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve({
        blob: new Blob(["second"], { type: "application/pdf" }),
        contentType: "application/pdf",
        disposition: 'inline; filename="sj-2.pdf"',
      });
    });

    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => root.render(<SalesPwaDocumentViewerPage />));
    mockParams = { id: "order-1", kind: "invoice", documentId: "inv-9" };
    await act(async () => root.render(<SalesPwaDocumentViewerPage />));
    await flush();

    expect(container.querySelector("h1").textContent).toBe("sj-2.pdf");
    const download = container.querySelector('[data-testid="sales-document-download"]').getAttribute("href");
    act(() => container.querySelector('[data-testid="trigger-pdf-error"]').click());
    expect(container.textContent).toContain("PDF rusak atau tidak dapat dibaca.");
    expect(container.querySelector('[data-testid="sales-document-open-original"]').getAttribute("href")).toBe(download);

    resolveFirst({
      blob: new Blob(["old"], { type: "application/pdf" }),
      contentType: "application/pdf",
      disposition: 'inline; filename="old.pdf"',
    });
    await flush();
    expect(container.textContent).not.toContain("old.pdf");
    expect(container.querySelector("h1").textContent).toBe("sj-2.pdf");

    act(() => root.unmount());
  });

  test("aborts the previous request and revokes its object URL when the document changes", async () => {
    let firstSignal;
    URL.createObjectURL = jest.fn()
      .mockReturnValueOnce("blob:first-doc")
      .mockReturnValueOnce("blob:second-doc");
    api.salesPwaDocumentFile.mockImplementation((id, kind, documentId, options) => {
      if (documentId === "sj-1") {
        firstSignal = options.signal;
        return Promise.resolve({
          blob: new Blob(["first"], { type: "application/pdf" }),
          contentType: "application/pdf",
          disposition: 'inline; filename="first.pdf"',
        });
      }
      return Promise.resolve({
        blob: new Blob(["second"], { type: "application/pdf" }),
        contentType: "application/pdf",
        disposition: 'inline; filename="second.pdf"',
      });
    });

    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => root.render(<SalesPwaDocumentViewerPage />));
    await flush();
    expect(container.querySelector("h1").textContent).toBe("first.pdf");

    mockParams = { id: "order-1", kind: "invoice", documentId: "inv-9" };
    await act(async () => root.render(<SalesPwaDocumentViewerPage />));
    await flush();

    expect(firstSignal.aborted).toBe(true);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:first-doc");
    expect(container.querySelector("h1").textContent).toBe("second.pdf");
    expect(container.querySelector('[data-testid="sales-document-download"]').getAttribute("href")).toBe("blob:second-doc");
    act(() => root.unmount());
  });

  test("previews a PDF attachment whose response type is generic", async () => {
    mockParams = { id: "order-1", kind: "proof", documentId: "lampiran-1" };
    const bytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31]);
    api.salesPwaDocumentFile.mockResolvedValue({
      blob: new Blob([bytes], { type: "application/octet-stream" }),
      contentType: "application/octet-stream",
      disposition: "",
    });
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => root.render(<SalesPwaDocumentViewerPage />));
    await flush();

    expect(container.querySelector('[data-testid="sales-pdf-viewer"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="sales-document-download"]').getAttribute("download")).toBe("proof-lampiran-1.pdf");
    act(() => root.unmount());
  });
});
