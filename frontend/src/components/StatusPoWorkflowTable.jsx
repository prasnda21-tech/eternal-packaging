import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { MoreHorizontal } from "lucide-react";
import { formatPoAgeLabel, formatStatusPoDate, itemLineSummary } from "../lib/statusPo";
import { capturePoOpenFromList, poNavStateFromList } from "../lib/poListReturn";
import {
  selectAllVisible,
  toggleId,
} from "../lib/rowSelection";
import {
  STATUS_PO_DEFAULT_WIDTHS,
  STATUS_PO_TABLE_KEYS,
  STATUS_PO_WORKFLOW_STAGES,
  clampColWidth,
  progressSegmentTone,
  readStoredColWidths,
  statusStageDisplayNumber,
  statusStripTone,
  stickyOffsets,
  workflowCellState,
  workflowCellTitle,
  workflowProgressPercent,
  writeStoredColWidths,
} from "../lib/statusPoWorkflow";

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

function RowActionMenu({ poNumber, poLink }) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef(null);
  const menuRef = useRef(null);
  const [pos, setPos] = useState({ top: 0, left: 0 });

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (event) => {
      if (btnRef.current?.contains(event.target) || menuRef.current?.contains(event.target)) return;
      setOpen(false);
    };
    const onKey = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!poLink) return "-";
  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="spo-action-link"
        aria-label={`Aksi ${poNumber}`}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Aksi"
        data-testid={`status-po-action-${poNumber}`}
        onClick={() => {
          const rect = btnRef.current?.getBoundingClientRect();
          if (rect) {
            const width = 148;
            const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
            setPos({ top: rect.bottom + 4, left });
          }
          setOpen((v) => !v);
        }}
      >
        <MoreHorizontal size={15} aria-hidden="true" />
      </button>
      {open && createPortal(
        <div
          ref={menuRef}
          role="menu"
          className="spo-action-menu"
          style={{ top: pos.top, left: pos.left }}
        >
          <Link
            role="menuitem"
            to={poLink.to}
            state={poLink.state}
            onClick={() => {
              setOpen(false);
              poLink.onClick();
            }}
          >
            Buka PO
          </Link>
        </div>,
        document.body,
      )}
    </>
  );
}

function openPoHandlers(r, user, location) {
  return {
    to: `/po/${r.id}`,
    state: poNavStateFromList({ pathname: location.pathname, search: location.search }),
    onClick: () => capturePoOpenFromList({
      user,
      orderId: r.id,
      pathname: location.pathname,
      search: location.search,
    }),
  };
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
  density = "rapat",
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
      "po", "date", "customer", "items", "status", "age", "progress", "pct", "action",
    ];
    return keys.reduce((sum, k) => sum + (widths[k] || STATUS_PO_DEFAULT_WIDTHS[k] || 60), 0);
  }, [widths, canSelect]);

  const colCount = (canSelect ? 1 : 0) + (STATUS_PO_TABLE_KEYS.length - 1);

  const stickyStyle = (key, extra = {}) => {
    const left = offsets[key];
    if (left == null) return extra;
    return {
      left: `${left}px`,
      ...extra,
    };
  };

  return (
    <table
      className={`spo-table ship-table spo-density-${density}`}
      style={{ width: "100%", minWidth: tableMin }}
      data-testid="status-po-matrix"
    >
      <colgroup>
        {canSelect && <col style={{ width: widths.check }} />}
        <col style={{ width: widths.po }} />
        <col style={{ width: widths.date }} />
        <col style={{ width: widths.customer }} />
        <col style={{ width: widths.items }} />
        <col style={{ width: widths.status }} />
        <col style={{ width: widths.age }} />
        <col style={{ width: widths.progress }} />
        <col style={{ width: widths.pct }} />
        <col style={{ width: widths.action }} />
      </colgroup>
      <thead>
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
            Tanggal PO
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
          <th className="spo-th-id" data-testid="status-po-th-items">
            Item / Produk
            <ResizeHandle colKey="items" onStart={onResizeStart} />
          </th>
          <th className="spo-th-id" data-testid="status-po-th-status">
            Status Saat Ini
            <ResizeHandle colKey="status" onStart={onResizeStart} />
          </th>
          <th className="spo-th-id" data-testid="status-po-th-age">
            Umur
            <ResizeHandle colKey="age" onStart={onResizeStart} />
          </th>
          <th className="spo-th-id" data-testid="status-po-th-progress">
            Progress (15 Tahap)
            <ResizeHandle colKey="progress" onStart={onResizeStart} />
          </th>
          <th className="spo-th-id spo-th-pct" data-testid="status-po-th-pct">
            %
            <ResizeHandle colKey="pct" onStart={onResizeStart} />
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
          const stripTone = statusStripTone(r);
          const stageNo = statusStageDisplayNumber(r);
          const items = itemLineSummary(r.item_lines);
          const poLink = r.id ? openPoHandlers(r, user, location) : null;
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
                {poLink ? (
                  <Link
                    to={poLink.to}
                    state={poLink.state}
                    onClick={poLink.onClick}
                    className="spo-po-link"
                    data-testid={`status-po-link-${r.po_number}`}
                  >
                    {r.po_number}
                  </Link>
                ) : (r.po_number || "-")}
              </td>
              <td className={`mono spo-sticky${narrow ? " spo-unstick-narrow" : ""}`} style={stickyStyle("date")}>
                {formatStatusPoDate(r.order_date)}
              </td>
              <td
                className={`ship-cust spo-sticky${narrow && offsets.customer != null ? " spo-sticky-last" : ""}`}
                style={stickyStyle("customer")}
                title={r.customer || undefined}
              >
                {r.customer || "-"}
              </td>
              <td
                className="spo-items"
                title={items.title}
                data-testid={`status-po-items-${r.po_number}`}
              >
                <span className="spo-item-line">{items.text}</span>
              </td>
              <td data-testid={`status-po-status-${r.po_number}`}>
                <span className={`spo-status-strip is-${stripTone}`} title={r.status || undefined}>
                  <span className="spo-status-num" aria-hidden="true">{stageNo}</span>
                  <span className="spo-status-label">{r.status || "-"}</span>
                </span>
              </td>
              <td className="mono spo-age" data-testid={`status-po-age-${r.po_number}`}>
                {formatPoAgeLabel(r.order_date)}
              </td>
              <td data-testid={`status-po-delivered-${r.po_number}`}>
                <div
                  className="spo-progress-segs"
                  data-testid={`status-po-progress-${r.po_number}`}
                  aria-label={`Progress ${pct}%`}
                >
                  {STATUS_PO_WORKFLOW_STAGES.map((s) => {
                    const state = workflowCellState(r, s.key);
                    const tone = progressSegmentTone(state);
                    return (
                      <span
                        key={s.key}
                        className={`spo-seg is-${tone}`}
                        title={`${s.full}: ${workflowCellTitle(state)}`}
                        data-testid={`status-po-wf-${r.po_number}-${s.key}`}
                        data-state={state}
                      />
                    );
                  })}
                </div>
              </td>
              <td className="spo-pct" data-testid={`status-po-pct-${r.po_number}`}>
                <span className="spo-progress-pct">{pct}%</span>
              </td>
              <td className="spo-action-cell">
                <RowActionMenu poNumber={r.po_number} poLink={poLink} />
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
