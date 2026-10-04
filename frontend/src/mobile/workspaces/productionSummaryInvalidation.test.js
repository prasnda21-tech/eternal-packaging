import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import { clearProductionSummaryCache, loadProductionSummary } from "../../lib/productionSummaryCache";
import MobileScreenerWorkspace from "./MobileScreenerWorkspace";
import MobileHelperPrepWorkspace from "./MobileHelperPrepWorkspace";
import MobilePackingWorkspace from "./MobilePackingWorkspace";
import MobilePrintWorkspace from "./MobilePrintWorkspace";

jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: jest.fn(() => ({ promise: Promise.resolve({ destroy: jest.fn(), numPages: 0 }) })),
}));

jest.mock("../../lib/api", () => ({
  __esModule: true,
  fileUrl: (storagePath) => `/api/files/${storagePath}`,
  default: {
    updateScreenerInfo: jest.fn(),
    updateHelperPrepItems: jest.fn(),
    savePackingInfo: jest.fn(),
    updateOperatorProgress: jest.fn(),
  },
}));

jest.mock("../../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "user-a", email: "operator.a@x.com" },
    hasPerm: () => false,
  }),
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const requestParams = {
  include_cancelled: false,
  include_completed: true,
  view: "summary",
};

const flush = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

function setValue(el, value) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

async function seedSummary() {
  await loadProductionSummary({
    userKey: "u-save",
    requestParams,
    fetcher: async () => ({ rows: [{ id: "cached", screen_qty: 1 }] }),
  });
}

async function expectSummaryStillCached() {
  const fetcher = jest.fn(async () => {
    throw new Error("failed save must not drop the summary");
  });
  const cached = await loadProductionSummary({
    userKey: "u-save",
    requestParams,
    fetcher,
  });
  expect(fetcher).not.toHaveBeenCalled();
  expect(cached.rows[0].id).toBe("cached");
}

async function expectSummaryDropped() {
  const fetcher = jest.fn(async () => ({ rows: [{ id: "fresh", screen_qty: 2 }] }));
  const fresh = await loadProductionSummary({
    userKey: "u-save",
    requestParams,
    fetcher,
  });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fresh.rows[0].id).toBe("fresh");
}

describe("workspace save invalidates the production summary only after success", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    clearProductionSummaryCache();
    api.updateScreenerInfo.mockReset();
    api.updateHelperPrepItems.mockReset();
    api.savePackingInfo.mockReset();
    api.updateOperatorProgress.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    clearProductionSummaryCache();
  });

  async function renderWorkspace(node) {
    await act(async () => { root.render(node); });
  }

  test("MobileScreenerWorkspace", async () => {
    const onUpdated = jest.fn();
    await seedSummary();
    await renderWorkspace(
      <MobileScreenerWorkspace
        order={{ id: "po-1", workflow_ready: { screener: true }, line_items: [] }}
        canEdit
        onUpdated={onUpdated}
      />,
    );
    await act(async () => { container.querySelector('[aria-label="Tambah"]').click(); });
    api.updateScreenerInfo.mockRejectedValueOnce(new Error("simpan gagal"));
    const save = [...container.querySelectorAll("button")].find((btn) => btn.textContent === "Simpan Screener");
    await act(async () => { save.click(); });
    await flush();
    expect(api.updateScreenerInfo).toHaveBeenCalledTimes(1);
    expect(onUpdated).not.toHaveBeenCalled();
    expect(container.textContent).toContain("simpan gagal");
    await expectSummaryStillCached();

    api.updateScreenerInfo.mockResolvedValueOnce({ order: { id: "po-1" } });
    await act(async () => { save.click(); });
    await flush();
    expect(onUpdated).toHaveBeenCalledTimes(1);
    await expectSummaryDropped();
  });

  test("MobileHelperPrepWorkspace", async () => {
    const onUpdated = jest.fn();
    await seedSummary();
    await renderWorkspace(
      <MobileHelperPrepWorkspace
        order={{
          id: "po-1",
          workflow_ready: { helper: true },
          line_items: [{ name: "Cup", quantity: 10, item_id: "item-0", production_process: "print" }],
        }}
        canEdit
        onUpdated={onUpdated}
      />,
    );
    await act(async () => { container.querySelector('[aria-label="Tambah"]').click(); });
    api.updateHelperPrepItems.mockRejectedValueOnce(new Error("simpan gagal"));
    const save = [...container.querySelectorAll("button")].find((btn) => btn.textContent === "Simpan Persiapan");
    await act(async () => { save.click(); });
    await flush();
    expect(api.updateHelperPrepItems).toHaveBeenCalledTimes(1);
    expect(onUpdated).not.toHaveBeenCalled();
    expect(container.textContent).toContain("simpan gagal");
    await expectSummaryStillCached();

    api.updateHelperPrepItems.mockResolvedValueOnce({ order: { id: "po-1" } });
    await act(async () => { save.click(); });
    await flush();
    expect(onUpdated).toHaveBeenCalledTimes(1);
    await expectSummaryDropped();
  });

  test("MobilePackingWorkspace", async () => {
    const onUpdated = jest.fn();
    await seedSummary();
    await renderWorkspace(
      <MobilePackingWorkspace
        order={{
          id: "po-1",
          packing_lines: [{
            line_idx: 0,
            name: "Cup",
            is_print: true,
            production_process: "print",
            qty_po: 5,
            remaining: 5,
            packed: 0,
            available: 5,
          }],
        }}
        canEdit
        onUpdated={onUpdated}
      />,
    );
    await act(async () => { container.querySelector('[aria-label="Tambah"]').click(); });
    api.savePackingInfo.mockRejectedValueOnce(new Error("simpan gagal"));
    const save = [...container.querySelectorAll("button")].find((btn) => btn.textContent === "Simpan Packing");
    await act(async () => { save.click(); });
    await flush();
    expect(api.savePackingInfo).toHaveBeenCalledTimes(1);
    expect(onUpdated).not.toHaveBeenCalled();
    expect(container.textContent).toContain("simpan gagal");
    await expectSummaryStillCached();

    api.savePackingInfo.mockResolvedValueOnce({ order: { id: "po-1" } });
    await act(async () => { save.click(); });
    await flush();
    expect(onUpdated).toHaveBeenCalledTimes(1);
    await expectSummaryDropped();
  });

  test("MobilePrintWorkspace", async () => {
    const onUpdated = jest.fn();
    const order = {
      id: "po-print-1",
      workflow_ready: { print_qty: true },
      line_items: [{ name: "Wrap Paper", quantity: 2000, item_id: "item-0", production_process: "print" }],
      operator_splits: [{
        id: "sp-a",
        operator_email: "operator.a@x.com",
        operator_user_id: "user-a",
        operator_name: "operator.a@x.com",
        line_idx: 0,
        quantity: 1200,
        qty_completed: 0,
        progress_entries: [],
      }],
    };
    await seedSummary();
    await renderWorkspace(<MobilePrintWorkspace order={order} canEdit onUpdated={onUpdated} />);
    const date = container.querySelector('[data-testid="mobile-print-date-0"]');
    const qty = container.querySelector('input[aria-label="Qty"]');
    await act(async () => {
      setValue(date, "2026-10-04");
      setValue(qty, "10");
    });
    api.updateOperatorProgress.mockRejectedValueOnce(new Error("simpan gagal"));
    const save = [...container.querySelectorAll("button")].find((btn) => btn.textContent === "Simpan Hasil Cetak");
    await act(async () => { save.click(); });
    await flush();
    expect(api.updateOperatorProgress).toHaveBeenCalledTimes(1);
    expect(onUpdated).not.toHaveBeenCalled();
    expect(container.textContent).toContain("simpan gagal");
    await expectSummaryStillCached();

    api.updateOperatorProgress.mockResolvedValueOnce({ order });
    await act(async () => { save.click(); });
    await flush();
    expect(onUpdated).toHaveBeenCalledTimes(1);
    await expectSummaryDropped();
  });
});
