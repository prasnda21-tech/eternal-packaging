import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Filter, GripVertical, RotateCcw } from "lucide-react";
import {
  clientFilterRows,
  clientSortRows,
  mergePaintLayout,
  moveReportColumn,
  readPaintLayout,
  writePaintLayout,
} from "./paintTableLayout";
import "./PaintAdvancedTable.css";

const MIN_W = {
  text: 90,
  number: 72,
  date: 120,
  enum: 88,
  default: 80,
};

function minWidthFor(col) {
  return col.minWidth || MIN_W[col.type] || MIN_W.default;
}

function capWidth(col, w) {
  const min = minWidthFor(col);
  const max = col.maxWidth || 480;
  return Math.max(min, Math.min(max, Number(w) || min));
}

/**
 * Compact advanced table for Sentralisasi Cat.
 * Reuses Report Workspace normalize/move helpers for layout persistence.
 * mode="server": sort/filter emitted to parent (backend).
 * mode="client": sort/filter applied locally (Ringkasan recent).
 */
export default function PaintAdvancedTable({
  tableId,
  userId = "anon",
  columns = [],
  rows = [],
  rowKey,
  mode = "server",
  sortBy = "",
  sortDir = "desc",
  filters = {},
  onSortChange,
  onFiltersChange,
  emptyLabel = "Tidak ada data",
  testId,
}) {
  const defs = useMemo(() => columns.filter((c) => c && c.key), [columns]);
  const [layout, setLayout] = useState(() => mergePaintLayout(defs, readPaintLayout(userId, tableId)));
  const [filterOpen, setFilterOpen] = useState(null);
  const [draftFilter, setDraftFilter] = useState({ op: "contains", value: "" });
  const [dragging, setDragging] = useState(null);
  const [dragArmed, setDragArmed] = useState(false);
  const resizeRef = useRef(null);
  const filterWrapRef = useRef(null);

  useEffect(() => {
    setLayout(mergePaintLayout(defs, readPaintLayout(userId, tableId)));
  }, [userId, tableId, defs]);

  useEffect(() => {
    writePaintLayout(userId, tableId, layout);
  }, [userId, tableId, layout]);

  useEffect(() => {
    function move(e) {
      const cur = resizeRef.current;
      if (!cur) return;
      const col = defs.find((c) => c.key === cur.key);
      if (!col) return;
      setLayout((old) => ({
        ...old,
        widths: { ...old.widths, [cur.key]: capWidth(col, cur.width + e.clientX - cur.x) },
      }));
    }
    function end() {
      resizeRef.current = null;
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
    };
  }, [defs]);

  useEffect(() => {
    if (!filterOpen) return undefined;
    function onDoc(e) {
      if (!filterWrapRef.current?.contains(e.target)) setFilterOpen(null);
    }
    function onKey(e) {
      if (e.key === "Escape") setFilterOpen(null);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [filterOpen]);

  const visibleKeys = layout.order.filter((k) => !layout.hidden.includes(k) && defs.some((c) => c.key === k));
  const byKey = useMemo(() => new Map(defs.map((c) => [c.key, c])), [defs]);

  const displayed = useMemo(() => {
    if (mode !== "client") return rows;
    const filtered = clientFilterRows(rows, filters);
    return clientSortRows(filtered, sortBy, sortDir, "transaction_id");
  }, [mode, rows, filters, sortBy, sortDir]);

  const resetLayout = () => {
    const next = mergePaintLayout(defs, {});
    setLayout(next);
    writePaintLayout(userId, tableId, next);
  };

  const toggleSort = (key) => {
    if (dragging || dragArmed) return;
    const nextDir = sortBy === key && sortDir === "asc" ? "desc" : "asc";
    onSortChange?.(key, sortBy === key ? nextDir : "asc");
  };

  const openFilter = (key, e) => {
    e.stopPropagation();
    const col = byKey.get(key);
    const existing = filters[key] || {};
    const defaultOp = col?.type === "number" ? "gte" : col?.type === "enum" ? "eq" : col?.type === "date" ? "date_gte" : "contains";
    setDraftFilter({ op: existing.op || defaultOp, value: existing.value || "" });
    setFilterOpen(key);
  };

  const applyFilter = () => {
    if (!filterOpen) return;
    const next = { ...filters };
    if (!String(draftFilter.value || "").trim()) delete next[filterOpen];
    else next[filterOpen] = { op: draftFilter.op, value: draftFilter.value };
    onFiltersChange?.(next);
    setFilterOpen(null);
  };

  const clearFilter = () => {
    if (!filterOpen) return;
    const next = { ...filters };
    delete next[filterOpen];
    onFiltersChange?.(next);
    setDraftFilter({ op: "contains", value: "" });
    setFilterOpen(null);
  };

  const onDropReorder = useCallback((toKey) => {
    if (!dragging || dragging === toKey) {
      setDragging(null);
      setDragArmed(false);
      return;
    }
    setLayout((old) => ({ ...old, order: moveReportColumn(old.order, dragging, toKey) }));
    setDragging(null);
    setDragArmed(false);
  }, [dragging]);

  const moveKeyboard = (key, dir) => {
    const idx = layout.order.indexOf(key);
    if (idx < 0) return;
    const target = layout.order[idx + dir];
    if (!target) return;
    setLayout((old) => ({ ...old, order: moveReportColumn(old.order, key, target) }));
  };

  return (
    <div className="paint-adv" data-testid={testId || `paint-adv-${tableId}`}>
      <div className="paint-adv-tools">
        <button type="button" className="paint-adv-reset" onClick={resetLayout} data-testid={`paint-reset-cols-${tableId}`}>
          <RotateCcw size={12} aria-hidden /> Reset Kolom
        </button>
      </div>
      <div className="paint-adv-scroll" role="region" aria-label="Tabel data" tabIndex={0}>
        <table className="paint-adv-table">
          <colgroup>
            {visibleKeys.map((k) => (
              <col key={k} style={{ width: layout.widths[k] || minWidthFor(byKey.get(k) || {}) }} />
            ))}
          </colgroup>
          <thead>
            <tr>
              {visibleKeys.map((k) => {
                const col = byKey.get(k);
                if (!col) return null;
                const activeFilter = Boolean(filters[k]?.value);
                const sorted = sortBy === k;
                return (
                  <th
                    key={k}
                    scope="col"
                    className={`${col.align === "right" ? "num" : ""}${dragging === k ? " is-dragging" : ""}`}
                    style={{ position: "relative" }}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => {
                      e.preventDefault();
                      onDropReorder(k);
                    }}
                  >
                    <div className="paint-adv-th">
                      <button
                        type="button"
                        className="paint-adv-grip"
                        draggable
                        aria-label={`Geser kolom ${col.label}`}
                        title="Tarik untuk urutkan kolom"
                        onDragStart={() => {
                          setDragging(k);
                          setDragArmed(true);
                        }}
                        onDragEnd={() => {
                          setDragging(null);
                          setTimeout(() => setDragArmed(false), 0);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "ArrowLeft") {
                            e.preventDefault();
                            moveKeyboard(k, -1);
                          }
                          if (e.key === "ArrowRight") {
                            e.preventDefault();
                            moveKeyboard(k, 1);
                          }
                        }}
                      >
                        <GripVertical size={12} aria-hidden />
                      </button>
                      {col.sortable === false ? (
                        <span className="paint-adv-label">{col.label}</span>
                      ) : (
                        <button
                          type="button"
                          className={`paint-adv-sort${sorted ? " is-active" : ""}`}
                          onClick={() => toggleSort(k)}
                          aria-label={`Urutkan ${col.label}`}
                          data-testid={`paint-sort-${tableId}-${k}`}
                        >
                          <span className="paint-adv-label">{col.label}</span>
                          {sorted ? (sortDir === "asc" ? <ArrowUp size={11} /> : <ArrowDown size={11} />) : null}
                        </button>
                      )}
                      {col.filterable !== false && (
                        <button
                          type="button"
                          className={`paint-adv-filter-btn${activeFilter ? " is-active" : ""}`}
                          onClick={(e) => openFilter(k, e)}
                          aria-label={`Filter ${col.label}`}
                          data-testid={`paint-filter-${tableId}-${k}`}
                        >
                          <Filter size={11} aria-hidden />
                        </button>
                      )}
                    </div>
                    <span
                      className="paint-adv-resizer"
                      role="separator"
                      aria-label={`Ubah lebar ${col.label}`}
                      onPointerDown={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        resizeRef.current = { key: k, x: e.clientX, width: layout.widths[k] || minWidthFor(col) };
                      }}
                    />
                    {filterOpen === k && (
                      <div className="paint-adv-filter-pop" ref={filterWrapRef} role="dialog" aria-label={`Filter ${col.label}`}>
                        {col.type === "enum" && Array.isArray(col.filterOptions) ? (
                          <label>
                            Nilai
                            <select
                              value={draftFilter.value}
                              onChange={(e) => setDraftFilter((d) => ({ ...d, op: "eq", value: e.target.value }))}
                            >
                              <option value="">Semua</option>
                              {col.filterOptions.map((o) => (
                                <option key={o.value} value={o.value}>{o.label}</option>
                              ))}
                            </select>
                          </label>
                        ) : col.type === "number" ? (
                          <>
                            <label>
                              Operator
                              <select value={draftFilter.op} onChange={(e) => setDraftFilter((d) => ({ ...d, op: e.target.value }))}>
                                <option value="gte">≥</option>
                                <option value="lte">≤</option>
                                <option value="eq_num">=</option>
                                <option value="gt">&gt;</option>
                                <option value="lt">&lt;</option>
                              </select>
                            </label>
                            <label>
                              Nilai
                              <input
                                type="number"
                                value={draftFilter.value}
                                onChange={(e) => setDraftFilter((d) => ({ ...d, value: e.target.value }))}
                              />
                            </label>
                          </>
                        ) : col.type === "date" ? (
                          <>
                            <label>
                              Operator
                              <select value={draftFilter.op} onChange={(e) => setDraftFilter((d) => ({ ...d, op: e.target.value }))}>
                                <option value="date_gte">Dari</option>
                                <option value="date_lte">Sampai</option>
                              </select>
                            </label>
                            <label>
                              Tanggal (yyyy-mm-dd)
                              <input
                                value={draftFilter.value}
                                onChange={(e) => setDraftFilter((d) => ({ ...d, value: e.target.value }))}
                                placeholder="yyyy-mm-dd"
                              />
                            </label>
                          </>
                        ) : (
                          <label>
                            Mengandung
                            <input
                              value={draftFilter.value}
                              onChange={(e) => setDraftFilter((d) => ({ ...d, op: "contains", value: e.target.value }))}
                              placeholder="Teks…"
                              autoFocus
                            />
                          </label>
                        )}
                        <div className="paint-adv-filter-actions">
                          <button type="button" className="ghost sm" onClick={clearFilter}>Clear</button>
                          <button type="button" className="paint-btn-primary sm" onClick={applyFilter}>Apply</button>
                        </div>
                      </div>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {!displayed.length && (
              <tr><td colSpan={visibleKeys.length || 1} className="muted">{emptyLabel}</td></tr>
            )}
            {displayed.map((row, idx) => {
              const key = typeof rowKey === "function" ? rowKey(row, idx) : row?.[rowKey] || idx;
              return (
                <tr key={key}>
                  {visibleKeys.map((k) => {
                    const col = byKey.get(k);
                    const raw = row?.[k];
                    const content = col?.render ? col.render(raw, row) : (raw == null || raw === "" ? "—" : String(raw));
                    return (
                      <td key={k} className={col?.align === "right" ? "num" : undefined}>
                        {content}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
