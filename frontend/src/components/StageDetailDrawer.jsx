import { X } from "lucide-react";
import { STAGES, formatDate } from "../lib/constants";

/**
 * Read-only drawer showing detail information about a specific stage.
 * Opening/closing the drawer does NOT modify workflow state.
 * Edit affordances (edit buttons) should be rendered by PODetail with proper
 * permission checks. This drawer only surfaces existing data.
 */
export default function StageDetailDrawer({ order, stageIdx, onClose, editSlot }) {
  if (stageIdx == null || !order) return null;
  const label = STAGES[stageIdx];
  const timeline = (order.timeline || []).filter((t) => (t.stage === stageIdx) || (t.stage_label && t.stage_label === label));

  return (
    <div className="drawer" role="dialog" aria-modal="true" data-testid="stage-drawer" onClick={onClose}>
      <div className="drawer-panel" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head">
          <div>
            <p className="eyebrow">Tahap {stageIdx + 1} dari {STAGES.length}</p>
            <h3 data-testid="stage-drawer-title">{label}</h3>
            <p className="muted small">PO {order.po_number} · {order.customer}</p>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Tutup" data-testid="stage-drawer-close"><X size={18}/></button>
        </header>

        <div className="drawer-body">
          <StageContent order={order} stageIdx={stageIdx} />

          {timeline.length > 0 && (
            <div className="drawer-block">
              <h4>Histori tahap ini</h4>
              <ul className="drawer-timeline" data-testid="stage-timeline">
                {timeline.slice().reverse().map((t, i) => (
                  <li key={i}>
                    <span className="mono small">{formatDate(t.at)}</span>
                    <span>{t.event || t.type || t.action}{t.note ? ` — ${t.note}` : ""}</span>
                    {t.by && <span className="muted small">oleh {t.by_name || t.by}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {editSlot && (
            <div className="drawer-actions" data-testid="stage-drawer-actions">
              {editSlot}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function KV({ label, value, testid }) {
  return (
    <div className="kv" data-testid={testid}>
      <dt>{label}</dt>
      <dd>{value == null || value === "" ? <span className="muted">-</span> : value}</dd>
    </div>
  );
}

function StageContent({ order, stageIdx }) {
  // Stage index reference (matches /lib/constants STAGES):
  // 0 Order Masuk · 1 Design · 2 Approval Design · 3 Payment · 4 Approval Payment
  // 5 Penjadwalan · 6 Screener · 7 Persiapan Helper · 8 Operator · 9 Konfirmasi Sample
  // 10 Cetak · 11 Packing · 12 Surat Jalan · 13 Loading · 14 Driver · 15 Terkirim
  if (stageIdx === 0) {
    return (
      <div className="drawer-block" data-testid="stage-detail-order">
        <h4>Order Masuk</h4>
        <dl className="kv-grid">
          <KV label="No PO" value={order.po_number} testid="stage-po-number"/>
          <KV label="Customer" value={order.customer}/>
          <KV label="Outlet" value={order.outlet}/>
          <KV label="Tanggal Order" value={order.order_date ? formatDate(order.order_date) : null}/>
          <KV label="Deadline" value={order.deadline ? formatDate(order.deadline) : null}/>
          <KV label="Total Qty" value={order.total_quantity != null ? Number(order.total_quantity).toLocaleString("id-ID") : null}/>
          <KV label="Sales" value={order.sales || order.sales_user}/>
          <KV label="Pembuat PO" value={order.created_by_name || order.created_by_user || order.imported_by}/>
        </dl>
      </div>
    );
  }
  if (stageIdx === 1 || stageIdx === 2) {
    const files = order.design_files || [];
    return (
      <div className="drawer-block" data-testid="stage-detail-design">
        <h4>Design</h4>
        <dl className="kv-grid">
          <KV label="Status" value={order.design_status || "-"} testid="stage-design-status"/>
          <KV label="Bypass" value={order.design_bypassed ? "Ya" : "Tidak"}/>
          <KV label="Approver" value={order.design_approved_by_name || order.design_approved_by}/>
          <KV label="Approved at" value={order.design_approved_at ? formatDate(order.design_approved_at) : null}/>
        </dl>
        <p className="drawer-sub">Berkas desain ({files.length})</p>
        <ul className="drawer-files">
          {files.map((f, i) => (
            <li key={i}><span className="mono small">{f.name || f.filename}</span> · <span className="muted small">oleh {f.uploaded_by_name || f.uploaded_by} · {formatDate(f.uploaded_at)}</span></li>
          ))}
          {!files.length && <li className="muted small">Belum ada berkas desain.</li>}
        </ul>
      </div>
    );
  }
  if (stageIdx === 3 || stageIdx === 4) {
    const files = order.payment_files || [];
    return (
      <div className="drawer-block" data-testid="stage-detail-payment">
        <h4>Payment</h4>
        <dl className="kv-grid">
          <KV label="Status" value={order.payment_status || "-"} testid="stage-payment-status"/>
          <KV label="Bypass" value={order.payment_bypassed ? "Ya" : "Tidak"}/>
          <KV label="Approver" value={order.payment_approved_by_name || order.payment_approved_by}/>
          <KV label="Approved at" value={order.payment_approved_at ? formatDate(order.payment_approved_at) : null}/>
        </dl>
        <p className="drawer-sub">Bukti pembayaran ({files.length})</p>
        <ul className="drawer-files">
          {files.map((f, i) => (
            <li key={i}><span className="mono small">{f.name || f.filename}</span> · <span className="muted small">oleh {f.uploaded_by_name || f.uploaded_by} · {formatDate(f.uploaded_at)}</span></li>
          ))}
          {!files.length && <li className="muted small">Belum ada bukti pembayaran.</li>}
        </ul>
      </div>
    );
  }
  if (stageIdx === 5) {
    return (
      <div className="drawer-block" data-testid="stage-detail-schedule">
        <h4>Penjadwalan</h4>
        <dl className="kv-grid">
          <KV label="Deadline" value={order.deadline ? formatDate(order.deadline) : null}/>
          <KV label="Prioritas" value={order.priority === "urgent" ? "URGENT" : "Normal"}/>
          <KV label="HOLD" value={order.hold ? "Ya" : "Tidak"}/>
          <KV label="Alasan HOLD" value={(order.hold_reasons || []).join("; ") || null}/>
        </dl>
      </div>
    );
  }
  if (stageIdx === 6) {
    const sc = order.screener_info || {};
    return (
      <div className="drawer-block" data-testid="stage-detail-screener">
        <h4>Persiapan Screen</h4>
        <dl className="kv-grid">
          <KV label="Screener" value={sc.screener_name || sc.screener || "-"}/>
          <KV label="Qty Screen" value={sc.screen_qty != null ? sc.screen_qty : "-"} testid="stage-screen-qty"/>
          <KV label="Catatan" value={sc.notes}/>
          <KV label="Waktu input" value={sc.updated_at ? formatDate(sc.updated_at) : null}/>
        </dl>
      </div>
    );
  }
  if (stageIdx === 7) {
    const hp = order.helper_prep || {};
    return (
      <div className="drawer-block" data-testid="stage-detail-helper">
        <h4>Persiapan Barang</h4>
        <dl className="kv-grid">
          <KV label="Helper" value={hp.helper_name || hp.helper || "-"}/>
          <KV label="Qty Dibutuhkan" value={hp.qty_required}/>
          <KV label="Qty Disiapkan" value={hp.qty_prepared}/>
          <KV label="Qty Kurang" value={hp.qty_shortage}/>
          <KV label="Status Barang" value={hp.shortage_status || "-"} testid="stage-helper-status"/>
          <KV label="Catatan" value={hp.notes}/>
        </dl>
      </div>
    );
  }
  if (stageIdx >= 8 && stageIdx <= 10) {
    const splits = order.operator_splits || [];
    return (
      <div className="drawer-block" data-testid="stage-detail-operator">
        <h4>Operator / Cetak</h4>
        {splits.length ? (
          <table className="drawer-table">
            <thead><tr><th>Operator</th><th>Qty</th><th>Selesai</th><th>Stage</th></tr></thead>
            <tbody>
              {splits.map((s, i) => (
                <tr key={i}>
                  <td>{s.operator_name || s.operator_email}</td>
                  <td className="num">{s.quantity || 0}</td>
                  <td className="num">{s.qty_completed || 0}</td>
                  <td>{STAGES[s.stage] || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="muted small">Belum ada penugasan operator.</p>}
        <p className="drawer-sub">Sample</p>
        <ul className="drawer-files">
          {(order.sample_files || []).map((f, i) => (
            <li key={i}><span className="mono small">{f.name || f.filename}</span> · <span className="muted small">oleh {f.uploaded_by_name || f.uploaded_by}</span></li>
          ))}
          {!(order.sample_files || []).length && <li className="muted small">Belum ada foto sample.</li>}
        </ul>
      </div>
    );
  }
  if (stageIdx === 11) {
    return (
      <div className="drawer-block" data-testid="stage-detail-packing">
        <h4>Packing</h4>
        <dl className="kv-grid">
          <KV label="Status" value={order.packing_status || "-"} testid="stage-packing-status"/>
          <KV label="PIC" value={order.packed_by_name || order.packed_by}/>
          <KV label="Waktu" value={order.packed_at ? formatDate(order.packed_at) : null}/>
        </dl>
      </div>
    );
  }
  if (stageIdx === 12) {
    return (
      <div className="drawer-block" data-testid="stage-detail-dn">
        <h4>Surat Jalan</h4>
        <dl className="kv-grid">
          <KV label="No Surat Jalan" value={order.delivery_note_number}/>
          <KV label="Dibuat oleh" value={order.delivery_note_created_by_name || order.delivery_note_created_by}/>
          <KV label="Waktu" value={order.delivery_note_created_at ? formatDate(order.delivery_note_created_at) : null}/>
        </dl>
      </div>
    );
  }
  if (stageIdx >= 13 && stageIdx <= 15) {
    const loading = order.loading_checklist || {};
    const proofs = order.delivery_proofs || [];
    return (
      <div className="drawer-block" data-testid="stage-detail-delivery">
        <h4>Loading & Pengiriman</h4>
        <dl className="kv-grid">
          <KV label="Barang Loaded" value={loading.items_loaded ? "Ya" : "Belum"}/>
          <KV label="Segel OK" value={loading.seal_ok ? "Ya" : "Belum"}/>
          <KV label="Kendaraan" value={loading.vehicle_plate}/>
          <KV label="Driver" value={loading.driver_name}/>
          <KV label="Status Kirim" value={order.delivery_status || "-"} testid="stage-delivery-status"/>
          <KV label="Waktu Kirim" value={order.delivered_at ? formatDate(order.delivered_at) : null}/>
        </dl>
        <p className="drawer-sub">Bukti kirim ({proofs.length})</p>
        <ul className="drawer-files">
          {proofs.map((f, i) => (
            <li key={i}><span className="mono small">{f.name || f.filename}</span> · <span className="muted small">oleh {f.uploaded_by_name || f.uploaded_by}</span></li>
          ))}
          {!proofs.length && <li className="muted small">Belum ada bukti kirim.</li>}
        </ul>
      </div>
    );
  }
  return <p className="muted">Tidak ada detail tambahan untuk tahap ini.</p>;
}
