/** Task-list and panel eligibility. Prefer backend flags; do not invent a second workflow. */

export type AnyOrder = Record<string, any>;

export type TaskReady = {
  actionable: boolean;
  lockedReason: string | null;
};

function hasValue(v: unknown): boolean {
  return v !== null && v !== undefined && String(v).trim() !== "";
}

export function workflowReadyFlag(order: AnyOrder | null | undefined, key: string): boolean | null {
  const ready = order?.workflow_ready;
  if (ready && typeof ready === "object" && typeof ready[key] === "boolean") {
    return ready[key];
  }
  return null;
}

/** Mirror workflow_gates.penjadwalan_done when workflow_ready is absent. */
export function penjadwalanDone(order: AnyOrder | null | undefined): boolean {
  if (!order) return false;
  if (order.requires_printing === false) return true;
  return Array.isArray(order.operator_splits) && order.operator_splits.length > 0;
}

export function helperPreparedDone(order: AnyOrder | null | undefined): boolean {
  if (!order) return false;
  if (order.requires_printing === false) return true;
  return hasValue(order?.helper_prep?.qty_prepared);
}

export function screenerTaskReady(order: AnyOrder | null | undefined): TaskReady {
  const flag = workflowReadyFlag(order, "screener");
  if (flag === true) return { actionable: true, lockedReason: null };
  if (flag === false) return { actionable: false, lockedReason: "Menunggu Penjadwalan" };
  if (penjadwalanDone(order)) return { actionable: true, lockedReason: null };
  return { actionable: false, lockedReason: "Menunggu Penjadwalan" };
}

export function helperTaskReady(order: AnyOrder | null | undefined): TaskReady {
  const flag = workflowReadyFlag(order, "helper");
  if (flag === true) return { actionable: true, lockedReason: null };
  if (flag === false) return { actionable: false, lockedReason: "Menunggu Penjadwalan" };
  if (penjadwalanDone(order)) return { actionable: true, lockedReason: null };
  return { actionable: false, lockedReason: "Menunggu Penjadwalan" };
}

export function sampleUploadTaskReady(order: AnyOrder | null | undefined): TaskReady {
  if (order?.requires_printing === false) {
    return { actionable: false, lockedReason: "PO non-cetak tidak butuh sampel." };
  }
  const flag = workflowReadyFlag(order, "sample_upload");
  if (flag === true) return { actionable: true, lockedReason: null };
  if (flag === false) {
    const reason = typeof order?.screener_gate_reason === "string" ? order.screener_gate_reason.trim() : "";
    return { actionable: false, lockedReason: reason || "Upload sampel tersedia setelah tahap sebelumnya selesai." };
  }
  if (penjadwalanDone(order) && helperPreparedDone(order)) {
    return { actionable: true, lockedReason: null };
  }
  return { actionable: false, lockedReason: "Upload sampel tersedia setelah tahap sebelumnya selesai." };
}

export function cetakTaskReady(order: AnyOrder | null | undefined): TaskReady {
  const flag = workflowReadyFlag(order, "print_qty");
  if (flag === true) return { actionable: true, lockedReason: null };
  if (flag === false) {
    const reason = typeof order?.print_qty_gate_reason === "string" ? order.print_qty_gate_reason.trim() : "";
    return { actionable: false, lockedReason: reason || "Menunggu Approval Sampel" };
  }
  if (order?.requires_printing === false) return { actionable: true, lockedReason: null };
  if (order?.sample_approved) return { actionable: true, lockedReason: null };
  return { actionable: false, lockedReason: "Cetak Sample harus disetujui sebelum Qty Selesai Cetak dapat diisi." };
}

export function packingTaskReady(order: AnyOrder | null | undefined): TaskReady {
  const flag = workflowReadyFlag(order, "packing");
  if (flag === false) {
    const reasons = Array.isArray(order?.packing_prereq_reasons) ? order.packing_prereq_reasons : [];
    const first = typeof reasons[0] === "string" ? reasons[0] : "";
    return { actionable: false, lockedReason: first || "Belum siap Packing" };
  }
  if (flag === true) return { actionable: true, lockedReason: null };
  return { actionable: true, lockedReason: null };
}

export function sjFilesForLoading(order: AnyOrder | null | undefined): any[] {
  return (order?.surat_jalan_files || []).filter((f: any) => f && f.id);
}

export function pickSjFile(files: any[], preferredId?: string | null): any | null {
  const list = Array.isArray(files) ? files.filter((f) => f && f.id) : [];
  if (!list.length) return null;
  if (preferredId) {
    const found = list.find((f) => String(f.id) === String(preferredId));
    if (found) return found;
  }
  return list.length === 1 ? list[0] : list[0];
}

export function loadingPhysicalAvailable(order: AnyOrder | null | undefined): number {
  const rows = Array.isArray(order?.shipment_lines) ? order.shipment_lines : [];
  if (rows.length) {
    return rows.reduce((s: number, r: any) => {
      const avail = r?.available_loading != null ? Number(r.available_loading) : 0;
      return s + Math.max(0, Number.isFinite(avail) ? avail : 0);
    }, 0);
  }
  const packLines = order?.packing_info?.lines || [];
  return packLines.reduce((s: number, x: any) => s + Math.max(0, Number(x?.packed_qty || 0)), 0);
}

export function loadingTaskReady(order: AnyOrder | null | undefined): TaskReady {
  const hasSj = sjFilesForLoading(order).length > 0 || !!(order?.surat_jalan_created);
  if (!hasSj) return { actionable: false, lockedReason: "Surat Jalan belum tersedia" };
  const avail = loadingPhysicalAvailable(order);
  if (avail <= 0) return { actionable: false, lockedReason: "Belum ada qty Packing" };
  return { actionable: true, lockedReason: null };
}

export function deliveryTaskReady(order: AnyOrder | null | undefined): TaskReady {
  if (order?.delivery_status === "Terkirim" || order?.terkirim_confirmed) {
    return { actionable: false, lockedReason: "Sudah Terkirim" };
  }
  const proofs = Array.isArray(order?.delivery_proofs) ? order.delivery_proofs.length : 0;
  const eligible = Number(order?.eligible_delivery_qty || 0);
  if (proofs > 0 || eligible > 0) return { actionable: true, lockedReason: null };
  return { actionable: false, lockedReason: "Belum ada qty eligible pengiriman" };
}

export type ListTask = {
  division?: string | null;
  remaining_qty?: number | null;
  status?: string | null;
  hold?: boolean;
};

/** Actionable Tugas Saya row. Permission is applied separately. */
export function isActionableDivisionTask(task: ListTask): boolean {
  if (task.hold) return false;
  const div = task.division || "";
  if (div === "surat_jalan") return false;
  if (div === "loading") return Number(task.remaining_qty) > 0;
  if (div === "screener" || div === "helper" || div === "packing" || div === "operator") {
    return Number(task.remaining_qty) > 0;
  }
  if (div === "terkirim") return task.status !== "selesai";
  if (div === "design_approve" || div === "sample_upload") return true;
  return false;
}

/** Exact list context. Never treat an empty/"all" workspace as a multi-stage merge. */
export function taskBelongsToWorkspace(
  task: { division?: string | null },
  workspaceId: string | null | undefined
): boolean {
  const ws = (workspaceId || "").trim();
  if (!ws || ws === "all") return false;
  return (task.division || "") === ws;
}

export function packingListHasForeignStages(tasks: { division?: string | null }[]): boolean {
  return tasks.some((t) => t.division === "helper" || t.division === "loading");
}

/** Task lists stay stage-scoped. No generic merged "Semua" list. */
export function showsMergedTaskList(_divisionIds?: string[]): boolean {
  return false;
}

export function defaultTaskWorkspace(divisionIds: string[]): string {
  return (divisionIds || []).find((id) => !!id) || "";
}
