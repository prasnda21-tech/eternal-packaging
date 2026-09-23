import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import SuratJalanPreviewModal from "./SuratJalanPreviewModal";
import { fetchSuratJalanBlob, downloadSuratJalanFile } from "../lib/suratJalan";

global.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("../lib/suratJalan", () => {
  const actual = jest.requireActual("../lib/suratJalan");
  return {
    ...actual,
    fetchSuratJalanBlob: jest.fn(),
    downloadSuratJalanFile: jest.fn(),
  };
});

const pdfBlob = new Blob([new Uint8Array([0x25, 0x50, 0x44, 0x46])], { type: "application/pdf" });

const order = { id: "po-1", po_number: "PO-100", customer_name: "PT Contoh" };

function entry(id, name) {
  return {
    file_id: id,
    name,
    uploaded_by_name: "Admin",
    uploaded_at: "2026-09-01T00:00:00.000Z",
    content_type: "application/pdf",
  };
}

function renderModal(props) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const render = (next) => {
    act(() => {
      root.render(<SuratJalanPreviewModal {...next} />);
    });
  };
  render(props);
  return {
    host,
    rerender: render,
    unmount() {
      act(() => root.unmount());
      host.remove();
    },
  };
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function rule(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\{([^}]+)\\}`));
  if (!match) throw new Error(`missing css rule ${selector}`);
  return match[1];
}

describe("Surat Jalan preview modal", () => {
  const preview = fs.readFileSync(path.join(__dirname, "SuratJalanPreviewModal.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/suratJalan.js"), "utf8");

  beforeAll(() => {
    if (typeof URL.createObjectURL !== "function") {
      URL.createObjectURL = () => "blob:surat-jalan";
    }
    if (typeof URL.revokeObjectURL !== "function") {
      URL.revokeObjectURL = () => {};
    }
  });

  beforeEach(() => {
    fetchSuratJalanBlob.mockReset();
    downloadSuratJalanFile.mockReset();
    fetchSuratJalanBlob.mockResolvedValue(pdfBlob);
    downloadSuratJalanFile.mockResolvedValue(undefined);
    document.body.style.overflow = "";
    document.documentElement.style.overflow = "";
  });

  afterEach(() => {
    document.body.innerHTML = "";
    document.body.style.overflow = "";
    document.documentElement.style.overflow = "";
  });

  test("layout contract keeps the dialog inside the viewport with one content scroller", () => {
    expect(css).toMatch(/\.modal\{[^}]*position:fixed;inset:0/);
    const overlay = rule(css, ".modal.sj-preview-modal");
    expect(overlay).toMatch(/height:100vh/);
    expect(overlay).toMatch(/height:100dvh/);
    expect(overlay).toMatch(/max-height:100dvh/);
    expect(overlay).toMatch(/overflow:hidden/);
    expect(overlay).toMatch(/overscroll-behavior:contain/);

    const dialog = rule(css, ".modal-inner.sj-preview-inner");
    expect(dialog).toMatch(/display:flex/);
    expect(dialog).toMatch(/flex-direction:column/);
    expect(dialog).toMatch(/max-height:calc\(100vh - 32px\)/);
    expect(dialog).toMatch(/max-height:calc\(100dvh - 32px\)/);
    expect(dialog).toMatch(/overflow:hidden/);
    expect(dialog).toMatch(/width:min\(860px,100%\)/);

    const body = rule(css, ".sj-preview-body");
    expect(body).toMatch(/flex:1 1 auto/);
    expect(body).toMatch(/min-height:0/);
    expect(body).toMatch(/overflow-y:auto/);
    expect(body).toMatch(/overscroll-behavior:contain/);

    const head = rule(css, ".sj-preview-inner > .modal-head");
    expect(head).toMatch(/flex-shrink:0/);
    const footer = rule(css, ".sj-preview-footer");
    expect(footer).toMatch(/flex-shrink:0/);
    expect(footer).toMatch(/position:static/);
    expect(footer).not.toMatch(/position:\s*(absolute|fixed|sticky)/);
    expect(footer).toMatch(/background:#fafaf7/);
    expect(footer).toMatch(/border-top:1px solid var\(--line\)/);

    const frame = rule(css, ".sj-preview-frame iframe,.sj-preview-frame img,.sj-preview-frame object");
    expect(frame).toMatch(/height:min\(640px,calc\(100dvh - 360px\)\)/);
    expect(frame).not.toMatch(/70vh/);
    expect(frame).not.toMatch(/min-height:420px/);
    expect(css).not.toMatch(/\.sj-preview-frame\{[^}]*min-height:420px/);
    expect(rule(css, ".sj-preview-filename")).toMatch(/overflow-wrap:anywhere/);
    expect(rule(css, ".sj-preview-list button")).toMatch(/overflow-wrap:anywhere/);
    expect(rule(css, ".sj-preview-list button")).toMatch(/white-space:normal/);
  });

  test("viewer height stays inside desktop, short laptop, tablet, and mobile viewports", () => {
    const viewports = [
      [1920, 1080],
      [1366, 768],
      [1024, 768],
      [390, 844],
    ];
    viewports.forEach(([width, height]) => {
      const narrow = width <= 720;
      const modalMax = height - (narrow ? 16 : 32);
      const preferred = Math.min(narrow ? 560 : 640, height - (narrow ? 320 : 360));
      const minHeight = Math.min(240, height * 0.36);
      const cap = height - (narrow ? 220 : 280);
      const used = Math.min(Math.max(preferred, minHeight), cap);
      expect(modalMax).toBeGreaterThan(0);
      expect(modalMax).toBeLessThanOrEqual(height);
      expect(used).toBeGreaterThanOrEqual(160);
      expect(used).toBeLessThanOrEqual(modalMax);
    });
  });

  test("renders a dialog, pdf viewer, selectable files, and the chosen download", async () => {
    const longName = `${"surat-jalan-pengiriman-gudang-utara-".repeat(8)}final.pdf`;
    const files = [entry("sj-a", longName), entry("sj-b", "sj-kedua.pdf")];
    const onClose = jest.fn();
    const view = renderModal({
      open: true,
      order,
      entries: files,
      canDownload: true,
      onClose,
    });
    await flush();

    const dialog = document.querySelector('[data-testid="sj-preview-modal"]');
    expect(dialog.getAttribute("role")).toBe("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.className).toContain("modal");
    expect(dialog.className).toContain("sj-preview-modal");
    expect(document.querySelector(".sj-preview-body").className).toContain("sj-preview-body");
    expect(document.querySelector(".sj-preview-inner").className).toContain("sj-preview-inner");
    expect(document.querySelector(".modal-head").textContent).toMatch(/SURAT JALAN/);
    expect(document.querySelector('[data-testid="sj-preview-footer"]').className).toContain("sj-preview-footer");
    const pdf = document.querySelector('[data-testid="sj-preview-pdf"]');
    expect(pdf.tagName).toBe("IFRAME");
    expect(pdf.getAttribute("src")).toContain("toolbar=1");
    expect(document.querySelector('[data-testid="sj-preview-filename"]').textContent).toBe(longName);
    expect(fetchSuratJalanBlob).toHaveBeenCalledWith("po-1", "sj-a", expect.objectContaining({ download: false, filename: longName }));

    await act(async () => {
      document.querySelector('[data-testid="sj-preview-pick-sj-b"]').click();
    });
    await flush();
    expect(document.querySelector('[data-testid="sj-preview-filename"]').textContent).toBe("sj-kedua.pdf");
    expect(fetchSuratJalanBlob).toHaveBeenCalledWith("po-1", "sj-b", expect.objectContaining({ filename: "sj-kedua.pdf" }));

    await act(async () => {
      document.querySelector('[data-testid="sj-preview-download"]').click();
    });
    expect(downloadSuratJalanFile).toHaveBeenCalledWith("po-1", "sj-b", "sj-kedua.pdf");

    await act(async () => {
      document.querySelector('[data-testid="sj-preview-close"]').click();
    });
    expect(onClose).toHaveBeenCalledTimes(1);

    await act(async () => {
      dialog.click();
    });
    expect(onClose).toHaveBeenCalledTimes(2);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(onClose).toHaveBeenCalledTimes(3);

    view.unmount();
  });

  test("locks body scroll while open and restores it on close and unmount", async () => {
    document.body.style.overflow = "scroll";
    document.documentElement.style.overflow = "visible";
    const onClose = jest.fn();
    const view = renderModal({
      open: true,
      order,
      entries: [entry("sj-a", "a.pdf")],
      canDownload: true,
      onClose,
    });
    await flush();
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.documentElement.style.overflow).toBe("hidden");
    expect(document.body.style.position).not.toBe("fixed");

    view.rerender({
      open: false,
      order,
      entries: [entry("sj-a", "a.pdf")],
      canDownload: true,
      onClose,
    });
    expect(document.body.style.overflow).toBe("scroll");
    expect(document.documentElement.style.overflow).toBe("visible");
    expect(document.querySelector('[data-testid="sj-preview-modal"]')).toBeNull();

    document.body.style.overflow = "auto";
    document.documentElement.style.overflow = "";
    view.rerender({
      open: true,
      order,
      entries: [entry("sj-a", "a.pdf")],
      canDownload: true,
      onClose,
    });
    expect(document.body.style.overflow).toBe("hidden");
    view.unmount();
    expect(document.body.style.overflow).toBe("auto");
    expect(document.documentElement.style.overflow).toBe("");
  });

  test("does not change the surat jalan file API", () => {
    expect(api).toMatch(/\/orders\/\$\{encodeURIComponent\(orderId\)\}\/surat-jalan\/files\/\$\{encodeURIComponent\(fileId\)\}/);
    expect(preview).toMatch(/fetchSuratJalanBlob\(order\.id, fileId/);
    expect(preview).toMatch(/downloadSuratJalanFile\(order\.id, active\.file_id \|\| active\.id, active\.name\)/);
    expect(preview).not.toMatch(/\bfetch\s*\(/);
    expect(preview).not.toMatch(/axios/);
  });
});
