import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { getDocument } from "pdfjs-dist";
import TrackingInvoicePdfViewer from "./TrackingInvoicePdfViewer";

jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {},
  getDocument: jest.fn(),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  publicApiUrl: (value) => (value ? `http://127.0.0.1:8010${value}` : ""),
  default: {},
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const PATH_A = "/api/public/tracking/tok-public-aaaaaaaa/invoices/inv-1/pdf?disposition=attachment";
const PATH_B = "/api/public/tracking/tok-public-aaaaaaaa/invoices/inv-2/pdf?disposition=attachment";

function pdfBytes(marker) {
  const bytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, marker]);
  return bytes.buffer;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function mockPdf(pages = 3) {
  const render = jest.fn(() => ({ promise: Promise.resolve(), cancel: jest.fn() }));
  const pdf = {
    numPages: pages,
    destroy: jest.fn(),
    getPage: jest.fn(async () => ({
      getViewport: ({ scale }) => ({ width: 600 * scale, height: 800 * scale }),
      render,
      cleanup: jest.fn(),
    })),
  };
  return { pdf, render };
}

function okResponse(marker) {
  return {
    ok: true,
    status: 200,
    arrayBuffer: async () => pdfBytes(marker),
  };
}

function fetchWithSignal(handler) {
  return jest.fn((url, options = {}) => new Promise((resolve, reject) => {
    const abort = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    if (options.signal?.aborted) {
      abort();
      return;
    }
    options.signal?.addEventListener("abort", abort, { once: true });
    Promise.resolve(handler(url, options)).then(resolve, reject);
  }));
}

async function renderViewer(props) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(<TrackingInvoicePdfViewer pdfPath={PATH_A} invoiceId="inv-1" {...props} />);
  });
  return {
    host,
    root,
    update(next) {
      return act(async () => {
        root.render(<TrackingInvoicePdfViewer pdfPath={PATH_A} invoiceId="inv-1" {...props} {...next} />);
      });
    },
  };
}

describe("TrackingInvoicePdfViewer", () => {
  let getContext;

  beforeEach(() => {
    getDocument.mockReset();
    window.innerWidth = 1366;
    getContext = jest.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({});
    document.body.innerHTML = "";
  });

  afterEach(() => {
    getContext.mockRestore();
    delete global.fetch;
  });

  test("source does not open a new window or store the pdf", () => {
    const src = fs.readFileSync(path.join(__dirname, "TrackingInvoicePdfViewer.jsx"), "utf8");
    expect(src).not.toMatch(/window\.open|localStorage|sessionStorage/);
    expect(src).toMatch(/credentials:\s*"omit"/);
    expect(src).toMatch(/cache:\s*"no-store"/);
    expect(src).toMatch(/isEvalSupported:\s*false/);
    expect(src).not.toMatch(/createObjectURL/);
  });

  test("renders the selected pdf with page, zoom, and fit controls", async () => {
    const { pdf, render } = mockPdf(3);
    const task = { promise: Promise.resolve(pdf), destroy: jest.fn() };
    getDocument.mockReturnValue(task);
    const gate = deferred();
    global.fetch = fetchWithSignal(() => gate.promise);
    const { root } = await renderViewer();
    expect(document.querySelector('[data-testid="tracking-pdf-loading"]').textContent).toBe("Memuat PDF...");
    await act(async () => {
      gate.resolve(okResponse(1));
    });
    await flush();
    await flush();
    expect(global.fetch).toHaveBeenCalledWith(
      `http://127.0.0.1:8010${PATH_A}`,
      expect.objectContaining({ credentials: "omit", cache: "no-store" }),
    );
    expect(getDocument).toHaveBeenCalledWith(expect.objectContaining({
      isEvalSupported: false,
      useWorkerFetch: false,
    }));
    expect(document.querySelector('[data-testid="tracking-pdf-page"]').textContent).toBe("1 / 3");
    expect(document.querySelector('[data-testid="tracking-pdf-prev"]').disabled).toBe(true);
    expect(document.querySelector('[data-testid="tracking-pdf-canvas"]').dataset.scale).toBeTruthy();
    const fitted = Number(document.querySelector('[data-testid="tracking-pdf-canvas"]').dataset.scale);

    await act(async () => {
      document.querySelector('[data-testid="tracking-pdf-next"]').click();
    });
    await flush();
    expect(document.querySelector('[data-testid="tracking-pdf-page"]').textContent).toBe("2 / 3");
    expect(pdf.getPage).toHaveBeenCalledWith(2);

    await act(async () => {
      document.querySelector('[data-testid="tracking-pdf-zoom-in"]').click();
    });
    await flush();
    const zoomed = Number(document.querySelector('[data-testid="tracking-pdf-canvas"]').dataset.scale);
    expect(zoomed).toBeGreaterThan(fitted);
    expect(document.querySelector('[data-testid="tracking-pdf-zoom-label"]').textContent).toBe("125%");

    await act(async () => {
      document.querySelector('[data-testid="tracking-pdf-fit"]').click();
    });
    await flush();
    expect(Number(document.querySelector('[data-testid="tracking-pdf-canvas"]').dataset.scale)).toBeCloseTo(fitted, 3);
    expect(document.querySelector('[data-testid="tracking-pdf-zoom-label"]').textContent).toBe("100%");
    expect(render).toHaveBeenCalled();

    await act(async () => { root.unmount(); });
    expect(task.destroy).toHaveBeenCalled();
  });

  test("failed load and denied access stay distinct and retry reloads", async () => {
    global.fetch = fetchWithSignal(() => ({ ok: false, status: 500, arrayBuffer: async () => new ArrayBuffer(0) }));
    const first = await renderViewer();
    await flush();
    expect(document.querySelector('[data-testid="tracking-pdf-error"]').textContent).toMatch(/PDF gagal dimuat/);
    expect(document.querySelector('[data-testid="tracking-pdf-retry"]').textContent).toBe("Coba lagi");
    expect(document.querySelector('[data-testid="tracking-pdf-canvas"]')).toBeNull();
    const { pdf } = mockPdf(1);
    getDocument.mockReturnValue({ promise: Promise.resolve(pdf), destroy: jest.fn() });
    global.fetch = fetchWithSignal(() => okResponse(4));
    await act(async () => {
      document.querySelector('[data-testid="tracking-pdf-retry"]').click();
    });
    await flush();
    await flush();
    expect(document.querySelector('[data-testid="tracking-pdf-canvas"]')).toBeTruthy();
    await act(async () => { first.root.unmount(); });

    getDocument.mockClear();
    global.fetch = fetchWithSignal(() => ({ ok: false, status: 403, arrayBuffer: async () => new ArrayBuffer(0) }));
    const denied = await renderViewer({ invoiceId: "inv-denied" });
    await flush();
    expect(document.querySelector('[data-testid="tracking-pdf-denied"]').textContent).toMatch(/Akses PDF ditolak/);
    expect(document.querySelector('[data-testid="tracking-pdf-error"]')).toBeNull();
    expect(document.querySelector('[data-testid="tracking-pdf-canvas"]')).toBeNull();
    expect(getDocument).not.toHaveBeenCalled();
    await act(async () => { denied.root.unmount(); });
  });

  test("a fast invoice switch renders only the latest pdf and cancels the first", async () => {
    const firstFetch = deferred();
    const secondFetch = deferred();
    global.fetch = jest.fn((url, options = {}) => {
      const gate = url.includes("inv-2") ? secondFetch : firstFetch;
      return new Promise((resolve, reject) => {
        const abort = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
        if (options.signal?.aborted) {
          abort();
          return;
        }
        options.signal?.addEventListener("abort", abort, { once: true });
        gate.promise.then(resolve, reject);
      });
    });
    const pdfA = mockPdf(1).pdf;
    const pdfB = mockPdf(2).pdf;
    const destroyA = jest.fn();
    const destroyB = jest.fn();
    getDocument.mockImplementation(({ data }) => ({
      promise: Promise.resolve(data[5] === 1 ? pdfA : pdfB),
      destroy: data[5] === 1 ? destroyA : destroyB,
    }));
    const view = await renderViewer({ invoiceId: "inv-1", pdfPath: PATH_A });
    await view.update({ invoiceId: "inv-2", pdfPath: PATH_B });
    await act(async () => {
      secondFetch.resolve(okResponse(2));
    });
    await flush();
    await flush();
    expect(document.querySelector('[data-testid="tracking-invoice-pdf-viewer"]').getAttribute("data-invoice-id")).toBe("inv-2");
    expect(document.querySelector('[data-testid="tracking-pdf-page"]').textContent).toBe("1 / 2");
    expect(getDocument).toHaveBeenCalledTimes(1);
    expect(getDocument.mock.calls[0][0].data[5]).toBe(2);
    await act(async () => {
      firstFetch.resolve(okResponse(1));
    });
    await flush();
    expect(getDocument).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[data-testid="tracking-pdf-page"]').textContent).toBe("1 / 2");
    await act(async () => { view.root.unmount(); });
    expect(destroyB).toHaveBeenCalled();
  });
});
