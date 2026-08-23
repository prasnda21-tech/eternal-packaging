import { useEffect, useMemo, useState } from "react";
import { CheckSquare, CircleAlert, ClipboardCheck, Package, RefreshCw, Square, Trash2, Truck, XCircle, Check, ChevronRight, Filter, X } from "lucide-react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLES, STAGES, isToday, formatDate } from "../lib/constants";
import POCard from "../components/POCard";
import ReminderList from "../components/ReminderList";

const NAV = [
  { key: "today", label: "Hari ini", icon: Package },
  { key: "schedule", label: "Jadwal", icon: ClipboardCheck },
  { key: "orders", label: "Semua PO", icon: Truck },
];

const STATUS_TABS = [
  { key: "aktif", label: "Aktif" },
  { key: "hold", label: "HOLD" },
  { key: "selesai", label: "Selesai" },
  { key: "cancelled", label: "Cancelled" },
];

export default function Dashboard() {
  const { user, hasPerm } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [orders, setOrders] = useState([]);
  const [counts, setCounts] = useState({ aktif: 0, selesai: 0, cancelled: 0 });
  const [stageCounts, setStageCounts] = useState({});
  const [reminders, setReminders] = useState(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState("today");
  const [statusTab, setStatusTab] = useState("aktif");
  const [selected, setSelected] = useState(new Set());
  const [toast, setToast] = useState("");

  // Bagian 7-11: 16 shortcut alur kerja pakai SLUG (semantic work-queue),
  // bukan index current_stage. Backend `_compute_pending_queues` = canonical
  // resolver; badge count DAN filter list pakai predicate yang sama. Satu PO
  // boleh muncul di beberapa queue (mis. Approval Design + Approval Payment).
  const WORK_QUEUE_SLUGS = [
    "order_in", "design", "approval_design", "payment", "approval_payment",
    "scheduling", "screener", "helper", "operator", "sample",
    "printing", "packing", "delivery_note", "loading", "driver", "delivered",
  ];
  const stageParam = searchParams.get("stage");
  const stageSlug = stageParam && WORK_QUEUE_SLUGS.includes(stageParam) ? stageParam : null;
  const stageIsValid = !!stageSlug;
  const stageIndex = stageSlug ? WORK_QUEUE_SLUGS.indexOf(stageSlug) : -1;

  // Pick up success toast passed via navigate state (e.g. after PO delete)
  useEffect(() => {
    if (location.state?.toast) {
      setToast(location.state.toast);
      // Clear so refresh doesn't re-show
      navigate(location.pathname, { replace: true, state: {} });
      const t = setTimeout(() => setToast(""), 3200);
      return () => clearTimeout(t);
    }
  }, [location, navigate]);

  const canCancel = hasPerm("po.cancel");
  const canDelete = hasPerm("po.delete");

  const load = async (status = statusTab) => {
    setBusy(true);
    try {
      const [d, r] = await Promise.all([
        api.listOrders(status),
        api.reminders().catch(() => null),
      ]);
      setOrders(d.orders || []);
      setCounts(d.counts || {});
      setStageCounts(d.stage_counts || {});
      setReminders(r);
      setSelected(new Set());
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (stageIsValid) {
      setView("orders");
      // stage_view returns non-cancelled orders + pending_queues[] per PO.
      load("stage_view");
    } else {
      load(statusTab);
    }
    // eslint-disable-next-line
  }, [stageIsValid, stageSlug, statusTab]);

  const clearStageFilter = () => {
    const p = new URLSearchParams(searchParams);
    p.delete("stage");
    setSearchParams(p, { replace: true });
  };

  const today = useMemo(() => orders.filter((o) => isToday(o.deadline)), [orders]);
  const baseList = view === "today" ? today : orders;
  const list = useMemo(() => {
    if (!stageIsValid) return baseList;
    // Bagian 7-11: filter by semantic pending_queues slug (canonical resolver).
    return baseList.filter((o) => Array.isArray(o.pending_queues) && o.pending_queues.includes(stageSlug));
  }, [baseList, stageIsValid, stageSlug]);
  const urgentCount = today.filter((o) => o.priority === "urgent").length;
  const pageTitle = stageIsValid ? `PO tahap: ${STAGES[stageIndex] || stageSlug}` : (view === "today" ? "Pekerjaan hari ini" : view === "schedule" ? "Jadwal produksi" : "Semua PO");

  const toggleSelect = (id) => {
    setSelected((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  };
  const toggleAll = () => {
    setSelected((prev) => (prev.size === list.length ? new Set() : new Set(list.map((o) => o.id))));
  };
  const doBulkCancel = async () => {
    const ids = [...selected]; if (!ids.length) return;
    const reason = window.prompt(`Batalkan ${ids.length} PO? Alasan (wajib):`);
    if (!reason || !reason.trim()) return;
    if (!window.confirm(`Anda akan membatalkan ${ids.length} PO. Lanjutkan?`)) return;
    try {
      const d = await api.bulkCancel(ids, reason.trim());
      alert(`${d.cancelled_count} PO dibatalkan`);
      await load(statusTab);
    } catch (ex) { alert(ex.response?.data?.detail || "Gagal bulk cancel"); }
  };
  const doBulkDelete = async () => {
    const ids = [...selected]; if (!ids.length) return;
    const reason = window.prompt(`Hapus permanen ${ids.length} PO? Alasan (wajib):`);
    if (!reason || !reason.trim()) return;
    const confirm = window.prompt(`Ketik "HAPUS" untuk konfirmasi menghapus ${ids.length} PO permanen. Tindakan ini tidak dapat dibatalkan.`);
    if ((confirm || "").trim().toUpperCase() !== "HAPUS") { alert("Konfirmasi tidak cocok."); return; }
    try {
      const d = await api.bulkDelete(ids, reason.trim(), confirm.trim().toUpperCase());
      alert(`${d.deleted_count} PO dihapus permanen`);
      await load(statusTab);
    } catch (ex) { alert(ex.response?.data?.detail?.message || ex.response?.data?.detail || "Gagal bulk delete"); }
  };

  const inBulkView = view === "orders";
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{new Date().toLocaleDateString("id-ID", { weekday: "long", day: "numeric", month: "long" })}</p>
          <h1 data-testid="page-title">{pageTitle}</h1>
          <p className="subhead">Antrian {ROLES[user.role] || user.role} · satu aksi berikutnya</p>
        </div>
        <button className="icon-button" onClick={() => load(statusTab)} data-testid="refresh-orders-button" aria-label="Muat ulang">
          <RefreshCw size={19} className={busy ? "spin" : ""} />
        </button>
      </div>

      {stageIsValid && (
        <div className="stage-filter-chip" data-testid="stage-filter-chip" style={{display:"flex",alignItems:"center",gap:10,padding:"10px 14px",margin:"6px 0 12px",background:"#f5efe0",borderRadius:10,fontSize:13}}>
          <Filter size={14} />
          <span data-testid="stage-filter-label">Menampilkan PO — <strong>{STAGES[stageIndex] || stageSlug}</strong></span>
          <span className="pill sm" data-testid="stage-filter-count">{list.length}</span>
          <button
            className="ghost sm"
            onClick={clearStageFilter}
            data-testid="stage-filter-clear"
            aria-label="Hapus filter tahap"
            style={{marginLeft:"auto"}}
          ><X size={12}/> Hapus filter</button>
        </div>
      )}

      {view === "today" && !stageIsValid && (
        <section className="signal-strip">
          <div><CircleAlert size={20} /><strong>{urgentCount} pekerjaan urgent</strong><span>perlu perhatian sebelum akhir shift</span></div>
          <span className="strip-time">Sinkron: baru saja</span>
        </section>
      )}

      {view === "today" && !stageIsValid && reminders && reminders.reminders.length > 0 && (
        <section className="panel reminder-panel-mobile" data-testid="mobile-reminder-panel">
          <header className="panel-head"><h3>Reminder deadline Anda</h3></header>
          <ReminderList {...reminders} testid="mobile-reminders" />
        </section>
      )}

      {inBulkView && (
        <div className="po-status-tabs" role="tablist">
          {STATUS_TABS.map((t) => (
            <button key={t.key} role="tab" className={statusTab === t.key ? "active" : ""}
              onClick={() => setStatusTab(t.key)} data-testid={`po-status-tab-${t.key}`}>
              {t.label}{t.key !== "hold" && <span className="pill sm">{counts[t.key] ?? "-"}</span>}
            </button>
          ))}
        </div>
      )}

      {inBulkView && selected.size > 0 && (
        <div className="bulk-bar" data-testid="bulk-bar">
          <span><b>{selected.size}</b> PO dipilih</span>
          <div className="bulk-actions">
            {canCancel && statusTab !== "cancelled" && (
              <button className="ghost sm" onClick={doBulkCancel} data-testid="bulk-cancel-button"><XCircle size={13}/> Batalkan Terpilih</button>
            )}
            {canDelete && (
              <button className="ghost sm danger" onClick={doBulkDelete} data-testid="bulk-delete-button"><Trash2 size={13}/> Hapus Terpilih</button>
            )}
            <button className="ghost sm" onClick={() => setSelected(new Set())} data-testid="bulk-clear">Batal pilih</button>
          </div>
        </div>
      )}

      <section className="jobs-section">
        <div className="section-label">
          <span>{view === "today" ? "Antrian Anda" : `PO ${statusTab}`}</span>
          <span data-testid="order-count">{list.length} entri</span>
          {inBulkView && list.length > 0 && (canCancel || canDelete) && (
            <button className="ghost sm" onClick={toggleAll} data-testid="bulk-toggle-all">
              {selected.size === list.length ? <CheckSquare size={13}/> : <Square size={13}/>} Pilih semua
            </button>
          )}
        </div>
        {list.length === 0 && (
          <div className="empty-state" data-testid="empty-state"><p>Tidak ada PO {view === "today" ? "untuk hari ini" : statusTab}.</p></div>
        )}
        {list.map((o) => (
          <div key={o.id} className={"po-row" + (selected.has(o.id) ? " selected" : "")} data-testid={`po-row-${o.po_number}`}>
            {inBulkView && (canCancel || canDelete) && statusTab !== "selesai" && (
              <label className="po-check" onClick={(e) => e.stopPropagation()}>
                <input type="checkbox" checked={selected.has(o.id)} onChange={() => toggleSelect(o.id)} data-testid={`po-check-${o.po_number}`}/>
              </label>
            )}
            <POCard order={o} />
            {statusTab === "cancelled" && (
              <div className="cancel-strip" data-testid={`cancel-info-${o.po_number}`}>
                <XCircle size={12}/> Dibatalkan oleh {o.cancelled_by_name || o.cancelled_by} · {formatDate(o.cancelled_at)} · {o.cancel_reason}
              </div>
            )}
          </div>
        ))}
      </section>

      {view === "schedule" && (
        <section className="schedule-note" data-testid="schedule-policy">
          <ClipboardCheck size={20} />
          <div>
            <strong>Kebijakan penjadwalan</strong>
            <p>Deadline standar tiga hari setelah PO masuk. Prioritas <em>urgent</em> hanya atas persetujuan Head of Production.</p>
          </div>
        </section>
      )}

      <section className="workflow-footer" data-testid="workflow-footer">
        <div><p className="eyebrow">Cakupan alur kerja</p><h2>Dari Order Masuk<br />sampai Terkirim.</h2></div>
        <p className="muted small" style={{marginTop:0,marginBottom:8}}>Klik salah satu tahap untuk melihat PO yang sedang berada di tahap tersebut.</p>
        <div className="workflow-list workflow-list-clickable">
          {STAGES.map((s, i) => {
            const slug = WORK_QUEUE_SLUGS[i];
            const count = stageCounts[slug] || 0;
            const active = stageIsValid && stageSlug === slug;
            return (
              <Link
                key={s}
                to={`/?stage=${slug}`}
                className={`workflow-stage-link${active ? " active" : ""}`}
                data-testid={`workflow-stage-${i}`}
                aria-label={`Buka daftar PO tahap ${s}`}
              >
                <b>{String(i + 1).padStart(2, "0")}</b>
                <span className="workflow-stage-label">{s}</span>
                <span className="workflow-stage-count" data-testid={`workflow-stage-count-${i}`}>{count}</span>
                <ChevronRight size={14} className="workflow-stage-chev"/>
              </Link>
            );
          })}
        </div>
      </section>

      <nav className="bottom-nav">
        {NAV.map(({ key, label, icon: Icon }) => (
          <button key={key} className={view === key ? "active" : ""} onClick={() => setView(key)} data-testid={`nav-${key}`}>
            <Icon size={19} /> {label}
          </button>
        ))}
      </nav>

      {toast && (
        <div className="toast" data-testid="dashboard-toast">
          <Check size={18} />{toast}
        </div>
      )}
    </>
  );
}
