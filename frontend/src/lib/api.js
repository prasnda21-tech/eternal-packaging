import axios from "axios";
import { resolveBackendOrigin } from "./backendOrigin";

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

export const api = {
  login: (email, password) => client.post("/auth/login", { email, password }).then((r) => r.data),
  logout: () => client.post("/auth/logout", {}).then((r) => r.data),
  me: () => client.get("/auth/me").then((r) => r.data),
  systemHealth: () => client.get("/system/health").then((r) => r.data),
  listOrders: (status) => client.get("/orders", { params: status ? { status } : {} }).then((r) => r.data),
  cancelPO: (id, reason) => client.post(`/orders/${id}/cancel`, { reason }).then((r) => r.data),
  deleteImpact: (id) => client.get(`/orders/${id}/delete-impact`).then((r) => r.data),
  deletePO: (id, reason, confirm) => client.delete(`/orders/${id}`, { data: { reason, confirm } }).then((r) => r.data),
  bulkCancel: (ids, reason) => client.post(`/orders-bulk/cancel`, { ids, reason }).then((r) => r.data),
  bulkDelete: (ids, reason, confirm) => client.post(`/orders-bulk/delete`, { ids, reason, confirm }).then((r) => r.data),
  getOrder: (id) => client.get(`/orders/${id}`).then((r) => r.data),
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
  addNote: (id, note) => client.post(`/orders/${id}/note`, { note }).then((r) => r.data),
  confirmLoading: (id, payload) => client.post(`/orders/${id}/loading-confirm`, payload).then((r) => r.data),
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
  uploadDeliveryProof: (id, file, note, onProgress) => {
    const form = new FormData();
    form.append("file", file);
    if (note) form.append("note", note);
    return client.post(`/orders/${id}/delivery-proof`, form, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100));
      },
    }).then((r) => r.data);
  },
  deleteDeliveryProof: (id, proofId) => client.delete(`/orders/${id}/delivery-proof/${proofId}`).then((r) => r.data),
  analyticsOperators: () => client.get(`/analytics/operators`).then((r) => r.data),
  analyticsOperatorKpis: (params = {}) => client.get(`/analytics/operators/kpis`, { params }).then((r) => r.data),
  analyticsOperatorDetail: (operatorId, params = {}) =>
    client.get(`/analytics/operators/${encodeURIComponent(operatorId)}`, { params }).then((r) => r.data),
  analyticsPacking: (params = {}) => client.get(`/analytics/packing`, { params }).then((r) => r.data),
  analyticsPreparation: (params = {}) => client.get(`/analytics/preparation`, { params }).then((r) => r.data),
  analyticsScreener: (params = {}) => client.get(`/analytics/screener`, { params }).then((r) => r.data),
  analyticsDesigns: (params = {}) => client.get(`/analytics/designs`, { params }).then((r) => r.data),
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
  listOperators: () => client.get(`/users/operators`).then((r) => r.data),
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
  salesOrderOptions: () => client.get(`/sales-orders/options`).then((r) => r.data),
  createSalesOrder: (payload) => client.post(`/sales-orders`, payload).then((r) => r.data),
  listSalesOrders: (params = {}) => client.get(`/sales-orders`, { params }).then((r) => r.data),
  masterItems: (params = {}) => client.get(`/master-items`, { params }).then((r) => r.data),
  masterItemsSync: () => client.post(`/master-items/sync`).then((r) => r.data),
  currentStock: (params = {}) => client.get(`/current-stock`, { params }).then((r) => r.data),
  currentStockUpdate: (itemId, payload) => client.patch(`/current-stock/${itemId}`, payload).then((r) => r.data),
  currentStockExport: (params = {}) =>
    client.get(`/current-stock/export`, { params, responseType: "blob" }).then((r) => r.data),
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
  purchaseReceiptsExport: (params = {}) =>
    client.get(`/purchase-receipts/export`, { params, responseType: "blob" }).then((r) => r.data),
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
  listSalesInvoices: (params = {}) => client.get(`/sales-invoices`, { params }).then((r) => r.data),
  getSalesInvoice: (id) => client.get(`/sales-invoices/${encodeURIComponent(id)}`).then((r) => r.data),
  getPoInvoicePreview: (poId) => client.get(`/sales-invoices/po/${encodeURIComponent(poId)}/preview`).then((r) => r.data),
  createSalesInvoice: (payload) => client.post(`/sales-invoices`, payload).then((r) => r.data),
  updateSalesInvoice: (id, payload) => client.put(`/sales-invoices/${encodeURIComponent(id)}`, payload).then((r) => r.data),
  // ---- Sales Receipt (Penerimaan Penjualan) ----
  listSalesReceipts: (params = {}) => client.get(`/sales-receipts`, { params }).then((r) => r.data),
  getSalesReceipt: (id) => client.get(`/sales-receipts/${encodeURIComponent(id)}`).then((r) => r.data),
  createSalesReceipt: (payload) => client.post(`/sales-receipts`, payload).then((r) => r.data),
  // ---- Delete (Safe) ----
  deleteSalesDelivery: (id) => client.delete(`/sales-deliveries/${encodeURIComponent(id)}`).then((r) => r.data),
  bulkDeleteSalesDeliveries: (ids, reason = "bulk") => client.post(`/sales-deliveries-bulk/delete`, { ids, reason }).then((r) => r.data),
  deleteSalesInvoice: (id) => client.delete(`/sales-invoices/${encodeURIComponent(id)}`).then((r) => r.data),
  bulkDeleteSalesInvoices: (ids, reason = "bulk") => client.post(`/sales-invoices-bulk/delete`, { ids, reason }).then((r) => r.data),
  deleteSalesReceipt: (id) => client.delete(`/sales-receipts/${encodeURIComponent(id)}`).then((r) => r.data),
  bulkDeleteSalesReceipts: (ids, reason = "bulk") => client.post(`/sales-receipts-bulk/delete`, { ids, reason }).then((r) => r.data),
};

export default api;
