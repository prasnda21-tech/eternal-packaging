import { Link } from "react-router-dom";
import { AlertOctagon, AlertTriangle, Clock } from "lucide-react";
import { STAGES, ROLES } from "../lib/constants";

const bucketMeta = {
  overdue: { label: "Overdue", icon: AlertOctagon, cls: "rem-overdue" },
  warn_6h: { label: "6 jam lagi", icon: AlertTriangle, cls: "rem-warn-6h" },
  warn_h1: { label: "H-1", icon: Clock, cls: "rem-warn-h1" },
};

const formatHours = (h) => {
  if (h === null || h === undefined) return "-";
  if (h < 0) return `Lewat ${Math.abs(h).toFixed(1)} jam`;
  if (h < 1) return `${Math.round(h * 60)} menit lagi`;
  if (h < 24) return `${h.toFixed(1)} jam lagi`;
  return `${(h / 24).toFixed(1)} hari lagi`;
};

export default function ReminderList({ reminders, counts, scope, testid = "reminder-list" }) {
  const items = reminders || [];
  if (items.length === 0) {
    return <p className="empty" data-testid={`${testid}-empty`}>Tidak ada reminder aktif · semua PO on-time.</p>;
  }
  return (
    <div className="reminder-list" data-testid={testid}>
      <div className="reminder-counts">
        <span className="rem-overdue">Overdue: <b>{counts?.overdue ?? 0}</b></span>
        <span className="rem-warn-6h">6 jam: <b>{counts?.warn_6h ?? 0}</b></span>
        <span className="rem-warn-h1">H-1: <b>{counts?.warn_h1 ?? 0}</b></span>
      </div>
      <ul>
        {items.map((r) => {
          const m = bucketMeta[r.bucket] || bucketMeta.warn_h1;
          const Icon = m.icon;
          return (
            <li key={r.id} className={`reminder-item ${m.cls}`} data-testid={`reminder-${r.po_number}`}>
              <Link to={`/po/${r.id}`}>
                <span className="rem-badge"><Icon size={12} /> {m.label}</span>
                <div className="rem-body">
                  <div>
                    <strong>{r.po_number}</strong>
                    <span className="muted"> · {r.outlet}</span>
                  </div>
                  <div className="rem-meta">
                    <span className="stage-chip">{STAGES[r.current_stage]}</span>
                    <span className="mono">Ditahan: {ROLES[r.owner_role] || r.owner_role}</span>
                    <span className="mono">{formatHours(r.hours_left)}</span>
                    {r.priority === "urgent" && <span className="urgent-badge">URGENT</span>}
                  </div>
                </div>
              </Link>
            </li>
          );
        })}
      </ul>
      {scope === "own" && <p className="muted small">Menampilkan hanya reminder yang menjadi tanggung jawab Anda.</p>}
    </div>
  );
}
