function detailText(err) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((row) => row.msg || String(row)).join(" ");
  return err?.message || "Gagal memproses stock opname.";
}

/** Map HTTP status to operator-facing guidance. Never auto-retry posting. */
export function stockOpnameErrorMessage(err, { sessionStatusLabel } = {}) {
  const status = err?.response?.status;
  const detail = detailText(err);
  if (status === 400) {
    return `Perbaiki input: ${detail}`;
  }
  if (status === 409) {
    const lower = String(detail || "").toLowerCase();
    const concurrent = lower.includes("perubahan bersamaan") || lower.includes("confirm_lock");
    const statusNote = sessionStatusLabel
      ? ` Status sesi saat ini: ${sessionStatusLabel}.`
      : "";
    if (concurrent) {
      return (
        `${detail}${statusNote} `
        + "Konfirmasi lain sedang diproses atau kunci sementara masih aktif. "
        + "Tunggu lalu coba sekali lagi. Tidak ada posting ulang otomatis."
      );
    }
    return `${detail}${statusNote} Data dipertahankan — review ulang angka/net movement sebelum konfirmasi. Tidak ada posting ulang otomatis.`;
  }
  if (status === 503) {
    return `${detail} Prasyarat: otoritas R2 fisik harus aktif di lingkungan yang diizinkan. Preview utama tetap R2 off.`;
  }
  const lower = String(detail || "").toLowerCase();
  if (lower.includes("r2") && (lower.includes("belum") || lower.includes("aktif"))) {
    return `${detail} Prasyarat: aktifkan otoritas R2 sebelum konfirmasi (bukan di preview utama).`;
  }
  return detail;
}
