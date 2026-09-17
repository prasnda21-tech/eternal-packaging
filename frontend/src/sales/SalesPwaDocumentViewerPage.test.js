import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import SalesPwaDocumentViewerPage from "./SalesPwaDocumentViewerPage";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useParams: () => ({ id: "order-1", kind: "sj", documentId: "sj-1" }),
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: { salesPwaDocumentFile: jest.fn() },
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
    URL.createObjectURL = jest.fn(() => "blob:owned-sj");
    URL.revokeObjectURL = jest.fn();
  });

  afterEach(() => {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
    jest.clearAllMocks();
  });

  test("fetches the owned SJ as a blob and renders it inside the PWA", async () => {
    api.salesPwaDocumentFile.mockResolvedValue({
      blob: new Blob(["pdf"], { type: "application/pdf" }),
      contentType: "application/pdf",
    });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<SalesPwaDocumentViewerPage />);
    });
    await flush();

    expect(api.salesPwaDocumentFile).toHaveBeenCalledWith("order-1", "sj", "sj-1");
    expect(container.querySelector('[data-testid="sales-document-frame"]').getAttribute("src")).toBe("blob:owned-sj");
    expect(container.querySelector('[data-testid="sales-document-back"]').getAttribute("href")).toBe("/sales/po/order-1");
    expect(container.querySelector('a[target="_blank"]')).toBeNull();

    act(() => root.unmount());
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:owned-sj");
    container.remove();
  });

  test("keeps authorization failures inside the viewer", async () => {
    api.salesPwaDocumentFile.mockRejectedValue({ response: { status: 404 } });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<SalesPwaDocumentViewerPage />);
    });
    await flush();

    expect(container.textContent).toContain("Dokumen tidak ditemukan.");
    expect(container.querySelector("iframe")).toBeNull();

    act(() => root.unmount());
    container.remove();
  });
});
