import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { getDocument } from "pdfjs-dist";
import InAppDocumentViewer, { fittedPageWidthWithinFrame } from "./InAppDocumentViewer";

jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: jest.fn(),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

function pdfBlob(marker = 1) {
  return new Blob([new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, marker])], { type: "application/pdf" });
}

function pngBlob() {
  return new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" });
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

function mockPdf(pages = 3, pageSize = { width: 200, height: 280 }) {
  const render = jest.fn(() => ({ promise: Promise.resolve(), cancel: jest.fn() }));
  return {
    pdf: {
      numPages: pages,
      destroy: jest.fn(),
      getPage: jest.fn(async () => ({
        getViewport: ({ scale }) => ({
          width: pageSize.width * scale,
          height: pageSize.height * scale,
        }),
        render,
        cleanup: jest.fn(),
      })),
    },
    render,
  };
}

function installFrame(widthPx, heightPx = 600) {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get() { return widthPx; },
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get() { return heightPx; },
  });
}

async function renderViewer(props) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(<InAppDocumentViewer testId="doc" allowDownload {...props} />);
  });
  await flush();
  return { host, root };
}

describe("InAppDocumentViewer", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    getDocument.mockReset();
    HTMLCanvasElement.prototype.getContext = jest.fn(() => ({}));
    URL.createObjectURL = jest.fn(() => "blob:doc");
    URL.revokeObjectURL = jest.fn();
    window.open = jest.fn();
    installFrame(800, 600);
    jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => {});
    jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => null);
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  test("multi-page PDF stacks every page, keeps the header, and closes without a new tab", async () => {
    const { pdf, render } = mockPdf(2);
    getDocument.mockReturnValue({ promise: Promise.resolve(pdf), destroy: jest.fn() });
    const onClose = jest.fn();
    const { host, root } = await renderViewer({
      documentKey: "a",
      title: "Bukti",
      fileName: "bukti.pdf",
      contentType: "application/pdf",
      loadFile: jest.fn(async () => pdfBlob()),
      onClose,
    });
    const sheets = host.querySelectorAll(".inapp-doc-sheet");
    const canvases = host.querySelectorAll('[data-testid="doc-canvas"]');
    expect(sheets).toHaveLength(2);
    expect(canvases).toHaveLength(2);
    expect(canvases[0].getAttribute("data-page")).toBe("1");
    expect(canvases[1].getAttribute("data-page")).toBe("2");
    expect(host.querySelector('[data-testid="doc"]').getAttribute("data-page-count")).toBe("2");
    expect(host.querySelector('[data-testid="doc"]').getAttribute("data-page-width")).toBe("784");
    expect(Number(canvases[0].style.width.replace("px", ""))).toBeLessThanOrEqual(920);
    expect(host.querySelector('[data-testid="doc-stage"]').contains(sheets[0])).toBe(true);
    expect(host.querySelector(".inapp-doc-head").textContent).toMatch(/Bukti/);
    expect(host.querySelector('[data-testid="doc-download"]').textContent).toBe("Unduh");
    expect(host.querySelector('[data-testid="doc-close"]')).toBeTruthy();
    expect(host.querySelector(".inapp-doc-head").compareDocumentPosition(host.querySelector('[data-testid="doc-stage"]')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    ["prev", "next", "page", "zoom-in", "zoom-out", "fit"].forEach((name) => {
      expect(host.querySelector(`[data-testid="doc-${name}"]`)).toBeFalsy();
    });
    expect(host.textContent).not.toMatch(/Sebelumnya|Berikutnya|Sesuaikan Lebar|Halaman \d+ \/ \d+/);
    expect(pdf.getPage).toHaveBeenCalledTimes(2);
    expect(render).toHaveBeenCalled();
    await act(async () => {
      host.querySelector('[data-testid="doc-close"]').click();
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(window.open).not.toHaveBeenCalled();
    expect(localStorage.setItem).not.toHaveBeenCalled();
    await act(async () => { root.unmount(); });
    expect(pdf.destroy).toHaveBeenCalled();
    expect(render.mock.results[0].value.cancel).toHaveBeenCalled();
  });

  test("narrow stage caps page width to the available space", async () => {
    installFrame(390, 700);
    const { pdf } = mockPdf(2);
    getDocument.mockReturnValue({ promise: Promise.resolve(pdf), destroy: jest.fn() });
    const { host, root } = await renderViewer({
      documentKey: "narrow",
      fileName: "bukti.pdf",
      contentType: "application/pdf",
      loadFile: jest.fn(async () => pdfBlob()),
      onClose: jest.fn(),
    });
    expect(host.querySelectorAll('[data-testid="doc-canvas"]')).toHaveLength(2);
    expect(host.querySelector('[data-testid="doc"]').getAttribute("data-page-width")).toBe("374");
    const width = Number(host.querySelector('[data-testid="doc-canvas"]').style.width.replace("px", ""));
    expect(width).toBe(374);
    expect(width).toBeLessThanOrEqual(390);
    await act(async () => { root.unmount(); });
  });

  test("fittedPageWidthWithinFrame scales by both width and height", () => {
    const node = { clientWidth: 800, clientHeight: 400 };
    // page aspect 1.4 → height budget caps width below width-only fit
    expect(fittedPageWidthWithinFrame(node, 1.4)).toBe(Math.floor((400 - 16) / 1.4));
    // tall frame: height no longer constrains → width-only fit (800 - 16)
    expect(fittedPageWidthWithinFrame({ clientWidth: 800, clientHeight: 1200 }, 1.4)).toBe(784);
  });

  test("compact layout is opt-in and default shell stays fullscreen", async () => {
    const { pdf } = mockPdf(1);
    getDocument.mockReturnValue({ promise: Promise.resolve(pdf), destroy: jest.fn() });
    const defaultView = await renderViewer({
      documentKey: "default-shell",
      fileName: "bukti.pdf",
      contentType: "application/pdf",
      loadFile: jest.fn(async () => pdfBlob()),
      onClose: jest.fn(),
    });
    expect(defaultView.host.querySelector('[data-testid="doc"]').getAttribute("data-layout")).toBe("default");
    expect(defaultView.host.querySelector(".inapp-doc-overlay--compact")).toBeFalsy();
    expect(defaultView.host.querySelector('[data-testid="doc"]').getAttribute("data-fit")).toBe("default");
    await act(async () => { defaultView.root.unmount(); });

    const compactView = await renderViewer({
      documentKey: "compact-shell",
      fileName: "bukti.pdf",
      contentType: "application/pdf",
      layout: "compact",
      loadFile: jest.fn(async () => pdfBlob()),
      onClose: jest.fn(),
    });
    expect(compactView.host.querySelector('[data-testid="doc"]').getAttribute("data-layout")).toBe("compact");
    expect(compactView.host.querySelector('[data-testid="doc"]').getAttribute("data-fit")).toBe("viewport");
    expect(compactView.host.querySelector(".inapp-doc-overlay--compact")).toBeTruthy();
    expect(compactView.host.querySelector(".inapp-doc--compact")).toBeTruthy();
    expect(compactView.host.querySelector(".inapp-doc-stage--fit")).toBeTruthy();
    expect(compactView.host.querySelector('[data-testid="doc-stage"]').getAttribute("data-overflow")).toBe("fit");
    expect(compactView.host.querySelectorAll('[data-testid="doc-canvas"]')).toHaveLength(1);
    await act(async () => { compactView.root.unmount(); });
  });

  test("compact PDF fits tall pages into the stage height and navigates multipage", async () => {
    installFrame(800, 360);
    const { pdf } = mockPdf(2, { width: 200, height: 400 });
    getDocument.mockReturnValue({ promise: Promise.resolve(pdf), destroy: jest.fn() });
    const { host, root } = await renderViewer({
      documentKey: "compact-pdf-fit",
      fileName: "bukti.pdf",
      contentType: "application/pdf",
      layout: "compact",
      loadFile: jest.fn(async () => pdfBlob()),
      onClose: jest.fn(),
    });
    await flush();
    const pageWidth = Number(host.querySelector('[data-testid="doc"]').getAttribute("data-page-width"));
    // height-limited: (360-16)/2 = 172
    expect(pageWidth).toBe(172);
    expect(host.querySelectorAll('[data-testid="doc-canvas"]')).toHaveLength(1);
    expect(host.querySelector('[data-testid="doc-page-label"]').textContent).toMatch(/1 \/ 2/);
    expect(host.querySelector('[data-testid="doc-prev-page"]').disabled).toBe(true);
    await act(async () => {
      host.querySelector('[data-testid="doc-next-page"]').click();
    });
    await flush();
    expect(host.querySelector('[data-testid="doc-page-label"]').textContent).toMatch(/2 \/ 2/);
    expect(host.querySelector('[data-testid="doc-next-page"]').disabled).toBe(true);
    expect(host.querySelector('[data-testid="doc-download"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="doc-close"]')).toBeTruthy();
    await act(async () => { root.unmount(); });
  });

  test("compact image uses contain fit with zoom controls", async () => {
    const { host, root } = await renderViewer({
      documentKey: "compact-img",
      fileName: "bukti.png",
      contentType: "image/png",
      layout: "compact",
      loadFile: jest.fn(async () => pngBlob()),
      onClose: jest.fn(),
    });
    const img = host.querySelector('[data-testid="doc-image"]');
    expect(img).toBeTruthy();
    expect(img.classList.contains("inapp-doc-image--contain")).toBe(true);
    expect(host.querySelector('[data-testid="doc-stage"]').getAttribute("data-overflow")).toBe("fit");
    expect(host.querySelector('[data-testid="doc"]').getAttribute("data-image-zoom")).toBe("1");
    expect(host.querySelector('[data-testid="doc-zoom-fit"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="doc-zoom-out"]').disabled).toBe(true);
    await act(async () => {
      host.querySelector('[data-testid="doc-zoom-in"]').click();
    });
    expect(host.querySelector('[data-testid="doc"]').getAttribute("data-image-zoom")).toBe("1.25");
    expect(host.querySelector('[data-testid="doc-stage"]').getAttribute("data-overflow")).toBe("scroll");
    expect(host.querySelector(".inapp-doc.is-zoomed")).toBeTruthy();
    await act(async () => {
      host.querySelector('[data-testid="doc-zoom-fit"]').click();
    });
    expect(host.querySelector('[data-testid="doc"]').getAttribute("data-image-zoom")).toBe("1");
    expect(host.querySelector('[data-testid="doc-stage"]').getAttribute("data-overflow")).toBe("fit");
    expect(host.querySelector('[data-testid="doc-download"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="doc-close"]')).toBeTruthy();
    await act(async () => { root.unmount(); });
  });

  test("switching documents hides the previous canvas", async () => {
    const pdfs = [];
    getDocument.mockImplementation(() => {
      const made = mockPdf(2);
      pdfs.push(made.pdf);
      return { promise: Promise.resolve(made.pdf), destroy: jest.fn() };
    });
    const loads = [];
    const loadFile = jest.fn(() => {
      const gate = deferred();
      loads.push(gate);
      return gate.promise.then(() => pdfBlob(loads.length));
    });
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const props = {
      title: "Bukti",
      fileName: "a.pdf",
      contentType: "application/pdf",
      loadFile,
      onClose: jest.fn(),
      testId: "doc",
    };
    await act(async () => {
      root.render(<InAppDocumentViewer documentKey="a" {...props} />);
    });
    await act(async () => { loads[0].resolve(); });
    await flush();
    expect(host.querySelector('[data-testid="doc-canvas"]')).toBeTruthy();
    await act(async () => {
      root.render(<InAppDocumentViewer documentKey="b" {...props} />);
    });
    expect(host.querySelector('[data-testid="doc"]').getAttribute("data-document-key")).toBe("b");
    expect(host.querySelector('[data-testid="doc-canvas"]')).toBeFalsy();
    expect(host.querySelector('[data-testid="doc-loading"]')).toBeTruthy();
    await act(async () => { loads[loads.length - 1].resolve(); });
    await flush();
    expect(host.querySelector('[data-testid="doc-canvas"]')).toBeTruthy();
    expect(pdfs[0].destroy).toHaveBeenCalled();
    await act(async () => { root.unmount(); });
  });

  test("jpg and png use the image viewer", async () => {
    const { host, root } = await renderViewer({
      documentKey: "img",
      fileName: "bukti.png",
      contentType: "image/png",
      loadFile: jest.fn(async () => pngBlob()),
      onClose: jest.fn(),
    });
    expect(host.querySelector('[data-testid="doc-image"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="doc-canvas"]')).toBeFalsy();
    expect(getDocument).not.toHaveBeenCalled();
    expect(window.open).not.toHaveBeenCalled();
    await act(async () => { root.unmount(); });
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  test("load failure can be retried and denial is distinct from unsupported", async () => {
    const loadFile = jest.fn()
      .mockRejectedValueOnce(Object.assign(new Error("nope"), { response: { status: 500 } }))
      .mockResolvedValueOnce(pngBlob());
    const { host, root } = await renderViewer({
      documentKey: "err",
      fileName: "bukti.png",
      contentType: "image/png",
      loadFile,
      onClose: jest.fn(),
    });
    expect(host.querySelector('[data-testid="doc-error"]').textContent).toMatch(/Dokumen gagal dimuat/);
    await act(async () => {
      host.querySelector('[data-testid="doc-retry"]').click();
    });
    await flush();
    expect(host.querySelector('[data-testid="doc-image"]')).toBeTruthy();
    await act(async () => { root.unmount(); });

    const denied = await renderViewer({
      documentKey: "denied",
      fileName: "bukti.pdf",
      loadFile: jest.fn(() => Promise.reject(Object.assign(new Error("no"), { response: { status: 403 } }))),
      onClose: jest.fn(),
    });
    expect(denied.host.querySelector('[data-testid="doc-denied"]').textContent).toMatch(/Akses ditolak/);
    expect(denied.host.querySelector('[data-testid="doc-retry"]')).toBeFalsy();
    await act(async () => { denied.root.unmount(); });

    const bad = await renderViewer({
      documentKey: "svg",
      fileName: "x.svg",
      contentType: "image/svg+xml",
      loadFile: jest.fn(async () => new Blob(["<svg></svg>"], { type: "image/svg+xml" })),
      onClose: jest.fn(),
    });
    expect(bad.host.querySelector('[data-testid="doc-unsupported"]').textContent).toMatch(/tidak dapat ditampilkan/);
    await act(async () => { bad.root.unmount(); });
  });

  test("design PDF download saves the previewed file under its original name", async () => {
    const { pdf } = mockPdf(1);
    getDocument.mockReturnValue({ promise: Promise.resolve(pdf), destroy: jest.fn() });
    const blob = pdfBlob();
    const loadFile = jest.fn(async () => blob);
    const clicks = [];
    const realCreate = document.createElement.bind(document);
    const { host, root } = await renderViewer({
      documentKey: "design-pdf",
      title: "Desain Final.pdf",
      fileName: "Desain Final.pdf",
      contentType: "application/pdf",
      allowDownload: false,
      pdfDownload: true,
      loadFile,
      onClose: jest.fn(),
    });
    const actions = host.querySelector(".inapp-doc-head-actions");
    const downloadBtn = host.querySelector('[data-testid="doc-download"]');
    const closeBtn = host.querySelector('[data-testid="doc-close"]');
    expect(actions.firstElementChild).toBe(downloadBtn);
    expect(downloadBtn.textContent).toMatch(/Unduh PDF/);
    expect(downloadBtn.querySelector("svg")).toBeTruthy();
    expect(downloadBtn.compareDocumentPosition(closeBtn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(downloadBtn.disabled).toBe(false);
    expect(host.querySelector('[data-testid="doc-canvas"]')).toBeTruthy();
    const callsBefore = loadFile.mock.calls.length;
    jest.spyOn(document, "createElement").mockImplementation((tag, options) => {
      const el = realCreate(tag, options);
      if (tag === "a") {
        el.click = () => {
          clicks.push({ href: el.href, download: el.download, target: el.target });
        };
      }
      return el;
    });
    await act(async () => {
      downloadBtn.click();
    });
    expect(loadFile).toHaveBeenCalledTimes(callsBefore);
    expect(URL.createObjectURL).toHaveBeenCalledWith(blob);
    expect(clicks).toEqual([
      expect.objectContaining({ download: "Desain Final.pdf", target: "" }),
    ]);
    expect(String(clicks[0].href || "")).not.toBe("");
    expect(window.open).not.toHaveBeenCalled();
    expect(host.querySelector('[data-testid="doc-canvas"]')).toBeTruthy();
    await act(async () => {
      closeBtn.click();
    });
    await act(async () => { root.unmount(); });
  });

  test("source keeps tokens out of localStorage and uses pdf.js", () => {
    const src = fs.readFileSync(path.join(__dirname, "InAppDocumentViewer.jsx"), "utf8");
    expect(src).toMatch(/pdfjs-dist/);
    expect(src).toMatch(/pdf\.worker\.min\.mjs/);
    expect(src).not.toMatch(/localStorage/);
    expect(src).toMatch(/const downloadPdf = async/);
    expect(src).toMatch(/Unduh PDF/);
    expect(src).not.toMatch(/window\.open/);
    expect(src).not.toMatch(/target=_blank/);
    expect(src).toMatch(/MAX_PAGE_WIDTH = 920/);
    expect(src).toMatch(/fittedPageWidthWithinFrame/);
    expect(src).toMatch(/layout === "compact"/);
    expect(src).toMatch(/object-fit|inapp-doc-image--contain/);
    expect(src).not.toMatch(/Sesuaikan Lebar|Zoom -/);
  });
});
