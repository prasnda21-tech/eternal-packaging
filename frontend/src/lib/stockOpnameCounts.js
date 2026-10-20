import { wibLocalInputToIso } from "./stockOpnameTime";

/**
 * Build Stock Opname count PATCH lines from UI drafts.
 * Operational mode requires an unambiguous physical_count_at for every qty line
 * (per-line WIB input or explicit bulk time). Never invents timestamps.
 *
 * @returns {{ ok: true, lines: object[] } | { ok: false, error: string, lines: object[] }}
 */
export function buildStockOpnameCountPayload({
  sessionLines = [],
  drafts = {},
  timeDrafts = {},
  applyBulkTime = false,
  bulkCountAt = "",
  mode = "freeze",
} = {}) {
  const isOperational = mode === "operational";
  const bulkIso = applyBulkTime ? wibLocalInputToIso(bulkCountAt) : null;
  if (applyBulkTime && !bulkIso) {
    return {
      ok: false,
      error: "Centang terapkan waktu hitung ke banyak baris memerlukan waktu WIB yang valid.",
      lines: [],
    };
  }

  const lines = [];
  for (const line of sessionLines || []) {
    const lineId = line.line_id;
    const raw = drafts[lineId];
    const timeRaw = timeDrafts[lineId];
    const timeText = String(timeRaw ?? "").trim();
    const hasQty = !(raw === "" || raw === undefined || raw === null);
    const lineTimeIso = wibLocalInputToIso(timeRaw);
    const physicalCountAt = applyBulkTime && hasQty ? bulkIso : lineTimeIso;

    if (hasQty && isOperational) {
      if (!applyBulkTime && timeText && !lineTimeIso) {
        return {
          ok: false,
          error: `Waktu hitung tidak valid untuk ${line.item_code || lineId}. Gunakan format WIB datetime-local.`,
          lines: [],
        };
      }
      if (!physicalCountAt && !line.physical_count_at) {
        return {
          ok: false,
          error: `Isi waktu hitung fisik (WIB) untuk ${line.item_code || lineId} sebelum simpan/review. Kosong tidak diizinkan di mode operational.`,
          lines: [],
        };
      }
    }

    if (!hasQty && !physicalCountAt) {
      continue;
    }

    const row = { line_id: lineId };
    if (hasQty) {
      row.physical_count_qty = raw;
    }
    if (physicalCountAt) {
      row.physical_count_at = physicalCountAt;
    }
    // Preserve existing server time when qty is resent without retyping time.
    // Do not invent timestamps; do not silently clear count time on qty-only save.
    if (row.physical_count_qty !== undefined || Object.prototype.hasOwnProperty.call(row, "physical_count_at")) {
      lines.push(row);
    }
  }

  if (!lines.length) {
    return {
      ok: false,
      error: "Isi Stock Fisik. Kosong bukan nol. Waktu hitung diisi terpisah (bukan waktu impor).",
      lines: [],
    };
  }

  return { ok: true, lines };
}
