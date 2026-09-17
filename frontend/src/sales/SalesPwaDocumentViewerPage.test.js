import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import SalesPwaDocumentViewerPage from "./SalesPwaDocumentViewerPage";

const mockNavigate = jest.fn();
let mockLocationState = { salesDocumentFrom: "/sales/po/order-1" };

jest.mock("react-router-dom", () => ({
  useLocation: () => ({ state: mockLocationState }),
  useNavigate: () => mockNavigate,
  useParams: () => ({ id: "order-1", kind: "sj", documentId: "sj-1" }),
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: { salesPwaDocumentFile: jest.fn() },
}));

jest.mock("./SalesPwaPdfViewer", () => ({
  __esModule: true,
  default: ({ label }) => <div data-testid="sales-pdf-viewer">{label}</div>,
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

    expect(api.salesPwaDocumentFile).toHaveBeenCalledWith("order-1", "sj", "sj-1");
    expect(container.querySelector('[data-testid="sales-pdf-viewer"]')).not.toBeNull();
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
});
