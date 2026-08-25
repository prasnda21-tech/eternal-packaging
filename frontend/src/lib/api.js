import axios from "axios";
import { resolveBackendOrigin } from "./backendOrigin";

const pageHost = typeof window !== "undefined" ? window.location.hostname : "";
const API = `${resolveBackendOrigin(process.env.REACT_APP_BACKEND_URL, pageHost)}/api`;

const client = axios.create({
  baseURL: API,
  withCredentials: true,
});

export const fileUrl = (storagePath) => `${API}/files/${storagePath}`;

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
  setUrgent: (id, urgent) => client.post(`/orders/${id}/urgent`, { urgent }).then((r) => r.data),
  setBufferStock: (id, buffer_stock) => client.post(`/orders/${id}/buffer-stock`, { buffer_stock }).then((r) => r.data),
  addAttachment: (id, payload) => client.post(`/orders/${id}/attachment`, payload).then((r) => r.data),
  addNote: (id, note) => client.post(`/orders/${id}/note`, { note }).then((r) => r.data),
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
  previewAccurateSuratJalan: (id) =>
    client.get(`/orders/${id}/surat-jalan/accurate-preview`).then((r) => r.data),
  importSuratJalan: (id, payload) =>
    client.post(`/orders/${id}/surat-jalan/import`, payload).then((r) => r.data),
  markDelivered: (id, qty_delivered = null) =>
    client.post(`/orders/${id}/delivered`, null, {
      params: qty_delivered == null ? {} : { qty_delivered },
    }).then((r) => r.data),
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
  listOperators: () => client.get(`/users/operators`).then((r) => r.data),
  dashboardSummary: () => client.get(`/dashboard/summary`).then((r) => r.data),
  dashboardToday: () => client.get(`/dashboard/today`).then((r) => r.data),
  updateOperatorProgress: (id, operatorId, qtyCompleted) =>
    client.post(`/orders/${id}/operator-progress`, { operator_id: operatorId, qty_completed: qtyCompleted }).then((r) => r.data),
  accurateImportPreview: (soNumber) => client.post(`/accurate/import/preview`, { so_number: soNumber }).then((r) => r.data),
  accurateImportConfirm: (soNumber) => client.post(`/accurate/import/confirm`, { so_number: soNumber }).then((r) => r.data),
  accurateImportPreviewBulk: (soNumbers) =>
    client.post(`/accurate/import/preview-bulk`, { so_numbers: soNumbers }).then((r) => r.data),
  accurateImportConfirmBulk: (soNumbers) =>
    client.post(`/accurate/import/confirm-bulk`, { so_numbers: soNumbers }).then((r) => r.data),
  accurateImportLogs: () => client.get(`/accurate/import/logs`).then((r) => r.data),
  accurateImportAvailable: () => client.get(`/accurate/import/available`).then((r) => r.data),
  masterItems: (params = {}) => client.get(`/master-items`, { params }).then((r) => r.data),
  masterItemsSync: () => client.post(`/master-items/sync`).then((r) => r.data),
  currentStock: (params = {}) => client.get(`/current-stock`, { params }).then((r) => r.data),
  currentStockUpdate: (itemId, payload) => client.patch(`/current-stock/${itemId}`, payload).then((r) => r.data),
  currentStockExport: (params = {}) =>
    client.get(`/current-stock/export`, { params, responseType: "blob" }).then((r) => r.data),
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
  productionExport: (payload = { scope: "filtered" }) => client.post(`/dashboard/production/export`, payload, { responseType: "blob" }).then((r) => r.data),
  // ---- Batch 3: Production routing + Action Center ----
  setLineItemRoute: (id, items) => client.post(`/orders/${id}/line-items/route`, { items }).then((r) => r.data),
  dashboardActionItems: () => client.get(`/dashboard/action-items`).then((r) => r.data),
  // ---- Delivery Deadline (Batch 1) ----
  deliveryDeadlineSettings: () => client.get(`/delivery-deadline/settings`).then((r) => r.data),
  updateDeliveryDeadlineSettings: (payload) => client.put(`/delivery-deadline/settings`, payload).then((r) => r.data),
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
};

export default api;
