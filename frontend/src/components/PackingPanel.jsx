import { useState } from "react";
import { Package, CheckCircle2 } from "lucide-react";
import { formatDate } from "../lib/constants";
import { packingSubmitEnabled } from "../lib/packingCard";

// Packing = print (setelah selesai operator) + non_print (langsung tersedia).
// Target akhir = packing_required_total. Partial packing boleh lanjut ke SJ.
export default function PackingPanel({ order, canManage, busy, onSaveQty, onComplete }) {
  const pi = order.packing_info || {};
  const [qty, setQty] = useState(pi.qty_packed ?? "");
  const [notes, setNotes] = useState(pi.notes ?? "");
  const hasQty = pi.qty_packed !== null && pi.qty_packed !== undefined && String(pi.qty_packed).trim() !== "" && Number(pi.qty_packed) > 0;
  const semantic = order.packing_status_semantic || "Belum";
  const fullyPacked = semantic === "Sudah";
  const lineItems = order.line_items || [];
  const requiredTotal = typeof order.packing_required_total === "number"
    ? order.packing_required_total
    : lineItems.reduce((s, li) => s + Number(li.quantity || 0), 0);
  const available = typeof order.available_for_packing === "number"
    ? order.available_for_packing
    : requiredTotal;
  const printCompleted = Number(order.print_completed_qty || 0);
  const nonPrintTotal = Number(order.non_print_qty_total || 0);
  const packedSaved = Number(pi.qty_packed || 0);
  const packedDraft = Number(qty || 0);
  const maxAllowed = Math.min(requiredTotal || Infinity, available || 0);
  const shortagePreview = Math.max(requiredTotal - packedDraft, 0);
  const shortageSaved = Math.max(requiredTotal - packedSaved, 0);
  const canProceedPartial = hasQty && packedSaved > 0;
  const isComplete = hasQty && packedSaved >= requiredTotal;
  const canSubmitQty = packingSubmitEnabled(canManage, available);
  const remainingAvailable = Math.max(available - packedSaved, 0);

  const handleSave = async () => {
    const parsed = parseInt(qty, 10);
    if (!Number.isFinite(parsed) || parsed <= 0) return;
    await onSaveQty(parsed, notes || "");
  };

  return (
    <section className="panel" data-testid="packing-panel">
      <header className="panel-head">
        <h3><Package size={14} /> Packing</h3>
        <span className={`pill sm ${semantic === "Sudah" ? "pill-ok" : ""}`} data-testid="packing-semantic-badge">
          {semantic === "Sudah" ? "Packing Selesai" : semantic === "Berjalan" ? "Packing Berjalan" : "Menunggu"}
        </span>
        <span className="pill sm" data-testid="packing-target-badge" style={{ background: "#e6ecf3", color: "#3a4b60" }}>
          Target: {requiredTotal.toLocaleString("id-ID")} pcs (semua line)
        </span>
        <span className="pill sm" data-testid="packing-available-badge" style={{ background: "#efe6d3", color: "#7a6f52" }}>
          Tersedia: {available.toLocaleString("id-ID")} pcs
        </span>
      </header>

      <p className="muted small" data-testid="packing-availability-note" style={{ marginTop: 0 }}>
        Non-cetak: <b>{nonPrintTotal.toLocaleString("id-ID")}</b> (langsung) · Cetak selesai operator:{" "}
        <b>{printCompleted.toLocaleString("id-ID")}</b> · Total tersedia packing:{" "}
        <b>{available.toLocaleString("id-ID")}</b> / {requiredTotal.toLocaleString("id-ID")}
        {" "}· Sudah packing: <b data-testid="packing-cumulative-packed">{packedSaved.toLocaleString("id-ID")}</b>
        {" "}· Sisa tersedia: <b data-testid="packing-remaining-available">{remainingAvailable.toLocaleString("id-ID")}</b>
      </p>

      {lineItems.length > 0 && (
        <table className="prep-table" data-testid="packing-lines-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, marginBottom: 10 }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
              <th style={{ padding: "6px 8px" }}>Barang</th>
              <th style={{ padding: "6px 8px" }}>Proses</th>
              <th className="num" style={{ padding: "6px 8px", textAlign: "right" }}>Qty PO</th>
            </tr>
          </thead>
          <tbody>
            {lineItems.map((li, idx) => {
              const isPrint = (li.production_process || "print").toLowerCase() !== "non_print";
              return (
                <tr key={idx} data-testid={`packing-line-row-${idx}`} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "8px" }}>
                    <div><strong>{li.name}</strong></div>
                    {li.color && <div className="muted small">{li.color}</div>}
                  </td>
                  <td style={{ padding: "8px" }}>
                    <span className="pill sm" style={{ background: isPrint ? "#e6ecf3" : "#efe6d3", color: isPrint ? "#3a4b60" : "#7a6f52" }} data-testid={`packing-line-process-${idx}`}>
                      {isPrint ? "Cetak" : "Non-Cetak"}
                    </span>
                  </td>
                  <td className="num mono" style={{ padding: "8px", textAlign: "right" }} data-testid={`packing-line-qty-${idx}`}>
                    {Number(li.quantity || 0).toLocaleString("id-ID")}
                  </td>
                </tr>
              );
            })}
            <tr style={{ background: "#faf6ee", fontWeight: 600 }}>
              <td style={{ padding: "8px" }}>Total (target Packing)</td>
              <td></td>
              <td className="num mono" style={{ padding: "8px", textAlign: "right" }} data-testid="packing-target-total">{requiredTotal.toLocaleString("id-ID")}</td>
            </tr>
          </tbody>
        </table>
      )}

      {hasQty && (
        <p className="muted small" data-testid="packing-progress-line" style={{ marginTop: 0 }}>
          Qty Packing tersimpan: <b data-testid="packing-saved-qty">{packedSaved.toLocaleString("id-ID")}</b>
          {" "}/ {requiredTotal.toLocaleString("id-ID")}
          {shortageSaved > 0 && (
            <span style={{ color: "#8f1f1f", marginLeft: 6 }} data-testid="packing-saved-shortage">
              — kurang {shortageSaved.toLocaleString("id-ID")}
            </span>
          )}
          {shortageSaved === 0 && (
            <span style={{ color: "#2b7a2b", marginLeft: 6 }} data-testid="packing-saved-complete">— lengkap</span>
          )}
        </p>
      )}

      {available <= 0 && !fullyPacked && (
        <p className="muted small" data-testid="packing-no-available-note">
          Belum ada qty yang tersedia untuk Packing. Qty Tidak Cetak dan qty Cetak yang sudah selesai operator akan muncul di sini.
        </p>
      )}

      {fullyPacked ? (
        <p className="muted small" data-testid="packing-done-note">
          Packing sudah lengkap — {hasQty ? `${packedSaved.toLocaleString("id-ID")} pcs` : ""}{pi.updated_by_name ? ` · oleh ${pi.updated_by_name}` : ""}{pi.updated_at ? ` · ${formatDate(pi.updated_at)}` : ""}.
        </p>
      ) : !canManage ? (
        <p className="muted small" data-testid="packing-readonly-note">
          Anda tidak memiliki permission <code>packing.update</code> untuk mengisi Qty Packing.
        </p>
      ) : (
        <div className="packing-form" data-testid="packing-form" style={{display:"grid",gap:12}}>
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12}}>
            <label>
              <span className="lbl">Qty Packing</span>
              <input
                type="number"
                min="1"
                max={maxAllowed || undefined}
                value={qty}
                onChange={(e) => setQty(e.target.value)}
                disabled={busy || !canSubmitQty}
                data-testid="packing-qty-input"
                placeholder="Jumlah unit yang di-packing"
              />
              <span className="muted small" data-testid="packing-qty-hint">
                Maks tersedia sekarang: {available.toLocaleString("id-ID")} pcs (cap total {requiredTotal.toLocaleString("id-ID")}).
                {packedDraft > 0 && (
                  shortagePreview > 0
                    ? <> Kurang {shortagePreview.toLocaleString("id-ID")} dari target.</>
                    : <> Lengkap.</>
                )}
              </span>
            </label>
            <label>
              <span className="lbl">Catatan (opsional)</span>
              <input
                type="text"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                disabled={busy || !canSubmitQty}
                data-testid="packing-notes-input"
              />
            </label>
          </div>
          {pi.updated_at && (
            <p className="muted small" data-testid="packing-last-update">
              Terakhir diupdate {formatDate(pi.updated_at)}
              {pi.updated_by_name ? ` oleh ${pi.updated_by_name}` : ""}
              {hasQty ? ` — Qty tersimpan: ${packedSaved.toLocaleString("id-ID")} pcs` : ""}
            </p>
          )}
          <div style={{display:"flex",flexWrap:"wrap",gap:8}}>
            <button
              type="button"
              className="primary"
              onClick={handleSave}
              disabled={busy || !canSubmitQty || !qty || Number(qty) <= 0}
              data-testid="save-packing-qty-button"
            >
              Simpan Qty Packing
            </button>
            <button
              type="button"
              className="ghost"
              onClick={onComplete}
              disabled={busy || !canProceedPartial}
              title={
                !hasQty
                  ? "Qty Packing harus diisi terlebih dahulu"
                  : isComplete
                    ? "Selesaikan Packing lengkap dan lanjut ke Surat Jalan"
                    : "Lanjut ke Surat Jalan dengan qty packing sebagian"
              }
              data-testid="complete-packing-button"
            >
              <CheckCircle2 size={16} /> {isComplete ? "Selesai Packing" : "Lanjut Packing Sebagian"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
