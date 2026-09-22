import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ClipboardPlus, Eye } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate, formatDisplayDateTime } from "../lib/constants";
import {
  DEADLINE_LABELS,
  PRIORITY_LABELS,
  SPK_NEW_PATH,
  STATUS_LABELS,
  formatPercent,
  formatSpkError,
  rowTone,
  visibleSpkTabs,
} from "../lib/spk";
import "./spk.css";

const EMPTY_SUMMARY = {
  active: 0,
  due_soon: 0,
  overdue: 0,
  waiting_verification: 0,
  completed_this_month: 0,
};

export default function SpkDashboard() {
  const navigate = useNavigate();
  const { hasPerm } = useAuth();
  const tabs = useMemo(() => visibleSpkTabs(hasPerm), [hasPerm]);
  const canCreate = hasPerm("spk.create") || hasPerm("spk.manage");
  const [tab, setTab] = useState(tabs[0]?.id || "mine");
  const [rows, setRows] = useState([]);
  const [summary, setSummary] = useState(EMPTY_SUMMARY);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [division, setDivision] = useState("");
  const [pic, setPic] = useState("");
  const [priority, setPriority] = useState("");
  const [deadline, setDeadline] = useState("");
  const [error, setError] = useState("");
  const [divisions, setDivisions] = useState([]);
  const [pics, setPics] = useState([]);

  useEffect(() => {
    if (!tabs.some((item) => item.id === tab)) setTab(tabs[0]?.id || "mine");
  }, [tabs, tab]);

  useEffect(() => {
    let cancelled = false;
    api.spkContext()
      .then((data) => {
        if (cancelled) return;
        setDivisions(data.context?.divisions || []);
        setPics(data.context?.pics || []);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setError("");
    api.listSpk({ tab, q, status, division, pic, priority, deadline_state: deadline })
      .then((data) => {
        if (cancelled) return;
        setRows(data.orders || []);
        setSummary(data.summary || EMPTY_SUMMARY);
      })
      .catch((err) => { if (!cancelled) setError(formatSpkError(err)); });
    return () => { cancelled = true; };
  }, [tab, q, status, division, pic, priority, deadline]);

  const applyCard = (nextTab, extra = {}) => {
    if (tabs.some((item) => item.id === nextTab)) setTab(nextTab);
    setStatus(extra.status || "");
    setDeadline(extra.deadline || "");
  };

  return (
    <div className="spk-page" data-testid="spk-dashboard">
      <header className="spk-head">
        <div>
          <div className="spk-kicker">Surat Perintah Kerja</div>
          <h1>SPK Antar Divisi</h1>
        </div>
        {canCreate && (
          <button type="button" className="spk-btn spk-btn-create" data-testid="spk-create" onClick={() => navigate(SPK_NEW_PATH)}>
            <ClipboardPlus size={15} /> Buat SPK
          </button>
        )}
      </header>

      <div className="spk-cards" data-testid="spk-summary">
        <button type="button" className="spk-card spk-card-active" onClick={() => applyCard("mine", { status: "IN_PROGRESS" })}>
          <span>Aktif</span><strong data-testid="spk-count-active">{summary.active}</strong>
        </button>
        <button type="button" className="spk-card spk-card-soon" onClick={() => applyCard(tab, { deadline: "DUE_SOON", status: "" })}>
          <span>Due ≤3 hari</span><strong data-testid="spk-count-soon">{summary.due_soon}</strong>
        </button>
        <button type="button" className="spk-card spk-card-overdue" onClick={() => applyCard("overdue", { deadline: "OVERDUE" })}>
          <span>Terlambat</span><strong data-testid="spk-count-overdue">{summary.overdue}</strong>
        </button>
        <button type="button" className="spk-card spk-card-verify" onClick={() => applyCard("verification")}>
          <span>Menunggu Verifikasi</span><strong data-testid="spk-count-verify">{summary.waiting_verification}</strong>
        </button>
        <button type="button" className="spk-card spk-card-done" onClick={() => applyCard(tabs.some((item) => item.id === "all") ? "all" : "mine", { status: "COMPLETED" })}>
          <span>Selesai bulan ini</span><strong data-testid="spk-count-done">{summary.completed_this_month}</strong>
        </button>
      </div>

      <div className="spk-toolbar">
        <div className="spk-tabs" data-testid="spk-tabs" role="tablist">
          {tabs.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              className={"spk-tab" + (tab === item.id ? " is-on" : "")}
              data-testid={`spk-tab-${item.id}`}
              aria-selected={tab === item.id}
              onClick={() => setTab(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="spk-filters">
          <input data-testid="spk-search" placeholder="No SPK / judul" value={q} onChange={(e) => setQ(e.target.value)} />
          <select data-testid="spk-filter-status" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="">Status</option>
            {Object.entries(STATUS_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
          <select data-testid="spk-filter-division" value={division} onChange={(e) => setDivision(e.target.value)} aria-label="Divisi">
            <option value="">Divisi</option>
            {divisions.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
          <select data-testid="spk-filter-pic" value={pic} onChange={(e) => setPic(e.target.value)} aria-label="PIC">
            <option value="">PIC</option>
            {pics.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
          <select data-testid="spk-filter-priority" value={priority} onChange={(e) => setPriority(e.target.value)} aria-label="Prioritas">
            <option value="">Prioritas</option>
            {Object.entries(PRIORITY_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
          <select data-testid="spk-filter-deadline" value={deadline} onChange={(e) => setDeadline(e.target.value)} aria-label="Deadline">
            <option value="">Deadline</option>
            <option value="ON_TRACK">On track</option>
            <option value="DUE_SOON">Due ≤3 hari</option>
            <option value="OVERDUE">Terlambat</option>
          </select>
        </div>
      </div>

      {error && <div className="spk-msg is-error" data-testid="spk-list-error">{error}</div>}

      <div className="spk-table-scroll">
        <table className="spk-table" data-testid="spk-table">
          <thead>
            <tr>
              <th>No SPK</th>
              <th>Judul</th>
              <th>Dari</th>
              <th>Kepada</th>
              <th>PIC</th>
              <th>Deadline</th>
              <th>Progress</th>
              <th>Status</th>
              <th>Update Terakhir</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={10} className="spk-empty">Belum ada SPK pada tampilan ini.</td></tr>
            )}
            {rows.map((row) => {
              const tone = rowTone(row);
              const pct = formatPercent(row.progress_percent);
              return (
                <tr key={row.id} className={`tone-${tone}`} data-testid={`spk-row-${row.id}`}>
                  <td className="spk-mono">{row.spk_number}</td>
                  <td className="spk-ellipsis">{row.title}</td>
                  <td>{row.source_division_label}</td>
                  <td>{row.target_division_label}</td>
                  <td className="spk-ellipsis">{row.pic_label || "-"}</td>
                  <td>
                    {formatDisplayDate(row.deadline)}
                    {row.deadline_state && (
                      <div className={`spk-pill spk-dead-${row.deadline_state}`} data-testid={`spk-deadline-${row.id}`}>
                        {DEADLINE_LABELS[row.deadline_state] || row.deadline_state}
                      </div>
                    )}
                  </td>
                  <td>
                    <div className="spk-bar" aria-hidden="true"><span style={{ width: `${Math.min(100, Number(row.progress_percent) || 0)}%` }} /></div>
                    <div className="spk-bar-label">{pct}%</div>
                  </td>
                  <td><span className={`spk-pill spk-st-${row.workflow_status}`}>{STATUS_LABELS[row.workflow_status] || row.workflow_status}</span></td>
                  <td>{formatDisplayDateTime(row.last_activity_at)}</td>
                  <td>
                    <button type="button" className="spk-btn spk-btn-detail" data-testid={`spk-open-${row.id}`} onClick={() => navigate(`/spk/${row.id}`)}>
                      <Eye size={14} /> Lihat Detail
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
