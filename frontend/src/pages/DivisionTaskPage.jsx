import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";
import { RefreshCw, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { firstTaskNavPath } from "../lib/navAccess";
import {
  DIVISION_TASK_PAGES,
  formatIdQty,
  statusTone,
  taskMatchesQuery,
  taskMatchesStatus,
  taskWorkHref,
} from "../lib/divisionTasks";

const STATUS_TABS = [
  { key: "semua", label: "Semua" },
  { key: "belum", label: "Belum Dikerjakan" },
  { key: "sebagian", label: "Sebagian" },
  { key: "siap", label: "Siap Konfirmasi" },
  { key: "selesai", label: "Selesai" },
];

export default function DivisionTaskPage() {
  const { pathname } = useLocation();
  const slug = (pathname.split("/").filter(Boolean).pop() || "").trim();
  const cfg = DIVISION_TASK_PAGES[slug];
  const { hasPerm } = useAuth();
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("semua");
  const [scope, setScope] = useState("mine");

  const load = async (nextScope = scope) => {
    if (!cfg) return;
    setBusy(true);
    setErr("");
    try {
      const d = await api.divisionTasks(cfg.id, nextScope);
      setData(d);
      if (!d.can_view_all) setScope("mine");
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat tugas");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    setQ("");
    setStatus("semua");
    setScope("mine");
    load("mine");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  const rows = useMemo(() => {
    return (data?.tasks || []).filter((t) => taskMatchesQuery(t, q) && taskMatchesStatus(t, status));
  }, [data, q, status]);

  if (!cfg) return <Navigate to={firstTaskNavPath(hasPerm)} replace />;

  const canViewAll = !!data?.can_view_all;
  const canViewPo = !!data?.can_view_po;
  const qty = cfg.qtyCols !== false;

  return (
    <div className="prod-page division-task-page" data-testid="division-task-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Tugas Produksi</p>
          <h1 data-testid="division-task-title">{cfg.title}</h1>
          <p className="muted small">{data?.scope === "all" ? "Semua tugas divisi" : "Tugas Saya"} · {rows.length} baris</p>
        </div>
        <button type="button" className="ghost sm" onClick={() => load()} disabled={busy} data-testid="division-task-refresh">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {err && <p className="error" data-testid="division-task-error">{err}</p>}

      <div className="division-task-toolbar">
        <label className="division-task-search">
          <Search size={14} />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Cari No PO / Customer / Item"
            data-testid="division-task-search"
          />
        </label>
        <div className="division-task-filters" data-testid="division-task-status-filters">
          {STATUS_TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              className={status === t.key ? "primary sm" : "ghost sm"}
              onClick={() => setStatus(t.key)}
              data-testid={`division-task-status-${t.key}`}
            >
              {t.label}
            </button>
          ))}
        </div>
        {canViewAll && (
          <div className="division-task-scope" data-testid="division-task-scope">
            <button type="button" className={scope === "mine" ? "primary sm" : "ghost sm"} onClick={() => { setScope("mine"); load("mine"); }} data-testid="division-task-scope-mine">Tugas Saya</button>
            <button type="button" className={scope === "all" ? "primary sm" : "ghost sm"} onClick={() => { setScope("all"); load("all"); }} data-testid="division-task-scope-all">Semua Tugas Divisi</button>
          </div>
        )}
      </div>

      <div className="prod-table-wrap">
        <table className="prod-table" data-testid="division-task-table">
          <thead>
            <tr>
              <th>No PO</th>
              <th>Customer</th>
              <th>Item</th>
              {cfg.extra === "design" && <th>Status Design</th>}
              {cfg.extra === "sj" && <th>Status SJ</th>}
              {cfg.extra === "driver" && <th>Bukti Foto</th>}
              {qty && <th className="num">{cfg.targetLabel || "Target"}</th>}
              {qty && <th className="num">{cfg.doneLabel || "Sudah"}</th>}
              {qty && <th className="num">{cfg.remainLabel || "Sisa"}</th>}
              <th>{cfg.inputColLabel || "Yang Harus Diinput"}</th>
              <th>Status</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={10} className="muted" data-testid="division-task-empty">Tidak ada tugas.</td>
              </tr>
            )}
            {rows.map((t) => (
              <tr key={t.task_id} data-testid={`division-task-row-${t.order_number}`}>
                <td className="mono">{t.order_number}</td>
                <td>{t.customer_name || "—"}</td>
                <td>{t.item || "—"}</td>
                {cfg.extra === "design" && <td data-testid={`division-task-design-${t.order_number}`}>{t.design_status_label || t.design_status || "—"}</td>}
                {cfg.extra === "sj" && <td>{t.sj_status || "—"}</td>}
                {cfg.extra === "driver" && <td className="mono">{t.photo_count ?? 0} foto</td>}
                {qty && <td className="num mono">{formatIdQty(t.target_qty)}</td>}
                {qty && <td className="num mono">{formatIdQty(t.completed_qty)}</td>}
                {qty && <td className="num mono">{formatIdQty(t.remaining_qty)}</td>}
                <td data-testid={`division-task-input-${t.order_number}`}>{t.required_input}</td>
                <td><span className={"pill sm " + statusTone(t.status)}>{t.status_label}</span></td>
                <td className="division-task-actions">
                  <Link className="primary sm" to={taskWorkHref(t)} data-testid={`division-task-kerja-${t.order_number}`}>
                    {t.action_label || "Kerjakan"}
                  </Link>
                  {canViewPo && (
                    <Link className="ghost sm" to={`/po/${t.order_id}`} data-testid={`division-task-po-${t.order_number}`}>Lihat PO</Link>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function DivisionTaskIndex() {
  const { hasPerm } = useAuth();
  return <Navigate to={firstTaskNavPath(hasPerm)} replace />;
}
