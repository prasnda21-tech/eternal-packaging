import { buildTransactionUomOptions, defaultTransactionUom, normalizeUom } from "../lib/transactionUomOptions";

/**
 * Minimal satuan picker for purchasing lines. No redesign — replaces read-only unit.
 */
export default function TransactionUomSelect({
  value,
  options,
  onChange,
  disabled = false,
  testId,
  "aria-label": ariaLabel = "Satuan transaksi",
}) {
  const list = Array.isArray(options) && options.length
    ? options
    : buildTransactionUomOptions(null, { unit: value || "PCS" });
  const current = defaultTransactionUom(list, value);
  const locked = list.length <= 1;

  if (locked) {
    return (
      <input
        value={normalizeUom(current)}
        readOnly
        tabIndex={-1}
        disabled={disabled}
        data-testid={testId}
        aria-label={ariaLabel}
        title="Hanya satuan dasar PCS (belum ada satuan alternatif pada master)"
      />
    );
  }

  return (
    <select
      value={current}
      disabled={disabled}
      data-testid={testId}
      aria-label={ariaLabel}
      title="Qty di baris ini memakai satuan terpilih; konversi ke PCS sekali di backend"
      onChange={(e) => onChange?.(normalizeUom(e.target.value))}
    >
      {list.map((row) => (
        <option key={row.uom} value={row.uom}>
          {row.label || row.uom}
        </option>
      ))}
    </select>
  );
}

export { buildTransactionUomOptions, defaultTransactionUom, normalizeUom };
