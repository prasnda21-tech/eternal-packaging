import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { PaymentFileRow } from "./PODetail";
import api from "../lib/api";

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { name: "Admin", email: "admin@x.com", role: "admin", permissions: [] },
    hasPerm: () => true,
    loading: false,
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    getOrder: jest.fn(),
    getOrderAttachment: jest.fn(),
    listSalesReceipts: jest.fn(),
    addAttachment: jest.fn(),
    me: jest.fn(),
  },
}));

jest.mock("../lib/useAutoRefresh", () => () => {});

jest.mock("react-router-dom", () => ({
  Link: ({ children }) => children,
  Navigate: () => null,
  useLocation: () => ({ pathname: "/po/po-1", search: "" }),
  useNavigate: () => jest.fn(),
  useParams: () => ({ id: "po-1" }),
}), { virtual: true });

global.IS_REACT_ACT_ENVIRONMENT = true;

const file = {
  id: "p1",
  name: "bukti.png",
  content_type: "image/png",
  uploaded_by: "admin@x.com",
  uploaded_by_name: "Admin",
  uploaded_at: "2026-09-19T08:00:00.000Z",
};

describe("PaymentFileRow lazy fetch", () => {
  let container;
  let root;
  let created;
  let opened;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    created = [];
    opened = [];
    URL.createObjectURL = jest.fn(() => {
      const url = `blob:pay-${created.length}`;
      created.push(url);
      return url;
    });
    URL.revokeObjectURL = jest.fn();
    window.open = jest.fn(() => {
      const w = { opener: "parent", location: { replace: jest.fn() }, close: jest.fn() };
      opened.push(w);
      return w;
    });
    api.getOrderAttachment.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("A: metadata renders without data_url", async () => {
    await act(async () => {
      root.render(<PaymentFileRow file={file} index={1} orderId="po-1" />);
    });
    expect(container.textContent).toContain("bukti.png");
    expect(container.textContent).toContain("Admin");
    expect(container.querySelector("img")).toBeFalsy();
  });

  test("B: initial render does not fetch file bytes", async () => {
    await act(async () => {
      root.render(<PaymentFileRow file={file} index={1} orderId="po-1" />);
    });
    expect(api.getOrderAttachment).not.toHaveBeenCalled();
  });

  test("C/D: click open fetches exact payment file once and previews", async () => {
    api.getOrderAttachment.mockResolvedValue(new Blob([new Uint8Array([4, 5])], { type: "image/png" }));
    await act(async () => {
      root.render(<PaymentFileRow file={file} index={1} orderId="po-1" />);
    });
    await act(async () => {
      container.querySelector('[data-testid="payment-file-open-p1"]').click();
    });
    expect(api.getOrderAttachment).toHaveBeenCalledTimes(1);
    expect(api.getOrderAttachment).toHaveBeenCalledWith("po-1", "payment", "p1");
    const img = container.querySelector('[data-testid="payment-file-preview-p1"] img');
    expect(img).toBeTruthy();
    expect(img.getAttribute("src")).toBe("blob:pay-0");
    expect(window.open).not.toHaveBeenCalled();
  });

  test("E: close preview revokes Blob URL", async () => {
    api.getOrderAttachment.mockResolvedValue(new Blob([new Uint8Array([7])], { type: "image/png" }));
    await act(async () => {
      root.render(<PaymentFileRow file={file} index={1} orderId="po-1" />);
    });
    await act(async () => {
      container.querySelector('[data-testid="payment-file-open-p1"]').click();
    });
    await act(async () => {
      container.querySelector('[data-testid="payment-file-preview-p1"]').click();
    });
    expect(container.querySelector('[data-testid="payment-file-preview-p1"]')).toBeFalsy();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:pay-0");
    expect(window.open).not.toHaveBeenCalled();
  });

  test("Payment PDF opens placeholder synchronously then assigns blob URL", async () => {
    const pdf = {
      id: "pdfp",
      name: "bukti.pdf",
      content_type: "application/pdf",
      uploaded_by_name: "Admin",
    };
    let resolveFetch;
    api.getOrderAttachment.mockReturnValue(new Promise((resolve) => { resolveFetch = resolve; }));
    await act(async () => {
      root.render(<PaymentFileRow file={pdf} index={1} orderId="po-1" />);
    });
    act(() => {
      container.querySelector('[data-testid="payment-file-open-pdfp"]').click();
    });
    expect(window.open).toHaveBeenCalledTimes(1);
    expect(window.open).toHaveBeenCalledWith("about:blank", "_blank");
    expect(opened[0].opener).toBe(null);
    expect(api.getOrderAttachment).toHaveBeenCalledTimes(1);
    expect(api.getOrderAttachment).toHaveBeenCalledWith("po-1", "payment", "pdfp");
    expect(opened[0].location.replace).not.toHaveBeenCalled();
    await act(async () => {
      resolveFetch(new Blob([new Uint8Array([37, 80, 68, 70])], { type: "application/pdf" }));
    });
    expect(opened[0].location.replace).toHaveBeenCalledWith("blob:pay-0");
    expect(window.open).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="payment-file-preview-pdfp"]')).toBeFalsy();
  });

  test("Payment PDF fetch failure closes placeholder and shows local error", async () => {
    const pdf = {
      id: "pdfp",
      name: "bukti.pdf",
      content_type: "application/pdf",
      uploaded_by_name: "Admin",
    };
    api.getOrderAttachment.mockRejectedValue(new Error("boom"));
    await act(async () => {
      root.render(<PaymentFileRow file={pdf} index={1} orderId="po-1" />);
    });
    await act(async () => {
      container.querySelector('[data-testid="payment-file-open-pdfp"]').click();
    });
    expect(opened[0].close).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="payment-file-error-pdfp"]').textContent).toMatch(
      /Gagal memuat file/,
    );
  });
});

describe("PODetail payment lazy contract", () => {
  const src = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");

  test("payment row fetches on demand and does not read data_url for thumbs", () => {
    expect(src).toMatch(/api\.getOrderAttachment\(orderId,\s*"payment",\s*file\.id\)/);
    expect(src).toMatch(/URL\.revokeObjectURL/);
    expect(src).toMatch(/Gagal memuat file\. Coba lagi\./);
    expect(src).not.toMatch(/<img src=\{url\} alt=\{file\.name\} \/>/);
  });
});
