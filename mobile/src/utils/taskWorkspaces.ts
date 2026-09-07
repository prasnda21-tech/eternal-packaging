/** Presentational task-menu / nav context. Not authorization. */

import {
  canApproveDesign,
  canUploadSample,
  permittedWorkDivisions,
  type PermCheck,
} from "./permissions";
import { defaultTaskWorkspace, taskBelongsToWorkspace } from "./taskEligibility";

const WORKSPACE_TITLES: Record<string, string> = {
  screener: "Screener",
  helper: "Persiapan Cup",
  operator: "Cetak",
  packing: "Packing",
  loading: "Loading",
  terkirim: "Pengiriman",
  sample_upload: "Upload Sampel Cetak",
  design_approve: "Approve Design",
};

export type TaskWorkspace = {
  id: string;
  label: string;
};

export function availableTaskWorkspaces(has: PermCheck): TaskWorkspace[] {
  const items: TaskWorkspace[] = permittedWorkDivisions(has).map((d) => ({
    id: d.id,
    label: d.label,
  }));
  if (canApproveDesign(has)) items.push({ id: "design_approve", label: "Approve Design" });
  if (canUploadSample(has)) items.push({ id: "sample_upload", label: "Upload Sampel Cetak" });
  return items;
}

export function taskWorkspaceIds(has: PermCheck): string[] {
  return availableTaskWorkspaces(has).map((w) => w.id);
}

export function taskEmptyMessage(workspaceId: string): string {
  switch (workspaceId) {
    case "packing":
      return "Belum ada tugas Packing";
    case "helper":
      return "Belum ada tugas Persiapan Cup";
    case "loading":
      return "Belum ada tugas Loading";
    case "terkirim":
      return "Belum ada tugas Pengiriman";
    case "screener":
      return "Belum ada tugas Screener";
    case "operator":
      return "Belum ada tugas Cetak";
    case "sample_upload":
      return "Belum ada tugas Upload Sampel Cetak";
    case "design_approve":
      return "Belum ada tugas Approve Design";
    default:
      return "Belum ada tugas untuk konteks ini.";
  }
}

export function persistSelectedWorkspace(prev: string | null | undefined, allowedIds: string[]): string {
  const id = (prev || "").trim();
  if (id && id !== "all" && allowedIds.includes(id)) return id;
  return defaultTaskWorkspace(allowedIds);
}

/** Route/nav context only. Never invent context from a crafted or missing `from=task`. */
export function resolveNavTaskContext(
  from: string | null | undefined,
  requested: string | null | undefined,
  allowedIds: string[]
): string | null {
  if ((from || "").trim() !== "task") return null;
  const id = (requested || "").trim();
  if (!id || id === "all" || !allowedIds.includes(id)) return null;
  return id;
}

export function taskContextBreadcrumb(workspaceId: string | null | undefined): string | null {
  if (!workspaceId) return null;
  return `Tugas Saya › ${WORKSPACE_TITLES[workspaceId] || "Tugas"}`;
}

export function tasksForSelectedWorkspace<T extends { division?: string | null }>(
  tasks: T[],
  workspaceId: string | null | undefined
): T[] {
  return (tasks || []).filter((t) => taskBelongsToWorkspace(t, workspaceId));
}

export function hasSemuaMenuLabel(labels: string[]): boolean {
  return labels.some((l) => l.trim().toLowerCase() === "semua");
}

/**
 * Exactly one permitted work panel. Selected workspace never grants a missing permission.
 * Unpermitted / "all" / empty selection falls back to the first allowed workspace.
 */
export function scopedWorkPanelIds(
  selectedWorkspace: string | null | undefined,
  allowedIds: string[]
): string[] {
  const selected = persistSelectedWorkspace(selectedWorkspace, allowedIds);
  if (!selected) return [];
  return [selected];
}

export function canRenderWorkPanel(
  panelId: string,
  selectedWorkspace: string | null | undefined,
  allowedIds: string[]
): boolean {
  if (!panelId || !allowedIds.includes(panelId)) return false;
  return scopedWorkPanelIds(selectedWorkspace, allowedIds)[0] === panelId;
}
