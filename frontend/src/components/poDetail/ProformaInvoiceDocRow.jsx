export default function ProformaInvoiceDocRow({ invoiceNumber, busy, onView }) {
  return (
    <li data-testid="po-doc-proforma">
      <span>Proforma Invoice</span>
      <strong data-testid="po-doc-proforma-number">{invoiceNumber || "Draft"}</strong>
      <button
        type="button"
        className="ghost sm"
        data-testid="btn-lihat-proforma"
        onClick={onView}
        disabled={busy}
      >
        Lihat Proforma
      </button>
    </li>
  );
}
