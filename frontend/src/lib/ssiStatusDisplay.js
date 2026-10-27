/**
 * SSI P0-B1 display-status contract (qualification only — numerics unchanged).
 *
 * Precedence: UNVERIFIED > INDICATIVE > READY (verified).
 * Stock Dapat Dijual: INDICATIVE when physical baseline incomplete.
 * Qty Polos: never READY/terverifikasi — no WIP/rusak/plain coverage field;
 *   UNVERIFIED when no defensible calc; INDICATIVE when formula produced a number.
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
 * Physical baseline incomplete when Stock cell would hide the number
 * (Belum saldo / Belum lengkap) OR API marks physical_baseline_complete false /
 * baseline unreconciled.
 */
export function isPhysicalBaselineIncomplete(item, physicalPrimary) {
  if (physicalPrimary && physicalPrimary.present === false) return true;
  if (item && item.physical_baseline_complete === false) return true;
  if (item && String(item.baseline_status || "") === "unreconciled") return true;
  return false;
}

/**
 * Resolve Stock Dapat Dijual display.
 * @returns {{ mode: 'unverified'|'indicative'|'qty', qty: number|null, status: string, reason: string|null, label: string|null, title: string }}
 */
export function resolveDapatDijualDisplay(item, physicalPrimary) {
  const qty = item?.stock_dapat_dijual ?? item?.qty_siap_jual;
  const apiStatus = normalizeSsiStatus(
    item?.stock_dapat_dijual_status || item?.qty_siap_jual_status,
    { qty },
  );
  const incomplete = isPhysicalBaselineIncomplete(item, physicalPrimary);
  const note = String(item?.qty_siap_jual_note || "").trim();
  const reasonCode = String(
    item?.stock_dapat_dijual_reason || item?.qty_siap_jual_reason || "",
  ).trim();

  if (qty == null && apiStatus === SSI_STATUS.UNVERIFIED) {
    return {
      mode: "unverified",
      qty: null,
      status: SSI_STATUS.UNVERIFIED,
      reason: reasonCode || null,
      label: "Belum terverifikasi",
      title: note || "Stock Dapat Dijual belum terverifikasi",
    };
  }

  // Precedence: incomplete baseline or API INDICATIVE → Indikatif (keep number)
  if (incomplete || apiStatus === SSI_STATUS.INDICATIVE) {
    const title = [
      "Indikatif — stok fisik belum lengkap",
      SSI_REASON_LABEL[reasonCode] || reasonCode,
      note,
      "Komitmen SO boleh negatif",
    ].filter(Boolean).join(" — ");
    return {
      mode: "indicative",
      qty: qty ?? null,
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
 * Resolve Qty Polos display.
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

  // Safe subset: never VERIFIED — INDICATIVE whenever a number exists
  const reason = reasonCode
    || (incomplete ? "BASELINE_INCOMPLETE" : "SIMPLE_POLOS_NO_WIP_COVERAGE");
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
