import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Clock, ArrowUpRight, ListChecks } from "lucide-react";
import api from "../lib/api";
import useAutoRefresh from "../lib/useAutoRefresh";

/**
 * ActionCenter — Batch 3 · "Tugas Saya" untuk user berdasarkan Effective
 * Permission + semantic queue state. Backend `/api/dashboard/action-items`
 * adalah SOURCE OF TRUTH (count == list). Tidak ada business predicate di sini.
 */
function fmtDeadline(iso) {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleString("id-ID", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  } catch { return String(iso); }
}

function DeadlineChip({ status }) {
  if (!status || status === "normal" || status === "unknown" || status === "terkirim") return null;
  const cfg = ({
    overdue: { text: "OVERDUE", bg: "#fce6e6", color: "#8f1f1f", border: "1px solid #efb9b9" },
    h6: { text: "≤6 JAM", bg: "#fce6e6", color: "#8f1f1f", border: "1px solid #efb9b9" },
    h1: { text: "H-1", bg: "#fef4d6", color: "#7a5a1e", border: "1px solid #efd9a1" },
  })[status];
  if (!cfg) return null;
  return (
    <span data-testid={`ac-deadline-${status}`} style={{ ...cfg, background: cfg.bg, padding: "2px 6px", borderRadius: 4, fontSize: 10.5, fontWeight: 700 }}>
      {cfg.text}
    </span>
  );
}

export default function ActionCenter() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [selectedKey, setSelectedKey] = useState(null);

  const load = () => api.dashboardActionItems().then(setData).catch((e) => setErr(e.response?.data?.detail || "Gagal memuat"));
  useEffect(() => { load(); }, []);

  // Batch 3: reuse auto-refresh polling untuk Action Center juga (30s).
  useAutoRefresh(() => api.dashboardActionItems(), {
    intervalMs: 30_000, enabled: !!data, onData: (d) => setData(d),
  });

  const actions = data?.actions || [];
  const nonEmpty = actions.filter((a) => (a.count || 0) > 0);
  const selected = useMemo(() => actions.find((a) => a.key === selectedKey), [actions, selectedKey]);

  if (err) return <div className="panel" data-testid="action-center-error"><p style={{ color: "#8f1f1f" }}>{err}</p></div>;
  if (!data) return null;
  if (nonEmpty.length === 0) {
    return (
      <section className="panel" data-testid="action-center">
        <header className="panel-head"><h3><ListChecks size={14}/> Tugas Saya</h3></header>
        <p className="muted small">Tidak ada tugas untuk Anda saat ini.</p>
      </section>
    );
  }

  return (
    <section className="panel" data-testid="action-center">
      <header className="panel-head" style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h3><ListChecks size={14}/> Tugas Saya {actions.some((a) => a.scope === "team") ? "& Tim" : ""}</h3>
        {selected && (
          <button className="ghost sm" onClick={() => setSelectedKey(null)} data-testid="ac-clear-filter">
            Hapus Filter
          </button>
        )}
      </header>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 10 }} data-testid="ac-cards">
        {nonEmpty.map((a) => (
          <button
            key={a.key}
            className={selectedKey === a.key ? "primary" : "ghost"}
            onClick={() => setSelectedKey(a.key === selectedKey ? null : a.key)}
            data-testid={`ac-card-${a.key}`}
            style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", padding: 10, textAlign: "left" }}
          >
            <span className="muted small">{a.label}</span>
            <span className="mono" style={{ fontSize: 20, fontWeight: 700 }} data-testid={`ac-count-${a.key}`}>{a.count}</span>
            {a.scope === "team" && <span className="pill sm" style={{ marginTop: 4, fontSize: 10 }}>Tim</span>}
          </button>
        ))}
      </div>

      {selected && (
        <div style={{ marginTop: 12 }} data-testid={`ac-list-${selected.key}`}>
          <p className="muted small" data-testid="ac-list-summary">
            Menampilkan: <b>{selected.label}</b> — {selected.count} PO
          </p>
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 6 }}>
            {selected.items.map((o) => (
              <li key={o.id}>
                <Link
                  to={`/po/${o.id}`}
                  data-testid={`ac-row-${selected.key}-${o.po_number}`}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "auto 1fr auto auto",
                    alignItems: "center",
                    gap: 8,
                    padding: "8px 10px",
                    background: "#fdfaf3",
                    border: "1px solid var(--line)",
                    borderRadius: 8,
                    fontSize: 13,
                    textDecoration: "none",
                    color: "inherit",
                  }}
                >
                  <span className="mono">{o.po_number}</span>
                  <span>
                    <strong>{o.outlet || "-"}</strong>
                    <span className="muted small" style={{ marginLeft: 6 }}>{o.customer || "Customer belum tersedia"}</span>
                    {selected.key === "operator_work" && o.my_operator_splits && (
                      <span className="muted small" style={{ display: "block" }}>
                        {o.my_operator_splits.map((s, i) => (
                          <span key={i}>
                            {s.operator_name}: {s.qty_completed}/{s.qty_assigned} (sisa {s.qty_remaining})
                            {s.work_date ? ` · ${s.work_date}` : ""}
                            {i < o.my_operator_splits.length - 1 ? " · " : ""}
                          </span>
                        ))}
                      </span>
                    )}
                  </span>
                  <span className="muted small">{fmtDeadline(o.delivery_deadline_effective)}</span>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <DeadlineChip status={o.delivery_deadline_status} />
                    <ArrowUpRight size={12} />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
