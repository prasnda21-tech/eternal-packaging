import { act } from "react";
import { createRoot } from "react-dom/client";
import { getDocument } from "pdfjs-dist";
import api from "../../lib/api";
import MobileDesignFile from "./MobileDesignFile";

jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: jest.fn(),
}));

jest.mock("../../lib/api", () => {
  return {
    __esModule: true,
    fileUrl: (storagePath) => `http://localhost/api/files/${storagePath}`,
    default: {
      getOrderAttachment: jest.fn(),
      fetchStoredFileBlob: jest.fn(),
    },
  };
});

global.IS_REACT_ACT_ENVIRONMENT = true;

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

function pdfBlob(marker = 1) {
  return new Blob([new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, marker])], { type: "application/pdf" });
}

function pdfDataUrl() {
  const bytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 3]);
  let binary = "";
  bytes.forEach((value) => {
    binary += String.fromCharCode(value);
  });
  return `data:application/pdf;base64,${btoa(binary)}`;
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

function mockPdf() {
  const render = jest.fn(() => ({ promise: Promise.resolve(), cancel: jest.fn() }));
  return {
    numPages: 1,
    destroy: jest.fn(),
    getPage: jest.fn(async () => ({
      getViewport: ({ scale }) => ({ width: 200 * scale, height: 280 * scale }),
      render,
      cleanup: jest.fn(),
    })),
  };
}

function installWidth(px) {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get() { return px; },
  });
}

function orderWith(files, id = "po-print-9") {
  return {
    id,
    design_status: "Approved",
    design_files: files,
  };
}

describe("MobileDesignFile PDF open", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    getDocument.mockReset();
    getDocument.mockImplementation(() => ({ promise: Promise.resolve(mockPdf()), destroy: jest.fn() }));
    api.getOrderAttachment.mockReset();
    api.fetchStoredFileBlob.mockReset();
    HTMLCanvasElement.prototype.getContext = jest.fn(() => ({}));
    installWidth(800);
    window.open = jest.fn(() => ({ closed: false }));
    window.alert = jest.fn();
    jest.spyOn(global, "fetch").mockRejectedValue(new Error("unexpected fetch"));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.restoreAllMocks();
  });

  async function renderDesign(order) {
    await act(async () => {
      root.render(<MobileDesignFile order={order} />);
    });
  }

  async function clickOpen() {
    await act(async () => {
      container.querySelector('[data-testid="mobile-lihat-desain"]').click();
    });
    await flush();
  }

  function viewer() {
    return container.querySelector('[data-testid="mobile-design-pdf-viewer"]');
  }

  test("metadata-only PDF by MIME fetches the attachment id and opens the viewer", async () => {
    api.getOrderAttachment.mockResolvedValue(pdfBlob(4));
    await renderDesign(orderWith([
      { id: "old-art", name: "old.pdf", content_type: "application/pdf" },
      { id: "design-42", name: "final-art", content_type: "application/pdf" },
    ]));

    await clickOpen();

    expect(api.getOrderAttachment).toHaveBeenCalledTimes(1);
    expect(api.getOrderAttachment).toHaveBeenCalledWith(
      "po-print-9",
      "design",
      "design-42",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(api.fetchStoredFileBlob).not.toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
    expect(viewer()).not.toBeNull();
    expect(viewer().getAttribute("data-document-key")).toContain("design-42");
    expect(container.querySelector('[data-testid="mobile-design-pdf-viewer-canvas"]')).not.toBeNull();
    expect(window.open).not.toHaveBeenCalled();
    expect(window.alert).not.toHaveBeenCalled();
  });

  test("metadata-only PDF by file name fetches the attachment without a direct URL", async () => {
    api.getOrderAttachment.mockResolvedValue(pdfBlob(5));
    await renderDesign(orderWith([
      { id: "design-7", filename: "Cetak.PDF" },
    ]));

    await clickOpen();

    expect(api.getOrderAttachment).toHaveBeenCalledWith(
      "po-print-9",
      "design",
      "design-7",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(container.querySelector('[data-testid="mobile-design-pdf-viewer-canvas"]')).not.toBeNull();
  });

  test("storage_path still loads the stored blob and does not call the attachment API", async () => {
    api.fetchStoredFileBlob.mockResolvedValue(pdfBlob(6));
    await renderDesign(orderWith([{
      id: "file-storage",
      name: "desain.pdf",
      content_type: "application/pdf",
      storage_path: "designs/a.pdf",
    }]));

    await clickOpen();

    expect(api.fetchStoredFileBlob).toHaveBeenCalledWith(
      "designs/a.pdf",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(api.getOrderAttachment).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mobile-design-pdf-viewer-canvas"]')).not.toBeNull();
  });

  test("data_url still opens inline and does not call the attachment API", async () => {
    await renderDesign(orderWith([{
      id: "file-data",
      name: "inline.pdf",
      data_url: pdfDataUrl(),
    }]));

    await clickOpen();

    expect(api.getOrderAttachment).not.toHaveBeenCalled();
    expect(api.fetchStoredFileBlob).not.toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mobile-design-pdf-viewer-canvas"]')).not.toBeNull();
  });

  test("same-origin PDF URL still fetches that URL and does not call the attachment API", async () => {
    const bytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 8]);
    global.fetch.mockResolvedValue({
      ok: true,
      headers: { get: () => "application/pdf" },
      arrayBuffer: async () => bytes.buffer,
    });
    await renderDesign(orderWith([{
      id: "file-local",
      name: "local.pdf",
      url: "http://localhost/files/local.pdf",
    }]));

    await clickOpen();

    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost/files/local.pdf",
      expect.objectContaining({ credentials: "include", cache: "no-store", signal: expect.any(AbortSignal) }),
    );
    expect(api.getOrderAttachment).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mobile-design-pdf-viewer-canvas"]')).not.toBeNull();
  });

  test("external PDF stays on the browser path", async () => {
    await renderDesign(orderWith([{
      id: "file-external",
      name: "remote.pdf",
      url: "https://files.example/desain.pdf",
    }]));

    expect(container.querySelector('[data-testid="mobile-design-pdf-external"]')).not.toBeNull();
    await clickOpen();

    expect(window.open).toHaveBeenCalledWith(
      "https://files.example/desain.pdf",
      "_blank",
      "noopener,noreferrer",
    );
    expect(viewer()).toBeNull();
    expect(api.getOrderAttachment).not.toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  test("missing file or order id shows a failure and does not request an empty id", async () => {
    await renderDesign(orderWith([{ name: "desain.pdf", content_type: "application/pdf" }]));
    await clickOpen();
    expect(container.querySelector('[data-testid="mobile-design-open-error"]').textContent)
      .toBe("File desain tidak dapat dibuka. ID tidak tersedia.");
    expect(viewer()).toBeNull();

    await renderDesign({
      design_files: [{ id: "   ", name: "desain.pdf", content_type: "application/pdf" }],
    });
    await clickOpen();

    expect(api.getOrderAttachment).not.toHaveBeenCalled();
    expect(window.alert).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("File tidak memiliki data yang dapat dibuka.");
  });

  test("403 stays on the viewer access-denied state", async () => {
    api.getOrderAttachment.mockRejectedValue(Object.assign(new Error("denied"), { response: { status: 403 } }));
    await renderDesign(orderWith([{ id: "design-42", name: "final.pdf", content_type: "application/pdf" }]));

    await clickOpen();

    expect(container.querySelector('[data-testid="mobile-design-pdf-viewer-denied"]').textContent).toMatch(/Akses ditolak/);
    expect(container.querySelector('[data-testid="mobile-design-pdf-viewer-retry"]')).toBeNull();
    expect(container.querySelector('[data-testid="mobile-design-open-error"]')).toBeNull();
  });

  test("404 reports that the file content is unavailable", async () => {
    api.getOrderAttachment.mockRejectedValue(Object.assign(new Error("missing"), { response: { status: 404 } }));
    await renderDesign(orderWith([{ id: "design-42", name: "final.pdf", content_type: "application/pdf" }]));

    await clickOpen();

    expect(api.getOrderAttachment).toHaveBeenCalledWith(
      "po-print-9",
      "design",
      "design-42",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(container.querySelector('[data-testid="mobile-design-open-error"]').textContent)
      .toBe("File atau isi desain tidak tersedia.");
    expect(container.querySelector('[data-testid="mobile-design-pdf-viewer-denied"]')).toBeNull();
    expect(container.textContent).not.toMatch(/Akses ditolak/);
    expect(container.querySelector('[data-testid="mobile-design-pdf-viewer-retry"]')).toBeNull();
  });

  test("network failure uses the viewer retry", async () => {
    api.getOrderAttachment
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(pdfBlob(2));
    await renderDesign(orderWith([{ id: "design-42", name: "final.pdf", content_type: "application/pdf" }]));

    await clickOpen();

    expect(container.querySelector('[data-testid="mobile-design-pdf-viewer-error"]').textContent).toMatch(/Dokumen gagal dimuat/);
    await act(async () => {
      container.querySelector('[data-testid="mobile-design-pdf-viewer-retry"]').click();
    });
    await flush();

    expect(api.getOrderAttachment).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[data-testid="mobile-design-pdf-viewer-canvas"]')).not.toBeNull();
  });

  test("closing the viewer aborts the request and ignores a late blob", async () => {
    const gate = deferred();
    api.getOrderAttachment.mockImplementation((_orderId, _kind, _fileId, config) => {
      config.signal.addEventListener("abort", () => gate.reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
      return gate.promise;
    });
    await renderDesign(orderWith([{ id: "design-42", name: "final.pdf", content_type: "application/pdf" }]));
    await clickOpen();
    const signal = api.getOrderAttachment.mock.calls[0][3].signal;
    expect(viewer()).not.toBeNull();
    expect(signal.aborted).toBe(false);

    await act(async () => {
      container.querySelector('[data-testid="mobile-design-pdf-viewer-close"]').click();
    });
    await flush();

    expect(signal.aborted).toBe(true);
    expect(viewer()).toBeNull();
    expect(getDocument).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="mobile-design-open-error"]')).toBeNull();
  });

  test("changing order or file aborts the old request and ignores its blob", async () => {
    const oldGate = deferred();
    api.getOrderAttachment.mockImplementation((orderId, _kind, fileId) => {
      if (orderId === "po-old") return oldGate.promise;
      return Promise.resolve(pdfBlob(fileId === "file-b" ? 7 : 1));
    });
    await renderDesign(orderWith([{ id: "file-a", name: "a.pdf", content_type: "application/pdf" }], "po-old"));
    await clickOpen();
    const oldSignal = api.getOrderAttachment.mock.calls[0][3].signal;

    await renderDesign(orderWith([{ id: "file-b", name: "b.pdf", content_type: "application/pdf" }], "po-new"));
    await flush();

    expect(oldSignal.aborted).toBe(true);
    expect(api.getOrderAttachment).toHaveBeenLastCalledWith(
      "po-new",
      "design",
      "file-b",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(viewer().getAttribute("data-document-key")).toContain("po-new");
    expect(viewer().getAttribute("data-document-key")).toContain("file-b");
    const markers = getDocument.mock.calls.map((call) => call[0].data[5]);
    expect(markers).toEqual([7]);

    await act(async () => {
      oldGate.resolve(pdfBlob(1));
    });
    await flush();

    expect(getDocument.mock.calls.map((call) => call[0].data[5])).toEqual([7]);
    expect(viewer().getAttribute("data-document-key")).toContain("file-b");
    expect(container.querySelector('[data-testid="mobile-design-pdf-viewer-canvas"]')).not.toBeNull();
  });

  test("image with an id opens through the attachment endpoint", async () => {
    URL.createObjectURL = jest.fn(() => "blob:image");
    URL.revokeObjectURL = jest.fn();
    api.getOrderAttachment.mockResolvedValue(new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" }));
    await renderDesign(orderWith([{ id: "img-1", name: "photo.png", content_type: "image/png" }]));
    await clickOpen();
    await flush();

    expect(api.getOrderAttachment).toHaveBeenCalledWith(
      "po-print-9",
      "design",
      "img-1",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(window.alert).not.toHaveBeenCalled();
    expect(viewer()).not.toBeNull();
  });

  test("metadata without an id or bytes keeps the unavailable alert", async () => {
    await renderDesign(orderWith([{ name: "photo.png", content_type: "image/png" }]));
    await clickOpen();

    expect(window.alert).toHaveBeenCalledWith("File tidak memiliki data yang dapat dibuka.");
    expect(api.getOrderAttachment).not.toHaveBeenCalled();
    expect(viewer()).toBeNull();
  });
});
