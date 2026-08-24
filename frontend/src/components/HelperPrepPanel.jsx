import { useEffect, useState } from "react";
import { Check, AlertTriangle, Info } from "lucide-react";
import api from "../lib/api";
import { formatApiError } from "../lib/errors";
import { formatDate } from "../lib/constants";

/**
 * HelperPrepPanel — Persiapan Helper PER LINE ITEM CETAK.
 * Revisi routing: hanya line item dengan production_process === "print" yang
 * masuk scope Persiapan Helper. Line non-print DIABAIKAN. Qty Dibutuhkan =
 * read-only dari line.quantity (source of truth PO). Legacy line tanpa
 * production_process dianggap "print" (backward compatible).
 */
const isPrintLine = (li) => (li?.production_process || "print").toLowerCase() !== "non_print";

export default function HelperPrepPanel({ order, canEdit, onUpdated, footerActions = null }) {
  const allLineItems = order?.line_items || [];
  // Filter hanya line Cetak — Helper hanya menyiapkan barang untuk Cetak.
  const printLines = allLineItems
    .map((li, idx) => ({ li, idx }))
    .filter(({ li }) => isPrintLine(li));
  const helperItems = order?.helper_prep?.items || [];
  const requiresPrinting = order?.requires_printing !== false; // legacy → print

  const initialDraft = () => {
    const map = {};
    for (const { idx } of printLines) {
      const existing = helperItems.find((it) => it.line_idx === idx);
      map[idx] = existing ? Number(existing.qty_prepared || 0) : 0;
    }
    return map;
  };
  const [draft, setDraft] = useState(initialDraft);
  const [notes, setNotes] = useState(order?.helper_prep?.notes || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    setDraft(initialDraft());
    setNotes(order?.helper_prep?.notes || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order?.id, order?.helper_prep?.updated_at, printLines.length]);

  const totalRequired = printLines.reduce((s, { li }) => s + Number(li.quantity || 0), 0);
  const totalPrepared = Object.values(draft).reduce((s, v) => s + Number(v || 0), 0);
  const totalShortage = Math.max(totalRequired - totalPrepared, 0);

  // Full non-print → Persiapan Helper TIDAK DIPERLUKAN.
  if (!requiresPrinting) {
    return (
      <section className="panel" data-testid="helper-prep-panel">
        <header className="panel-head">
          <h3>Persiapan Helper</h3>
          <span className="pill sm" data-testid="helper-not-required-badge" style={{ background: "#efe6d3", color: "#7a6f52" }}>
            Tidak Diperlukan
          </span>
        </header>
        <p className="muted small" data-testid="helper-not-required-note">
          <Info size={12}/> PO ini tidak memiliki barang dengan proses Cetak. Persiapan Helper hanya menyiapkan barang yang akan melalui proses Cetak.
        </p>
        {footerActions}
      </section>
    );
  }

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      // Kirim hanya line CETAK. Backend endpoint helper-prep/items menerima
      // list items apa saja dan meng-loop line_items yang ada, jadi non-print
      // line yang tidak dikirim akan tetap ada tapi qty_prepared=0 (aman).
      const items = Object.entries(draft).map(([idx, qty]) => ({
        line_idx: Number(idx),
        qty_prepared: Math.max(0, Number(qty || 0)),
      }));
      const r = await api.updateHelperPrepItems(order.id, items, notes);
      onUpdated?.(r.order);
    } catch (e) {
      setErr(formatApiError(e, "Gagal menyimpan Persiapan Helper"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel" data-testid="helper-prep-panel">
      <header className="panel-head">
        <h3>Persiapan Helper</h3>
        <span className="pill sm" data-testid="helper-scope-badge" style={{ background: "#e6ecf3", color: "#3a4b60" }}>
          Scope Cetak: {totalRequired.toLocaleString("id-ID")} pcs
        </span>
        {totalShortage > 0 && (
          <span className="urgent-badge" data-testid="helper-total-shortage-badge">
            <AlertTriangle size={12}/> KURANG {totalShortage.toLocaleString("id-ID")} PCS
          </span>
        )}
        {totalShortage === 0 && totalPrepared >= totalRequired && totalRequired > 0 && (
          <span className="pill sm" style={{ background:"#e5f5e5", color:"#2b7a2b" }} data-testid="helper-total-ok-badge">Cukup</span>
        )}
      </header>

      <p className="muted small" style={{ marginTop: 0 }}>
        Menampilkan hanya barang dengan proses <b>Cetak</b>. Barang Non-Cetak tidak melewati Persiapan Helper.
      </p>

      <table className="prep-table" data-testid="helper-prep-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
            <th style={{ padding: "6px 8px" }}>Barang</th>
            <th className="num" style={{ padding: "6px 8px", textAlign: "right" }}>Qty Dibutuhkan</th>
            <th className="num" style={{ padding: "6px 8px", textAlign: "right" }}>Qty Disiapkan</th>
            <th className="num" style={{ padding: "6px 8px", textAlign: "right" }}>Qty Kurang</th>
          </tr>
        </thead>
        <tbody>
          {printLines.length === 0 && (
            <tr>
              <td colSpan={4} className="muted small" style={{ padding: "10px 8px" }}>
                Tidak ada line item Cetak. (Semua line item sudah diset Non-Cetak.)
              </td>
            </tr>
          )}
          {printLines.map(({ li, idx }) => {
            const req = Number(li.quantity || 0);
            const prep = Number(draft[idx] ?? 0);
            const shortage = Math.max(req - prep, 0);
            return (
              <tr key={idx} data-testid={`helper-prep-row-${idx}`} style={{ borderBottom: "1px solid var(--line)" }}>
                <td style={{ padding: "8px" }}>
                  <div><strong>{li.name}</strong></div>
                  {li.color && <div className="muted small">{li.color}</div>}
                </td>
                <td className="num mono" style={{ padding: "8px", textAlign: "right" }} data-testid={`helper-required-${idx}`}>
                  {req.toLocaleString("id-ID")}
                </td>
                <td className="num" style={{ padding: "8px", textAlign: "right" }}>
                  <input
                    type="number"
                    min="0"
                    max={req}
                    value={draft[idx] ?? 0}
                    onChange={(e) => setDraft({ ...draft, [idx]: e.target.value })}
                    disabled={!canEdit || busy}
                    data-testid={`helper-prepared-${idx}`}
                    style={{ width: 100, textAlign: "right" }}
                  />
                </td>
                <td className="num mono" style={{ padding: "8px", textAlign: "right", color: shortage > 0 ? "#8f1f1f" : "#2b7a2b" }} data-testid={`helper-shortage-${idx}`}>
                  {shortage > 0 ? `-${shortage.toLocaleString("id-ID")}` : "0"}
                </td>
              </tr>
            );
          })}
          {printLines.length > 0 && (
            <tr style={{ background: "#faf6ee", fontWeight: 600 }}>
              <td style={{ padding: "8px" }}>Total Cetak</td>
              <td className="num mono" style={{ padding: "8px", textAlign: "right" }} data-testid="helper-total-required">{totalRequired.toLocaleString("id-ID")}</td>
              <td className="num mono" style={{ padding: "8px", textAlign: "right" }} data-testid="helper-total-prepared">{totalPrepared.toLocaleString("id-ID")}</td>
              <td className="num mono" style={{ padding: "8px", textAlign: "right", color: totalShortage > 0 ? "#8f1f1f" : "#2b7a2b" }} data-testid="helper-total-shortage">
                {totalShortage > 0 ? `-${totalShortage.toLocaleString("id-ID")}` : "0"}
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <label style={{ display: "block", marginTop: 10 }}>
        <span className="muted small">Catatan</span>
        <input
          type="text"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          disabled={!canEdit || busy}
          placeholder="Catatan opsional"
          data-testid="helper-notes-input"
          style={{ width: "100%", maxWidth: 480, display: "block", marginTop: 4 }}
        />
      </label>

      {err && <p style={{ color: "#8f1f1f", marginTop: 6 }} data-testid="helper-prep-error">{err}</p>}

      {canEdit && printLines.length > 0 && (
        <div style={{ marginTop: 10, display: "flex", gap: 10, alignItems: "center" }}>
          <button className="primary sm" onClick={save} disabled={busy} data-testid="helper-save-button">
            <Check size={13}/> Simpan Persiapan
          </button>
          {order?.helper_prep?.updated_at && (
            <span className="muted small" data-testid="helper-updated">
              Update terakhir: {order.helper_prep.updated_by_name || order.helper_prep.updated_by} · {formatDate(order.helper_prep.updated_at)}
            </span>
          )}
        </div>
      )}
      {!canEdit && (
        <p className="muted small" style={{ marginTop: 6 }}>Butuh permission <code>helper.prepare</code>.</p>
      )}
      {footerActions}
    </section>
  );
}
