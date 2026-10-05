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

test("windowed mode keeps at most three canvases after scroll and zoom", async () => {
  const cleanups = [];
  const cancels = [];
  const render = jest.fn(() => {
    const cancel = jest.fn();
    cancels.push(cancel);
    return { promise: Promise.resolve(), cancel };
  });
  const pdf = {
    numPages: 12,
    destroy: jest.fn(),
    getPage: jest.fn(async (pageNumber) => ({
      pageNumber,
      getViewport: ({ scale }) => ({ width: 600 * scale, height: 800 * scale }),
      render,
      cleanup: jest.fn(() => cleanups.push(pageNumber)),
    })),
  };
  getDocument.mockReturnValue({ promise: Promise.resolve(pdf), destroy: jest.fn() });
  const blob = { arrayBuffer: jest.fn(async () => new ArrayBuffer(8)) };
  const observers = [];
  class MockObserver {
    constructor(callback) {
      this.callback = callback;
      this.nodes = [];
      this.disconnected = false;
      observers.push(this);
    }
    observe(node) { this.nodes.push(node); }
    disconnect() { this.disconnected = true; }
    fire(pageNumber) {
      const target = this.nodes.find((node) => node.getAttribute("data-pdf-page") === String(pageNumber));
      this.callback([{ target, isIntersecting: true, intersectionRatio: 0.8 }]);
    }
  }
  const originalObserver = global.IntersectionObserver;
  global.IntersectionObserver = MockObserver;
  const container = document.createElement("div");
  const root = createRoot(container);
  const getContext = jest.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({});

  try {
    await act(async () => {
      root.render(<SalesPwaPdfViewer blob={blob} label="PDF panjang" mode="windowed" />);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(container.querySelectorAll("[data-pdf-page]")).toHaveLength(12);
    expect(container.querySelectorAll("canvas").length).toBeLessThanOrEqual(3);

    await act(async () => {
      observers[observers.length - 1].fire(10);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(container.querySelector('[data-testid="sales-pdf-page-label"]').textContent).toBe("Halaman 10 dari 12");
    expect(container.querySelectorAll("canvas")).toHaveLength(3);
    const laterPages = pdf.getPage.mock.calls.map(([page]) => page).filter((page) => page > 3);
    expect([...new Set(laterPages)].sort((left, right) => left - right)).toEqual([9, 10, 11]);

    await act(async () => {
      container.querySelector('[data-testid="sales-pdf-zoom-in"]').click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(container.querySelector('[data-testid="sales-pdf-viewer"]').getAttribute("data-zoom")).toBe("1.5");
    expect(container.querySelectorAll("canvas").length).toBeLessThanOrEqual(3);
    expect(pdf.getPage).not.toHaveBeenCalledWith(12);

    act(() => root.unmount());
    expect(pdf.destroy).toHaveBeenCalled();
    expect(cleanups.length).toBeGreaterThan(0);
    expect(cancels.some((cancel) => cancel.mock.calls.length > 0)).toBe(true);
    expect(observers.some((observer) => observer.disconnected)).toBe(true);
  } finally {
    global.IntersectionObserver = originalObserver;
    getContext.mockRestore();
  }
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
