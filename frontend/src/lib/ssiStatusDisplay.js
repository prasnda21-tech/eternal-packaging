/**
 * SSI UI-only display-status contract (Jalur B Option A + prior P0-B1 fields).
 *
 * Uses existing catalog fields + stockPhysicalPrimaryDisplay incompleteness.
 * Does not change API payloads, Excel formulas, ATP math, DB, or valuation.
 *
 * Qty Polos null/UNVERIFIED → presentation numeric 0 in UI only
 *   (title/legend: "0 tampilan; jumlah sebenarnya belum diketahui").
 * Missing ATP qty → never show sellable 0; use em dash in UI.
 * Main quantity cells: numbers only (no Belum terverifikasi / Indikatif badges).
 */

export const SSI_STATUS = {
  UNVERIFIED: "UNVERIFIED",
  INDICATIVE: "INDICATIVE",
  READY: "READY",
};

/** Accessible title/legend for presentation-only zero (never confirmed blank stock). */
export const SSI_PRESENTATION_ZERO_HINT =
  "0 tampilan; jumlah sebenarnya belum diketahui";

export const SSI_REASON_LABEL = {
  PRINT_DELIVERY_UNPROVEN: "Pengiriman cetak belum terbukti dari SJ print",
  COMPLETION_UNPROVEN: "Konfirmasi selesai cetak induk belum terverifikasi (multiwarna/Add On)",
  NO_OPERATOR_EVIDENCE: "Belum ada konfirmasi cetak selesai operator yang valid",
  DEPENDENT_UNKNOWN: "Nilai bergantung kolom yang belum terverifikasi",
  BASELINE_INCOMPLETE: "Stok fisik belum lengkap / belum direkonsiliasi",
  WIP_COMPOSITION_UNPROVEN:
    "Komposisi polos/WIP belum terbukti (print ordered > completed)",
  SIMPLE_POLOS_NO_WIP_COVERAGE:
    "Formula sederhana (Stock − Tercetak Belum Kirim); WIP/rusak/pengiriman polos terpisah belum tercakup",
  NO_BALANCE: "Belum ada saldo fisik",
};

/** @param {string|undefined|null} status */
export function normalizeSsiStatus(status, { qty } = {}) {
  const st = String(status || "").trim().toUpperCase();
  if (st === "BELUM_TERSEDIA" || st === "UNKNOWN") return SSI_STATUS.UNVERIFIED;
  if (st === SSI_STATUS.UNVERIFIED || st === SSI_STATUS.INDICATIVE || st === SSI_STATUS.READY) {
    return st;
  }
  if (qty == null) return SSI_STATUS.UNVERIFIED;
  return SSI_STATUS.READY;
}

/**
 * Physical baseline incomplete when the Stock cell hides the number
 * (Belum saldo / Belum lengkap). Prefer physicalPrimary from
 * stockPhysicalPrimaryDisplay; fall back to warehouse_balances / baseline_status.
 */
export function isPhysicalBaselineIncomplete(item, physicalPrimary) {
  // G1: proven no-activity operational zeros are complete (numeric 0), not Belum.
  if (item?.operational_qty_proven || item?.no_relevant_activity) return false;
  if (physicalPrimary && physicalPrimary.present === false) return true;
  if (item && String(item.baseline_status || "") === "unreconciled") return true;
  const balances = Array.isArray(item?.warehouse_balances) ? item.warehouse_balances : [];
  if (balances.length > 0) {
    if (balances.some((wb) => (
      wb?.balance_present === false
      && !wb?.operational_qty_proven
      && !wb?.no_relevant_activity
    ))) return true;
    if (balances.some((wb) => (
      String(wb?.baseline_status || "") === "unreconciled"
      && !wb?.operational_qty_proven
      && !wb?.no_relevant_activity
    ))) return true;
  }
  return false;
}

/**
 * Resolve Stock Dapat Dijual display from existing API numerics + FE completeness.
 * Numeric ATP (incl. negative) always shown as a number — no Indikatif cell badge.
 * Null/unproven ATP → unverified (UI shows "—", never sellable 0).
 */
export function resolveDapatDijualDisplay(item, physicalPrimary) {
  const qty = item?.stock_dapat_dijual ?? item?.qty_siap_jual;
  const apiStatus = normalizeSsiStatus(item?.qty_siap_jual_status, { qty });
  const incomplete = isPhysicalBaselineIncomplete(item, physicalPrimary);
  const note = String(item?.qty_siap_jual_note || "").trim();
  const reasonCode = String(item?.qty_siap_jual_reason || "").trim();

  if (qty == null) {
    return {
      mode: "unverified",
      qty: null,
      status: SSI_STATUS.UNVERIFIED,
      reason: reasonCode || null,
      label: null,
      presentationFallback: false,
      title: note || "Stock Dapat Dijual belum terverifikasi — bukan ketersediaan jual",
    };
  }

  const titleParts = [
    incomplete ? (SSI_REASON_LABEL.BASELINE_INCOMPLETE) : null,
    SSI_REASON_LABEL[reasonCode] || reasonCode || null,
    note || null,
    "Stock − komitmen SO aktif (boleh negatif)",
  ].filter(Boolean);

  // Keep negatives; UI shows number only (status retained for data-* / a11y title).
  if (incomplete || apiStatus === SSI_STATUS.UNVERIFIED) {
    return {
      mode: "qty",
      qty,
      status: SSI_STATUS.INDICATIVE,
      reason: reasonCode || "BASELINE_INCOMPLETE",
      label: null,
      presentationFallback: false,
      title: titleParts.join(" — "),
    };
  }

  return {
    mode: "qty",
    qty,
    status: SSI_STATUS.READY,
    reason: null,
    label: null,
    presentationFallback: false,
    title: titleParts.join(" — "),
  };
}

/**
 * Resolve Qty Polos display.
 * - Known numeric (incl. 0) → show exact qty (no Indikatif badge).
 * - null / UNVERIFIED → presentation 0 only; API/DB semantics unchanged.
 */
export function resolvePolosDisplay(item, physicalPrimary) {
  const qty = item?.qty_polos ?? item?.qty_polos_gudang;
  const apiStatus = normalizeSsiStatus(
    item?.qty_polos_status || item?.qty_polos_gudang_status,
    { qty },
  );
  const reasonCode = String(
    item?.qty_polos_reason || item?.qty_polos_gudang_reason || "",
  ).trim();
  const note = String(item?.qty_polos_gudang_note || "").trim();
  const incomplete = isPhysicalBaselineIncomplete(item, physicalPrimary);

  // Presentation fallback only when backend qty is null/unknown — never overwrite a proven number.
  if (qty == null) {
    return {
      mode: "presentation_zero",
      qty: 0,
      status: SSI_STATUS.UNVERIFIED,
      reason: reasonCode || "DEPENDENT_UNKNOWN",
      label: null,
      presentationFallback: true,
      title: [
        SSI_PRESENTATION_ZERO_HINT,
        SSI_REASON_LABEL[reasonCode] || reasonCode,
        note,
      ].filter(Boolean).join(" — "),
    };
  }

  const reason = incomplete
    ? "BASELINE_INCOMPLETE"
    : (reasonCode || "SIMPLE_POLOS_NO_WIP_COVERAGE");
  const status = apiStatus === SSI_STATUS.READY
    ? SSI_STATUS.READY
    : (apiStatus === SSI_STATUS.UNVERIFIED ? SSI_STATUS.UNVERIFIED : SSI_STATUS.INDICATIVE);
  return {
    mode: "qty",
    qty,
    status,
    reason,
    label: null,
    presentationFallback: false,
    title: [
      SSI_REASON_LABEL[reason] || reason,
      note,
    ].filter(Boolean).join(" — "),
  };
}

/**
 * CSV/export helper: never treat presentation fallback as a confirmed quantity.
 * Returns "" when backend qty is null/unknown; otherwise the raw numeric.
 */
export function exportQtyCellValue(row, key) {
  const v = row?.[key];
  if (v == null || v === "") return "";
  return v;
}
