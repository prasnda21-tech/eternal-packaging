import { useState } from "react";
import { FileText, Truck } from "lucide-react";
import { formatDate } from "../lib/constants";

// Task 13 bugfix: Panel Surat Jalan — source of truth completion SJ.
// - Muncul saat current_stage >= 12 (Packing sudah selesai).
// - User dengan `delivery_note.manage` bisa create/update SJ.
// - Create SJ mengisi delivery_note_created_by/at → gate Loading terpenuhi.
export default function SuratJalanPanel({ order, canManage, busy, onCreate }) {
  const [number, setNumber] = useState(order.delivery_note_number || "");
  const [notes, setNotes] = useState(order.delivery_note_notes || "");
  const created = !!order.surat_jalan_created;
  const stageIsSJ = order.current_stage === 12;
  const stagePastSJ = order.current_stage >= 13;

  if ((order.current_stage || 0) < 12) return null;

  const handleCreate = async () => {
    await onCreate(number || null, notes || "");
  };

  return (
    <section className="panel" data-testid="surat-jalan-panel">
      <header className="panel-head">
        <h3><FileText size={14} /> Surat Jalan</h3>
        <span
          className={`pill sm ${created ? "pill-ok" : ""}`}
          data-testid="surat-jalan-semantic-badge"
        >
          {stagePastSJ ? "Surat Jalan Selesai" : created ? "SJ dibuat — siap Loading" : "Menunggu pembuatan Surat Jalan"}
        </span>
      </header>

      {stagePastSJ ? (
        <p className="muted small" data-testid="surat-jalan-done-note">
          SJ #{order.delivery_note_number} · dibuat oleh {order.delivery_note_created_by_name || order.delivery_note_created_by} · {formatDate(order.delivery_note_created_at)}
        </p>
      ) : !canManage ? (
        <p className="muted small" data-testid="surat-jalan-readonly-note">
          Anda tidak memiliki permission <code>delivery_note.manage</code> untuk membuat Surat Jalan.
        </p>
      ) : (
        <div className="surat-jalan-form" data-testid="surat-jalan-form" style={{display:"grid",gap:12}}>
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12}}>
            <label>
              <span className="lbl">Nomor Surat Jalan</span>
              <input
                type="text"
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
          <div style={{display:"flex",flexWrap:"wrap",gap:8}}>
            <button
              type="button"
              className="primary"
              onClick={handleCreate}
              disabled={busy}
              data-testid="create-surat-jalan-button"
            >
              <Truck size={16} /> {created ? "Perbarui Surat Jalan" : "Buat Surat Jalan"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
