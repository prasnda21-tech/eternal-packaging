import { act } from "react";
import { createRoot } from "react-dom/client";
import { getDocument } from "pdfjs-dist";
import SalesPwaPdfViewer from "./SalesPwaPdfViewer";

jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {},
  getDocument: jest.fn(),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

test("renders every PDF page into internal canvases", async () => {
  const render = jest.fn(() => ({ promise: Promise.resolve(), cancel: jest.fn() }));
  const pdf = {
    numPages: 2,
    getPage: jest.fn(async () => ({
      getViewport: ({ scale }) => ({ width: 600 * scale, height: 800 * scale }),
      render,
      cleanup: jest.fn(),
    })),
  };
  getDocument.mockReturnValue({ promise: Promise.resolve(pdf), destroy: jest.fn() });

  const blob = {
    arrayBuffer: jest.fn(async () => new ArrayBuffer(8)),
  };
  const container = document.createElement("div");
  const root = createRoot(container);
  const getContext = jest.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({});

  await act(async () => {
    root.render(<SalesPwaPdfViewer blob={blob} label="Surat Jalan" />);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  expect(getDocument).toHaveBeenCalledWith(expect.objectContaining({
    isEvalSupported: false,
    useWorkerFetch: false,
  }));
  expect(container.querySelectorAll("canvas")).toHaveLength(2);
  expect(pdf.getPage).toHaveBeenCalledTimes(2);
  expect(render).toHaveBeenCalledTimes(2);

  act(() => root.unmount());
  getContext.mockRestore();
});

test("windowed mode keeps only nearby pages on a canvas", async () => {
  const render = jest.fn(() => ({ promise: Promise.resolve(), cancel: jest.fn() }));
  const pdf = {
    numPages: 5,
    destroy: jest.fn(),
    getPage: jest.fn(async () => ({
      getViewport: ({ scale }) => ({ width: 600 * scale, height: 800 * scale }),
      render,
      cleanup: jest.fn(),
    })),
  };
  getDocument.mockReturnValue({ promise: Promise.resolve(pdf), destroy: jest.fn() });
  const blob = { arrayBuffer: jest.fn(async () => new ArrayBuffer(8)) };
  const container = document.createElement("div");
  const root = createRoot(container);
  const getContext = jest.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({});

  await act(async () => {
    root.render(<SalesPwaPdfViewer blob={blob} label="Invoice panjang" mode="windowed" />);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  const canvases = container.querySelectorAll("canvas");
  expect(container.querySelector('[data-testid="sales-pdf-page-label"]').textContent).toBe("Halaman 1 dari 5");
  expect(canvases.length).toBeGreaterThan(0);
  expect(canvases.length).toBeLessThanOrEqual(3);
  expect(pdf.getPage.mock.calls.length).toBeLessThan(5);

  await act(async () => {
    container.querySelector('[data-testid="sales-pdf-zoom-in"]').click();
  });
  expect(container.querySelector('[data-testid="sales-pdf-viewer"]').getAttribute("data-zoom")).toBe("1.5");

  act(() => root.unmount());
  expect(pdf.destroy).toHaveBeenCalled();
  getContext.mockRestore();
});

test("windowed mode reports a protected PDF", async () => {
  const protectedPdf = new Error("password");
  protectedPdf.name = "PasswordException";
  getDocument.mockImplementation(() => ({
    promise: new Promise((_, reject) => {
      queueMicrotask(() => reject(protectedPdf));
    }),
    destroy: jest.fn(),
  }));
  const blob = { arrayBuffer: jest.fn(async () => new ArrayBuffer(8)) };
  const container = document.createElement("div");
  const root = createRoot(container);

  await act(async () => {
    root.render(<SalesPwaPdfViewer blob={blob} label="PO" mode="windowed" />);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  expect(container.textContent).toContain("PDF terproteksi dan tidak dapat ditampilkan.");
  expect(container.querySelector('[data-testid="sales-document-retry"]')).not.toBeNull();
  act(() => root.unmount());
});

test("ignores a file buffer that arrives after the viewer closes", async () => {
  getDocument.mockClear();
  let release = () => {};
  const blob = {
    arrayBuffer: () => new Promise((resolve) => {
      release = () => resolve(new ArrayBuffer(8));
    }),
  };
  const container = document.createElement("div");
  const root = createRoot(container);

  await act(async () => {
    root.render(<SalesPwaPdfViewer blob={blob} label="PO" mode="windowed" />);
  });
  act(() => root.unmount());
  await act(async () => {
    release();
    await Promise.resolve();
  });

  expect(getDocument).not.toHaveBeenCalled();
});
