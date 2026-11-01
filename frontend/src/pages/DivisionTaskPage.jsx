import "../productionCompact.css";
import "../deliveryCompact.css";
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { RefreshCw, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { firstTaskNavPath } from "../lib/navAccess";
import {
  DIVISION_TASK_PAGES,
  formatCurrentStock,
  formatIdQty,
  statusTone,
  taskMatchesQuery,
  taskMatchesStatus,
  taskWorkHref,
} from "../lib/divisionTasks";
import {
  KP_KPI_ORDER,
  KP_KPI_META,
  formatDeliveryDate,
  formatKpiCardValue,
  kpDetailPath,
  priorityBadgeClass,
  rowMatchesExtraFilters,
} from "../lib/kepalaProduksiBoard";
import {
  capturePoOpenFromList,
  poNavStateFromList,
} from "../lib/poListReturn";
import ProductionSchedulePriorityBadges from "../components/ProductionSchedulePriorityBadges";
import PoIdentityBadges from "../components/PoIdentityBadges";

const STATUS_TABS = [
  { key: "semua", label: "Semua" },
  { key: "belum", label: "Belum Dikerjakan" },
  { key: "sebagian", label: "Sebagian" },
  { key: "siap", label: "Siap Konfirmasi" },
  { key: "selesai", label: "Selesai" },
];

function statusTabsFor(isKepala) {
  if (!isKepala) return STATUS_TABS;
  return STATUS_TABS.map((t) => (
    t.key === "belum" ? { ...t, label: "Belum Dijadwalkan" } : t
  ));
}

export default function DivisionTaskPage() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const slug = (pathname.split("/").filter(Boolean).pop() || "").trim();
  const cfg = DIVISION_TASK_PAGES[slug];
  const isKepala = cfg?.id === "kepala_produksi";
  const { user, hasPerm } = useAuth();
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState(() => searchParams.get("q") || "");
  const [status, setStatus] = useState(() => searchParams.get("status") || "semua");
  const [scope, setScope] = useState(() => searchParams.get("scope") || "mine");
  const [priority, setPriority] = useState(() => searchParams.get("priority") || "");
  const [holdFilter, setHoldFilter] = useState(() => searchParams.get("hold") || "");

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
    const urlQ = searchParams.get("q") || "";
    const urlStatus = searchParams.get("status") || "semua";
    const urlScope = searchParams.get("scope") || "mine";
    setQ(urlQ);
    setStatus(urlStatus);
    setScope(urlScope);
    setPriority(searchParams.get("priority") || "");
    setHoldFilter(searchParams.get("hold") || "");
    load(urlScope);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (q.trim()) next.set("q", q.trim());
    if (status && status !== "semua") next.set("status", status);
    if (scope && scope !== "mine") next.set("scope", scope);
    if (isKepala && priority) next.set("priority", priority);
    if (isKepala && holdFilter) next.set("hold", holdFilter);
    if (next.toString() === searchParams.toString()) return;
    setSearchParams(next, { replace: true });
  }, [q, status, scope, priority, holdFilter, isKepala, searchParams, setSearchParams]);

  const rows = useMemo(() => {
    const source = data?.tasks || [];
    return source.filter((t) => {
      if (!taskMatchesQuery(t, q) || !taskMatchesStatus(t, status)) return false;
      if (!isKepala) return true;
      return rowMatchesExtraFilters(t, { priority, hold: holdFilter });
    });
  }, [data, q, status, isKepala, priority, holdFilter]);

  if (!cfg) return <Navigate to={firstTaskNavPath(hasPerm)} replace />;

  const canViewAll = !!data?.can_view_all;
  const canViewPo = !!data?.can_view_po;
  const qty = cfg.qtyCols !== false;
  const showStock = !!cfg.showCurrentStock;
  const extraCol = cfg.extra === "design" || cfg.extra === "sj" || cfg.extra === "driver" ? 1 : 0;
  const emptyCols = isKepala ? 12 : 6 + (qty ? 3 : 0) + (showStock ? 1 : 0) + extraCol;
  const listNavState = poNavStateFromList({ pathname, search });
  const onOpenPo = (orderId) => {
    capturePoOpenFromList({ user, orderId, pathname, search });
  };
  const kpis = data?.kpis || {};

  return (
    <div
      className={"prod-page division-task-page" + (["designer", "kepala-produksi", "screener", "helper", "operator", "packing"].includes(slug) ? " production-compact" : "") + (["surat-jalan", "loading", "delivery"].includes(slug) ? " delivery-compact" : "") + (isKepala ? " kp-board-page" : "")}
      data-testid="division-task-page"
    >
      <div className="page-heading">
        <div>
          <p className="eyebrow">{isKepala ? "Priority Board" : "Tugas Produksi"}</p>
          <h1 data-testid="division-task-title">{cfg.title}</h1>
          <p className="muted small">
            {data?.scope === "all" ? "Semua tugas divisi" : "Tugas Saya"} · {rows.length} baris
            {busy ? " · memuat…" : ""}
          </p>
        </div>
        <button type="button" className="ghost sm" onClick={() => load()} disabled={busy} data-testid="division-task-refresh">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {err && <p className="error" data-testid="division-task-error">{err}</p>}

      {isKepala && (
        <div className="kp-kpi-strip" data-testid="kp-kpi-strip">
          {KP_KPI_ORDER.map((key) => {
            const card = kpis[key];
            const meta = KP_KPI_META[key];
            return (
              <button
                key={key}
                type="button"
                className={"kpi-card kpi-card-clickable kp-kpi-card " + (meta?.tone || "")}
                onClick={() => navigate(kpDetailPath(key))}
                data-testid={`kp-kpi-card-${key}`}
                title={meta?.description}
              >
                <span className="kpi-head">{meta?.title || key}</span>
                <strong>{busy && !card ? "…" : formatKpiCardValue(card)}</strong>
                <em className="muted small">Klik untuk detail</em>
              </button>
            );
          })}
        </div>
      )}

      <div className="division-task-toolbar">
        <label className="division-task-search">
          <Search size={14} aria-hidden="true" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Cari PO, customer, item"
            aria-label="Cari PO, customer, item"
            data-testid="division-task-search"
          />
        </label>
        <div className="division-task-filters" data-testid="division-task-status-filters">
          {statusTabsFor(isKepala).map((t) => (
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
        {isKepala && (
          <>
            <select
              className="sm"
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              data-testid="kp-board-filter-priority"
              aria-label="Filter prioritas"
            >
              <option value="">Prioritas</option>
              <option value="urgent">Urgent</option>
              <option value="overdue">Overdue</option>
              <option value="normal">Normal</option>
            </select>
            <select
              className="sm"
              value={holdFilter}
              onChange={(e) => setHoldFilter(e.target.value)}
              data-testid="kp-board-filter-hold"
              aria-label="Filter HOLD"
            >
              <option value="">HOLD</option>
              <option value="yes">HOLD saja</option>
              <option value="no">Tanpa HOLD</option>
            </select>
          </>
        )}
        {canViewAll && (
          <div className="division-task-scope" data-testid="division-task-scope">
            <button type="button" className={scope === "mine" ? "primary sm" : "ghost sm"} onClick={() => { setScope("mine"); load("mine"); }} data-testid="division-task-scope-mine">Tugas Saya</button>
            <button type="button" className={scope === "all" ? "primary sm" : "ghost sm"} onClick={() => { setScope("all"); load("all"); }} data-testid="division-task-scope-all">Semua Tugas Divisi</button>
          </div>
        )}
      </div>

      {isKepala && (
        <div className="kp-queue-tabs" data-testid="kp-queue-tabs" role="tablist" aria-label="Antrian Kepala Produksi">
          <span className="kp-queue-tab is-active" role="tab" aria-selected="true" data-testid="kp-queue-actionable">
            Antrian Penjadwalan ({rows.length.toLocaleString("id-ID")})
          </span>
          <Link
            className="kp-queue-tab"
            role="tab"
            aria-selected="false"
            to={kpDetailPath("hold")}
            data-testid="kp-queue-hold"
          >
            HOLD — pemantauan ({Number(data?.hold_readonly_count || kpis?.hold?.count || 0).toLocaleString("id-ID")})
          </Link>
        </div>
      )}

      <div className={"prod-table-wrap" + (isKepala ? " kp-table-wrap" : "")}>
        <table className={"prod-table" + (isKepala ? " kp-priority-table" : "")} data-testid="division-task-table">
          <thead>
            {isKepala && (
              <tr className="production-quantity-groups">
                <th scope="colgroup" colSpan={4}>Pekerjaan</th>
                <th scope="colgroup">Pesanan</th>
                <th scope="colgroup" colSpan={2}>Hasil Produksi</th>
                <th scope="colgroup" colSpan={2}>Penjadwalan</th>
                <th scope="colgroup">Stok</th>
                <th scope="colgroup" colSpan={2}>Tindak Lanjut</th>
              </tr>
            )}
            {isKepala ? (
              <tr>
                <th>Prioritas</th>
                <th>PO / Customer</th>
                <th>Item</th>
                <th>Tanggal Kirim</th>
                <th className="num">Total Order</th>
                <th className="num">Sudah Diproduksi</th>
                <th className="num">Belum Diproduksi</th>
                <th className="num">Sudah Dijadwalkan</th>
                <th className="num">Belum Dijadwalkan</th>
                <th className="num">Stock Saat Ini</th>
                <th>Status</th>
                <th>Aksi</th>
              </tr>
            ) : (
              <tr>
                <th>No PO</th>
                <th>Customer</th>
                <th>Item</th>
                {showStock && <th className="num">Stock Saat Ini</th>}
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
            )}
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={emptyCols} className="muted" data-testid="division-task-empty">
                  {isKepala ? (
                    <div className="kp-empty-actionable" data-testid="kp-empty-actionable">
                      <p className="kp-empty-title">Tidak ada pekerjaan dalam antrian penjadwalan</p>
                      <p className="kp-empty-copy">
                        Antrian ini hanya menampilkan PO yang eligible untuk dijadwalkan.
                        {(Number(data?.hold_readonly_count) > 0 || Number(kpis?.hold?.count) > 0) && (
                          <>
                            {" "}Ada{" "}
                            <strong data-testid="kp-empty-hold-count">
                              {Number(data?.hold_readonly_count || kpis?.hold?.count || 0).toLocaleString("id-ID")}
                            </strong>
                            {" "}PO HOLD untuk dipantau (bukan antrian jadwalkan).
                          </>
                        )}
                      </p>
                      {(Number(data?.hold_readonly_count) > 0 || Number(kpis?.hold?.count) > 0) && (
                        <Link
                          className="primary sm"
                          to={kpDetailPath("hold")}
                          data-testid="kp-empty-hold-link"
                        >
                          Lihat PO HOLD
                        </Link>
                      )}
                    </div>
                  ) : (
                    "Tidak ada tugas."
                  )}
                </td>
              </tr>
            )}
            {rows.map((t) => (
              isKepala ? (
                <tr key={t.task_id} data-testid={`division-task-row-${t.order_number}`}>
                  <td>
                    <span className="kp-priority-badges" data-testid={`kp-priority-${t.task_id}`}>
                      {(t.priority_display || ["NORMAL"]).map((lab) => (
                        <span key={lab} className={priorityBadgeClass(lab)}>{lab}</span>
                      ))}
                    </span>
                  </td>
                  <td>
                    <div className="kp-po-cell">
                      <div className="mono kp-po-number">{t.order_number}</div>
                      <PoIdentityBadges order={t} testIdPrefix={`kp-po-${t.order_number || t.task_id}`} omitUrgent />
                      <div className="muted small">{t.customer_name || "—"}</div>
                    </div>
                  </td>
                  <td title={t.item || ""}>{t.item || "—"}</td>
                  <td className="mono" data-testid={`kp-delivery-${t.task_id}`}>{formatDeliveryDate(t)}</td>
                  <td className="num mono">{formatIdQty(t.qty_order_current ?? t.target_qty)}</td>
                  <td className="num mono">{formatIdQty(t.qty_produced)}</td>
                  <td className="num mono">{formatIdQty(t.qty_production_remaining)}</td>
                  <td className="num mono">{formatIdQty(t.qty_scheduled ?? t.completed_qty)}</td>
                  <td className="num mono">{formatIdQty(t.qty_unscheduled ?? t.remaining_qty)}</td>
                  <td className="num mono" data-testid={`division-task-stock-${t.task_id || t.order_number}`}>
                    {formatCurrentStock(t.current_stock)}
                  </td>
                  <td><span className={"pill sm " + statusTone(t.status)}>{t.status_label}</span></td>
                  <td className="division-task-actions">
                    {!t.schedule_eligible || t.authoritative_hold || t.hold ? (
                      <button
                        type="button"
                        className="ghost sm"
                        disabled
                        data-testid={`division-task-kerja-${t.order_number}`}
                        title={(t.hold_reasons || []).join("; ") || "Tidak eligible untuk jadwalkan"}
                      >
                        {t.authoritative_hold || t.hold ? "HOLD" : "Blokir"}
                      </button>
                    ) : (
                      <Link
                        className="primary sm"
                        to={taskWorkHref(t)}
                        state={listNavState}
                        onClick={() => onOpenPo(t.order_id)}
                        data-testid={`division-task-kerja-${t.order_number}`}
                      >
                        {t.action_label || "Jadwalkan"}
                      </Link>
                    )}
                    {canViewPo && (
                      <Link
                        className="ghost sm"
                        to={`/po/${t.order_id}`}
                        state={listNavState}
                        onClick={() => onOpenPo(t.order_id)}
                        data-testid={`division-task-po-${t.order_number}`}
                      >
                        Lihat PO
                      </Link>
                    )}
                  </td>
                </tr>
              ) : (
                <tr key={t.task_id} data-testid={`division-task-row-${t.order_number}`}>
                  <td className="mono division-task-po-cell">
                    <div className="division-task-po-stack">
                      <span className="division-task-po-number">{t.order_number}</span>
                      <span className="division-task-po-badges">
                        <ProductionSchedulePriorityBadges row={t} testIdPrefix={`division-task-priority-${t.task_id || t.order_number}`} />
                        <PoIdentityBadges order={t} testIdPrefix={`division-task-po-${t.order_number || t.task_id}`} omitUrgent />
                      </span>
                    </div>
                  </td>
                  <td>{t.customer_name || "—"}</td>
                  <td>{t.item || "—"}</td>
                  {showStock && (
                    <td className="num mono" data-testid={`division-task-stock-${t.task_id || t.order_number}`}>
                      {formatCurrentStock(t.current_stock)}
                    </td>
                  )}
                  {cfg.extra === "design" && <td data-testid={`division-task-design-${t.order_number}`}>{t.design_status_label || t.design_status || "—"}</td>}
                  {cfg.extra === "sj" && <td>{t.sj_status || "—"}</td>}
                  {cfg.extra === "driver" && <td className="mono">{t.photo_count ?? 0} foto</td>}
                  {qty && <td className="num mono">{formatIdQty(t.target_qty)}</td>}
                  {qty && <td className="num mono">{formatIdQty(t.completed_qty)}</td>}
                  {qty && <td className="num mono">{formatIdQty(t.remaining_qty)}</td>}
                  <td data-testid={`division-task-input-${t.order_number}`}>{t.required_input}</td>
                  <td><span className={"pill sm " + statusTone(t.status)}>{t.status_label}</span></td>
                  <td className="division-task-actions">
                    <Link
                      className="primary sm"
                      to={taskWorkHref(t)}
                      state={listNavState}
                      onClick={() => onOpenPo(t.order_id)}
                      data-testid={`division-task-kerja-${t.order_number}`}
                    >
                      {t.action_label || "Kerjakan"}
                    </Link>
                    {canViewPo && (
                      <Link
                        className="ghost sm"
                        to={`/po/${t.order_id}`}
                        state={listNavState}
                        onClick={() => onOpenPo(t.order_id)}
                        data-testid={`division-task-po-${t.order_number}`}
                      >
                        Lihat PO
                      </Link>
                    )}
                  </td>
                </tr>
              )
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

