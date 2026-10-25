import { X } from "lucide-react";
import { STAGES, formatDate } from "../../lib/constants";
import { isCustomerDesignApproved } from "../../lib/designEvidence";
import { formatPhysicalOrderQty } from "../../lib/orderPhysicalQty";
import PoIdentityBadges from "../PoIdentityBadges";

/**
 * Cross-stage PO summary. Presentation only — no fetch or write.
 */
export default function PoSummaryDrawer({
  open,
  order,
  actualStage,
  viewedStage,
  onClose,
}) {
  if (!order) return null;
  const lineCount = order.item_count ?? (order.line_items || []).length;
  const designOk = order.design_status === "Approved" || isCustomerDesignApproved(order);
  const paymentOk = order.payment_status === "Approved";

  return (
    <div
      className={"po-summary-drawer-root" + (open ? " is-open" : "")}
      data-testid="po-summary-drawer-root"
      hidden={!open}
    >
      <button
        type="button"
        className="po-summary-drawer-scrim"
        data-testid="po-summary-drawer-scrim"
        aria-label="Tutup ringkasan"
        onClick={onClose}
      />
      <aside
        className="po-summary-drawer"
        data-testid="po-summary-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="po-summary-drawer-title"
      >
        <header className="po-summary-drawer-head">
          <div>
            <p className="po-summary-kicker">Ringkasan PO</p>
            <h2 id="po-summary-drawer-title">{order.customer || "Customer"}</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            data-testid="po-summary-drawer-close"
            aria-label="Tutup"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </header>
        <div className="po-summary-drawer-body">
          <dl className="po-summary-dl">
            <div>
              <dt>Nomor PO</dt>
              <dd className="mono">
                {order.po_number || "-"}{" "}
                <PoIdentityBadges order={order} testIdPrefix="po-summary-identity" />
              </dd>
            </div>
            <div>
              <dt>Outlet / cabang</dt>
              <dd>{order.outlet || order.branch_name || order.branch || "-"}</dd>
            </div>
            <div>
              <dt>Alamat</dt>
              <dd>{order.address || "-"}</dd>
            </div>
            <div>
              <dt>Sales</dt>
              <dd>{order.sales || order.sales_user || "-"}</dd>
            </div>
            <div>
              <dt>Tanggal PO</dt>
              <dd>{formatDate(order.order_date) || "-"}</dd>
            </div>
            <div>
              <dt>Target pengiriman</dt>
              <dd>
                {formatDate(
                  order.tanggal_pengiriman
                  || order.effective_delivery_date
                  || order.delivery_deadline_effective
                  || order.scheduled_delivery_date
                  || order.delivery_deadline_manual
                ) || "-"}
              </dd>
            </div>
            <div>
              <dt>Total fisik</dt>
              <dd data-testid="po-summary-physical-qty">
                {formatPhysicalOrderQty(order) ?? "Qty fisik tidak tersedia"}
              </dd>
            </div>
            <div>
              <dt>Line item</dt>
              <dd>{lineCount}</dd>
            </div>
            <div>
              <dt>Payment</dt>
              <dd>{paymentOk ? "Approved" : (order.payment_status || "Menunggu")}</dd>
            </div>
            <div>
              <dt>Design</dt>
              <dd>{designOk ? "Approved" : (order.design_status || "Menunggu")}</dd>
            </div>
            <div>
              <dt>Tahap aktif</dt>
              <dd>{actualStage + 1}. {STAGES[actualStage]}</dd>
            </div>
            <div>
              <dt>Workspace dilihat</dt>
              <dd data-testid="po-summary-viewed-stage">{viewedStage + 1}. {STAGES[viewedStage]}</dd>
            </div>
            <div>
              <dt>Cetak selesai</dt>
              <dd>
                {Number(order.print_completed_qty ?? 0).toLocaleString("id-ID")}
                {" / "}
                {Number(order.print_qty_target ?? order.print_qty_total ?? 0).toLocaleString("id-ID")}
              </dd>
            </div>
            <div>
              <dt>Packing</dt>
              <dd>{Number(order.packing_info?.qty_packed || 0).toLocaleString("id-ID")} pcs</dd>
            </div>
          </dl>
          {order.notes ? (
            <div className="po-summary-notes">
              <h3>Catatan</h3>
              <pre>{order.notes}</pre>
            </div>
          ) : (
            <p className="muted small">Belum ada catatan produksi.</p>
          )}
          {order.source_pdf?.original_filename ? (
            <p className="muted small">Dokumen: {order.source_pdf.original_filename}</p>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
