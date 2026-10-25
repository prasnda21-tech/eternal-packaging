import { useMemo, useState } from "react";
import { AlertTriangle, Clock, Calendar, RotateCcw, Edit3 } from "lucide-react";
import api from "../lib/api";
import { formatApiError } from "../lib/errors";

function fmtDateKey(key) {
  if (!key || String(key).length < 10) return "-";
  const [y, m, d] = String(key).slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return String(key);
  return new Date(y, m - 1, d).toLocaleDateString("id-ID", { day: "2-digit", month: "short", year: "numeric" });
}

function fmt(iso) {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleString("id-ID", {
      day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch { return String(iso); }
}

function remainingLabel(iso) {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  const abs = Math.abs(ms);
  const h = Math.floor(abs / 3600_000);
  const m = Math.floor((abs % 3600_000) / 60_000);
  const label = h >= 1 ? `${h} jam ${m} menit` : `${m} menit`;
  return ms < 0 ? `lewat ${label}` : `sisa ${label}`;
}

// Datetime-local input value from ISO
function toDateTimeLocal(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function DeliveryDeadlinePanel({ order, canEdit, onUpdated }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [draftDt, setDraftDt] = useState("");
  const [draftReason, setDraftReason] = useState("");

  const fact = order?.delivery_sla_effective;
  const effective = order?.delivery_deadline_effective;
  const source = order?.delivery_deadline_source || "none";
  const status = order?.delivery_deadline_status || "unknown";
  const calc = order?.delivery_deadline_calculated;
  const lead = order?.delivery_deadline_lead_time_days;
  const manualBy = order?.delivery_deadline_manual_by_name || order?.delivery_deadline_manual_by;
  const isDelivered = order?.delivery_status === "Terkirim" || (order?.current_stage || 0) >= 14;

  const openEdit = () => {
    setDraftDt(toDateTimeLocal(effective || calc || new Date().toISOString()));
    setDraftReason("");
    setEditing(true);
    setErr("");
  };

  const saveManual = async () => {
    if (!draftDt) { setErr("Tanggal + Jam wajib diisi"); return; }
    setBusy(true);
    setErr("");
    try {
      const iso = new Date(draftDt).toISOString();
      const r = await api.setDeliveryDeadlineManual(order.id, iso, draftReason);
      onUpdated?.(r.order);
      setEditing(false);
    } catch (e) {
      setErr(formatApiError(e, "Gagal menyimpan deadline"));
    } finally {
      setBusy(false);
    }
  };

  const resetManual = async () => {
    if (!window.confirm("Reset ke deadline otomatis? Manual override akan dihapus.")) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api.resetDeliveryDeadlineManual(order.id);
      onUpdated?.(r.order);
    } catch (e) {
      setErr(formatApiError(e, "Gagal reset deadline"));
    } finally {
      setBusy(false);
    }
  };

  const banner = useMemo(() => {
    if (isDelivered) return null;
    if (status === "overdue") return { cls: "err", icon: <AlertTriangle size={14}/>, text: `OVERDUE — deadline telah terlewat (${remainingLabel(effective)})` };
    if (status === "h6") return { cls: "err", icon: <AlertTriangle size={14}/>, text: `Deadline ≤ 6 Jam — ${remainingLabel(effective)}` };
    if (status === "h1") return { cls: "warn", icon: <Clock size={14}/>, text: `H-1 Pengiriman — ${remainingLabel(effective)}` };
    return null;
  }, [status, effective, isDelivered]);

  return (
    <section className="panel" data-testid="delivery-deadline-panel">
      <header className="panel-head">
        <h3><Calendar size={14}/> Deadline Pengiriman</h3>
        <span className={`pill sm dd-source-${fact?.source || source}`} data-testid="dd-source-badge">
          {fact?.source === "jadwal_pengiriman" ? "Jadwal Pengiriman"
            : fact?.source === "otomatis" ? "Otomatis"
            : fact?.source === "manual" ? "Manual"
            : fact?.source === "belum_dijadwalkan" ? "Belum Dijadwalkan"
            : fact?.contract === "legacy_working_days" || fact?.contract === "legacy_calendar"
              || fact?.source === "legacy" ? "Otomatis (Legacy · Hari Kerja)"
            : source === "manual" ? "Manual"
            : source === "automatic" ? "Otomatis"
            : "Belum tersedia"}
        </span>
      </header>

      {banner && (
        <div className={`dd-banner dd-${status}`} data-testid={`dd-warning-${status}`} role="alert"
             style={{
               display: "flex", gap: 8, alignItems: "center", padding: "8px 12px",
               borderRadius: 8, marginBottom: 8,
               background: banner.cls === "err" ? "#fce6e6" : "#fef4d6",
               color: banner.cls === "err" ? "#8f1f1f" : "#7a5a1e",
               border: banner.cls === "err" ? "1px solid #efb9b9" : "1px solid #efd9a1",
             }}>
          {banner.icon}<b>{banner.text}</b>
        </div>
      )}

      <div className="row" style={{ display: "flex", flexWrap: "wrap", gap: "8px 20px", fontSize: 13 }}>
        <div><span className="muted small">Effective</span><br/><strong data-testid="dd-effective">{fact?.date ? fmtDateKey(fact.date) : fmt(effective)}</strong></div>
        {source === "manual" && <div><span className="muted small">Manual oleh</span><br/>{manualBy || "-"}</div>}
        {fact?.contract === "sla_v2" && fact?.source === "otomatis" && <div><span className="muted small">Formula</span><br/>{order?.delivery_sla_v2?.working_days || 3} hari kerja setelah kedua approval</div>}
        {(fact?.contract === "legacy_working_days" || fact?.contract === "legacy_calendar"
          || (source === "automatic" && fact?.source === "legacy")) && lead && (
          <div><span className="muted small">Formula</span><br/>Approval terakhir + {lead} hari kerja</div>
        )}
        {calc && source === "manual" && <div><span className="muted small">Otomatis (ref)</span><br/>{fmt(calc)}</div>}
      </div>

      {isDelivered && (
        <p className="muted small" style={{ marginTop: 6 }} data-testid="dd-delivered-note">
          PO sudah Terkirim — warning aktif dinonaktifkan.
        </p>
      )}

      {editing && (
        <div style={{ marginTop: 10, padding: 10, background: "#fdfaf3", border: "1px solid var(--line)", borderRadius: 8 }}>
          <label style={{ display: "block", marginBottom: 6 }}>
            <span className="muted small">Tanggal + Jam Pengiriman</span>
            <input
              type="datetime-local"
              value={draftDt}
              onChange={(e) => setDraftDt(e.target.value)}
              disabled={busy}
              data-testid="dd-manual-input"
              style={{ display: "block", marginTop: 4 }}
            />
          </label>
          <label style={{ display: "block", marginBottom: 6 }}>
            <span className="muted small">Alasan (opsional)</span>
            <input
              type="text"
              value={draftReason}
              onChange={(e) => setDraftReason(e.target.value)}
              disabled={busy}
              placeholder="Mis. customer minta lebih awal"
              data-testid="dd-manual-reason"
              style={{ display: "block", marginTop: 4, width: "100%", maxWidth: 360 }}
            />
          </label>
          {err && <p style={{ color: "#8f1f1f", marginTop: 4 }}>{err}</p>}
          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <button className="primary sm" onClick={saveManual} disabled={busy} data-testid="dd-manual-save">Simpan</button>
            <button className="ghost sm" onClick={() => setEditing(false)} disabled={busy}>Batal</button>
          </div>
        </div>
      )}

      {canEdit && !editing && (
        <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
          <button className="ghost sm" onClick={openEdit} disabled={busy} data-testid="dd-edit-button">
            <Edit3 size={13}/> Edit Deadline
          </button>
          {source === "manual" && (
            <button className="ghost sm" onClick={resetManual} disabled={busy} data-testid="dd-reset-button">
              <RotateCcw size={13}/> Gunakan Deadline Otomatis
            </button>
          )}
        </div>
      )}
      {err && !editing && <p style={{ color: "#8f1f1f", marginTop: 6 }} data-testid="dd-error">{err}</p>}
    </section>
  );
}
