import { useEffect, useState } from "react";
import { FileText, Truck } from "lucide-react";
import { formatDate, formatDisplayDateTime } from "../lib/constants";
import { useAuth } from "../lib/auth";
import { shipmentLinesForOrder, storedShipments } from "../lib/shipmentLines";
import { MetricCell, MetricRow, QuantityInput, QtyOpsHead, statusClassFor } from "./qtyUi";
import ConfirmActionModal from "./ConfirmActionModal";

function fmt(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

function initialDraft(rows) {
  const map = {};
  for (const row of rows) {
    map[row.line_idx] = Number(row.available_sj || 0);
  }
  return map;
}

function statusLabel(order, created) {
  const semantic = order.surat_jalan_status_semantic;
  if ((order.current_stage || 0) >= 13 && created) return "Surat Jalan Selesai";
  if (semantic === "completed") return "SJ lengkap — siap Loading";
  if (semantic === "partial") return "SJ parsial — siap Loading";
  if (created) return "SJ dibuat — siap Loading";
  return "Menunggu pembuatan Surat Jalan";
}

export default function SuratJalanPanel({
  order,
  canManage,
  busy,
  onCreate,
  footerActions = null,
}) {
  const { user } = useAuth();
  const rows = shipmentLinesForOrder(order);
  const shipments = storedShipments(order);
  const [number, setNumber] = useState(order.delivery_note_number || "");
  const [notes, setNotes] = useState(order.delivery_note_notes || "");
  const [draft, setDraft] = useState(() => initialDraft(rows));
  const [confirm, setConfirm] = useState(null);
  const [err, setErr] = useState("");
  const created = !!order.surat_jalan_created;
  const availableTotal = rows.reduce((s, r) => s + Number(r.available_sj || 0), 0);
  const packedTotal = rows.reduce((s, r) => s + Number(r.packed || 0), 0);
  const shippedTotal = rows.reduce((s, r) => s + Number(r.shipped || 0), 0);
  const draftTotal = rows.reduce((s, r) => s + Number(draft[r.line_idx] || 0), 0);
  const canSubmit = canManage && !busy && availableTotal > 0 && draftTotal > 0;
  const packingWait = (order.current_stage || 0) < 12 && !created;

  useEffect(() => {
    setNumber(order.delivery_note_number || "");
    setNotes(order.delivery_note_notes || "");
    setDraft(initialDraft(shipmentLinesForOrder(order)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order?.id, order?.delivery_note_number, order?.surat_jalan_status_semantic, packedTotal, shippedTotal, shipments.length]);

  const openConfirm = () => {
    const lines = rows
      .map((row) => ({
        line_idx: row.line_idx,
        qty: Math.max(0, parseInt(draft[row.line_idx], 10) || 0),
        accurate_item_id: (order.line_items || [])[row.line_idx]?.accurate_item_id || undefined,
      }))
      .filter((l) => l.qty > 0);
    if (!lines.length) return;
    setErr("");
    setConfirm({
      lines,
      qty: lines.reduce((s, l) => s + l.qty, 0),
      time: formatDisplayDateTime(new Date().toISOString()),
    });
  };

  const executeCreate = async () => {
    if (!confirm || busy) return;
    setErr("");
    try {
      await onCreate(number || null, notes || "", confirm.lines);
      setConfirm(null);
    } catch (e) {
      const d = e?.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Gagal membuat Surat Jalan"));
    }
  };

  return (
    <section className="panel packing-panel" data-testid="surat-jalan-panel">
      <QtyOpsHead icon={FileText} title="Surat Jalan">
        <span
          className={`qty-status ${statusClassFor(created ? "SAVED" : packingWait ? "IDLE" : "READY")}`}
          data-testid="surat-jalan-semantic-badge"
        >
          {packingWait ? "Menunggu Packing" : statusLabel(order, created)}
        </span>
      </QtyOpsHead>

      {packingWait ? (
        <p className="muted small" data-testid="surat-jalan-wait-note" style={{ padding: "8px 16px 0" }}>
          Surat Jalan dapat dibuat setelah Qty Packing disimpan dan packing sebagian/lengkap dikonfirmasi.
        </p>
      ) : (
        <>
          <MetricRow testid="sj-summary">
            <MetricCell label="Packed" value={packedTotal} testid="sj-packed-total" />
            <MetricCell label="Sudah SJ" value={shippedTotal} testid="sj-shipped-total" />
            <MetricCell label="Tersedia" value={availableTotal} testid="sj-available-total" />
          </MetricRow>

          {rows.length > 0 && (
            <div className="packing-lines-wrap">
              <table className="packing-lines-table" data-testid="sj-lines-table">
                <thead>
                  <tr>
                    <th>Barang / Item</th>
                    <th className="num">Qty PO</th>
                    <th className="num">Packed</th>
                    <th className="num">Sudah SJ</th>
                    <th className="num">Tersedia</th>
                    <th className="num">Input</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, idx) => {
                    const avail = Number(row.available_sj || 0);
                    return (
                      <tr key={row.line_idx ?? idx} data-testid={`sj-line-row-${idx}`}>
                        <td>
                          <div><strong>{row.name}</strong></div>
                          {row.color && <div className="muted small">{row.color}</div>}
                          {row.mismatch && (
                            <div className="muted small" data-testid={`sj-line-mismatch-${idx}`} style={{ color: "#8f1f1f" }}>
                              Qty SJ melebihi packing
                            </div>
                          )}
                        </td>
                        <td className="num mono">{fmt(row.qty_po)}</td>
                        <td className="num mono pack-cell-done">{fmt(row.packed)}</td>
                        <td className="num mono" data-testid={`sj-line-shipped-${idx}`}>{fmt(row.shipped)}</td>
                        <td className="num mono pack-cell-ready" data-testid={`sj-line-available-${idx}`}>{fmt(avail)}</td>
                        <td className="num packing-line-input-cell">
                          {!canManage || avail <= 0 ? (
                            <span className="mono">{fmt(0)}</span>
                          ) : (
                            <QuantityInput
                              label=""
                              className="is-compact"
                              min="0"
                              max={avail}
                              value={draft[row.line_idx] ?? avail}
                              onChange={(e) => setDraft((prev) => ({ ...prev, [row.line_idx]: e.target.value }))}
                              disabled={busy || avail <= 0}
                              testid={`sj-qty-input-${row.line_idx}`}
                              aria-label={`Qty Surat Jalan ${row.name}`}
                            />
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {shipments.length > 0 && (
            <div className="qty-ops-body" data-testid="sj-history">
              <p className="muted small" style={{ margin: "8px 0 4px" }}>Surat Jalan tersimpan</p>
              <table className="packing-lines-table" data-testid="sj-history-table">
                <thead>
                  <tr>
                    <th>Nomor</th>
                    <th>Sumber</th>
                    <th className="num">Qty</th>
                    <th>Waktu</th>
                  </tr>
                </thead>
                <tbody>
                  {shipments.map((sh, i) => (
                    <tr key={sh.id || sh.accurate_shipment_id || i} data-testid={`sj-history-row-${i}`}>
                      <td>{sh.number || sh.accurate_shipment_id}</td>
                      <td>{sh.source === "accurate" ? "Accurate" : "Eternal"}</td>
                      <td className="num mono">
                        {fmt((sh.lines || []).reduce((s, l) => s + Number(l.qty || l.quantity || 0), 0))}
                      </td>
                      <td className="muted small">{sh.created_at ? formatDate(sh.created_at) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {!canManage ? (
            <p className="muted small" data-testid="surat-jalan-readonly-note">
              Anda tidak memiliki permission <code>delivery_note.manage</code> untuk membuat Surat Jalan.
            </p>
          ) : (
            <div className="surat-jalan-form packing-form" data-testid="surat-jalan-form">
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <label>
                  <span className="lbl">Nomor Surat Jalan</span>
                  <input
                    type="text"
                    className="manual-input"
                    value={number}
                    onChange={(e) => setNumber(e.target.value)}
                    disabled={busy}
                    placeholder={`SJ-${(order.po_number || "").replace(/^PO[.-]/, "")}`}
                    data-testid="surat-jalan-number-input"
                  />
                </label>
                <label>
                  <span className="lbl">Catatan</span>
                  <input
                    type="text"
                    className="manual-input"
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    disabled={busy}
                    data-testid="surat-jalan-notes-input"
                  />
                </label>
              </div>
              {created && (
                <p className="muted small" data-testid="surat-jalan-last-update">
                  Terakhir: SJ #{order.delivery_note_number} oleh {order.delivery_note_created_by_name || order.delivery_note_created_by} · {formatDate(order.delivery_note_created_at)}
                </p>
              )}
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                <button
                  type="button"
                  className="primary"
                  onClick={openConfirm}
                  disabled={!canSubmit}
                  data-testid="create-surat-jalan-button"
                >
                  <Truck size={16} /> {created ? "Buat Surat Jalan berikutnya" : "Buat Surat Jalan"}
                </button>
              </div>
            </div>
          )}
        </>
      )}
      {footerActions}
      <ConfirmActionModal
        open={!!confirm}
        stage="Surat Jalan"
        poNumber={order.po_number}
        action={number ? `Buat Surat Jalan ${number}` : "Buat Surat Jalan"}
        qty={confirm?.qty}
        name={user?.name || user?.email}
        time={confirm?.time}
        extra={number ? <p className="muted small">No SJ: {number}</p> : null}
        busy={busy}
        error={err}
        onCancel={() => { if (!busy) setConfirm(null); }}
        onConfirm={executeCreate}
      />
    </section>
  );
}
