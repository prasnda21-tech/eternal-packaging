import axios from "axios";
import { resolveBackendOrigin } from "./backendOrigin";
import { dedupeInflight } from "./dedupeInflight";

const pageHost = typeof window !== "undefined" ? window.location.hostname : "";
const API = `${resolveBackendOrigin(process.env.REACT_APP_BACKEND_URL, pageHost)}/api`;

const client = axios.create({
  baseURL: API,
  withCredentials: true,
});

export const fileUrl = (storagePath) => `${API}/files/${storagePath}`;

export const publicApiUrl = (path) => {
  if (!path) return "";
  if (/^https?:/i.test(path)) return path;
  const origin = API.replace(/\/api$/, "");
  return path.startsWith("/") ? `${origin}${path}` : `${origin}/${path}`;
};

const publicJsonRequest = async (path, options = {}) => {
  const response = await fetch(`${API}${path}`, {
    credentials: "omit",
    ...options,
  });
  let data = {};
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) throw { response: { status: response.status, data } };
  return data;
};

export const api = {
  login: (email, password) => client.post("/auth/login", { email, password }).then((r) => r.data),
  logout: () => client.post("/auth/logout", {}).then((r) => r.data),
  me: dedupeInflight(() => client.get("/auth/me").then((r) => r.data)),
  systemHealth: () => client.get("/system/health").then((r) => r.data),
  listOrders: (status, extra) => client.get("/orders", {
    params: {
      ...(status ? { status } : {}),
      ...(extra && typeof extra === "object" ? extra : {}),
    },
  }).then((r) => r.data),
  cancelPO: (id, reason) => client.post(`/orders/${id}/cancel`, { reason }).then((r) => r.data),
  deleteImpact: (id) => client.get(`/orders/${id}/delete-impact`).then((r) => r.data),
  deletePO: (id, reason, confirm) => client.delete(`/orders/${id}`, { data: { reason, confirm } }).then((r) => r.data),
  bulkCancel: (ids, reason) => client.post(`/orders-bulk/cancel`, { ids, reason }).then((r) => r.data),
  bulkDelete: (ids, reason, confirm) => client.post(`/orders-bulk/delete`, { ids, reason, confirm }).then((r) => r.data),
  getOrder: dedupeInflight(
    (id) => client.get(`/orders/${id}`).then((r) => r.data),
    (id) => `getOrder:${id}`,
  ),
  updateOrderInfo: (id, payload) => client.patch(`/orders/${id}/info`, payload).then((r) => r.data),
  advance: (id, payload = {}) => client.post(`/orders/${id}/advance`, payload).then((r) => r.data),
  approveDesign: (id, approved = true, note = "") => client.post(`/orders/${id}/approve-design`, { approved, note }).then((r) => r.data),
  approvePayment: (id, approved = true, note = "") => client.post(`/orders/${id}/approve-payment`, { approved, note }).then((r) => r.data),
  approveSample: (id, approved = true, note = "") => client.post(`/orders/${id}/approve-sample`, { approved, note }).then((r) => r.data),
  bypassDesign: (id, reason) => client.post(`/orders/${id}/design/bypass`, { reason }).then((r) => r.data),
  bypassPayment: (id, reason) => client.post(`/orders/${id}/payment/bypass`, { reason }).then((r) => r.data),
  rollbackStage: (id, to_stage, reason) => client.post(`/orders/${id}/rollback`, { to_stage, reason }).then((r) => r.data),
  updateHelperPrep: (id, payload) => client.post(`/orders/${id}/helper-prep`, payload).then((r) => r.data),
  updateHelperPrepItems: (id, items, notes = "") => client.post(`/orders/${id}/helper-prep/items`, { items, notes }).then((r) => r.data),
  updateScreenerInfo: (id, payload) => client.post(`/orders/${id}/screener-info`, payload).then((r) => r.data),
  assignOperators: (id, splits) => client.post(`/orders/${id}/assign-operators`, { splits }).then((r) => r.data),
  cancelOperatorSplit: (id, splitId, qty, reason = "") =>
    client.post(`/orders/${id}/operator-splits/${splitId}/cancel`, { qty, reason }).then((r) => r.data),
  setUrgent: (id, urgent) => client.post(`/orders/${id}/urgent`, { urgent }).then((r) => r.data),
  setBufferStock: (id, buffer_stock) => client.post(`/orders/${id}/buffer-stock`, { buffer_stock }).then((r) => r.data),
  addAttachment: (id, payload) => client.post(`/orders/${id}/attachment`, payload).then((r) => r.data),
  deleteDesignFile: (id, fileId) =>
    client.delete(`/orders/${id}/design/files/${encodeURIComponent(fileId)}`).then((r) => r.data),

  addNote: (id, note) => client.post(`/orders/${id}/note`, { note }).then((r) => r.data),
  confirmLoading: (id, payload) => client.post(`/orders/${id}/loading-confirm`, payload).then((r) => r.data),
  confirmLoadingMatch: (id, payload) => client.post(`/orders/${id}/loading-confirm-match`, payload).then((r) => r.data),
  reportLoadingDiscrepancy: (id, payload) => client.post(`/orders/${id}/loading-report-discrepancy`, payload).then((r) => r.data),
  deleteLoadingConfirm: (id, confirmationId) =>
    client.delete(`/orders/${id}/loading-confirm/${encodeURIComponent(confirmationId)}`).then((r) => r.data),
  updateLoading: (id, payload) => client.post(`/orders/${id}/loading`, payload).then((r) => r.data),
  markPacked: (id) => client.post(`/orders/${id}/packed`).then((r) => r.data),
  savePackingInfo: (id, linesOrQty, notes = "") => {
    const body = Array.isArray(linesOrQty)
      ? { lines: linesOrQty, notes }
      : { qty_packed: linesOrQty, notes };
    return client.post(`/orders/${id}/packing-info`, body).then((r) => r.data);
  },
  updateOrderWorkDate: (id, work_date) => client.patch(`/orders/${id}/work-date`, { work_date }).then((r) => r.data),
  updateOperatorWorkDate: (id, split_id, work_date) => client.patch(`/orders/${id}/operator-splits/${split_id}/work-date`, { work_date }).then((r) => r.data),
  createSuratJalan: (id, delivery_note_number = null, notes = "", lines = null) =>
    client.post(`/orders/${id}/surat-jalan`, {
      delivery_note_number,
      notes,
      ...(Array.isArray(lines) ? { lines } : {}),
    }).then((r) => r.data),
  deleteSuratJalanFile: (id, fileId) =>
    client.delete(`/orders/${id}/surat-jalan/files/${encodeURIComponent(fileId)}`).then((r) => r.data),
  confirmDelivered: (id, payload = {}) => client.post(`/orders/${id}/confirm-delivered`, payload).then((r) => r.data),
  syncAccurate: () => client.post(`/accurate/sync`).then((r) => r.data),
  accurateStatus: () => client.get(`/accurate/status`).then((r) => r.data),
  accurateList: (limit = 20, refresh = false, page = 1, keyword = "") =>
    client.get(`/accurate/list`, { params: { limit, refresh, page, keyword } }).then((r) => r.data),
  reminders: () => client.get(`/reminders`).then((r) => r.data),
  uploadDeliveryProof: (id, file, note, onProgress, shipmentId) => {
    const form = new FormData();
    form.append("file", file);
    if (note) form.append("note", note);
    if (shipmentId) form.append("shipment_id", shipmentId);
    return client.post(`/orders/${id}/delivery-proof`, form, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100));
      },
    }).then((r) => r.data);
  },
  deleteDeliveryProof: (id, proofId) => client.delete(`/orders/${id}/delivery-proof/${proofId}`).then((r) => r.data),
  uploadDeliveryRecipientSignature: (id, { recipientName, signatureDataUrl, shipmentId, sjFileId }) => {
    const form = new FormData();
    form.append("recipient_name", recipientName || "");
    if (signatureDataUrl) form.append("signature_data_url", signatureDataUrl);
    if (shipmentId) form.append("shipment_id", shipmentId);
    if (sjFileId) form.append("sj_file_id", sjFileId);
    return client.post(`/orders/${id}/delivery-recipient-signature`, form).then((r) => r.data);
  },
  clearDeliveryRecipientSignature: (id) =>
    client.delete(`/orders/${id}/delivery-recipient-signature`).then((r) => r.data),
  uploadDeliveryDriverSignature: (id, { signatureDataUrl, shipmentId, sjFileId }) => {
    const form = new FormData();
    if (signatureDataUrl) form.append("signature_data_url", signatureDataUrl);
    if (shipmentId) form.append("shipment_id", shipmentId);
    if (sjFileId) form.append("sj_file_id", sjFileId);
    return client.post(`/orders/${id}/delivery-driver-signature`, form).then((r) => r.data);
  },
  clearDeliveryDriverSignature: (id) =>
    client.delete(`/orders/${id}/delivery-driver-signature`).then((r) => r.data),
  analyticsOperators: () => client.get(`/analytics/operators`).then((r) => r.data),
  analyticsOperatorKpis: (params = {}) => client.get(`/analytics/operators/kpis`, { params }).then((r) => r.data),
  analyticsOperatorDetail: (operatorId, params = {}) =>
    client.get(`/analytics/operators/${encodeURIComponent(operatorId)}`, { params }).then((r) => r.data),
  analyticsPacking: (params = {}) => client.get(`/analytics/packing`, { params }).then((r) => r.data),
  analyticsPreparation: (params = {}) => client.get(`/analytics/preparation`, { params }).then((r) => r.data),
  analyticsScreener: (params = {}) => client.get(`/analytics/screener`, { params }).then((r) => r.data),
  analyticsDesigns: (params = {}) => client.get(`/analytics/designs`, { params }).then((r) => r.data),
  analyticsDriver: (params = {}) => client.get(`/analytics/driver`, { params }).then((r) => r.data),
  analyticsOperatorTargets: () => client.get(`/analytics/operator-targets`).then((r) => r.data),
  updateOperatorTargets: (payload) => client.put(`/analytics/operator-targets`, payload).then((r) => r.data),
  analyticsOutputExport: (params = {}) =>
    client.get(`/analytics/output/export`, { params, responseType: "blob" }).then((r) => r.data),
  gajiOperator: (params = {}) => client.get(`/reports/gaji-operator`, { params }).then((r) => r.data),
  gajiOperatorExport: (params = {}) =>
    client.get(`/reports/gaji-operator/export`, { params, responseType: "blob" }).then((r) => r.data),
  orderPrediction: (params = {}) => client.get(`/reports/order-prediction`, { params }).then((r) => r.data),
  orderPredictionDetail: (customerKey) =>
    client.get(`/reports/order-prediction/${encodeURIComponent(customerKey)}`).then((r) => r.data),
  orderPredictionImportPreview: (file) => {
    const form = new FormData();
    form.append("file", file);
    // Let the browser set multipart boundary (do not set headers manually).
    return client.post(`/reports/order-prediction/import/preview`, form, {
      timeout: 90000,
    }).then((r) => r.data);
  },
  orderPredictionImportApply: (rows) =>
    client.post(`/reports/order-prediction/import/apply`, { rows }).then((r) => r.data),
  operatorWageSettings: () => client.get(`/settings/operator-wage`).then((r) => r.data),
  updateOperatorWageSettings: (payload) => client.put(`/settings/operator-wage`, payload).then((r) => r.data),
  // Detail PO mounts two OperatorSplitPanels; collapse concurrent identical GETs.
  listOperators: dedupeInflight(() => client.get(`/users/operators`).then((r) => r.data)),
  dashboardSummary: () => client.get(`/dashboard/summary`).then((r) => r.data),
  dashboardToday: () => client.get(`/dashboard/today`).then((r) => r.data),
  updateOperatorProgress: (id, operatorId, qtyCompleted, extra = {}) =>
    client.post(`/orders/${id}/operator-progress`, {
      operator_id: operatorId,
      qty: extra.qty != null ? extra.qty : undefined,
      qty_completed: extra.qty == null ? qtyCompleted : undefined,
      action_id: extra.action_id,
    }).then((r) => r.data),
  accurateImportPreview: (soNumber) => client.post(`/accurate/import/preview`, { so_number: soNumber }).then((r) => r.data),
  accurateImportConfirm: (soNumber) => client.post(`/accurate/import/confirm`, { so_number: soNumber }).then((r) => r.data),
  accurateImportPreviewBulk: (soNumbers) =>
    client.post(`/accurate/import/preview-bulk`, { so_numbers: soNumbers }).then((r) => r.data),
  accurateImportConfirmBulk: (soNumbers) =>
    client.post(`/accurate/import/confirm-bulk`, { so_numbers: soNumbers }).then((r) => r.data),
  accurateImportLogs: () => client.get(`/accurate/import/logs`).then((r) => r.data),
  accurateImportAvailable: () => client.get(`/accurate/import/available`).then((r) => r.data),
  listSalesCustomers: (params = {}) => client.get(`/sales-customers`, { params }).then((r) => r.data),
  getSalesCustomer: (id) => client.get(`/sales-customers/${id}`).then((r) => r.data),
  createSalesCustomer: (payload) => client.post(`/sales-customers`, payload).then((r) => r.data),
  updateSalesCustomer: (id, payload) => client.put(`/sales-customers/${id}`, payload).then((r) => r.data),
  updateSalesCustomerByKey: (payload) => client.put(`/sales-customers/by-key`, payload).then((r) => r.data),
  setSalesCustomerStatus: (id, payload) => client.patch(`/sales-customers/${id}/status`, payload).then((r) => r.data),
  setSalesCustomerStatusByKey: (payload) => client.patch(`/sales-customers/by-key/status`, payload).then((r) => r.data),
  salesOrderOptions: () => client.get(`/sales-orders/options`).then((r) => r.data),
  createSalesOrder: (payload) => client.post(`/sales-orders`, payload).then((r) => r.data),
  listSalesOrders: (params = {}, config = {}) => client.get(`/sales-orders`, { params, ...config }).then((r) => r.data),
  salesOrderRepeatSource: (customerId, config = {}) =>
    client
      .get(`/sales-orders/repeat-source`, {
        params: { customer_id: customerId },
        ...config,
      })
      .then((r) => r.data),
  salesOrderRepeatSourceByOrder: (orderId, config = {}) =>
    client
      .get(`/sales-orders/repeat-source`, {
        params: { order_id: orderId },
        ...config,
      })
      .then((r) => r.data),
  masterItems: (params = {}) => client.get(`/master-items`, { params }).then((r) => r.data),
  masterItemsPicker: (params = {}) => client.get(`/master-items`, {
    params: {
      q: params.q ?? "",
      status: params.status ?? "",
      page_size: params.page_size,
      picker: true,
    },
  }).then((r) => r.data),
  masterItemsSync: () => client.post(`/master-items/sync`).then((r) => r.data),
  replaceSalesOrderLineProduct: (orderId, lineIdx, payload) =>
    client.patch(`/sales-orders/${orderId}/lines/${lineIdx}/product`, payload).then((r) => r.data),
  masterItemDetail: (id) => client.get(`/master-items/${encodeURIComponent(id)}/detail`).then((r) => r.data),
  saveUnitConversion: (id, payload) => client.put(`/master-items/${encodeURIComponent(id)}/unit-conversion`, payload).then((r) => r.data),
  initOpeningBalance: (id, payload) => client.post(`/master-items/${encodeURIComponent(id)}/opening-balance`, payload).then((r) => r.data),
  listProductMutations: (id, params = {}) => client.get(`/master-items/${encodeURIComponent(id)}/mutations`, { params }).then((r) => r.data),
  getMutationDetail: (id, mutationId) => client.get(`/master-items/${encodeURIComponent(id)}/mutations/${encodeURIComponent(mutationId)}`).then((r) => r.data),
  ledgerAccounts: (params = {}) => client.get(`/ledger/accounts`, { params }).then((r) => r.data),
  ledgerAccountTypes: () => client.get(`/ledger/account-types`).then((r) => r.data),
  createLedgerAccount: (payload) => client.post(`/ledger/accounts`, payload).then((r) => r.data),
  updateLedgerAccount: (id, payload) => client.patch(`/ledger/accounts/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  deleteLedgerAccount: (id) => client.delete(`/ledger/accounts/${encodeURIComponent(id)}`).then((r) => r.data),
  generalJournals: (params = {}) => client.get(`/ledger/general-journals`, { params }).then((r) => r.data),
  getGeneralJournal: (id) => client.get(`/ledger/general-journals/${encodeURIComponent(id)}`).then((r) => r.data),
  createGeneralJournal: (payload) => client.post(`/ledger/general-journals`, payload).then((r) => r.data),
  updateGeneralJournal: (id, payload) => client.patch(`/ledger/general-journals/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  deleteGeneralJournal: (id) => client.delete(`/ledger/general-journals/${encodeURIComponent(id)}`).then((r) => r.data),
  postGeneralJournal: (id) => client.post(`/ledger/general-journals/${encodeURIComponent(id)}/post`).then((r) => r.data),
  reverseGeneralJournal: (id) => client.post(`/ledger/general-journals/${encodeURIComponent(id)}/reverse`).then((r) => r.data),
  openingBalances: (params = {}) => client.get(`/ledger/opening-balances`, { params }).then((r) => r.data),
  getOpeningBalance: (id) => client.get(`/ledger/opening-balances/${encodeURIComponent(id)}`).then((r) => r.data),
  createOpeningBalance: (payload) => client.post(`/ledger/opening-balances`, payload).then((r) => r.data),
  updateOpeningBalance: (id, payload) => client.patch(`/ledger/opening-balances/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  deleteOpeningBalance: (id) => client.delete(`/ledger/opening-balances/${encodeURIComponent(id)}`).then((r) => r.data),
  postOpeningBalance: (id) => client.post(`/ledger/opening-balances/${encodeURIComponent(id)}/post`).then((r) => r.data),
  reverseOpeningBalance: (id) => client.post(`/ledger/opening-balances/${encodeURIComponent(id)}/reverse`).then((r) => r.data),
  generalLedger: (params = {}) => client.get(`/ledger/general-ledger`, { params }).then((r) => r.data),
  trialBalance: (params = {}) => client.get(`/ledger/trial-balance`, { params }).then((r) => r.data),
  profitLoss: (params = {}) => client.get(`/ledger/profit-loss`, { params }).then((r) => r.data),
  balanceSheet: (params = {}) => client.get(`/ledger/balance-sheet`, { params }).then((r) => r.data),
  cashBankAccounts: (params = {}) => client.get(`/cash-bank/accounts`, { params }).then((r) => r.data),
  getCashBankAccount: (id) => client.get(`/cash-bank/accounts/${encodeURIComponent(id)}`).then((r) => r.data),
  createCashBankAccount: (payload) => client.post(`/cash-bank/accounts`, payload).then((r) => r.data),
  updateCashBankAccount: (id, payload) => client.patch(`/cash-bank/accounts/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  cashBankReceipts: (params = {}) => client.get(`/cash-bank/receipts`, { params }).then((r) => r.data),
  getCashBankReceipt: (id) => client.get(`/cash-bank/receipts/${encodeURIComponent(id)}`).then((r) => r.data),
  createCashBankReceipt: (payload) => client.post(`/cash-bank/receipts`, payload).then((r) => r.data),
  updateCashBankReceipt: (id, payload) => client.patch(`/cash-bank/receipts/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  deleteCashBankReceipt: (id) => client.delete(`/cash-bank/receipts/${encodeURIComponent(id)}`).then((r) => r.data),
  postCashBankReceipt: (id) => client.post(`/cash-bank/receipts/${encodeURIComponent(id)}/post`).then((r) => r.data),
  reverseCashBankReceipt: (id) => client.post(`/cash-bank/receipts/${encodeURIComponent(id)}/reverse`).then((r) => r.data),
  cashBankPayments: (params = {}) => client.get(`/cash-bank/payments`, { params }).then((r) => r.data),
  getCashBankPayment: (id) => client.get(`/cash-bank/payments/${encodeURIComponent(id)}`).then((r) => r.data),
  createCashBankPayment: (payload) => client.post(`/cash-bank/payments`, payload).then((r) => r.data),
  updateCashBankPayment: (id, payload) => client.patch(`/cash-bank/payments/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  deleteCashBankPayment: (id) => client.delete(`/cash-bank/payments/${encodeURIComponent(id)}`).then((r) => r.data),
  postCashBankPayment: (id) => client.post(`/cash-bank/payments/${encodeURIComponent(id)}/post`).then((r) => r.data),
  reverseCashBankPayment: (id) => client.post(`/cash-bank/payments/${encodeURIComponent(id)}/reverse`).then((r) => r.data),
  cashBankTransfers: (params = {}) => client.get(`/cash-bank/transfers`, { params }).then((r) => r.data),
  getCashBankTransfer: (id) => client.get(`/cash-bank/transfers/${encodeURIComponent(id)}`).then((r) => r.data),
  createCashBankTransfer: (payload) => client.post(`/cash-bank/transfers`, payload).then((r) => r.data),
  updateCashBankTransfer: (id, payload) => client.patch(`/cash-bank/transfers/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  deleteCashBankTransfer: (id) => client.delete(`/cash-bank/transfers/${encodeURIComponent(id)}`).then((r) => r.data),
  postCashBankTransfer: (id) => client.post(`/cash-bank/transfers/${encodeURIComponent(id)}/post`).then((r) => r.data),
  reverseCashBankTransfer: (id) => client.post(`/cash-bank/transfers/${encodeURIComponent(id)}/reverse`).then((r) => r.data),
  cashBankReceiptAttachmentUrl: (id) => `${API}/cash-bank/receipts/${encodeURIComponent(id)}/attachment`,
  cashBankPaymentAttachmentUrl: (id) => `${API}/cash-bank/payments/${encodeURIComponent(id)}/attachment`,
  cashBankMutasi: (params = {}) => client.get(`/cash-bank/mutasi`, { params }).then((r) => r.data),
  cashBankReconciliations: (params = {}) => client.get(`/cash-bank/reconciliations`, { params }).then((r) => r.data),
  cashBankReconciliationWorkspace: (params = {}) => client.get(`/cash-bank/reconciliations/workspace`, { params }).then((r) => r.data),
  getCashBankReconciliation: (id) => client.get(`/cash-bank/reconciliations/${encodeURIComponent(id)}`).then((r) => r.data),
  createCashBankReconciliation: (payload) => client.post(`/cash-bank/reconciliations`, payload).then((r) => r.data),
  updateCashBankReconciliation: (id, payload) => client.patch(`/cash-bank/reconciliations/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  setCashBankReconciliationMatches: (id, journal_line_ids) => client.put(`/cash-bank/reconciliations/${encodeURIComponent(id)}/matches`, { journal_line_ids }).then((r) => r.data),
  deleteCashBankReconciliation: (id) => client.delete(`/cash-bank/reconciliations/${encodeURIComponent(id)}`).then((r) => r.data),
  finalizeCashBankReconciliation: (id) => client.post(`/cash-bank/reconciliations/${encodeURIComponent(id)}/finalize`).then((r) => r.data),
  currentStock: (params = {}) => client.get(`/current-stock`, { params }).then((r) => r.data),
  currentStockUpdate: (itemId, payload) => client.patch(`/current-stock/${itemId}`, payload).then((r) => r.data),
  currentStockExport: (params = {}) =>
    client.get(`/current-stock/export`, { params, responseType: "blob" }).then((r) => r.data),
  stockOpnameItems: (params = {}) => client.get(`/stock-opname/items`, { params }).then((r) => r.data),
  stockOpnameExportTemplate: () =>
    client.get(`/stock-opname/export-template`, { responseType: "blob" }).then((r) => r.data),
  stockOpnameImportPreview: (file) => {
    const form = new FormData();
    form.append("file", file);
    return client.post(`/stock-opname/import/preview`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  stockOpnameImportApply: (payload) =>
    client.post(`/stock-opname/import/apply`, payload).then((r) => r.data),
  currentStockImportPreview: (file) => {
    const form = new FormData();
    form.append("file", file);
    return client.post(`/current-stock/import/preview`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  currentStockImportApply: (rows) =>
    client.post(`/current-stock/import/apply`, { rows }).then((r) => r.data),
  purchaseReceipts: (params = {}) => client.get(`/purchase-receipts`, { params }).then((r) => r.data),
  purchaseReceiptsSync: () => client.post(`/purchase-receipts/sync`).then((r) => r.data),
  createPurchaseReceipt: (payload) => client.post(`/purchase-receipts`, payload).then((r) => r.data),
  cancelManualPurchaseReceipt: (id, reason) =>
    client.post(`/purchase-receipts/${encodeURIComponent(id)}/cancel`, { reason }).then((r) => r.data),
  purchaseReceiptsExport: (params = {}) =>
    client.get(`/purchase-receipts/export`, { params, responseType: "blob" }).then((r) => r.data),
  listPurchaseRequests: (params = {}) => client.get(`/purchase-requests`, { params }).then((r) => r.data),
  getPurchaseRequest: (id) => client.get(`/purchase-requests/${encodeURIComponent(id)}`).then((r) => r.data),
  createPurchaseRequest: (payload) => client.post(`/purchase-requests`, payload).then((r) => r.data),
  purchaseRequestOptions: () => client.get(`/purchase-requests/options`).then((r) => r.data),
  listPurchaseOrders: (params = {}) => client.get(`/purchase-orders`, { params }).then((r) => r.data),
  getPurchaseOrder: (id) => client.get(`/purchase-orders/${encodeURIComponent(id)}`).then((r) => r.data),
  purchaseOrderPreview: (requestId) => client.get(`/purchase-orders/from-request/${encodeURIComponent(requestId)}/preview`).then((r) => r.data),
  createPurchaseOrder: (payload) => client.post(`/purchase-orders`, payload).then((r) => r.data),
  listPurchaseGoodsReceipts: (params = {}) => client.get(`/purchase-goods-receipts`, { params }).then((r) => r.data),
  purchaseGoodsReceiptPreview: (poId) => client.get(`/purchase-goods-receipts/from-po/${encodeURIComponent(poId)}/preview`).then((r) => r.data),
  createPurchaseGoodsReceipt: (payload) => client.post(`/purchase-goods-receipts`, payload).then((r) => r.data),
  allocatePurchaseReceipt: (payload) => client.post(`/purchase-receipts/allocate`, payload).then((r) => r.data),
  listPurchaseInvoices: (params = {}) => client.get(`/purchase-invoices`, { params }).then((r) => r.data),
  getPurchaseInvoice: (id) => client.get(`/purchase-invoices/${encodeURIComponent(id)}`).then((r) => r.data),
  purchaseInvoicePreview: (poId) => client.get(`/purchase-invoices/from-po/${encodeURIComponent(poId)}/preview`).then((r) => r.data),
  createPurchaseInvoice: (payload) => client.post(`/purchase-invoices`, payload).then((r) => r.data),
  listPurchasePayments: (params = {}) => client.get(`/purchase-payments`, { params }).then((r) => r.data),
  getPurchasePayment: (id) => client.get(`/purchase-payments/${encodeURIComponent(id)}`).then((r) => r.data),
  createPurchasePayment: (payload) => client.post(`/purchase-payments`, payload).then((r) => r.data),

  cancelPurchaseRequest: (id, reason = "") =>
    client.post(`/purchase-requests/${encodeURIComponent(id)}/cancel`, reason ? { reason } : {}).then((r) => r.data),
  cancelPurchaseOrder: (id, reason = "") =>
    client.post(`/purchase-orders/${encodeURIComponent(id)}/cancel`, reason ? { reason } : {}).then((r) => r.data),
  getPurchaseGoodsReceipt: (id) => client.get(`/purchase-goods-receipts/${encodeURIComponent(id)}`).then((r) => r.data),
  cancelPurchaseGoodsReceipt: (id, reason = "") =>
    client.post(`/purchase-goods-receipts/${encodeURIComponent(id)}/cancel`, reason ? { reason } : {}).then((r) => r.data),
  purchaseInvoiceFromReceiptPreview: (receiptId) => client.get(`/purchase-invoices/from-receipt/${encodeURIComponent(receiptId)}/preview`).then((r) => r.data),
  cancelPurchaseInvoice: (id, reason = "") =>
    client.post(`/purchase-invoices/${encodeURIComponent(id)}/cancel`, reason ? { reason } : {}).then((r) => r.data),
  cancelPurchasePayment: (id, reason = "") =>
    client.post(`/purchase-payments/${encodeURIComponent(id)}/cancel`, reason ? { reason } : {}).then((r) => r.data),
  listPurchasePaymentSubmitTargets: (params = {}) =>
    client.get(`/purchase-payments/submit-targets`, { params }).then((r) => r.data),
  submitPurchasePayment: (id, payload) =>
    client.post(`/purchase-payments/${encodeURIComponent(id)}/submit`, payload).then((r) => r.data),
  ajukanPurchasePayment: (payload) =>
    client.post(`/purchase-payments/ajukan`, payload).then((r) => r.data),
  confirmPurchasePaymentPaid: (id) =>
    client.post(`/purchase-payments/${encodeURIComponent(id)}/confirm-paid`, {}).then((r) => r.data),
  listPurchaseDocuments: (params = {}) =>
    client.get(`/purchase-documents`, { params }).then((r) => r.data),
  uploadPurchaseDocument: ({ entity_type, entity_id, document_name, file }) => {
    const form = new FormData();
    form.append("entity_type", entity_type);
    form.append("entity_id", entity_id);
    if (document_name) form.append("document_name", document_name);
    form.append("file", file);
    return client
      .post(`/purchase-documents`, form, { headers: { "Content-Type": "multipart/form-data" } })
      .then((r) => r.data);
  },
  fetchPurchaseDocumentBlob: (id, { download = false } = {}) =>
    client
      .get(`/purchase-documents/${encodeURIComponent(id)}/content`, {
        params: { download: download ? true : false },
        responseType: "blob",
      })
      .then((r) => r.data),
  deletePurchaseDocument: (id) =>
    client.delete(`/purchase-documents/${encodeURIComponent(id)}`).then((r) => r.data),
  hppSettings: () => client.get(`/settings/hpp`).then((r) => r.data),
  updateHppSettings: (payload) => client.put(`/settings/hpp`, payload).then((r) => r.data),
  physicalStockSettings: () => client.get(`/settings/physical-stock`).then((r) => r.data),
  updatePhysicalStockSettings: (payload) => client.put(`/settings/physical-stock`, payload).then((r) => r.data),
  listNotifications: (params = {}) => client.get(`/notifications`, { params }).then((r) => r.data),
  markNotificationRead: (id) => client.post(`/notifications/${encodeURIComponent(id)}/read`).then((r) => r.data),
  customerDesignApprovalNotificationSettings: () => client.get(`/settings/notifications/customer-design-approval`).then((r) => r.data),
  updateCustomerDesignApprovalNotificationSettings: (payload) => client.put(`/settings/notifications/customer-design-approval`, payload).then((r) => r.data),
  salesAccountingSettings: () => client.get(`/settings/sales-accounting`).then((r) => r.data),
  updateSalesAccountingSettings: (payload) => client.put(`/settings/sales-accounting`, payload).then((r) => r.data),
  payrollAccountingSettings: () => client.get(`/settings/payroll-accounting`).then((r) => r.data),
  updatePayrollAccountingSettings: (payload) => client.put(`/settings/payroll-accounting`, payload).then((r) => r.data),
  hrPayrollAccountingReadiness: (id) => client.get(`/hr-payroll/periods/${encodeURIComponent(id)}/accounting-readiness`).then((r) => r.data),
  recalculateHpp: (payload = {}) => client.post(`/settings/hpp/recalculate`, payload).then((r) => r.data),
  hppCostQuery: (params = {}) => client.get(`/hpp/cost-query`, { params }).then((r) => r.data),
  customerPricingSettings: () => client.get(`/settings/customer-pricing`).then((r) => r.data),
  updateCustomerPricingSettings: (payload) => client.put(`/settings/customer-pricing`, payload).then((r) => r.data),
  customerPricingGroups: (params = {}) => client.get(`/customer-pricing/groups`, { params }).then((r) => r.data),
  createCustomerPricingGroup: (payload) => client.post(`/customer-pricing/groups`, payload).then((r) => r.data),
  customerPricingGroup: (id) => client.get(`/customer-pricing/groups/${encodeURIComponent(id)}`).then((r) => r.data),
  patchCustomerPricingGroup: (id, payload) => client.patch(`/customer-pricing/groups/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  customerPricingCatalogImageUrl: (id, version = "") =>
    `${API}/customer-pricing/groups/${encodeURIComponent(id)}/catalog-image${version ? `?v=${encodeURIComponent(version)}` : ""}`,
  uploadCustomerPricingCatalogImage: (id, file) => {
    const form = new FormData();
    form.append("file", file);
    return client.post(
      `/customer-pricing/groups/${encodeURIComponent(id)}/catalog-image`,
      form,
      { headers: { "Content-Type": "multipart/form-data" } }
    ).then((r) => r.data);
  },
  deleteCustomerPricingCatalogImage: (id) =>
    client.delete(`/customer-pricing/groups/${encodeURIComponent(id)}/catalog-image`).then((r) => r.data),
  customerPricingProductImageUrl: (groupId, accurateItemId, version = "") =>
    `${API}/customer-pricing/groups/${encodeURIComponent(groupId)}/products/${encodeURIComponent(accurateItemId)}/image${version ? `?v=${encodeURIComponent(version)}` : ""}`,
  uploadCustomerPricingProductImage: (groupId, accurateItemId, file) => {
    const form = new FormData();
    form.append("file", file);
    return client.post(
      `/customer-pricing/groups/${encodeURIComponent(groupId)}/products/${encodeURIComponent(accurateItemId)}/image`,
      form,
      { headers: { "Content-Type": "multipart/form-data" } }
    ).then((r) => r.data);
  },
  deleteCustomerPricingProductImage: (groupId, accurateItemId) =>
    client.delete(`/customer-pricing/groups/${encodeURIComponent(groupId)}/products/${encodeURIComponent(accurateItemId)}/image`).then((r) => r.data),
  createCustomerPricingDraft: (id) => client.post(`/customer-pricing/groups/${encodeURIComponent(id)}/draft`).then((r) => r.data),
  customerPricingVersions: (id) => client.get(`/customer-pricing/groups/${encodeURIComponent(id)}/versions`).then((r) => r.data),
  customerPricingVersion: (id) => client.get(`/customer-pricing/versions/${encodeURIComponent(id)}`).then((r) => r.data),
  updateCustomerPricingVersion: (id, payload) => client.put(`/customer-pricing/versions/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  refreshCustomerPricingHpp: (id) => client.post(`/customer-pricing/versions/${encodeURIComponent(id)}/refresh-hpp`).then((r) => r.data),
  previewCustomerPricingVersion: (id, payload = {}) => client.post(`/customer-pricing/versions/${encodeURIComponent(id)}/preview`, payload).then((r) => r.data),
  publishCustomerPricingVersion: (id, payload = {}) => client.post(`/customer-pricing/versions/${encodeURIComponent(id)}/publish`, payload).then((r) => r.data),
  importFilePreview: (file, onProgress) => {
    const form = new FormData();
    form.append("file", file);
    return client.post(`/import/file/preview`, form, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100));
      },
    }).then((r) => r.data);
  },
  importFileConfirm: (preview, source = "file") => client.post(`/import/file/confirm`, { preview, source }).then((r) => r.data),
  importPdfPreview: (file, onProgress) => {
    const form = new FormData();
    form.append("file", file);
    return client.post(`/import/pdf/preview`, form, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100));
      },
    }).then((r) => r.data);
  },
  // ---- Admin: employees, roles, audit ----
  adminListUsers: (params = {}) => client.get(`/admin/users`, { params }).then((r) => r.data),
  adminCreateUser: (payload) => client.post(`/admin/users`, payload).then((r) => r.data),
  adminUpdateUser: (id, payload) => client.put(`/admin/users/${id}`, payload).then((r) => r.data),
  adminDeactivateUser: (id) => client.post(`/admin/users/${id}/deactivate`).then((r) => r.data),
  adminActivateUser: (id) => client.post(`/admin/users/${id}/activate`).then((r) => r.data),
  adminResetPassword: (id, newPassword) => client.post(`/admin/users/${id}/reset-password`, { new_password: newPassword }).then((r) => r.data),
  adminUserEffectivePerms: (id) => client.get(`/admin/users/${id}/permissions/effective`).then((r) => r.data),
  adminResetUserOverride: (id) => client.post(`/admin/users/${id}/permissions/reset-override`).then((r) => r.data),
  orderSetWorkDate: (id, workDate, note) => client.patch(`/orders/${id}/work-date`, { work_date: workDate, note }).then((r) => r.data),
  orderSplitSetWorkDate: (id, splitId, workDate) => client.patch(`/orders/${id}/operator-splits/${splitId}/work-date`, { work_date: workDate }).then((r) => r.data),
  adminListRoles: () => client.get(`/admin/roles`).then((r) => r.data),
  adminCreateRole: (payload) => client.post(`/admin/roles`, payload).then((r) => r.data),
  adminUpdateRole: (role, payload) => client.patch(`/admin/roles/${role}`, payload).then((r) => r.data),
  adminUpdateRolePermissions: (role, permissions) => client.put(`/admin/roles/${role}/permissions`, { permissions }).then((r) => r.data),
  adminDeactivateRole: (role) => client.post(`/admin/roles/${role}/deactivate`).then((r) => r.data),
  adminActivateRole: (role) => client.post(`/admin/roles/${role}/activate`).then((r) => r.data),
  adminDeleteRole: (role) => client.delete(`/admin/roles/${role}`).then((r) => r.data),
  adminGrantSuperAdmin: (id) => client.post(`/admin/users/${id}/super-admin/grant`).then((r) => r.data),
  adminRevokeSuperAdmin: (id) => client.post(`/admin/users/${id}/super-admin/revoke`).then((r) => r.data),
  adminTransferSuperAdmin: (fromId, toUserId, reason) => client.post(`/admin/users/${fromId}/super-admin/transfer`, { to_user_id: toUserId, reason }).then((r) => r.data),
  adminPermissionCatalog: () => client.get(`/admin/permissions/catalog`).then((r) => r.data),
  adminAuditLogs: (params = {}) => client.get(`/admin/audit-logs`, { params }).then((r) => r.data),
  // ---- Production dashboard (Req 6) ----
  productionDashboard: (params = {}) => client.get(`/dashboard/production`, { params }).then((r) => r.data),
  shipmentStatus: (params = {}) => client.get(`/dashboard/shipment-status`, { params }).then((r) => r.data),
  statusPo: (params = {}) => client.get(`/dashboard/status-po`, { params }).then((r) => r.data),
  productionExport: (payload = { scope: "filtered" }) => client.post(`/dashboard/production/export`, payload, { responseType: "blob" }).then((r) => r.data),
  // ---- Batch 3: Production routing + Action Center ----
  setLineItemRoute: (id, items) => client.post(`/orders/${id}/line-items/route`, { items }).then((r) => r.data),
  dashboardActionItems: () => client.get(`/dashboard/action-items`).then((r) => r.data),
  divisionTasks: (division, scope = "mine") => client.get(`/work/division-tasks`, { params: { division, scope } }).then((r) => r.data),
  // ---- Delivery Deadline (Batch 1) ----
  deliveryDeadlineSettings: () => client.get(`/delivery-deadline/settings`).then((r) => r.data),
  updateDeliveryDeadlineSettings: (payload) => client.put(`/delivery-deadline/settings`, payload).then((r) => r.data),
  workPanelSettings: () => client.get(`/work-panel/settings`).then((r) => r.data),
  updateWorkPanelSettings: (payload) => client.put(`/work-panel/settings`, payload).then((r) => r.data),
  accurateCreatorSettings: () => client.get(`/settings/accurate-creators`).then((r) => r.data),
  updateAccurateCreatorSettings: (payload) => client.put(`/settings/accurate-creators`, payload).then((r) => r.data),
  scanAccurateCreators: (limit = 100) => client.post(`/settings/accurate-creators/scan`, { limit }).then((r) => r.data),
  verifyAccurateCreator: (payload) => client.post(`/settings/accurate-creators/verify`, payload).then((r) => r.data),
  setDeliveryDeadlineManual: (id, deadlineAt, reason = "") => client.post(`/orders/${id}/delivery-deadline`, { deadline_at: deadlineAt, reason }).then((r) => r.data),
  resetDeliveryDeadlineManual: (id) => client.post(`/orders/${id}/delivery-deadline/reset`).then((r) => r.data),
  // ---- Customer Design Approval ----
  customerApprovalSettings: () => client.get(`/customer-approval/settings`).then((r) => r.data),
  updateCustomerApprovalSettings: (payload) => client.put(`/customer-approval/settings`, payload).then((r) => r.data),
  generateCustomerApproval: (id, payload = {}) => client.post(`/orders/${id}/customer-approval/generate`, payload).then((r) => r.data),
  revokeCustomerApproval: (id, reason = "") => client.post(`/orders/${id}/customer-approval/revoke`, { reason }).then((r) => r.data),
  // Public — TIDAK pakai withCredentials, kirim langsung via axios agar tidak
  // menyertakan cookie internal user (customer harusnya anonim).
  publicCustomerApprovalView: async (token) => {
    const r = await fetch(`${API}/public/customer-approval/${encodeURIComponent(token)}`);
    const d = await r.json();
    if (!r.ok) throw { response: { status: r.status, data: d } };
    return d;
  },
  publicCustomerApprovalDesignUrl: (token) => {
    if (!token) return "";
    return `${API}/public/customer-approval/${encodeURIComponent(token)}/design`;
  },
  publicCustomerApprovalSubmit: async (token, payload) => {
    const r = await fetch(`${API}/public/customer-approval/${encodeURIComponent(token)}/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (!r.ok) throw { response: { status: r.status, data: d } };
    return d;
  },
  publicCustomerPricingGroups: () =>
    publicJsonRequest("/public/customer-pricing/groups"),
  publicCustomerPricingGroup: (publicId) =>
    publicJsonRequest(`/public/customer-pricing/groups/${encodeURIComponent(publicId)}`),
  calculatePublicCustomerPrice: (payload) =>
    publicJsonRequest("/public/customer-pricing/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        group_public_id: payload.group_public_id,
        public_variant_id: payload.public_variant_id,
        quantity: payload.quantity,
      }),
    }),
  orderTrackingLink: (id) => client.post(`/orders/${id}/tracking-link`).then((r) => r.data),
  deliverySchedule: (params = {}) => client.get(`/delivery/schedule`, { params }).then((r) => r.data),
  deliveryReschedules: (params = {}) => client.get(`/delivery/reschedules`, { params }).then((r) => r.data),
  requestDeliveryReschedule: (id, payload) => client.post(`/orders/${id}/delivery-reschedule`, payload).then((r) => r.data),
  setDeliveryRescheduleDate: (rescheduleId, payload) => client.patch(`/delivery/reschedules/${encodeURIComponent(rescheduleId)}`, payload).then((r) => r.data),
  listDeliveryBatches: () => client.get(`/delivery/batches`).then((r) => r.data),
  getDeliveryBatch: (id) => client.get(`/delivery/batches/${encodeURIComponent(id)}`).then((r) => r.data),
  patchDeliveryBatch: (id, payload) => client.patch(`/delivery/batches/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  searchDeliveryPlaces: (q) => client.get(`/delivery/places/search`, { params: { q } }).then((r) => r.data),
  deliveryPlaceDetails: (placeId) => client.get(`/delivery/places/details`, { params: { place_id: placeId } }).then((r) => r.data),
  resolveDeliveryMapsLink: (url) => client.post(`/delivery/places/resolve-maps-link`, { url }).then((r) => r.data),
  listDeliveryOutlets: (customer) => client.get(`/delivery/outlets`, { params: customer ? { customer } : {} }).then((r) => r.data),
  saveDeliveryOutlet: (payload) => client.post(`/delivery/outlets`, payload).then((r) => r.data),
  patchDeliveryOutlet: (id, payload) => client.patch(`/delivery/outlets/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  publicTracking: async (token) => {
    const r = await fetch(`${API}/public/tracking/${encodeURIComponent(token)}`);
    let d = {};
    try { d = await r.json(); } catch { d = {}; }
    if (!r.ok) throw { response: { status: r.status, data: d } };
    return d;
  },
  // ---- Sales Delivery (Pengiriman Pesanan) ----
  listSalesDeliveries: (params = {}) => client.get(`/sales-deliveries`, { params }).then((r) => r.data),
  getSalesDelivery: (id) => client.get(`/sales-deliveries/${encodeURIComponent(id)}`).then((r) => r.data),
  getPoDeliverySummary: (poId) => client.get(`/sales-deliveries/po/${encodeURIComponent(poId)}/summary`).then((r) => r.data),
  createSalesDelivery: (payload) => client.post(`/sales-deliveries`, payload).then((r) => r.data),
  salesDeliveryPdfUrl: (id) => `${API}/sales-deliveries/${encodeURIComponent(id)}/pdf`,
  // ---- Sales Invoice (Faktur Penjualan) ----

  downloadSalesInvoicePdf: (id) => client.get(`/sales-invoices/${encodeURIComponent(id)}/pdf`, { responseType: "blob" }).then((r) => r.data),
  downloadSalesReceiptPdf: (id) => client.get(`/sales-receipts/${encodeURIComponent(id)}/pdf`, { responseType: "blob" }).then((r) => r.data),
  downloadSalesReceiptPaymentProof: (id, download = false) =>
    client
      .get(`/sales-receipts/${encodeURIComponent(id)}/payment-proof`, {
        params: download ? { download: true } : {},
        responseType: "blob",
      })
      .then((r) => r.data),

  listSalesInvoices: (params = {}) => client.get(`/sales-invoices`, { params }).then((r) => r.data),
  getSalesInvoice: (id) => client.get(`/sales-invoices/${encodeURIComponent(id)}`).then((r) => r.data),
  getPoInvoicePreview: (poId) => client.get(`/sales-invoices/po/${encodeURIComponent(poId)}/preview`).then((r) => r.data),
  createSalesInvoice: (payload) => client.post(`/sales-invoices`, payload).then((r) => r.data),
  updateSalesInvoice: (id, payload) => client.put(`/sales-invoices/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  finalizeSalesInvoice: (id) => client.post(`/sales-invoices/${encodeURIComponent(id)}/finalize`).then((r) => r.data),
  cancelSalesInvoice: (id) => client.post(`/sales-invoices/${encodeURIComponent(id)}/cancel`).then((r) => r.data),
  postSalesInvoiceAccounting: (id) => client.post(`/sales-invoices/${encodeURIComponent(id)}/post-accounting`).then((r) => r.data),
  // ---- Sales Receipt (Penerimaan Penjualan) ----
  listSalesReceipts: dedupeInflight(
    (params = {}) => client.get(`/sales-receipts`, { params }).then((r) => r.data),
    (params = {}) => `listSalesReceipts:${JSON.stringify(params || {})}`,
  ),
  getSalesReceipt: (id) => client.get(`/sales-receipts/${encodeURIComponent(id)}`).then((r) => r.data),
  createSalesReceipt: (payload) => client.post(`/sales-receipts`, payload).then((r) => r.data),
  attachSalesReceiptPaymentProof: (id, paymentProof) =>
    client.post(`/sales-receipts/${encodeURIComponent(id)}/payment-proof`, { payment_proof: paymentProof }).then((r) => r.data),
  // ---- Delete (Safe) ----
  deleteSalesDelivery: (id) => client.delete(`/sales-deliveries/${encodeURIComponent(id)}`).then((r) => r.data),
  bulkDeleteSalesDeliveries: (ids, reason = "bulk") => client.post(`/sales-deliveries-bulk/delete`, { ids, reason }).then((r) => r.data),
  deleteSalesInvoice: (id) => client.delete(`/sales-invoices/${encodeURIComponent(id)}`).then((r) => r.data),
  bulkDeleteSalesInvoices: (ids, reason = "bulk") => client.post(`/sales-invoices-bulk/delete`, { ids, reason }).then((r) => r.data),
  deleteSalesReceipt: (id) => client.delete(`/sales-receipts/${encodeURIComponent(id)}`).then((r) => r.data),
  bulkDeleteSalesReceipts: (ids, reason = "bulk") => client.post(`/sales-receipts-bulk/delete`, { ids, reason }).then((r) => r.data),
  employeeAdminMeta: () => client.get(`/employee-admin/meta`).then((r) => r.data),
  employeeAdminTypes: (includeInactive = true) => client.get(`/employee-admin/types`, { params: { include_inactive: includeInactive } }).then((r) => r.data),
  employeeAdminEligibleTypes: () => client.get(`/employee-admin/types/eligible`).then((r) => r.data),
  employeeAdminGetType: (id) => client.get(`/employee-admin/types/${encodeURIComponent(id)}`).then((r) => r.data),
  employeeAdminCreateType: (payload) => client.post(`/employee-admin/types`, payload).then((r) => r.data),
  employeeAdminUpdateType: (id, payload) => client.put(`/employee-admin/types/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  employeeAdminRequests: (scope = "mine", q = "") => client.get(`/employee-admin/requests`, { params: { scope, q } }).then((r) => r.data),
  employeeAdminGetRequest: (id) => client.get(`/employee-admin/requests/${encodeURIComponent(id)}`).then((r) => r.data),
  employeeAdminSubmitRequest: (payload) => client.post(`/employee-admin/requests`, payload).then((r) => r.data),
  employeeAdminDecide: (id, payload) => client.post(`/employee-admin/requests/${encodeURIComponent(id)}/decide`, payload).then((r) => r.data),
  employeeAdminUpload: (file, fieldKey = "") => {
    const form = new FormData();
    form.append("file", file);
    form.append("field_key", fieldKey);
    return client.post(`/employee-admin/uploads`, form).then((r) => r.data);
  },
  employeeAdminFileUrl: (requestId, fileId) => `${API}/employee-admin/requests/${encodeURIComponent(requestId)}/files/${encodeURIComponent(fileId)}`,
  employeeAdminDocumentUrl: (requestId) => `${API}/employee-admin/requests/${encodeURIComponent(requestId)}/document`,
  hrPayrollMeta: () => client.get(`/hr-payroll/meta`).then((r) => r.data),
  hrPayrollEmployees: (params = {}) => client.get(`/hr-payroll/employees`, { params }).then((r) => r.data),
  hrPayrollEmployee: (id) => client.get(`/hr-payroll/employees/${encodeURIComponent(id)}`).then((r) => r.data),
  hrPayrollCreateEmployee: (payload) => client.post(`/hr-payroll/employees`, payload).then((r) => r.data),
  hrPayrollCreateEternalAccount: (employeeId, payload) => client.post(`/hr-payroll/employees/${encodeURIComponent(employeeId)}/eternal-account`, payload).then((r) => r.data),
  hrPayrollUpdateEmployee: (id, payload) => client.put(`/hr-payroll/employees/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  hrPayrollComponents: (params = {}) => client.get(`/hr-payroll/components`, { params }).then((r) => r.data),
  hrPayrollCreateComponent: (payload) => client.post(`/hr-payroll/components`, payload).then((r) => r.data),
  hrPayrollUpdateComponent: (id, payload) => client.put(`/hr-payroll/components/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  hrPayrollSettings: () => client.get(`/hr-payroll/settings`).then((r) => r.data),
  hrPayrollUpdateSettings: (payload) => client.put(`/hr-payroll/settings`, payload).then((r) => r.data),
  hrPayrollPeriods: (params = {}) => client.get(`/hr-payroll/periods`, { params }).then((r) => r.data),
  hrPayrollCreatePeriod: (payload) => client.post(`/hr-payroll/periods`, payload).then((r) => r.data),
  hrPayrollPeriod: (id) => client.get(`/hr-payroll/periods/${encodeURIComponent(id)}`).then((r) => r.data),
  hrPayrollCalculate: (id) => client.post(`/hr-payroll/periods/${encodeURIComponent(id)}/calculate`).then((r) => r.data),
  hrPayrollTransition: (id, toStatus, note = "") => client.post(`/hr-payroll/periods/${encodeURIComponent(id)}/transition`, { to_status: toStatus, note }).then((r) => r.data),
  hrPayrollReopen: (id, reason) => client.post(`/hr-payroll/periods/${encodeURIComponent(id)}/reopen`, { reason }).then((r) => r.data),
  hrPayrollLines: (periodId) => client.get(`/hr-payroll/periods/${encodeURIComponent(periodId)}/lines`).then((r) => r.data),
  hrPayrollLine: (id) => client.get(`/hr-payroll/lines/${encodeURIComponent(id)}`).then((r) => r.data),
  hrPayrollAdjustLine: (periodId, lineId, payload) => client.post(`/hr-payroll/periods/${encodeURIComponent(periodId)}/lines/${encodeURIComponent(lineId)}/adjust`, payload).then((r) => r.data),
  hrPayrollAttendance: (periodId) => client.get(`/hr-payroll/periods/${encodeURIComponent(periodId)}/attendance`).then((r) => r.data),
  hrPayrollUpdateAttendance: (periodId, employeeId, payload) => client.put(`/hr-payroll/periods/${encodeURIComponent(periodId)}/attendance/${encodeURIComponent(employeeId)}`, payload).then((r) => r.data),
  hrPayrollKasbonSchedules: (params = {}) => client.get(`/hr-payroll/kasbon-schedules`, { params }).then((r) => r.data),
  hrPayrollCreateKasbonSchedule: (payload) => client.post(`/hr-payroll/kasbon-schedules`, payload).then((r) => r.data),
  hrPayrollPayslipUrl: (periodId, lineId, download = true) => `${API}/hr-payroll/periods/${encodeURIComponent(periodId)}/lines/${encodeURIComponent(lineId)}/payslip?download=${download}`,
  hrPayrollHistory: (params = {}) => client.get(`/hr-payroll/history`, { params }).then((r) => r.data),
  salesActivitySettings: () => client.get(`/sales-activity/settings`).then((r) => r.data),
  updateSalesActivitySettings: (payload) => client.put(`/sales-activity/settings`, payload).then((r) => r.data),
  salesActivityUsers: () => client.get(`/sales-activity/sales-users`).then((r) => r.data),
  salesActivityProspectPicUsers: () => client.get(`/sales-activity/prospect-pic-users`).then((r) => r.data),
  listSalesActivityOutlets: (params = {}) => client.get(`/sales-activity/outlets`, { params }).then((r) => r.data),
  createSalesActivityOutlet: (payload) => client.post(`/sales-activity/outlets`, payload).then((r) => r.data),
  salesActivityOutlet: (id) => client.get(`/sales-activity/outlets/${encodeURIComponent(id)}`).then((r) => r.data),
  listSalesActivities: (params = {}) => client.get(`/sales-activity/activities`, { params }).then((r) => r.data),
  createSalesActivity: (payload) => client.post(`/sales-activity/activities`, payload).then((r) => r.data),
  salesActivityDetail: (id) => client.get(`/sales-activity/activities/${encodeURIComponent(id)}`).then((r) => r.data),
  uploadSalesActivityVisitPhoto: (form) => client.post(`/sales-activity/visit-photos`, form, { timeout: 60000 }).then((r) => r.data),
  deleteSalesActivityVisitPhoto: (photoId) => client.delete(`/sales-activity/visit-photos/${encodeURIComponent(photoId)}`).then((r) => r.data),
  salesActivityVisitPhotoUrl: (photoId) => `${API}/sales-activity/visit-photos/${encodeURIComponent(photoId)}/file`,
  listSalesActivityPool: (params = {}) => client.get(`/sales-activity/pool`, { params }).then((r) => r.data),
  salesActivityReassign: (id, payload) => client.post(`/sales-activity/outlets/${encodeURIComponent(id)}/reassign`, payload).then((r) => r.data),
  salesActivityExtend: (id, payload) => client.post(`/sales-activity/outlets/${encodeURIComponent(id)}/extend`, payload).then((r) => r.data),
  salesActivityPause: (id, payload) => client.post(`/sales-activity/outlets/${encodeURIComponent(id)}/pause`, payload).then((r) => r.data),
  salesActivityResume: (id, payload) => client.post(`/sales-activity/outlets/${encodeURIComponent(id)}/resume`, payload).then((r) => r.data),
  salesActivitySendToPool: (id, payload) => client.post(`/sales-activity/outlets/${encodeURIComponent(id)}/send-to-pool`, payload).then((r) => r.data),
  salesActivityAssignFromPool: (id, payload) => client.post(`/sales-activity/outlets/${encodeURIComponent(id)}/assign-from-pool`, payload).then((r) => r.data),
  salesActivityImportPreview: (file) => {
    const form = new FormData();
    form.append("file", file);
    return client.post(`/sales-activity/import/preview`, form, { timeout: 90000 }).then((r) => r.data);
  },
  salesActivityImportConfirm: (batchId) => client.post(`/sales-activity/import/batches/${encodeURIComponent(batchId)}/confirm`).then((r) => r.data),
  salesActivityImportBatches: () => client.get(`/sales-activity/import/batches`).then((r) => r.data),
  salesActivityActivationPreview: (batchId) => client.get(`/sales-activity/import/batches/${encodeURIComponent(batchId)}/activation-preview`).then((r) => r.data),
  salesActivityActivateMigration: (batchId) => client.post(`/sales-activity/import/batches/${encodeURIComponent(batchId)}/activate`, { confirm: true }).then((r) => r.data),
  independentRouteSettings: () => client.get(`/route-optimizer/settings`).then((r) => r.data),
  updateIndependentRouteSettings: (payload) => client.put(`/route-optimizer/settings`, payload).then((r) => r.data),
  getArmadaCapacity: () => client.get(`/route-optimizer/settings/armada-capacity`).then((r) => r.data),
  saveArmadaCapacity: (payload) => client.put(`/route-optimizer/settings/armada-capacity`, payload).then((r) => r.data),
  searchIndependentRouteOutlets: (q) => client.get(`/route-optimizer/outlets/search`, { params: { q } }).then((r) => r.data),
  independentRoutePlacesStatus: () => client.get(`/route-optimizer/places/status`).then((r) => r.data),
  searchIndependentRoutePlaces: (q) => client.get(`/route-optimizer/places/search`, { params: { q } }).then((r) => r.data),
  independentRoutePlaceDetails: (placeId) => client.get(`/route-optimizer/places/details`, { params: { place_id: placeId } }).then((r) => r.data),
  listIndependentRoutes: () => client.get(`/route-optimizer/routes`).then((r) => r.data),
  createIndependentRoute: (payload) => client.post(`/route-optimizer/routes`, payload).then((r) => r.data),
  getIndependentRoute: (id) => client.get(`/route-optimizer/routes/${encodeURIComponent(id)}`).then((r) => r.data),
  updateIndependentRoute: (id, payload) => client.patch(`/route-optimizer/routes/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  addIndependentStop: (id, payload) => client.post(`/route-optimizer/routes/${encodeURIComponent(id)}/stops`, payload).then((r) => r.data),
  updateIndependentStop: (routeId, stopId, payload) => client.patch(`/route-optimizer/routes/${encodeURIComponent(routeId)}/stops/${encodeURIComponent(stopId)}`, payload).then((r) => r.data),
  deleteIndependentStop: (routeId, stopId) => client.delete(`/route-optimizer/routes/${encodeURIComponent(routeId)}/stops/${encodeURIComponent(stopId)}`).then((r) => r.data),
  deleteIndependentRoute: (id) => client.delete(`/route-optimizer/routes/${encodeURIComponent(id)}`).then((r) => r.data),
  optimizeIndependentRoute: (id, payload = {}) => client.post(`/route-optimizer/routes/${encodeURIComponent(id)}/optimize`, payload).then((r) => r.data),
  reorderIndependentRoute: (id, payload) => client.post(`/route-optimizer/routes/${encodeURIComponent(id)}/reorder`, payload).then((r) => r.data),
  listIndependentRouteVersions: (id) => client.get(`/route-optimizer/routes/${encodeURIComponent(id)}/versions`).then((r) => r.data),
  reviewScheduleRouteOptimize: (orderIds) => client.post(`/route-optimizer/from-schedule/review`, { order_ids: orderIds || [] }).then((r) => r.data),
  /** Non-persistent 1/2-armada optimization preview. */
  previewFleetsFromSchedule: (orderIds, fleetCount) => client.post(`/route-optimizer/from-schedule/preview-fleets`, { order_ids: orderIds || [], fleet_count: fleetCount }).then((r) => r.data),
  /** One-stage driverless create+optimize (1 or 2 armada). */
  createFleetsFromSchedule: (orderIds, fleetCount) => client.post(`/route-optimizer/from-schedule/optimize`, { order_ids: orderIds || [], fleet_count: fleetCount }).then((r) => r.data),
  /** Legacy compatibility only — released `{ order_ids }` payload. New UI must not call this. */
  optimizeFromSchedule: (orderIds) => client.post(`/route-optimizer/from-schedule/optimize`, { order_ids: orderIds || [] }).then((r) => r.data),
  scheduleRouteOptimizeStatus: (orderIds) => client.get(`/route-optimizer/from-schedule/status`, {
    params: { ids: (orderIds || []).join(",") },
  }).then((r) => r.data),
  listEligibleDeliveryDrivers: () => client.get(`/delivery/drivers`).then((r) => r.data),
  assignDeliveryDriver: (orderId, driverUserId) => client.patch(
    `/delivery/orders/${encodeURIComponent(orderId)}/driver`,
    { driver_user_id: driverUserId },
  ).then((r) => r.data),
  bulkAssignDeliveryDriver: (orderIds, driverUserId) => client.post(`/delivery/orders/assign-driver`, {
    order_ids: orderIds || [],
    driver_user_id: driverUserId,
  }).then((r) => r.data),

  // Internal Work Chat
  chatUnreadSummary: () => client.get("/chat/unread-summary").then((r) => r.data),
  chatListRooms: (filter) => client.get("/chat/rooms", { params: filter ? { filter } : {} }).then((r) => r.data),
  chatGetRoom: (roomId) => client.get(`/chat/rooms/${encodeURIComponent(roomId)}`).then((r) => r.data),
  chatCreateDm: (peerUserId) => client.post("/chat/rooms/dm", { peer_user_id: peerUserId }).then((r) => r.data),
  chatCreateCustomGroup: (payload) => client.post("/chat/rooms/custom", payload).then((r) => r.data),
  chatCreateWorkRoom: (payload) => client.post("/chat/rooms/work", payload).then((r) => r.data),
  chatUpdateRoom: (roomId, payload) => client.patch(`/chat/rooms/${encodeURIComponent(roomId)}`, payload).then((r) => r.data),
  chatArchiveRoom: (roomId) => client.post(`/chat/rooms/${encodeURIComponent(roomId)}/archive`).then((r) => r.data),
  chatAddMembers: (roomId, userIds) => client.post(`/chat/rooms/${encodeURIComponent(roomId)}/members`, { user_ids: userIds }).then((r) => r.data),
  chatRemoveMembers: (roomId, userIds) => client.delete(`/chat/rooms/${encodeURIComponent(roomId)}/members`, { data: { user_ids: userIds } }).then((r) => r.data),
  chatListMessages: (roomId, params = {}) => client.get(`/chat/rooms/${encodeURIComponent(roomId)}/messages`, { params }).then((r) => r.data),
  chatSendMessage: (roomId, payload) => client.post(`/chat/rooms/${encodeURIComponent(roomId)}/messages`, payload).then((r) => r.data),
  chatEditMessage: (messageId, payload) => client.patch(`/chat/messages/${encodeURIComponent(messageId)}`, payload).then((r) => r.data),
  chatDeleteMessage: (messageId) => client.delete(`/chat/messages/${encodeURIComponent(messageId)}`).then((r) => r.data),
  chatMarkRead: (roomId, sequence) => client.post(`/chat/rooms/${encodeURIComponent(roomId)}/read`, { sequence }).then((r) => r.data),
  chatSearchUsers: (q) => client.get("/chat/users", { params: { q: q || "" } }).then((r) => r.data),
  chatSearchWorkOrders: (q) => client.get("/chat/work/orders", { params: { q: q || "" } }).then((r) => r.data),
  chatWorkOrderLines: (orderId) => client.get(`/chat/work/orders/${encodeURIComponent(orderId)}/lines`).then((r) => r.data),
  chatWorkOrderTasks: (orderId, lineIdx) => client.get(`/chat/work/orders/${encodeURIComponent(orderId)}/tasks`, {
    params: lineIdx == null || lineIdx === "" ? {} : { line_idx: lineIdx },
  }).then((r) => r.data),
  chatUploadAttachment: (roomId, file) => {
    const form = new FormData();
    form.append("file", file);
    return client.post(`/chat/rooms/${encodeURIComponent(roomId)}/attachments`, form).then((r) => r.data);
  },
  chatAttachmentDownloadUrl: (attachmentId) => `${API}/chat/attachments/${encodeURIComponent(attachmentId)}/download`,

  salesUserMapping: () => client.get("/settings/sales-user-mapping").then((r) => r.data),
  updateSalesUserMapping: (payload) => client.put("/settings/sales-user-mapping", payload).then((r) => r.data),
  salesPwaMe: () => client.get("/sales-pwa/me").then((r) => r.data),
  salesPwaDashboard: (params = {}) => client.get("/sales-pwa/dashboard", { params }).then((r) => r.data),
  salesPwaOrders: (params = {}) => client.get("/sales-pwa/orders", { params }).then((r) => r.data),
  salesPwaOrder: (id) => client.get(`/sales-pwa/orders/${encodeURIComponent(id)}`).then((r) => r.data),
  salesPwaDocuments: (id) => client.get(`/sales-pwa/orders/${encodeURIComponent(id)}/documents`).then((r) => r.data),
  salesPwaDocumentFile: (id, kind, documentId) => {
    const orderId = encodeURIComponent(id);
    const docId = encodeURIComponent(documentId);
    const paths = {
      po: `/sales-pwa/orders/${orderId}/files/po`,
      sj: `/sales-pwa/orders/${orderId}/files/sj/${docId}`,
      invoice: `/sales-pwa/orders/${orderId}/files/invoices/${docId}`,
      proof: `/sales-pwa/orders/${orderId}/files/proofs/${docId}`,
    };
    const path = paths[kind];
    if (!path) return Promise.reject(new Error("Jenis dokumen tidak didukung"));
    return client.get(path, { responseType: "blob" }).then((r) => ({
      blob: r.data,
      contentType: r.headers?.["content-type"] || r.data?.type || "",
      disposition: r.headers?.["content-disposition"] || "",
    }));
  },
  salesPwaCustomers: (params = {}) => client.get("/sales-pwa/customers", { params }).then((r) => r.data),
  salesPwaPredictions: () => client.get("/sales-pwa/predictions").then((r) => r.data),
  salesPwaPredictionDetail: (customerKey) => client.get(`/sales-pwa/predictions/${encodeURIComponent(customerKey)}`).then((r) => r.data),
  salesPwaVisits: (params = {}) => client.get("/sales-pwa/visits", { params }).then((r) => r.data),
  createSalesPwaVisit: (payload) => client.post("/sales-pwa/visits", payload).then((r) => r.data),
  uploadSalesPwaVisitPhoto: (form) => client.post("/sales-pwa/visits/photos", form, { timeout: 60000 }).then((r) => r.data),
  salesPwaVisitPhotoUrl: (photoId) => `${API}/sales-pwa/visits/photos/${encodeURIComponent(photoId)}/file`,
};

export default api;
