import { act } from "react";
import { createRoot } from "react-dom/client";
import SuratJalanPanel from "./SuratJalanPanel";

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ user: { name: "Admin", email: "admin@x.com" }, hasPerm: () => true }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    addAttachment: jest.fn(),
    deleteSuratJalanFile: jest.fn(),
  },
}));

jest.mock("./ConfirmActionModal", () => () => null);
jest.mock("./SuratJalanPreviewModal", () => () => null);

global.IS_REACT_ACT_ENVIRONMENT = true;

const order = {
  id: "po-1",
  po_number: "PO-1",
  sj_files: [{
    id: "f1",
    file_id: "f1",
    name: "SJ-EXISTING.pdf",
    uploaded_by_name: "Budi",
    uploaded_at: "2026-09-01T00:00:00Z",
    has_file: true,
  }],
};

describe("SuratJalanPanel Detail PO presentation", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("Detail PO hide-upload keeps date and saved-file actions", async () => {
    await act(async () => {
      root.render(
        <SuratJalanPanel
          order={order}
          canManage
          canViewFile
          canDeleteFile
          allowUpload={false}
        />,
      );
    });
    const text = container.textContent;
    expect(text).not.toMatch(/Pilih File/);
    expect(text).not.toMatch(/Upload Surat Jalan/);
    expect(container.querySelector('[data-testid="surat-jalan-upload-button"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="surat-jalan-submit-button"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="surat-jalan-file-input"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="surat-jalan-delivery-date"]')).toBeTruthy();
    expect(text).toContain("Tanggal pengiriman");
    expect(text).toContain("SJ-EXISTING.pdf");
    expect(text).toContain("Budi");
    expect(text).toContain("Lihat Surat Jalan");
    expect(text).toContain("Download");
    expect(text).toContain("Hapus File Surat Jalan");
    expect(container.querySelector('[data-testid="sj-lihat-f1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="sj-download-f1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="sj-hapus-f1"]')).toBeTruthy();
  });
});
