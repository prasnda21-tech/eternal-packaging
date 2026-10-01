import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { formatSalesRupiah } from "../lib/salesPwa";
import SalesPwaPredictionsPage from "./SalesPwaPredictionsPage";

const mockRouter = { params: {} };

jest.mock("react-router-dom", () => ({
  useParams: () => mockRouter.params,
  Link: ({ children, to, ...props }) => <a href={typeof to === "string" ? to : ""} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesPwaPredictions: jest.fn(),
    salesPwaPredictionDetail: jest.fn(),
    scheduleSalesPwaVisit: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const rows = [
  { customer_key: "id:RISK", customer_name: "Toko Risiko", status: "RISIKO", reason: "Perlu dicek", last_order_date: "2026-08-01", median_gap: 9, average_gap: 9, pattern: "MINGGUAN", prediction_center: "2026-08-10", prediction_start: "2026-08-07", prediction_end: "2026-08-13", days_late: 13 },
  { customer_key: "id:LATE", customer_name: "Toko Telat", status: "TERLAMBAT", reason: "Lewat 2 hari dari akhir perkiraan.", last_order_date: "2026-09-17", median_gap: 9, average_gap: 9, pattern: "MINGGUAN", prediction_center: "2026-09-26", prediction_start: "2026-09-23", prediction_end: "2026-09-29", days_late: 2 },
  { customer_key: "id:DUE", customer_name: "Toko Jatuh", status: "JATUH TEMPO", reason: "Hubungi sekarang", last_order_date: "2026-09-22", median_gap: 9, average_gap: 9, pattern: "MINGGUAN", prediction_center: "2026-10-01", prediction_start: "2026-09-28", prediction_end: "2026-10-04" },
  { customer_key: "id:THIN", customer_name: "Toko Tipis", status: "DATA BELUM CUKUP", reason: "Belum cukup data", last_order_date: "2026-09-30", median_gap: null, average_gap: null, pattern: "DATA BELUM CUKUP" },
];

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

async function renderPage() {
  const node = document.createElement("div");
  document.body.appendChild(node);
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaPredictionsPage />); });
  return { node, root };
}

beforeEach(() => {
  mockRouter.params = {};
  api.salesPwaPredictions.mockReset();
  api.salesPwaPredictionDetail.mockReset();
  api.scheduleSalesPwaVisit.mockReset();
});

test("loading is separate from the empty prediction list", async () => {
  const pending = deferred();
  api.salesPwaPredictions.mockReturnValue(pending.promise);
  const { node, root } = await renderPage();
  expect(node.querySelector('[data-testid="sales-pred-loading"]').textContent).toBe("Memuat prediksi...");
  expect(node.textContent).not.toContain("Tidak ada");
  await act(async () => { pending.resolve({ rows }); });
  expect(node.querySelector('[data-testid="sales-pred-loading"]')).toBeNull();
  expect(node.querySelector('[data-testid="sales-pred-row-id:RISK"]').textContent).toContain("Toko Risiko");
  act(() => root.unmount());
  node.remove();
});

test("renders an empty result only after the request finishes", async () => {
  api.salesPwaPredictions.mockResolvedValue({ rows: [] });
  const { node, root } = await renderPage();
  expect(node.querySelector('[data-testid="sales-pred-empty"]').textContent).toBe("Tidak ada prediksi pada PO Anda.");
  expect(node.querySelector('[data-testid="sales-pred-loading"]')).toBeNull();
  act(() => root.unmount());
  node.remove();
});

test("shows an error without pretending the list is empty", async () => {
  api.salesPwaPredictions.mockRejectedValue(new Error("jaringan putus"));
  const { node, root } = await renderPage();
  expect(node.querySelector('[data-testid="sales-pred-error"]').textContent).toContain("jaringan putus");
  expect(node.querySelector('[data-testid="sales-pred-empty"]')).toBeNull();
  act(() => root.unmount());
  node.remove();
});

test("lists predictions in the returned priority and filters by status", async () => {
  api.salesPwaPredictions.mockResolvedValue({ rows });
  const { node, root } = await renderPage();
  const text = node.querySelector('[data-testid="sales-pwa-predictions"]').textContent;
  expect(text.indexOf("Toko Risiko")).toBeLessThan(text.indexOf("Toko Telat"));
  expect(text.indexOf("Toko Telat")).toBeLessThan(text.indexOf("Toko Jatuh"));
  expect(text.indexOf("Toko Jatuh")).toBeLessThan(text.indexOf("Toko Tipis"));
  expect(node.textContent).toContain("Lewat 2 hari");
  expect(node.textContent).toContain("Terakhir order 22/09/26");
  expect(node.textContent).toContain("Biasanya setiap 9 hari");
  expect(node.textContent).toContain("Perkiraan 01/10/26");
  const late = Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "TERLAMBAT");
  await act(async () => { late.click(); });
  expect(node.textContent).toContain("Toko Telat");
  expect(node.textContent).not.toContain("Toko Risiko");
  expect(node.textContent).not.toContain("Toko Tipis");
  act(() => root.unmount());
  node.remove();
});

test("renders history, products, and a complete rupiah value", async () => {
  mockRouter.params = { customerKey: "id:DUE" };
  api.salesPwaPredictionDetail.mockResolvedValue({
    analysis: rows[2],
    value: { value_status: "complete", historical_order_value: 30000, average_order_value: 10000 },
    products: [
      { product_key: "a:CUP", name: "Paper Cup", unit: "pcs", purchase_count: 3, total_quantity: 17, quantity_complete: true, last_purchase_date: "2026-09-22" },
      { product_key: "a:DUS", name: "Dus", unit: "pcs", purchase_count: 1, total_quantity: 1, quantity_complete: true, last_purchase_date: "2026-09-04" },
    ],
    history: [
      { id: "3", order_date: "2026-09-22", po_number: "PO-NEW", outlet: "Gudang Utara", gap_days: 9, notes: "Catatan follow up", value_status: "complete", order_value: 10000 },
      { id: "2", order_date: "2026-09-13", po_number: "PO-MID", outlet: "Gudang Utara", gap_days: 9, value_status: "complete", order_value: 10000 },
      { id: "1", order_date: "2026-09-04", po_number: "PO-OLD", outlet: "Gudang Utara", gap_days: null, value_status: "complete", order_value: 10000 },
    ],
    agenda: { linked: false, outlets: [] },
  });
  const { node, root } = await renderPage();
  const history = node.querySelector('[data-testid="sales-pred-history"]').textContent;
  expect(history.indexOf("PO-NEW")).toBeLessThan(history.indexOf("PO-MID"));
  expect(history.indexOf("PO-MID")).toBeLessThan(history.indexOf("PO-OLD"));
  expect(history).toContain("Gudang Utara");
  expect(history).toContain("jeda 9 hari");
  expect(history).toContain("Catatan follow up");
  expect(history).toContain(formatSalesRupiah(10000));
  const products = node.querySelector('[data-testid="sales-pred-products"]').textContent;
  expect(products.indexOf("Paper Cup")).toBeLessThan(products.indexOf("Dus"));
  expect(products).toContain("3 order");
  expect(products).toContain("17");
  expect(node.querySelector('[data-testid="sales-pred-value"]').textContent).toContain(formatSalesRupiah(30000));
  expect(node.querySelector('[data-testid="sales-pred-value"]').textContent).toContain(formatSalesRupiah(10000));
  expect(node.querySelector('[data-testid="sales-pred-value-gap"]')).toBeNull();
  expect(node.querySelector('[data-testid="sales-pred-unlinked"]').textContent).toBe("Customer belum terhubung ke outlet.");
  expect(api.scheduleSalesPwaVisit).not.toHaveBeenCalled();
  act(() => root.unmount());
  node.remove();
});

test("shows incomplete price as unknown instead of rupiah zero", async () => {
  mockRouter.params = { customerKey: "id:PART" };
  api.salesPwaPredictionDetail.mockResolvedValue({
    analysis: { customer_key: "id:PART", customer_name: "Toko Sebagian", status: "JATUH TEMPO", reason: "Hubungi", last_order_date: "2026-09-22", median_gap: 9, prediction_center: "2026-10-01", prediction_start: "2026-09-28", prediction_end: "2026-10-04" },
    value: { value_status: "partial", historical_order_value: null, average_order_value: null, known_value: 2000 },
    products: [{ product_key: "name:gelas", name: "Gelas", unit: "pcs", purchase_count: 2, total_quantity: 4, quantity_complete: false, last_purchase_date: "2026-09-22" }],
    history: [{ id: "p", order_date: "2026-09-22", po_number: "PO-PART", outlet: "Gudang", gap_days: 9, value_status: "unknown", order_value: null }],
    agenda: { linked: false, outlets: [] },
  });
  const { node, root } = await renderPage();
  expect(node.querySelector('[data-testid="sales-pred-value"]').textContent).toContain("—");
  expect(node.querySelector('[data-testid="sales-pred-value"]').textContent).not.toContain("Rp0");
  expect(node.querySelector('[data-testid="sales-pred-value"]').textContent).not.toContain(formatSalesRupiah(2000));
  expect(node.querySelector('[data-testid="sales-pred-value-gap"]').textContent).toBe("Data harga tidak lengkap");
  expect(node.querySelector('[data-testid="sales-pred-products"]').textContent).toContain("qty tidak lengkap");
  expect(node.querySelector('[data-testid="sales-pred-history"]').textContent).not.toContain("Rp");
  act(() => root.unmount());
  node.remove();
});

test("asks for an outlet when several exact links exist and schedules only the selected one", async () => {
  mockRouter.params = { customerKey: "id:DUE" };
  api.salesPwaPredictionDetail.mockResolvedValue({
    analysis: rows[2],
    value: { value_status: "unknown", historical_order_value: null, average_order_value: null },
    products: [],
    history: [],
    agenda: {
      linked: true,
      outlets: [
        { outlet_id: "out-dua", name: "Outlet Dua", has_active_agenda: false, next_follow_up_at: null },
        { outlet_id: "out-satu", name: "Outlet Satu", has_active_agenda: false, next_follow_up_at: null },
      ],
    },
  });
  api.scheduleSalesPwaVisit.mockResolvedValue({ scheduled: true });
  const { node, root } = await renderPage();
  const button = node.querySelector('[data-testid="sales-pred-agenda-submit"]');
  expect(button.disabled).toBe(true);
  await act(async () => { button.click(); });
  expect(api.scheduleSalesPwaVisit).not.toHaveBeenCalled();
  const radio = node.querySelector('[data-testid="sales-pred-outlet-out-dua"] input');
  await act(async () => { radio.click(); });
  const input = node.querySelector('input[type="datetime-local"]');
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setter.call(input, "2026-10-06T09:00");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => { button.click(); });
  expect(api.scheduleSalesPwaVisit).toHaveBeenCalledWith({ outlet_id: "out-dua", next_follow_up_at: "2026-10-06T09:00" });
  expect(node.querySelector('[data-testid="sales-pred-notice"]').textContent).toBe("Ditambahkan ke Agenda");
  act(() => root.unmount());
  node.remove();
});

test("uses a provisional interval until three orders exist", async () => {
  api.salesPwaPredictions.mockResolvedValue({
    rows: [{
      customer_key: "id:TWO",
      customer_name: "Toko Dua",
      status: "DATA BELUM CUKUP",
      history_count: 2,
      reason: "Tambah histori sampai minimal 3 order.",
      last_order_date: "2026-09-22",
      median_gap: 10,
      average_gap: 10,
      pattern: "DATA BELUM CUKUP",
    }],
  });
  const { node, root } = await renderPage();
  expect(node.textContent).toContain("Jarak order sementara: 10 hari");
  expect(node.textContent).not.toContain("Biasanya setiap");
  act(() => root.unmount());
  node.remove();
});

test("hides agenda when the customer identity is only a name", async () => {
  mockRouter.params = { customerKey: "name:toko nama" };
  api.salesPwaPredictionDetail.mockResolvedValue({
    analysis: { customer_key: "name:toko nama", customer_name: "Toko Nama", status: "DATA BELUM CUKUP", last_order_date: "2026-09-22", median_gap: null },
    value: { value_status: "unknown" },
    products: [],
    history: [],
    agenda: { linked: false, identity: "name", customer_id: null, outlets: [] },
  });
  const { node, root } = await renderPage();
  expect(node.querySelector('[data-testid="sales-pred-identity"]').textContent).toBe("Customer belum memiliki identitas yang cukup untuk dihubungkan ke agenda.");
  expect(node.querySelector('[data-testid="sales-pred-agenda-submit"]')).toBeNull();
  expect(node.querySelector('[data-testid="sales-pred-unlinked"]')).toBeNull();
  act(() => root.unmount());
  node.remove();
});

test("requires confirmation again after the first agenda save", async () => {
  mockRouter.params = { customerKey: "id:DUE" };
  api.salesPwaPredictionDetail.mockResolvedValue({
    analysis: rows[2],
    value: { value_status: "unknown", historical_order_value: null, average_order_value: null },
    products: [],
    history: [],
    agenda: {
      linked: true,
      identity: "stable",
      outlets: [{ outlet_id: "out-satu", name: "Outlet Satu", has_active_agenda: false, next_follow_up_at: null }],
    },
  });
  let resolveSave;
  api.scheduleSalesPwaVisit.mockImplementation(() => new Promise((resolve) => { resolveSave = resolve; }));
  const { node, root } = await renderPage();
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  const fillTime = async (value) => {
    const input = node.querySelector('input[type="datetime-local"]');
    await act(async () => {
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  };
  await fillTime("2026-10-06T09:00");
  const button = node.querySelector('[data-testid="sales-pred-agenda-submit"]');
  await act(async () => { button.click(); button.click(); });
  expect(api.scheduleSalesPwaVisit).toHaveBeenCalledTimes(1);
  await act(async () => { resolveSave({ next_follow_up_at: "2026-10-06T02:00:00+00:00" }); });
  expect(node.querySelector('[data-testid="sales-pred-agenda-existing"]').textContent).toContain("06/10/26 09:00");
  expect(node.querySelector('[data-testid="sales-pred-agenda-submit"]').disabled).toBe(true);
  await fillTime("2026-10-09T11:30");
  await act(async () => { node.querySelector('[data-testid="sales-pred-agenda-submit"]').click(); });
  expect(api.scheduleSalesPwaVisit).toHaveBeenCalledTimes(1);
  await act(async () => { node.querySelector('[data-testid="sales-pred-agenda-confirm"]').click(); });
  api.scheduleSalesPwaVisit.mockResolvedValue({ next_follow_up_at: "2026-10-09T04:30:00+00:00" });
  await act(async () => { node.querySelector('[data-testid="sales-pred-agenda-submit"]').click(); });
  expect(api.scheduleSalesPwaVisit).toHaveBeenCalledTimes(2);
  expect(api.scheduleSalesPwaVisit).toHaveBeenLastCalledWith({ outlet_id: "out-satu", next_follow_up_at: "2026-10-09T11:30" });
  act(() => root.unmount());
  node.remove();
});

test("requires confirmation before replacing an active agenda", async () => {
  mockRouter.params = { customerKey: "id:DUE" };
  api.salesPwaPredictionDetail.mockResolvedValue({
    analysis: rows[2],
    value: { value_status: "unknown", historical_order_value: null, average_order_value: null },
    products: [],
    history: [],
    agenda: {
      linked: true,
      outlets: [{ outlet_id: "out-satu", name: "Outlet Satu", has_active_agenda: true, next_follow_up_at: "2026-10-02T02:00:00+00:00" }],
    },
  });
  api.scheduleSalesPwaVisit.mockResolvedValue({ scheduled: true });
  const { node, root } = await renderPage();
  expect(node.querySelector('[data-testid="sales-pred-agenda-existing"]').textContent).toContain("Outlet ini sudah memiliki agenda aktif.");
  expect(node.querySelector('[data-testid="sales-pred-agenda-existing"]').textContent).toContain("02/10/26 09:00");
  const button = node.querySelector('[data-testid="sales-pred-agenda-submit"]');
  expect(button.disabled).toBe(true);
  const input = node.querySelector('input[type="datetime-local"]');
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setter.call(input, "2026-10-06T09:00");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => { button.click(); });
  expect(api.scheduleSalesPwaVisit).not.toHaveBeenCalled();
  await act(async () => { node.querySelector('[data-testid="sales-pred-agenda-confirm"]').click(); });
  expect(node.querySelector('[data-testid="sales-pred-agenda-submit"]').disabled).toBe(false);
  await act(async () => { node.querySelector('[data-testid="sales-pred-agenda-submit"]').click(); });
  expect(api.scheduleSalesPwaVisit).toHaveBeenCalledWith({ outlet_id: "out-satu", next_follow_up_at: "2026-10-06T09:00" });
  expect(node.querySelector('[data-testid="sales-pred-notice"]').textContent).toBe("Ditambahkan ke Agenda");
  act(() => root.unmount());
  node.remove();
});
