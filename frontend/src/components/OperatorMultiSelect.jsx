import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import {
  allOperatorIds,
  filterOperatorSummary,
  nextSelectAll,
  operatorKey,
  searchOperators,
  selectAllState,
  toggleOperatorId,
} from "../lib/operatorFilter";

export default function OperatorMultiSelect({
  operators = [],
  selectedIds = [],
  onChange,
  testid = "gaji-filter-operator",
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState(selectedIds);
  const rootRef = useRef(null);
  const allRef = useRef(null);

  const allIds = useMemo(() => allOperatorIds(operators), [operators]);
  const visible = useMemo(() => searchOperators(operators, query), [operators, query]);
  const state = selectAllState(draft, allIds);
  const summary = filterOperatorSummary(draft, operators);

  useEffect(() => {
    if (open) setDraft(selectedIds);
  }, [open, selectedIds]);

  useEffect(() => {
    if (allRef.current) allRef.current.indeterminate = state === "partial";
  }, [state, open]);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const apply = (next) => {
    setDraft(next);
    onChange?.(next);
  };

  return (
    <div className="ship-filter-field gaji-op-ms" ref={rootRef} data-testid={testid}>
      <span>Operator</span>
      <button
        type="button"
        className="gaji-op-ms-trigger"
        data-testid="gaji-filter-operator-trigger"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span data-testid="gaji-filter-operator-summary">{summary}</span>
        <ChevronDown size={14} />
      </button>
      {open && (
        <div className="gaji-op-ms-panel" data-testid="gaji-filter-operator-panel" role="dialog">
          <input
            type="search"
            placeholder="Cari operator..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            data-testid="gaji-filter-operator-search"
            className="gaji-op-ms-search"
          />
          <label className="gaji-op-check gaji-op-check-all">
            <input
              ref={allRef}
              type="checkbox"
              checked={state === "all"}
              onChange={() => apply(nextSelectAll(draft, allIds))}
              data-testid="gaji-filter-operator-all"
            />
            Pilih Semua Operator
          </label>
          <div className="gaji-op-ms-list">
            {visible.map((o) => {
              const id = operatorKey(o);
              return (
                <label key={id} className="gaji-op-check">
                  <input
                    type="checkbox"
                    checked={draft.includes(id)}
                    onChange={() => apply(toggleOperatorId(draft, id))}
                    data-testid={`gaji-filter-operator-${id}`}
                  />
                  {o.name || o.operator_name || o.email || id}
                </label>
              );
            })}
            {!visible.length && <p className="muted small">Tidak ada operator.</p>}
          </div>
          <div className="gaji-op-ms-actions">
            <button
              type="button"
              className="ghost sm"
              onClick={() => apply([])}
              data-testid="gaji-filter-operator-clear"
            >
              Bersihkan
            </button>
            <button
              type="button"
              className="primary sm"
              onClick={() => { onChange?.(draft); setOpen(false); }}
              data-testid="gaji-filter-operator-apply"
            >
              Terapkan
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
