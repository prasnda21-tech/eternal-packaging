import { act } from "react";
import { createRoot } from "react-dom/client";
import AttachmentsPanel from "./AttachmentsPanel";
import api from "../lib/api";

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { name: "Admin", email: "admin@x.com" },
    hasPerm: () => true,
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    addAttachment: jest.fn(),
    deleteDesignFile: jest.fn(),
    getOrderAttachment: jest.fn(),
  },
}));

jest.mock("./ConfirmActionModal", () => () => null);

global.IS_REACT_ACT_ENVIRONMENT = true;

const designFile = {
  id: "d1",
  name: "art.png",
  content_type: "image/png",
  uploaded_by: "designer@x.com",
  uploaded_by_role: "designer",
  uploaded_by_name: "Designer",
  uploaded_at: "2026-09-19T08:00:00.000Z",
};

const order = {
  id: "po-heavy",
  po_number: "PO-AUDIT-HEAVY",
  design_files: [designFile],
  sample_files: [],
};

describe("AttachmentsPanel lazy design fetch", () => {
  let container;
  let root;
  let created;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    created = [];
    URL.createObjectURL = jest.fn((blob) => {
      const url = `blob:design-${created.length}`;
      created.push({ url, blob });
      return url;
    });
    URL.revokeObjectURL = jest.fn();
    api.getOrderAttachment.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  test("A: metadata renders without data_url", async () => {
    await act(async () => {
      root.render(<AttachmentsPanel order={order} user={{ permissions: [] }} section="design" />);
    });
    expect(container.textContent).toContain("art.png");
    expect(container.querySelector('[data-testid="design-file-d1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="view-design-file-d1"]')).toBeTruthy();
    expect(container.querySelector("img")).toBeFalsy();
  });

  test("B: initial render does not fetch file bytes", async () => {
    await act(async () => {
      root.render(<AttachmentsPanel order={order} user={{ permissions: [] }} section="design" />);
    });
    expect(api.getOrderAttachment).not.toHaveBeenCalled();
  });

  test("C/D: click Lihat fetches exact file once and shows preview", async () => {
    api.getOrderAttachment.mockResolvedValue(new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" }));
    await act(async () => {
      root.render(<AttachmentsPanel order={order} user={{ permissions: [] }} section="design" />);
    });
    await act(async () => {
      container.querySelector('[data-testid="view-design-file-d1"]').click();
    });
    expect(api.getOrderAttachment).toHaveBeenCalledTimes(1);
    expect(api.getOrderAttachment).toHaveBeenCalledWith("po-heavy", "design", "d1");
    const preview = container.querySelector('[data-testid="attachment-preview"] img');
    expect(preview).toBeTruthy();
    expect(preview.getAttribute("src")).toBe("blob:design-0");
  });

  test("E: close preview revokes Blob URL", async () => {
    api.getOrderAttachment.mockResolvedValue(new Blob([new Uint8Array([9])], { type: "image/png" }));
    await act(async () => {
      root.render(<AttachmentsPanel order={order} user={{ permissions: [] }} section="design" />);
    });
    await act(async () => {
      container.querySelector('[data-testid="view-design-file-d1"]').click();
    });
    expect(container.querySelector('[data-testid="attachment-preview"]')).toBeTruthy();
    await act(async () => {
      container.querySelector('[data-testid="attachment-preview"]').click();
    });
    expect(container.querySelector('[data-testid="attachment-preview"]')).toBeFalsy();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:design-0");
  });

  test("preview error stays local", async () => {
    api.getOrderAttachment.mockRejectedValue(new Error("boom"));
    await act(async () => {
      root.render(<AttachmentsPanel order={order} user={{ permissions: [] }} section="design" />);
    });
    await act(async () => {
      container.querySelector('[data-testid="view-design-file-d1"]').click();
    });
    expect(container.querySelector('[data-testid="attachment-preview-error"]').textContent).toMatch(
      /Gagal memuat file/,
    );
    expect(container.querySelector('[data-testid="design-files-panel"]')).toBeTruthy();
  });
});
