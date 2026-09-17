/**
 * Detail PO presentation helpers only.
 * Derives visual tone/summary from existing resolveStageState — no workflow mutation.
 */
import { STAGES } from "./constants";
import { resolveStageState } from "./stageState";

const PRINT_ONLY_STAGES = new Set([6, 7, 8, 9, 10]); // Screener … Cetak

/**
 * @returns {{ state: string, label: string, tone: 'completed'|'active'|'blocked'|'not-required'|'upcoming', summary: string }}
 */
export function getStagePresentation(idx, order, currentStage) {
  const { state, label } = resolveStageState(idx, order, currentStage);
  const reqPrint = order?.requires_printing !== false;

  if (!reqPrint && PRINT_ONLY_STAGES.has(idx)) {
    return {
      state,
      label,
      tone: "not-required",
      summary: "Tidak diperlukan (Non-Cetak)",
    };
  }

  if (state === "done" || state === "bypass") {
    return { state, label, tone: "completed", summary: label || "Selesai" };
  }

  if (state === "current") {
    const blockedHint = /menunggu approval|menunggu input|mismatch|held|hold|validasi|error|belum/i.test(
      String(label || "")
    );
    // Waiting-for-input at the active stage stays "active" (operational focus),
    // but amber "blocked" when hold/mismatch style labels appear.
    const hardBlock = /mismatch|held|hold|error|validasi/i.test(String(label || ""));
    return {
      state,
      label,
      tone: hardBlock ? "blocked" : "active",
      summary: label || (blockedHint ? "Menunggu tindakan" : "Berjalan"),
    };
  }

  // pending
  if ((idx || 0) > (currentStage || 0)) {
    return { state, label, tone: "upcoming", summary: label || "Belum tersedia" };
  }
  return { state, label, tone: "blocked", summary: label || "Menunggu" };
}

/**
 * Legacy helper — Detail PO stage cards no longer auto-expand.
 * Always returns false so initial render / refresh stay fully collapsed.
 * Kept for callers/tests that still import the name.
 */
export function shouldExpandStageByDefault(_idx, _order, _currentStage, _opts = {}) {
  return false;
}

/**
 * Pick the stage that should drive "Fokus Saat Ini".
 * Uses existing resolveStageState only — no new gating.
 */
export function resolveOperationalFocus(order) {
  if (!order) return null;
  const currentStage = order.current_stage || 0;

  if (order.cancelled) {
    return {
      stageIndex: currentStage,
      stageName: STAGES[currentStage] || "—",
      tone: "blocked",
      title: "PO dibatalkan",
      detail: order.cancel_reason || "Tidak ada tindakan operasional.",
      holdReasons: [],
    };
  }

  if (order.hold) {
    return {
      stageIndex: currentStage,
      stageName: STAGES[currentStage] || "—",
      tone: "blocked",
      title: "PO HOLD — fokus dihentikan",
      detail: "Selesaikan alasan hold sebelum lanjut Penjadwalan.",
      holdReasons: Array.isArray(order.hold_reasons) ? order.hold_reasons : [],
    };
  }

  // Prefer first active, then blocked, else current_stage
  let focusIdx = currentStage;
  let focusPres = getStagePresentation(currentStage, order, currentStage);
  for (let i = 0; i < STAGES.length; i++) {
    const p = getStagePresentation(i, order, currentStage);
    if (p.tone === "active") {
      focusIdx = i;
      focusPres = p;
      break;
    }
  }
  if (focusPres.tone !== "active") {
    for (let i = 0; i < STAGES.length; i++) {
      const p = getStagePresentation(i, order, currentStage);
      if (p.tone === "blocked") {
        focusIdx = i;
        focusPres = p;
        break;
      }
    }
  }

  return {
    stageIndex: focusIdx,
    stageName: STAGES[focusIdx] || "—",
    tone: focusPres.tone,
    title: focusPres.tone === "completed" ? "Tidak ada aksi aktif" : `Tahap: ${STAGES[focusIdx]}`,
    detail: focusPres.summary,
    holdReasons: [],
  };
}

export function stageBarClassForTone(tone) {
  switch (tone) {
    case "completed":
      return "done";
    case "active":
      return "current";
    case "blocked":
      return "blocked";
    case "upcoming":
      return "waiting";
    case "not-required":
      return "not-required";
    default:
      return "waiting";
  }
}
