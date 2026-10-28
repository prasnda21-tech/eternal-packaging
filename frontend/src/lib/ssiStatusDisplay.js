/**
 * SSI P0-B1 UI-only display-status contract.
 *
 * Uses existing catalog fields + stockPhysicalPrimaryDisplay incompleteness.
 * Does not change API payloads or Excel formulas.
 *
 * Precedence: UNVERIFIED > INDICATIVE > READY (display verified).
 * - Stock Dapat Dijual: Indikatif when Stock cell is Belum lengkap / Belum saldo.
 * - Qty Polos: never shown as fully verified (no WIP/rusak/plain coverage in API);
 *   Belum terverifikasi when qty null / UNVERIFIED; Indikatif when a number exists.
 */

export const SSI_STATUS = {
  UNVERIFIED: "UNVERIFIED",
  INDICATIVE: "INDICATIVE",
  READY: "READY",
};

export const SSI_REASON_LABEL = {
  PRINT_DELIVERY_UNPROVEN: "Pengiriman cetak belum terbukti dari SJ print",
  COMPLETION_UNPROVEN: "Konfirmasi selesai cetak induk belum terverifikasi (multiwarna/Add On)",
  NO_OPERATOR_EVIDENCE: "Belum ada konfirmasi cetak selesai operator yang valid",
  DEPENDENT_UNKNOWN: "Nilai bergantung kolom yang belum terverifikasi",
  BASELINE_INCOMPLETE: "Stok fisik belum lengkap / belum direkonsiliasi",
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
      label: "Belum terverifikasi",
      title: note || "Stock Dapat Dijual belum terverifikasi",
    };
  }

  // Keep negatives; qualify when Stock baseline incomplete or API says unavailable.
  if (incomplete || apiStatus === SSI_STATUS.UNVERIFIED) {
    const title = [
      "Indikatif — stok fisik belum lengkap",
      SSI_REASON_LABEL[reasonCode] || reasonCode,
      note,
      "Komitmen SO boleh negatif",
    ].filter(Boolean).join(" — ");
    return {
      mode: "indicative",
      qty,
      status: SSI_STATUS.INDICATIVE,
      reason: reasonCode || "BASELINE_INCOMPLETE",
      label: "Indikatif",
      title,
    };
  }

  return {
    mode: "qty",
    qty,
    status: SSI_STATUS.READY,
    reason: null,
    label: null,
    title: note || "Stock − (PO aktif − qty sudah dikirim); boleh negatif",
  };
}

/**
 * Resolve Qty Polos display — never fully verified without WIP coverage evidence.
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

  if (qty == null || apiStatus === SSI_STATUS.UNVERIFIED) {
    return {
      mode: "unverified",
      qty: null,
      status: SSI_STATUS.UNVERIFIED,
      reason: reasonCode || "DEPENDENT_UNKNOWN",
      label: "Belum terverifikasi",
      title: [
        note || "Belum terverifikasi",
        SSI_REASON_LABEL[reasonCode] || reasonCode,
      ].filter(Boolean).join(" — "),
    };
  }

  // Numeric exists (often API READY when print/SJ ready) — still Indikatif only.
  const reason = incomplete
    ? "BASELINE_INCOMPLETE"
    : (reasonCode || "SIMPLE_POLOS_NO_WIP_COVERAGE");
  return {
    mode: "indicative",
    qty,
    status: SSI_STATUS.INDICATIVE,
    reason,
    label: "Indikatif",
    title: [
      "Indikatif",
      SSI_REASON_LABEL[reason] || reason,
      note,
    ].filter(Boolean).join(" — "),
  };
}
