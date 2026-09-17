import { FileText } from "lucide-react";
import { getCanonicalCustomerName } from "../../lib/canonicalCustomer";
import { pengirimanSjDisplayLabel, shipmentIdentityKeys } from "../../lib/deliveryProof";
import { deliveryAddressFromOrder } from "../../lib/mobileWorkspaces";
import { mobileSuratJalanViewerHref } from "../../lib/suratJalan";
import MobileTaskModal from "./MobileTaskModal";

function formatKirimDate(raw) {
  const text = String(raw || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return "—";
  const [y, m, d] = text.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.toLocaleDateString("id-ID", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function sjDetailLines(order, shipment) {
  const lines = Array.isArray(shipment?.lines) ? shipment.lines : [];
  return lines.map((ln, i) => {
    const idx = ln.line_idx != null ? Number(ln.line_idx) : i;
    const li = (order?.line_items || [])[idx] || {};
    return {
      key: `${idx}-${ln.line_id || i}`,
      name: String(ln.name || li.name || li.item_name || `Line ${idx + 1}`),
      qty: Number(ln.qty ?? ln.quantity ?? 0) || 0,
    };
  });
}

export function sjViewFileId(order, shipment) {
  if (!shipment) return "";
  const keys = shipmentIdentityKeys(order, shipment);
  const files = [
    ...(Array.isArray(order?.sj_files) ? order.sj_files : []),
    ...(Array.isArray(order?.surat_jalan_files) ? order.surat_jalan_files : []),
  ];
  const hit = files.find((f) => {
    const ids = [f?.id, f?.file_id, f?.shipment_id, f?.sj_number, f?.number]
      .map((v) => String(v || "").trim())
      .filter(Boolean);
    return ids.some((id) => keys.has(id));
  });
  return String(hit?.file_id || hit?.id || "").trim();
}

export default function MobileDeliverySjModal({
  open,
  onClose,
  order,
  shipment,
  sjOption,
  statusLabel,
}) {
  if (!open) return null;
  const number = pengirimanSjDisplayLabel(sjOption || { number: shipment?.number || shipment?.sj_number });
  const lines = sjDetailLines(order, shipment);
  const fileId = sjViewFileId(order, shipment);
  const viewHref = fileId && order?.id && !order?.preview
    ? mobileSuratJalanViewerHref(order.id, fileId, "delivery")
    : "";
  const date = formatKirimDate(shipment?.delivery_date || sjOption?.date || order?.tanggal_pengiriman);

  return (
    <MobileTaskModal
      open={open}
      onClose={onClose}
      title="Surat Jalan"
      subtitle={number}
      icon={<FileText size={22} aria-hidden="true" />}
      testid="mobile-sj-detail-modal"
      labelledBy="mobile-sj-detail-title"
    >
      <dl className="mop-sj-detail-dl">
        <div>
          <dt>No. Surat Jalan</dt>
          <dd data-testid="mobile-sj-detail-number">{number || "—"}</dd>
        </div>
        <div>
          <dt>PO</dt>
          <dd data-testid="mobile-sj-detail-po">{order?.po_number || "—"}</dd>
        </div>
        <div>
          <dt>Customer</dt>
          <dd data-testid="mobile-sj-detail-customer">{getCanonicalCustomerName(order)}</dd>
        </div>
        <div>
          <dt>Alamat</dt>
          <dd data-testid="mobile-sj-detail-address">{deliveryAddressFromOrder(order) || "Alamat tidak tersedia"}</dd>
        </div>
        <div>
          <dt>Tanggal</dt>
          <dd data-testid="mobile-sj-detail-date">{date}</dd>
        </div>
        {statusLabel ? (
          <div>
            <dt>Status</dt>
            <dd data-testid="mobile-sj-detail-status">{statusLabel}</dd>
          </div>
        ) : null}
      </dl>
      <h3 className="mop-sj-detail-lines-title">Item</h3>
      <ul className="mop-sj-detail-lines" data-testid="mobile-sj-detail-lines">
        {lines.length ? lines.map((ln) => (
          <li key={ln.key}>
            <span>{ln.name}</span>
            <strong>{Number(ln.qty).toLocaleString("id-ID")} pcs</strong>
          </li>
        )) : (
          <li className="mop-muted">Tidak ada item pada Surat Jalan ini.</li>
        )}
      </ul>
      {viewHref ? (
        <a
          className="mop-btn-primary mop-sj-detail-print"
          href={viewHref}
          data-testid="mobile-sj-view-print"
        >
          Lihat / Cetak Surat Jalan
        </a>
      ) : null}
      <button
        type="button"
        className="mop-task-close-btn"
        onClick={onClose}
        data-testid="mobile-sj-detail-close"
      >
        Tutup
      </button>
    </MobileTaskModal>
  );
}

export { formatKirimDate };
