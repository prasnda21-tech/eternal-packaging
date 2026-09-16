import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";
import { formatDisplayDate } from "../lib/constants";
import { capturePoOpenFromList, poNavStateFromList } from "../lib/poListReturn";
import {
  selectAllVisible,
  toggleId,
} from "../lib/rowSelection";
import {
  STATUS_PO_DEFAULT_WIDTHS,
  STATUS_PO_WORKFLOW_STAGES,
  clampColWidth,
  readStoredColWidths,
  statusBadgeTone,
  stickyOffsets,
  workflowCellState,
  workflowCellTitle,
  workflowProgressColor,
  workflowProgressPercent,
  writeStoredColWidths,
} from "../lib/statusPoWorkflow";

function WorkflowMark({ state, color }) {
  if (state === "completed" || state === "done") {
    return (
      <span className="spo-mark" data-state="completed" title="Selesai">
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="7" fill="#22A447" />
          <path d="M5 8.2l2 2.1 4.2-4.4" fill="none" stroke="#fff" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
    );
  }
  if (state === "active" || state === "current") {
    return (
      <span className="spo-mark" data-state="active" title="Sedang berjalan">
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="6.2" fill="none" stroke={color} strokeWidth="2" />
          <circle cx="8" cy="8" r="2.4" fill={color} />
        </svg>
      </span>
    );
  }
  if (state === "hold") {
    return (
      <span className="spo-mark" data-state="hold" title="Tertahan">
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="7" fill="#E6A817" />
          <path d="M8 4.6v4.2" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
          <circle cx="8" cy="11.2" r="0.9" fill="#fff" />
        </svg>
      </span>
    );
  }
  if (state === "cancelled") {
    return (
      <span className="spo-mark" data-state="cancelled" title="Dibatalkan">
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="7" fill="#C0392B" />
          <path d="M5.4 5.4l5.2 5.2M10.6 5.4l-5.2 5.2" stroke="#fff" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
    );
  }
  if (state === "unknown") {
    return (
      <span className="spo-mark" data-state="unknown" title="Tidak dapat ditentukan">
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="6.2" fill="none" stroke="#9AA8B5" strokeWidth="1.6" />
          <text x="8" y="11.2" textAnchor="middle" fontSize="9" fontWeight="700" fill="#9AA8B5">?</text>
        </svg>
      </span>
    );
  }
  return (
    <span className="spo-mark" data-state="pending" title="Belum dimulai">
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
        <path d="M4 8h8" stroke="#B4C0CC" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    </span>
  );
}

function ResizeHandle({ colKey, onStart }) {
  return (
    <span
      className="spo-resize"
      role="separator"
      aria-orientation="vertical"
      data-testid={`status-po-resize-${colKey}`}
      onMouseDown={(e) => onStart(e, colKey)}
    />
  );
}

export default function StatusPoWorkflowTable({
  visible,
  canSelect,
  selected,
  setSelected,
  visibleIds,
  headerState,
  headerRef,
  user,
  location,
}) {
  const [widths, setWidths] = useState(() => readStoredColWidths());
  const dragRef = useRef(null);
  const [narrow, setNarrow] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return undefined;
    const mq = window.matchMedia("(max-width: 900px)");
    const apply = () => setNarrow(Boolean(mq.matches));
    apply();
    if (mq.addEventListener) mq.addEventListener("change", apply);
    else if (mq.addListener) mq.addListener(apply);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener("change", apply);
      else if (mq.removeListener) mq.removeListener(apply);
    };
  }, []);

  useEffect(() => {
    writeStoredColWidths(widths);
  }, [widths]);

  const onResizeStart = useCallback((event, key) => {
    event.preventDefault();
    event.stopPropagation();
    dragRef.current = { key, startX: event.clientX, startW: widths[key] };
    const onMove = (e) => {
      if (!dragRef.current) return;
      const delta = e.clientX - dragRef.current.startX;
      const nextW = clampColWidth(dragRef.current.key, dragRef.current.startW + delta);
      setWidths((prev) => ({ ...prev, [dragRef.current.key]: nextW }));
    };
    const onUp = () => {
      dragRef.current = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, [widths]);

  const offsets = useMemo(
    () => stickyOffsets(widths, { hasCheck: canSelect, narrow }),
    [widths, canSelect, narrow],
  );

  const tableMin = useMemo(() => {
    const keys = [
      ...(canSelect ? ["check"] : []),
      "po", "date", "customer", "items", "status",
      ...STATUS_PO_WORKFLOW_STAGES.map((s) => s.key),
      "progress", "action",
    ];
    return keys.reduce((sum, k) => sum + (widths[k] || STATUS_PO_DEFAULT_WIDTHS[k] || 60), 0);
  }, [widths, canSelect]);

  const dataSpan = (canSelect ? 1 : 0) + 5;
  const colCount = dataSpan + STATUS_PO_WORKFLOW_STAGES.length + 2;

  const stickyStyle = (key, extra = {}) => {
    const left = offsets[key];
    if (left == null) return extra;
    return {
      left: `${left}px`,
      ...extra,
    };
  };

  return (
    <table className="spo-table ship-table" style={{ minWidth: tableMin }} data-testid="status-po-matrix">
      <colgroup>
        {canSelect && <col style={{ width: widths.check }} />}
        <col style={{ width: widths.po }} />
        <col style={{ width: widths.date }} />
        <col style={{ width: widths.customer }} />
        <col style={{ width: widths.items }} />
        <col style={{ width: widths.status }} />
        {STATUS_PO_WORKFLOW_STAGES.map((s) => (
          <col key={s.key} style={{ width: widths[s.key] }} />
        ))}
        <col style={{ width: widths.progress }} />
        <col style={{ width: widths.action }} />
      </colgroup>
      <thead>
        <tr className="spo-group-row" data-testid="status-po-group-header">
          <th className="spo-th-group-data spo-sticky" colSpan={dataSpan} style={stickyStyle("check", { left: 0 })}>
            DATA PO
          </th>
          <th className="spo-th-group-wf" colSpan={STATUS_PO_WORKFLOW_STAGES.length} data-testid="status-po-group-workflow">
            PROGRESS WORKFLOW
          </th>
          <th className="spo-th-group-sum" colSpan={2} data-testid="status-po-group-summary">RINGKASAN</th>
        </tr>
        <tr className="spo-col-row" data-testid="status-po-col-header">
          {canSelect && (
            <th className="ship-check spo-sticky" style={stickyStyle("check")}>
              <input
                ref={headerRef}
                type="checkbox"
                checked={headerState.checked}
                disabled={!visibleIds.length}
                onChange={() => setSelected(selectAllVisible(visibleIds, selected))}
                aria-label="Pilih Semua (baris tampil)"
                data-testid="status-po-select-all"
              />
              <ResizeHandle colKey="check" onStart={onResizeStart} />
            </th>
          )}
          <th className="spo-th-id spo-sticky" style={stickyStyle("po")} data-testid="status-po-th-po">
            No PO
            <ResizeHandle colKey="po" onStart={onResizeStart} />
          </th>
          <th
            className={`spo-th-id spo-sticky${narrow ? " spo-unstick-narrow" : ""}`}
            style={stickyStyle("date")}
            data-testid="status-po-th-date"
          >
            Tanggal
            <ResizeHandle colKey="date" onStart={onResizeStart} />
          </th>
          <th
            className={`spo-th-id spo-sticky${narrow && offsets.customer != null ? " spo-sticky-last" : ""}`}
            style={stickyStyle("customer")}
            data-testid="status-po-th-customer"
          >
            Pelanggan
            <ResizeHandle colKey="customer" onStart={onResizeStart} />
          </th>
          <th
            className={`spo-th-id spo-sticky${narrow ? " spo-unstick-narrow" : ""}`}
            style={stickyStyle("items")}
            data-testid="status-po-th-items"
          >
            Item Line
            <ResizeHandle colKey="items" onStart={onResizeStart} />
          </th>
          <th
            className={`spo-th-id spo-sticky spo-sticky-last${narrow ? " spo-unstick-narrow" : ""}`}
            style={stickyStyle("status")}
            data-testid="status-po-th-status"
          >
            Status Saat Ini
            <ResizeHandle colKey="status" onStart={onResizeStart} />
          </th>
          {STATUS_PO_WORKFLOW_STAGES.map((s) => (
            <th
              key={s.key}
              className="spo-wf-th"
              style={{ "--spo-stage": s.color }}
              title={s.full}
              data-testid={`status-po-th-wf-${s.key}`}
            >
              {s.header}
              <ResizeHandle colKey={s.key} onStart={onResizeStart} />
            </th>
          ))}
          <th className="spo-th-id" data-testid="status-po-th-progress">
            Progress
            <ResizeHandle colKey="progress" onStart={onResizeStart} />
          </th>
          <th className="spo-th-id" data-testid="status-po-th-action">
            Aksi
            <ResizeHandle colKey="action" onStart={onResizeStart} />
          </th>
        </tr>
      </thead>
      <tbody>
        {visible.map((r) => {
          const pct = workflowProgressPercent(r);
          const barColor = workflowProgressColor(r);
          const tone = statusBadgeTone(r.status);
          return (
            <tr key={r.id} data-testid={`status-po-row-${r.po_number}`} className={selected.has(r.id) ? "is-selected" : ""}>
              {canSelect && (
                <td className="ship-check spo-sticky" style={stickyStyle("check")}>
                  <input
                    type="checkbox"
                    checked={selected.has(r.id)}
                    onChange={() => setSelected(toggleId(selected, r.id))}
                    aria-label={`Pilih ${r.po_number}`}
                    data-testid={`status-po-check-${r.po_number}`}
                  />
                </td>
              )}
              <td className="mono spo-sticky" style={stickyStyle("po")}>
                {r.id ? (
                  <Link
                    to={`/po/${r.id}`}
                    state={poNavStateFromList({ pathname: location.pathname, search: location.search })}
                    onClick={() => capturePoOpenFromList({
                      user,
                      orderId: r.id,
                      pathname: location.pathname,
                      search: location.search,
                    })}
                    data-testid={`status-po-link-${r.po_number}`}
                  >
                    {r.po_number}
                  </Link>
                ) : (r.po_number || "-")}
              </td>
              <td className={`mono spo-sticky${narrow ? " spo-unstick-narrow" : ""}`} style={stickyStyle("date")}>
                {formatDisplayDate(r.order_date)}
              </td>
              <td
                className={`ship-cust spo-sticky${narrow && offsets.customer != null ? " spo-sticky-last" : ""}`}
                style={stickyStyle("customer")}
                title={r.customer || undefined}
              >
                {r.customer || "-"}
              </td>
              <td
                className={`spo-items spo-sticky${narrow ? " spo-unstick-narrow" : ""}`}
                style={stickyStyle("items")}
                data-testid={`status-po-items-${r.po_number}`}
              >
                {(r.item_lines || []).length ? (
                  <div className="spo-item-lines">
                    {(r.item_lines || []).map((name) => (
                      <div key={name} className="spo-item-line">{name}</div>
                    ))}
                  </div>
                ) : "-"}
              </td>
              <td
                className={`spo-sticky spo-sticky-last${narrow ? " spo-unstick-narrow" : ""}`}
                style={stickyStyle("status")}
                data-testid={`status-po-status-${r.po_number}`}
              >
                <span className={`spo-badge spo-badge-${tone}`}>{r.status || "-"}</span>
              </td>
              {STATUS_PO_WORKFLOW_STAGES.map((s) => {
                const state = workflowCellState(r, s.key);
                return (
                  <td
                    key={s.key}
                    className="spo-wf-cell"
                    title={workflowCellTitle(state)}
                    data-testid={`status-po-wf-${r.po_number}-${s.key}`}
                    data-state={state}
                  >
                    <WorkflowMark state={state} color={s.color} />
                  </td>
                );
              })}
              <td data-testid={`status-po-delivered-${r.po_number}`}>
                <div className="spo-progress" data-testid={`status-po-progress-${r.po_number}`}>
                  <div className="spo-progress-track">
                    <div className="spo-progress-bar" style={{ width: `${pct}%`, background: barColor }} />
                  </div>
                  <span className="spo-progress-pct">{pct}%</span>
                </div>
              </td>
              <td>
                {r.id ? (
                  <Link
                    to={`/po/${r.id}`}
                    className="spo-action-link"
                    title="Buka PO"
                    state={poNavStateFromList({ pathname: location.pathname, search: location.search })}
                    onClick={() => capturePoOpenFromList({
                      user,
                      orderId: r.id,
                      pathname: location.pathname,
                      search: location.search,
                    })}
                    data-testid={`status-po-action-${r.po_number}`}
                  >
                    <ExternalLink size={13} />
                  </Link>
                ) : "-"}
              </td>
            </tr>
          );
        })}
        {!visible.length && (
          <tr>
            <td colSpan={colCount} className="empty-state">Tidak ada PO.</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
