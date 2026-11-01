import "../productionCompact.css";
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, RefreshCw, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  formatCurrentStock,
  formatIdQty,
  statusTone,
  taskMatchesQuery,
  taskWorkHref,
} from "../lib/divisionTasks";
import {
  KP_KPI_META,
  formatDeliveryDate,
  formatKpiCardValue,
  paginateRows,
  priorityBadgeClass,
  rowMatchesExtraFilters,
  uniquePoCount,
} from "../lib/kepalaProduksiBoard";
import {
  capturePoOpenFromList,
  poNavStateFromList,
} from "../lib/poListReturn";
import PoIdentityBadges from "../components/PoIdentityBadges";

const PAGE_SIZE = 50;

export default function KepalaProduksiKpiDetailPage() {
  const { kpi } = useParams();
  const meta = KP_KPI_META[kpi];
  const { user, hasPerm } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState(() => searchParams.get("q") || "");
  const [priority, setPriority] = useState(() => searchParams.get("priority") || "");
  const [hold, setHold] = useState(() => searchParams.get("hold") || "");
  const [dateFrom, setDateFrom] = useState(() => searchParams.get("from") || "");
  const [dateTo, setDateTo] = useState(() => searchParams.get("to") || "");
  const [page, setPage] = useState(() => Number(searchParams.get("page") || 1));

  const load = async () => {
    if (!meta) return;
    setBusy(true);
    setErr("");
    try {
      const d = await api.divisionTasks("kepala_produksi", "all", { kpi });
      setData(d);
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat detail KPI");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kpi]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (q.trim()) next.set("q", q.trim());
    if (priority) next.set("priority", priority);
    if (hold) next.set("hold", hold);
    if (dateFrom) next.set("from", dateFrom);
    if (dateTo) next.set("to", dateTo);
    if (page > 1) next.set("page", String(page));
    if (next.toString() === searchParams.toString()) return;
    setSearchParams(next, { replace: true });
  }, [q, priority, hold, dateFrom, dateTo, page, searchParams, setSearchParams]);

  const filtered = useMemo(() => {
    const base = data?.detail_rows || [];
    return base.filter(
      (t) =>
        taskMatchesQuery(t, q) &&
        rowMatchesExtraFilters(t, { priority, hold, dateFrom, dateTo }),
    );
  }, [data, q, priority, hold, dateFrom, dateTo]);

  const paged = useMemo(() => paginateRows(filtered, page, PAGE_SIZE), [filtered, page]);
  const poCount = uniquePoCount(filtered);

  useEffect(() => {
    if (page !== paged.page) setPage(paged.page);
  }, [paged.page, page]);

  if (!meta) {
    return <Navigate to="/tasks/kepala-produksi" replace />;
  }

  const canViewPo = !!data?.can_view_po;
  const listNavState = poNavStateFromList({
    pathname: `/tasks/kepala-produksi/prioritas/${kpi}`,
    search: searchParams.toString() ? `?${searchParams}` : "",
  });
  const card = data?.kpi_value;
  const onOpenPo = (orderId) => {
    capturePoOpenFromList({
      user,
      orderId,
      pathname: `/tasks/kepala-produksi/prioritas/${kpi}`,
      search: searchParams.toString() ? `?${searchParams}` : "",
    });
  };
  const resetExtra = () => {
    setPriority("");
    setHold("");
    setDateFrom("");
    setDateTo("");
    setQ("");
    setPage(1);
  };

  return (
    <div className="prod-page division-task-page kp-board-page production-compact" data-testid="kp-kpi-detail-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Priority Board · Detail KPI</p>
          <h1 data-testid="kp-kpi-detail-title">{meta.title}</h1>
          <p className="muted small" data-testid="kp-kpi-detail-desc">{meta.description}</p>
          <p className="muted small" data-testid="kp-kpi-detail-value">
            Nilai KPI: <strong>{formatKpiCardValue(card)}</strong>
            {" · "}
            {poCount} PO unik · {filtered.length} baris item
            {busy ? " · memuat…" : ""}
          </p>
        </div>
        <div className="division-task-actions">
          <Link className="ghost sm" to="/tasks/kepala-produksi" data-testid="kp-kpi-detail-back">
            <ArrowLeft size={14} /> Kembali
          </Link>
          <button type="button" className="ghost sm" onClick={load} disabled={busy} data-testid="kp-kpi-detail-refresh">
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {err && <p className="error" data-testid="kp-kpi-detail-error">{err}</p>}

      <div className="division-task-toolbar kp-detail-toolbar">
        <label className="division-task-search">
          <Search size={14} aria-hidden="true" />
          <input
            value={q}
            onChange={(e) => { setQ(e.target.value); setPage(1); }}
            placeholder="Cari PO, customer, item"
            aria-label="Cari PO, customer, item"
            data-testid="kp-kpi-detail-search"
          />
        </label>
        <select
          className="sm"
          value={priority}
          onChange={(e) => { setPriority(e.target.value); setPage(1); }}
          data-testid="kp-kpi-filter-priority"
          aria-label="Filter prioritas"
        >
          <option value="">Prioritas (semua)</option>
          <option value="urgent">Urgent</option>
          <option value="overdue">Overdue</option>
          <option value="normal">Normal</option>
        </select>
        <select
          className="sm"
          value={hold}
          onChange={(e) => { setHold(e.target.value); setPage(1); }}
          data-testid="kp-kpi-filter-hold"
          aria-label="Filter HOLD"
        >
          <option value="">HOLD (semua)</option>
          <option value="yes">HOLD saja</option>
          <option value="no">Tanpa HOLD</option>
        </select>
        <label className="kp-date-filter">
          <span className="muted small">Dari</span>
          <input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(1); }} data-testid="kp-kpi-filter-from" />
        </label>
        <label className="kp-date-filter">
          <span className="muted small">Sampai</span>
          <input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(1); }} data-testid="kp-kpi-filter-to" />
        </label>
        <button type="button" className="ghost sm" onClick={resetExtra} data-testid="kp-kpi-filter-reset">
          Reset filter
        </button>
      </div>

      <div className="prod-table-wrap kp-table-wrap">
        <table className="prod-table kp-priority-table" data-testid="kp-kpi-detail-table">
          <thead>
            <tr className="production-quantity-groups">
              <th scope="colgroup" colSpan={4}>Pekerjaan</th>
              <th scope="colgroup">Pesanan</th>
              <th scope="colgroup" colSpan={2}>Hasil Produksi</th>
              <th scope="colgroup" colSpan={2}>Penjadwalan</th>
              <th scope="colgroup">Stok</th>
              <th scope="colgroup" colSpan={2}>Tindak Lanjut</th>
            </tr>
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
          </thead>
          <tbody>
            {paged.rows.length === 0 && (
              <tr>
                <td colSpan={12} className="muted" data-testid="kp-kpi-detail-empty">
                  Tidak ada PO untuk KPI ini.
                </td>
              </tr>
            )}
            {paged.rows.map((t) => {
              const blocked = !t.schedule_eligible || t.authoritative_hold || t.hold || t.readonly_hold;
              return (
                <tr key={t.task_id} data-testid={`kp-kpi-detail-row-${t.order_number}`}>
                  <td>
                    <span className="kp-priority-badges">
                      {(t.priority_display || ["NORMAL"]).map((lab) => (
                        <span key={lab} className={priorityBadgeClass(lab)}>{lab}</span>
                      ))}
                    </span>
                  </td>
                  <td>
                    <div className="kp-po-cell">
                      <div className="mono kp-po-number">{t.order_number}</div>
                      <PoIdentityBadges order={t} testIdPrefix={`kp-kpi-po-${t.order_number}`} omitUrgent />
                      <div className="muted small">{t.customer_name || "—"}</div>
                    </div>
                  </td>
                  <td title={t.item || ""}>{t.item || "—"}{t.unit ? ` (${t.unit})` : ""}</td>
                  <td className="mono">{formatDeliveryDate(t)}</td>
                  <td className="num mono">{formatIdQty(t.qty_order_current ?? t.target_qty)}</td>
                  <td className="num mono">{formatIdQty(t.qty_produced)}</td>
                  <td className="num mono">{formatIdQty(t.qty_production_remaining)}</td>
                  <td className="num mono">{formatIdQty(t.qty_scheduled ?? t.completed_qty)}</td>
                  <td className="num mono">{formatIdQty(t.qty_unscheduled ?? t.remaining_qty)}</td>
                  <td className="num mono">{formatCurrentStock(t.current_stock)}</td>
                  <td><span className={"pill sm " + statusTone(t.status)}>{t.status_label}</span></td>
                  <td className="division-task-actions">
                    {blocked ? (
                      <button type="button" className="ghost sm" disabled data-testid={`kp-kpi-jadwal-blocked-${t.order_number}`} title={(t.hold_reasons || []).join("; ") || "Tidak eligible"}>
                        {t.authoritative_hold || t.hold ? "HOLD" : "Blokir"}
                      </button>
                    ) : (
                      <Link
                        className="primary sm"
                        to={taskWorkHref(t)}
                        state={listNavState}
                        onClick={() => onOpenPo(t.order_id)}
                        data-testid={`kp-kpi-jadwal-${t.order_number}`}
                      >
                        Jadwalkan
                      </Link>
                    )}
                    {canViewPo && (
                      <Link
                        className="ghost sm"
                        to={`/po/${t.order_id}`}
                        state={listNavState}
                        onClick={() => onOpenPo(t.order_id)}
                        data-testid={`kp-kpi-po-${t.order_number}`}
                      >
                        Lihat PO
                      </Link>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="kp-pagination" data-testid="kp-kpi-pagination">
        <button type="button" className="ghost sm" disabled={paged.page <= 1} onClick={() => setPage((p) => p - 1)}>Sebelumnya</button>
        <span className="muted small">Halaman {paged.page} / {paged.pages} · {paged.total} baris</span>
        <button type="button" className="ghost sm" disabled={paged.page >= paged.pages} onClick={() => setPage((p) => p + 1)}>Berikutnya</button>
      </div>
      {!hasPerm("schedule.edit") && !hasPerm("nav.tasks.kepala_produksi") ? null : null}
    </div>
  );
}

