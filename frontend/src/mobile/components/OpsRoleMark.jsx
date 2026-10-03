/** Compact Cup/Screen/Cetak/Pack/Load/Kirim marker. Tone is pre-derived. */

export default function OpsRoleMark({ tone = "idle", title = "", testid }) {
  if (tone === "unavailable") {
    return (
      <span
        className="mop-ops-mark is-unavailable"
        title={title || "Status belum tersedia"}
        data-testid={testid}
        aria-label="Status belum tersedia"
      >
        ·
      </span>
    );
  }
  const kind = tone === "done" || tone === "pending" ? tone : "idle";
  const label = kind === "done" ? "selesai semua" : kind === "pending" ? "sebagian dikerjakan" : "belum dikerjakan";
  return (
    <span
      className={`mop-ops-mark is-${kind}`}
      title={title || label}
      data-testid={testid}
      aria-label={label}
    >
      {kind === "done" ? "✓" : kind === "idle" ? "—" : ""}
    </span>
  );
}
