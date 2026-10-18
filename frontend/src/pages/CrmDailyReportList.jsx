import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  CRM_DAILY_REPORT_NEW_PATH,
  canAccessCrmDailyReport,
  canCreateCrmDailyReport,
  canViewAllCrmDailyReports,
  complaintLabel,
  issueLabel,
  truncateText,
} from "../lib/crmDailyReport";
import {
  CRM_DAILY_COL_LABELS,
  CRM_DAILY_DEFAULT_COL_ORDER,
  CRM_DAILY_DEFAULT_COL_WIDTHS,
  clearCrmDailyColPrefs,
  clampCrmDailyColWidth,
  crmDailyTableMinWidth,
  moveCrmDailyColumn,
  readCrmDailyColPrefs,
  shiftCrmDailyColumn,
  writeCrmDailyColPrefs,
} from "../lib/crmDailyReportColumns";
import "./crmDailyReport.css";

function errText(e) {
  const d = e?.response?.data?.detail;
  if (typeof d === "string") return d;
  return d?.message || e?.message || "Gagal memuat laporan";
}

const EMPTY_FILTERS = {
  date_from: "",
  date_to: "",
  crm_user_id: "",
  sales: "",
  status_order: "",
  status_customer: "",
  has_complaint: "",
  has_issue: "",
  q: "",
};

function CdrColResizeHandle({ colKey, onPointerDown }) {
  return (
    <span
      className="cdr-col-resize"
      role="separator"
      aria-orientation="vertical"
      aria-label={`Resize ${CRM_DAILY_COL_LABELS[colKey] || colKey}`}
      data-testid={`cdr-resize-${colKey}`}
      onPointerDown={(event) => onPointerDown(event, colKey)}
    />
  );
}

function renderCell(key, row, idx, start) {
  switch (key) {
    case "no":
      return start + idx;
    case "tanggal":
      return row.report_date;
    case "crm":
      return row.created_by_name || "—";
    case "customer":
      return truncateText(row.outlet_name || (row.status_customer === "New" ? "(tanpa nama)" : "—"), 42);
    case "wa":
      return row.whatsapp || "—";
    case "po":
      return row.po_number || "—";
    case "sales":
      return row.sales_label || "—";
    case "status_order":
      return (
        <span className={`cdr-badge ${row.status_order === "ORDER" ? "is-order" : "is-no-order"}`}>
          {row.status_order}
        </span>
      );
    case "status_customer":
      return (
        <span className={`cdr-badge ${row.status_customer === "New" ? "is-new" : "is-repeat"}`}>
          {row.status_customer}
        </span>
      );
    case "no_order_reason":
      return truncateText(row.no_order_reason, 40);
    case "complaint":
      return row.has_complaint
        ? <span className="cdr-badge is-ya">{complaintLabel(row)}</span>
        : <span className="cdr-muted">Tidak</span>;
    case "issue":
      return row.has_issue
        ? <span title={row.issue_description || ""}>{issueLabel(row)}</span>
        : <span className="cdr-muted">Tidak</span>;
    case "actions":
      return (
        <div className="cdr-actions">
          <Link className="ghost" to={`/penjualan/laporan-harian-crm/${row.id}`} data-testid={`cdr-detail-${row.id}`}>Detail</Link>
          {row.can_edit && (
            <Link className="ghost" to={`/penjualan/laporan-harian-crm/${row.id}?edit=1`} data-testid={`cdr-edit-${row.id}`}>Edit</Link>
          )}
        </div>
      );
    default:
      return "—";
  }
}

export default function CrmDailyReportList() {
  const { hasPerm } = useAuth();
  const navigate = useNavigate();
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [qDraft, setQDraft] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [data, setData] = useState({ items: [], total: 0, page: 1, page_count: 1, summary: {} });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [colOrder, setColOrder] = useState(() => readCrmDailyColPrefs().order);
  const [colWidths, setColWidths] = useState(() => readCrmDailyColPrefs().widths);
  const [draggingKey, setDraggingKey] = useState("");
  const [dropTargetKey, setDropTargetKey] = useState("");
  const [hScrollable, setHScrollable] = useState(false);
  const colResizeRef = useRef(null);
  const tableScrollRef = useRef(null);
  const viewAll = canViewAllCrmDailyReports(hasPerm);

  const persistPrefs = useCallback((order, widths) => {
    writeCrmDailyColPrefs({ order, widths });
  }, []);

  const canAccess = canAccessCrmDailyReport(hasPerm);
  const canCreate = canCreateCrmDailyReport(hasPerm);

  const load = useCallback(async () => {
    if (!canAccess) return;
    setLoading(true);
    setError("");
    try {
      const params = {
        page,
        page_size: pageSize,
        ...Object.fromEntries(
          Object.entries({ ...filters, q: qDraft }).filter(([, v]) => String(v || "").trim() !== ""),
        ),
      };
      const res = await api.listCrmDailyReports(params);
      setData(res);
    } catch (e) {
      setError(errText(e));
    } finally {
      setLoading(false);
    }
  }, [canAccess, filters, page, pageSize, qDraft]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => () => {
    const active = colResizeRef.current;
    if (!active) return;
    active.handleEl.removeEventListener("pointermove", active.onMove);
    active.handleEl.removeEventListener("pointerup", active.onUp);
    active.handleEl.removeEventListener("pointercancel", active.onUp);
    document.body.classList.remove("cdr-col-resizing");
  }, []);

  const tableMinWidth = useMemo(() => crmDailyTableMinWidth(colWidths), [colWidths]);
  const rowCount = (data.items || []).length;

  const refreshHScroll = useCallback(() => {
    const el = tableScrollRef.current;
    if (!el) return;
    setHScrollable(el.scrollWidth > el.clientWidth + 2);
  }, []);

  useEffect(() => {
    refreshHScroll();
    const el = tableScrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => refreshHScroll());
    ro.observe(el);
    return () => ro.disconnect();
  }, [refreshHScroll, tableMinWidth, rowCount, colOrder, colWidths]);

  const onColResizePointerDown = useCallback((event, key) => {
    if (event.button != null && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const handleEl = event.currentTarget;
    const pointerId = event.pointerId;
    const startX = event.clientX;
    const startW = colWidths[key] ?? CRM_DAILY_DEFAULT_COL_WIDTHS[key];
    try {
      handleEl.setPointerCapture(pointerId);
    } catch {
      /* ignore */
    }
    document.body.classList.add("cdr-col-resizing");
    handleEl.classList.add("is-active");

    const onMove = (moveEvent) => {
      if (moveEvent.pointerId !== pointerId) return;
      const nextW = clampCrmDailyColWidth(key, startW + (moveEvent.clientX - startX));
      setColWidths((prev) => (prev[key] === nextW ? prev : { ...prev, [key]: nextW }));
    };
    const onUp = (upEvent) => {
      if (upEvent.pointerId !== pointerId) return;
      handleEl.removeEventListener("pointermove", onMove);
      handleEl.removeEventListener("pointerup", onUp);
      handleEl.removeEventListener("pointercancel", onUp);
      try {
        handleEl.releasePointerCapture(pointerId);
      } catch {
        /* ignore */
      }
      handleEl.classList.remove("is-active");
      document.body.classList.remove("cdr-col-resizing");
      setColWidths((prev) => {
        persistPrefs(colOrder, prev);
        return prev;
      });
      if (colResizeRef.current?.pointerId === pointerId) colResizeRef.current = null;
    };

    colResizeRef.current = { pointerId, handleEl, onMove, onUp };
    handleEl.addEventListener("pointermove", onMove);
    handleEl.addEventListener("pointerup", onUp);
    handleEl.addEventListener("pointercancel", onUp);
  }, [colOrder, colWidths, persistPrefs]);

  const applyOrder = useCallback((nextOrder) => {
    setColOrder(nextOrder);
    persistPrefs(nextOrder, colWidths);
  }, [colWidths, persistPrefs]);

  const onDragStart = (event, key) => {
    if (event.target?.closest?.(".cdr-col-resize")) {
      event.preventDefault();
      return;
    }
    setDraggingKey(key);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/cdr-col", key);
  };
  const onDragOver = (event, key) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    if (dropTargetKey !== key) setDropTargetKey(key);
  };
  const onDrop = (event, key) => {
    event.preventDefault();
    const from = event.dataTransfer.getData("text/cdr-col") || draggingKey;
    if (from) applyOrder(moveCrmDailyColumn(colOrder, from, key));
    setDraggingKey("");
    setDropTargetKey("");
  };
  const onDragEnd = () => {
    setDraggingKey("");
    setDropTargetKey("");
  };

  const moveColumnKeyboard = (key, direction) => {
    applyOrder(shiftCrmDailyColumn(colOrder, key, direction));
  };

  const resetColumns = () => {
    clearCrmDailyColPrefs();
    setColOrder([...CRM_DAILY_DEFAULT_COL_ORDER]);
    setColWidths({ ...CRM_DAILY_DEFAULT_COL_WIDTHS });
  };

  if (!canAccess) {
    return <div className="cdr-page" data-testid="crm-daily-report-denied"><p>Akses ditolak.</p></div>;
  }

  const summary = data.summary || {};
  const start = data.total ? (page - 1) * pageSize + 1 : 0;
  const end = Math.min(page * pageSize, data.total || 0);

  return (
    <div className="cdr-page" data-testid="crm-daily-report-list">
      <div className="cdr-head">
        <div>
          <h1>Laporan Harian CRM</h1>
          <p>Aktivitas customer, hasil order, komplain dan kendala.</p>
        </div>
        <div className="cdr-head-actions">
          <button type="button" className="ghost" onClick={resetColumns} data-testid="cdr-reset-columns">
            Reset Kolom
          </button>
          {canCreate && (
            <button type="button" className="primary" data-testid="cdr-create" onClick={() => navigate(CRM_DAILY_REPORT_NEW_PATH)}>
              + Buat Laporan
            </button>
          )}
        </div>
      </div>

      <div className="cdr-toolbar" data-testid="cdr-filters">
        <div className="cdr-filters">
          <label>Tanggal dari
            <input type="date" value={filters.date_from} onChange={(e) => { setPage(1); setFilters((f) => ({ ...f, date_from: e.target.value })); }} data-testid="cdr-filter-date-from" />
          </label>
          <label>Tanggal sampai
            <input type="date" value={filters.date_to} onChange={(e) => { setPage(1); setFilters((f) => ({ ...f, date_to: e.target.value })); }} data-testid="cdr-filter-date-to" />
          </label>
          {viewAll && (
            <label>CRM
              <input value={filters.crm_user_id} placeholder="ID CRM (opsional)" onChange={(e) => { setPage(1); setFilters((f) => ({ ...f, crm_user_id: e.target.value })); }} data-testid="cdr-filter-crm" />
            </label>
          )}
          <label>Sales
            <input value={filters.sales} onChange={(e) => { setPage(1); setFilters((f) => ({ ...f, sales: e.target.value })); }} data-testid="cdr-filter-sales" />
          </label>
          <label>Status Order
            <select value={filters.status_order} onChange={(e) => { setPage(1); setFilters((f) => ({ ...f, status_order: e.target.value })); }} data-testid="cdr-filter-status-order">
              <option value="">Semua</option>
              <option value="ORDER">ORDER</option>
              <option value="TIDAK ORDER">TIDAK ORDER</option>
            </select>
          </label>
          <label>Status Customer
            <select value={filters.status_customer} onChange={(e) => { setPage(1); setFilters((f) => ({ ...f, status_customer: e.target.value })); }} data-testid="cdr-filter-status-customer">
              <option value="">Semua</option>
              <option value="New">New</option>
              <option value="Repeat">Repeat</option>
            </select>
          </label>
          <label>Komplain
            <select value={filters.has_complaint} onChange={(e) => { setPage(1); setFilters((f) => ({ ...f, has_complaint: e.target.value })); }} data-testid="cdr-filter-complaint">
              <option value="">Semua</option>
              <option value="true">Ya</option>
              <option value="false">Tidak</option>
            </select>
          </label>
          <label>Kendala
            <select value={filters.has_issue} onChange={(e) => { setPage(1); setFilters((f) => ({ ...f, has_issue: e.target.value })); }} data-testid="cdr-filter-issue">
              <option value="">Semua</option>
              <option value="true">Ya</option>
              <option value="false">Tidak</option>
            </select>
          </label>
        </div>
        <div className="cdr-search-row">
          <input
            value={qDraft}
            onChange={(e) => setQDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { setPage(1); load(); } }}
            placeholder="Cari nama customer, outlet, no WA, no PO…"
            data-testid="cdr-search"
          />
          <button type="button" className="ghost" onClick={() => { setPage(1); load(); }} data-testid="cdr-refresh">Refresh</button>
        </div>
      </div>

      <div className="cdr-summary" data-testid="cdr-summary">
        <span>Total <strong>{summary.total ?? data.total ?? 0}</strong></span>
        <span>ORDER <strong>{summary.order_count ?? 0}</strong></span>
        <span>TIDAK ORDER <strong>{summary.no_order_count ?? 0}</strong></span>
        <span>Kendala <strong>{summary.issue_count ?? 0}</strong></span>
      </div>

      {error && <p className="cdr-field-error" data-testid="cdr-list-error">{error}</p>}

      <div
        className={`cdr-table-shell${hScrollable ? " is-h-scroll" : ""}`}
        data-rows={rowCount}
        data-testid="cdr-table-shell"
      >
        {hScrollable && (
          <p className="cdr-hscroll-caption" data-testid="cdr-hscroll-hint">
            Geser horizontal untuk melihat kolom lain →
          </p>
        )}
        <div
          className="cdr-table-wrap"
          data-testid="cdr-table-scroll"
          ref={tableScrollRef}
          onScroll={refreshHScroll}
        >
          <table className="cdr-table" style={{ minWidth: tableMinWidth }} data-testid="cdr-table">
            <colgroup>
              {colOrder.map((key) => (
                <col key={key} data-col={key} style={{ width: colWidths[key] || CRM_DAILY_DEFAULT_COL_WIDTHS[key] }} />
              ))}
            </colgroup>
            <thead>
              <tr>
                {colOrder.map((key) => {
                  const label = CRM_DAILY_COL_LABELS[key] || key;
                  return (
                    <th
                      key={key}
                      className={`cdr-th${draggingKey === key ? " is-dragging" : ""}${dropTargetKey === key ? " is-drop-target" : ""}`}
                      data-testid={`cdr-th-${key}`}
                      title={label}
                      aria-label={label}
                      draggable={key !== "actions"}
                      onDragStart={(e) => onDragStart(e, key)}
                      onDragOver={(e) => onDragOver(e, key)}
                      onDrop={(e) => onDrop(e, key)}
                      onDragEnd={onDragEnd}
                    >
                      <div className="cdr-th-inner">
                        {key !== "actions" && (
                          <button
                            type="button"
                            className="cdr-th-drag"
                            title={`Geser untuk urutkan: ${label}`}
                            aria-label={`Pindahkan kolom ${label}`}
                            data-testid={`cdr-drag-${key}`}
                            tabIndex={-1}
                            onMouseDown={(e) => e.stopPropagation()}
                          >
                            ⋮⋮
                          </button>
                        )}
                        <span className="cdr-th-label" title={label}>{label}</span>
                        {key !== "actions" && (
                          <button
                            type="button"
                            className="cdr-th-menu"
                            title={`${label} — pindah kolom (←/→)`}
                            aria-label={`Pindah kolom ${label} dengan keyboard`}
                            data-testid={`cdr-move-${key}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              const dir = e.shiftKey ? 1 : -1;
                              moveColumnKeyboard(key, dir);
                            }}
                            onKeyDown={(e) => {
                              if (e.key === "ArrowLeft") {
                                e.preventDefault();
                                moveColumnKeyboard(key, -1);
                              } else if (e.key === "ArrowRight") {
                                e.preventDefault();
                                moveColumnKeyboard(key, 1);
                              }
                            }}
                          >
                            ⇄
                          </button>
                        )}
                        <CdrColResizeHandle colKey={key} onPointerDown={onColResizePointerDown} />
                      </div>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {!loading && rowCount === 0 && (
                <tr className="cdr-empty-row" data-testid="cdr-empty-state">
                  <td colSpan={colOrder.length}>
                    <p className="cdr-empty-msg">Belum ada laporan. Gunakan filter lain atau buat laporan baru.</p>
                  </td>
                </tr>
              )}
              {(data.items || []).map((row, idx) => (
                <tr key={row.id} data-testid={`cdr-row-${row.id}`}>
                  {colOrder.map((key) => (
                    <td key={key} data-col={key} className={key === "actions" || key === "status_order" || key === "status_customer" ? "is-wrap" : undefined}>
                      {renderCell(key, row, idx, start)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {hScrollable && <div className="cdr-hscroll-fade" aria-hidden="true" />}
      </div>

      <div className="cdr-pager">
        <span data-testid="cdr-pager-label">Menampilkan {start}–{end} dari {data.total || 0} laporan</span>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select value={pageSize} onChange={(e) => { setPage(1); setPageSize(Number(e.target.value)); }} data-testid="cdr-page-size">
            {[10, 20, 50].map((n) => <option key={n} value={n}>{n} per halaman</option>)}
          </select>
          <button type="button" className="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
          <span>{page} / {data.page_count || 1}</span>
          <button type="button" className="ghost" disabled={page >= (data.page_count || 1)} onClick={() => setPage((p) => p + 1)}>Next</button>
        </div>
      </div>
    </div>
  );
}
