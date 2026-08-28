import { apiRequest, authenticatedFileUrl } from "./client";

export type MeUser = {
  id?: string;
  email: string;
  name?: string;
  role: string;
  permissions: string[];
  is_super_admin?: boolean;
};

export type LoginResult = MeUser & {
  access_token: string;
};

export type DivisionTask = {
  task_id: string;
  order_id: string;
  order_number?: string;
  customer_name?: string;
  item?: string;
  division: string;
  work_date?: string | null;
  target_qty?: number | null;
  completed_qty?: number | null;
  remaining_qty?: number | null;
  status?: string;
  status_label?: string;
  hold?: boolean;
  action_label?: string;
  assigned_user_name?: string | null;
};

export type DivisionTasksResponse = {
  division: string;
  label: string;
  scope: string;
  tasks: DivisionTask[];
  count: number;
};

export type ProductionRow = {
  id: string;
  po_number?: string;
  customer?: string;
  outlet?: string;
  work_date?: string | null;
  total_quantity?: number;
  print_qty_total?: number;
  operator_assigned_qty?: number;
  status_label?: string;
  status_final?: string;
  current_stage?: number;
  hold?: boolean;
  operators?: { operator_name?: string }[];
};

export const eternalApi = {
  login: (email: string, password: string) =>
    apiRequest<LoginResult>("/auth/login", {
      method: "POST",
      body: { email, password },
      token: null,
    }),

  logout: () => apiRequest<{ ok: boolean }>("/auth/logout", { method: "POST", body: {} }),

  me: (token?: string | null) =>
    apiRequest<MeUser>("/auth/me", { token }),

  productionSchedule: () =>
    apiRequest<{ rows: ProductionRow[]; total: number }>("/dashboard/production"),

  divisionTasks: (division: string, scope = "mine") =>
    apiRequest<DivisionTasksResponse>(
      `/work/division-tasks?division=${encodeURIComponent(division)}&scope=${encodeURIComponent(scope)}`
    ),

  getOrder: (id: string) =>
    apiRequest<{ order: Record<string, unknown> }>(`/orders/${encodeURIComponent(id)}`),

  workPanelSettings: () =>
    apiRequest<{ settings?: { divisions?: unknown[] } }>("/work-panel/settings"),

  updateScreenerInfo: (id: string, payload: { qty: number; action_id: string; notes?: string }) =>
    apiRequest<{ order: Record<string, unknown> }>(`/orders/${encodeURIComponent(id)}/screener-info`, {
      method: "POST",
      body: payload,
    }),

  updateHelperPrepItems: (
    id: string,
    items: { line_idx: number; qty: number; action_id: string }[],
    notes = ""
  ) =>
    apiRequest<{ order: Record<string, unknown> }>(
      `/orders/${encodeURIComponent(id)}/helper-prep/items`,
      { method: "POST", body: { items, notes } }
    ),

  updateOperatorProgress: (
    id: string,
    operatorId: string,
    qty: number,
    actionId: string
  ) =>
    apiRequest<{ order: Record<string, unknown> }>(
      `/orders/${encodeURIComponent(id)}/operator-progress`,
      {
        method: "POST",
        body: { operator_id: operatorId, qty, action_id: actionId },
      }
    ),

  addAttachment: (
    id: string,
    payload: { name: string; data_url: string; kind: string }
  ) =>
    apiRequest<{ order: Record<string, unknown> }>(
      `/orders/${encodeURIComponent(id)}/attachment`,
      { method: "POST", body: payload }
    ),

  uploadDeliveryProof: async (id: string, uri: string, fileName: string, mime: string, note?: string) => {
    const form = new FormData();
    form.append("file", {
      uri,
      name: fileName,
      type: mime || "image/jpeg",
    } as unknown as Blob);
    if (note) form.append("note", note);
    return apiRequest<{ order: Record<string, unknown>; proof?: unknown }>(
      `/orders/${encodeURIComponent(id)}/delivery-proof`,
      { method: "POST", formData: form }
    );
  },

  confirmDelivered: (id: string, actionId: string) =>
    apiRequest<{ order: Record<string, unknown> }>(
      `/orders/${encodeURIComponent(id)}/confirm-delivered`,
      { method: "POST", body: { action_id: actionId } }
    ),

  suratJalanFileUrl: (orderId: string, fileId: string, token: string | null) =>
    authenticatedFileUrl(
      `/orders/${encodeURIComponent(orderId)}/surat-jalan/files/${encodeURIComponent(fileId)}`,
      token
    ),

  fileUrl: (storagePath: string, token: string | null) =>
    authenticatedFileUrl(`/files/${storagePath.replace(/^\/+/, "")}`, token),
};
