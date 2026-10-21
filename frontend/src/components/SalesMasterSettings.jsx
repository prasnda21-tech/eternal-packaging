import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Building2,
  Filter,
  GripVertical,
  Link2,
  Plus,
  RefreshCw,
  Save,
  Search,
  User,
  X,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  SALES_MASTER_COL_LABELS,
  SALES_MASTER_DEFAULT_COL_FILTERS,
  SALES_MASTER_DEFAULT_COL_ORDER,
  SALES_MASTER_DEFAULT_COL_WIDTHS,
  SALES_MASTER_UNASSIGNED_BRANCH,
  SALES_MASTER_UNLINKED_USER,
  clearSalesMasterColPrefs,
  clampSalesMasterColWidth,
  countActiveSalesMasterFilters,
  filterSalesMasterRows,
  isSalesMasterColFilterActive,
  moveSalesMasterColumn,
  readSalesMasterColPrefs,
  salesMasterTableMinWidth,
  shiftSalesMasterColumn,
  writeSalesMasterColPrefs,
} from "../lib/salesMasterColumns";
import "./SalesMasterSettings.css";

const EMPTY_DRAFT = {
  id: null,
  sales_name: "",
  user_id: "",
  branch_ids: [],
  active: true,
  sort_order: 0,
  doc_version: 1,
};

function errText(e) {
  const d = e?.response?.data?.detail;
  if (typeof d === "string") return d;
  return d?.message || e?.message || "Gagal";
}

function SmColResizeHandle({ colKey, onPointerDown }) {
  return (
    <span
      className="sm-col-resize"
      role="separator"
      aria-orientation="vertical"
      aria-label={`Resize ${SALES_MASTER_COL_LABELS[colKey] || colKey}`}
      data-testid={`sm-resize-${colKey}`}
      onPointerDown={(event) => onPointerDown(event, colKey)}
    />
  );
}

function ColumnFilterPopover({
  colKey,
  anchorRect,
  filters,
  branches,
  users,
  onChange,
  onClose,
  onApply,
}) {
  const panelRef = useRef(null);
  useEffect(() => {
    const onDoc = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) onClose();
    };
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  if (!anchorRect) return null;
  const top = Math.min(anchorRect.bottom + 4, window.innerHeight - 12);
  const left = Math.max(8, Math.min(anchorRect.left, window.innerWidth - 268));

  const toggleBranch = (id) => {
    const set = new Set(filters.branches || []);
    if (set.has(id)) set.delete(id);
    else set.add(id);
    onChange({ ...filters, branches: Array.from(set) });
  };

  return createPortal(
    <div
      ref={panelRef}
      className="sm-filter-popover"
      style={{ top, left }}
      role="dialog"
      aria-label={`Filter ${SALES_MASTER_COL_LABELS[colKey]}`}
      data-testid={`sm-filter-pop-${colKey}`}
    >
      {colKey === "name" && (
        <label className="sm-filter-field">
          <span>Cari nama</span>
          <input
            autoFocus
            value={filters.name}
            onChange={(e) => onChange({ ...filters, name: e.target.value })}
            data-testid="sm-filter-value-name"
            placeholder="Teks nama sales…"
          />
        </label>
      )}
      {colKey === "branches" && (
        <div className="sm-filter-field">
          <span>Cabang</span>
          <div className="sm-filter-checklist" data-testid="sm-filter-value-branches">
            <label>
              <input
                type="checkbox"
                checked={(filters.branches || []).includes(SALES_MASTER_UNASSIGNED_BRANCH)}
                onChange={() => toggleBranch(SALES_MASTER_UNASSIGNED_BRANCH)}
              />
              <span>Semua / belum ditugaskan</span>
            </label>
            {branches.filter((b) => b.active !== false).map((b) => (
              <label key={b.id}>
                <input
                  type="checkbox"
                  checked={(filters.branches || []).includes(b.id)}
                  onChange={() => toggleBranch(b.id)}
                />
                <span>{b.name || b.code || b.id}</span>
              </label>
            ))}
          </div>
        </div>
      )}
      {colKey === "user" && (
        <label className="sm-filter-field">
          <span>Akun Eternal</span>
          <select
            value={filters.user}
            onChange={(e) => onChange({ ...filters, user: e.target.value })}
            data-testid="sm-filter-value-user"
          >
            <option value="">Semua</option>
            <option value={SALES_MASTER_UNLINKED_USER}>Belum dihubungkan</option>
            {(Array.isArray(users) ? users : []).filter((u) => u.status !== "inactive").map((u) => (
              <option key={u.id} value={u.id}>{u.name || u.email}</option>
            ))}
          </select>
        </label>
      )}
      {colKey === "status" && (
        <label className="sm-filter-field">
          <span>Status</span>
          <select
            value={filters.status}
            onChange={(e) => onChange({ ...filters, status: e.target.value })}
            data-testid="sm-filter-value-status"
          >
            <option value="">Semua</option>
            <option value="active">Aktif</option>
            <option value="inactive">Nonaktif</option>
          </select>
        </label>
      )}
      {colKey === "sort_order" && (
        <div className="sm-filter-range">
          <label className="sm-filter-field">
            <span>Min</span>
            <input
              type="number"
              value={filters.sort_min}
              onChange={(e) => onChange({ ...filters, sort_min: e.target.value })}
              data-testid="sm-filter-value-sort-min"
            />
          </label>
          <label className="sm-filter-field">
            <span>Max</span>
            <input
              type="number"
              value={filters.sort_max}
              onChange={(e) => onChange({ ...filters, sort_max: e.target.value })}
              data-testid="sm-filter-value-sort-max"
            />
          </label>
        </div>
      )}
      <div className="sm-filter-pop-actions">
        <button
          type="button"
          className="ghost sm"
          data-testid={`sm-filter-clear-${colKey}`}
          onClick={() => {
            const next = { ...filters };
            if (colKey === "name") next.name = "";
            if (colKey === "branches") next.branches = [];
            if (colKey === "user") next.user = "";
            if (colKey === "status") next.status = "";
            if (colKey === "sort_order") {
              next.sort_min = "";
              next.sort_max = "";
            }
            onChange(next);
            onApply(next);
          }}
        >
          Hapus
        </button>
        <button
          type="button"
          className="primary sm"
          data-testid={`sm-filter-apply-${colKey}`}
          onClick={() => onApply(filters)}
        >
          Terapkan
        </button>
      </div>
    </div>,
    document.body,
  );
}

export default function SalesMasterSettings({ notify, canEdit }) {
  const formId = useId();
  const { user } = useAuth() || {};
  const prefsUserId = user?.id || "";
  const initialPrefs = useMemo(() => readSalesMasterColPrefs(undefined, prefsUserId), [prefsUserId]);
  const [items, setItems] = useState([]);
  const [users, setUsers] = useState([]);
  const [branches, setBranches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [fieldError, setFieldError] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState(null);
  const [query, setQuery] = useState("");
  const [colOrder, setColOrder] = useState(initialPrefs.order);
  const [colWidths, setColWidths] = useState(initialPrefs.widths);
  const [colFilters, setColFilters] = useState(initialPrefs.filters);
  const [draftFilters, setDraftFilters] = useState(initialPrefs.filters);
  const [openFilterKey, setOpenFilterKey] = useState("");
  const [filterAnchor, setFilterAnchor] = useState(null);
  const [draggingKey, setDraggingKey] = useState("");
  const [dropTargetKey, setDropTargetKey] = useState("");
  const colResizeRef = useRef(null);
  const filterBtnRefs = useRef({});
  const prefsHydratedFor = useRef("");

  // Re-hydrate prefs when session user id becomes available.
  useEffect(() => {
    if (!prefsUserId || prefsHydratedFor.current === prefsUserId) return;
    const prefs = readSalesMasterColPrefs(undefined, prefsUserId);
    setColOrder(prefs.order);
    setColWidths(prefs.widths);
    setColFilters(prefs.filters);
    setDraftFilters(prefs.filters);
    prefsHydratedFor.current = prefsUserId;
  }, [prefsUserId]);

  const persistPrefs = useCallback((order, widths, filters) => {
    writeSalesMasterColPrefs({ order, widths, filters }, undefined, prefsUserId);
  }, [prefsUserId]);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const [salesRes, usersRes, branchRes] = await Promise.all([
        api.salesMasterList(),
        api.adminListUsers().catch(() => ({ users: [] })),
        api.branches().catch(() => ({ items: [] })),
      ]);
      setItems(salesRes.items || []);
      setUsers(usersRes.users || usersRes || []);
      const branchRows = branchRes.items || branchRes.branches || branchRes || [];
      setBranches(Array.isArray(branchRows) ? branchRows : []);
    } catch (e) {
      setErr(errText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => () => {
    const active = colResizeRef.current;
    if (!active) return;
    active.handleEl.removeEventListener("pointermove", active.onMove);
    active.handleEl.removeEventListener("pointerup", active.onUp);
    active.handleEl.removeEventListener("pointercancel", active.onUp);
    document.body.classList.remove("sm-col-resizing");
  }, []);

  useLayoutEffect(() => {
    if (!openFilterKey) {
      setFilterAnchor(null);
      return undefined;
    }
    const btn = filterBtnRefs.current[openFilterKey];
    if (!btn) return undefined;
    const update = () => setFilterAnchor(btn.getBoundingClientRect());
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [openFilterKey]);

  const patchDraft = (key, value) => {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
    if (key === "sales_name" && fieldError) setFieldError("");
  };

  const toggleBranch = (branchId) => {
    setDraft((prev) => {
      if (!prev) return prev;
      const set = new Set(prev.branch_ids || []);
      if (set.has(branchId)) set.delete(branchId);
      else set.add(branchId);
      return { ...prev, branch_ids: Array.from(set) };
    });
  };

  const openCreate = () => {
    setFieldError("");
    setErr("");
    setDraft({ ...EMPTY_DRAFT, sort_order: (items.length + 1) * 10 });
  };

  const openEdit = (row) => {
    setFieldError("");
    setErr("");
    setDraft({
      id: row.id,
      sales_name: row.sales_name || "",
      user_id: row.user_id || "",
      branch_ids: Array.isArray(row.branch_ids) ? [...row.branch_ids] : [],
      active: Boolean(row.active),
      sort_order: Number(row.sort_order) || 0,
      doc_version: Number(row.doc_version) || 1,
    });
  };

  const closeDraft = () => {
    setDraft(null);
    setFieldError("");
  };

  const save = async () => {
    if (!draft || !canEdit) return;
    setBusy(true);
    setErr("");
    setFieldError("");
    try {
      const payload = {
        sales_name: String(draft.sales_name || "").trim(),
        user_id: draft.user_id || null,
        branch_ids: draft.branch_ids || [],
        active: Boolean(draft.active),
        sort_order: Number(draft.sort_order) || 0,
      };
      if (!payload.sales_name) {
        setFieldError("Nama Sales wajib diisi");
        return;
      }
      if (draft.id) {
        payload.doc_version = draft.doc_version;
        await api.salesMasterUpdate(draft.id, payload);
        notify?.("Master Sales diperbarui");
      } else {
        await api.salesMasterCreate(payload);
        notify?.("Master Sales ditambahkan");
      }
      setDraft(null);
      await load();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async (row) => {
    if (!canEdit) return;
    setBusy(true);
    setErr("");
    try {
      await api.salesMasterUpdate(row.id, {
        active: !row.active,
        doc_version: row.doc_version,
        sales_name: row.sales_name,
      });
      notify?.(row.active ? "Sales dinonaktifkan" : "Sales diaktifkan");
      await load();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const branchLabel = (ids) => {
    if (!ids?.length) return "Semua / belum ditugaskan";
    const map = Object.fromEntries(branches.map((b) => [b.id, b.name || b.code || b.id]));
    return ids.map((id) => map[id] || id).join(", ");
  };

  const userLabel = (uid) => {
    if (!uid) return "—";
    const u = users.find((x) => x.id === uid);
    return u ? `${u.name || u.email}` : uid;
  };

  const filtered = useMemo(
    () => filterSalesMasterRows(items, { query, filters: colFilters }),
    [items, query, colFilters],
  );
  const tableMinWidth = useMemo(
    () => salesMasterTableMinWidth(colWidths, colOrder),
    [colWidths, colOrder],
  );
  const activeFilterCount = countActiveSalesMasterFilters(colFilters);

  const onColResizePointerDown = useCallback((event, key) => {
    if (event.button != null && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const handleEl = event.currentTarget;
    const pointerId = event.pointerId;
    const startX = event.clientX;
    const startW = colWidths[key] ?? SALES_MASTER_DEFAULT_COL_WIDTHS[key];
    try {
      handleEl.setPointerCapture(pointerId);
    } catch {
      /* ignore */
    }
    document.body.classList.add("sm-col-resizing");
    handleEl.classList.add("is-active");

    const onMove = (moveEvent) => {
      if (moveEvent.pointerId !== pointerId) return;
      const nextW = clampSalesMasterColWidth(key, startW + (moveEvent.clientX - startX));
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
      document.body.classList.remove("sm-col-resizing");
      setColWidths((prev) => {
        persistPrefs(colOrder, prev, colFilters);
        return prev;
      });
      if (colResizeRef.current?.pointerId === pointerId) colResizeRef.current = null;
    };

    colResizeRef.current = { pointerId, handleEl, onMove, onUp };
    handleEl.addEventListener("pointermove", onMove);
    handleEl.addEventListener("pointerup", onUp);
    handleEl.addEventListener("pointercancel", onUp);
  }, [colFilters, colOrder, colWidths, persistPrefs]);

  const applyOrder = useCallback((nextOrder) => {
    setColOrder(nextOrder);
    persistPrefs(nextOrder, colWidths, colFilters);
  }, [colFilters, colWidths, persistPrefs]);

  const applyFilters = useCallback((nextFilters) => {
    setColFilters(nextFilters);
    setDraftFilters(nextFilters);
    persistPrefs(colOrder, colWidths, nextFilters);
    setOpenFilterKey("");
  }, [colOrder, colWidths, persistPrefs]);

  const resetFilters = () => {
    const empty = { ...SALES_MASTER_DEFAULT_COL_FILTERS, branches: [] };
    applyFilters(empty);
  };

  const resetLayout = () => {
    clearSalesMasterColPrefs(undefined, prefsUserId);
    setColOrder([...SALES_MASTER_DEFAULT_COL_ORDER]);
    setColWidths({ ...SALES_MASTER_DEFAULT_COL_WIDTHS });
    const empty = { ...SALES_MASTER_DEFAULT_COL_FILTERS, branches: [] };
    setColFilters(empty);
    setDraftFilters(empty);
    setOpenFilterKey("");
  };

  const onDragStart = (event, key) => {
    if (event.target?.closest?.(".sm-col-resize, .sm-th-filter")) {
      event.preventDefault();
      return;
    }
    setDraggingKey(key);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/sm-col", key);
  };
  const onDragOver = (event, key) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    if (dropTargetKey !== key) setDropTargetKey(key);
  };
  const onDrop = (event, key) => {
    event.preventDefault();
    const from = event.dataTransfer.getData("text/sm-col") || draggingKey;
    if (from) applyOrder(moveSalesMasterColumn(colOrder, from, key));
    setDraggingKey("");
    setDropTargetKey("");
  };
  const onDragEnd = () => {
    setDraggingKey("");
    setDropTargetKey("");
  };

  const moveColumnKeyboard = (key, direction) => {
    applyOrder(shiftSalesMasterColumn(colOrder, key, direction));
  };

  const openFilter = (key, event) => {
    event.stopPropagation();
    if (openFilterKey === key) {
      setOpenFilterKey("");
      return;
    }
    setDraftFilters(colFilters);
    setOpenFilterKey(key);
  };

  const renderCell = (key, row) => {
    switch (key) {
      case "name": {
        const text = row.sales_name || "";
        return (
          <span className="sm-cell-clip" title={text} data-testid={`sm-cell-name-${row.id}`}>
            {text}
          </span>
        );
      }
      case "branches": {
        const text = branchLabel(row.branch_ids);
        return <span className="sm-cell-clip muted small" title={text}>{text}</span>;
      }
      case "user": {
        const text = userLabel(row.user_id);
        return <span className="sm-cell-clip muted small" title={text}>{text}</span>;
      }
      case "status":
        return (
          <span className={`pill ${row.active ? "pill-ok" : "pill-cancel"}`}>
            {row.active ? "Aktif" : "Nonaktif"}
          </span>
        );
      case "sort_order":
        return row.sort_order;
      case "actions":
        if (!canEdit) return null;
        return (
          <span className="sales-master-action-row">
            <button
              type="button"
              className="ghost sm"
              onClick={() => openEdit(row)}
              data-testid={`sales-master-edit-${row.id}`}
            >
              Edit
            </button>
            <button
              type="button"
              className={`ghost sm ${row.active ? "danger" : "ok"}`}
              onClick={() => toggleActive(row)}
              disabled={busy}
              data-testid={`sales-master-toggle-${row.id}`}
            >
              {row.active ? "Nonaktifkan" : "Aktifkan"}
            </button>
          </span>
        );
      default:
        return null;
    }
  };

  const drawer = draft ? (
    <div
      className="sales-master-drawer-root"
      data-testid="sales-master-drawer"
      onClick={(e) => { if (e.target === e.currentTarget) closeDraft(); }}
    >
      <div
        className="sales-master-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${formId}-title`}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="sales-master-drawer-head">
          <h3 id={`${formId}-title`}>{draft.id ? "Edit Master Sales" : "Tambah Master Sales"}</h3>
          <button type="button" className="icon-button" onClick={closeDraft} aria-label="Tutup" data-testid="sales-master-drawer-close">
            <X size={18} />
          </button>
        </header>

        <div className="sales-master-drawer-body">
          {err ? <p className="sales-master-banner-error" data-testid="sales-master-drawer-error">{err}</p> : null}

          <section className="sales-master-section">
            <h4 className="sales-master-section-title">
              <User size={16} aria-hidden="true" />
              Informasi Sales
            </h4>
            <label className={`sales-master-field${fieldError ? " is-invalid" : ""}`}>
              <span className="sales-master-field-label">
                Nama Sales (label Accurate) <span className="sales-master-req" aria-hidden="true">*</span>
              </span>
              <input
                id={`${formId}-name`}
                value={draft.sales_name}
                onChange={(e) => patchDraft("sales_name", e.target.value)}
                data-testid="sales-master-name"
                autoComplete="off"
                placeholder="Contoh: Ayub MS"
              />
              {fieldError ? (
                <p className="sales-master-field-error" data-testid="sales-master-name-error">{fieldError}</p>
              ) : (
                <p className="hint">Nama harus identik dengan label di Accurate.</p>
              )}
            </label>
            <label className="sales-master-field">
              <span className="sales-master-field-label">
                Urutan <span className="sales-master-req" aria-hidden="true">*</span>
              </span>
              <input
                type="number"
                value={draft.sort_order}
                onChange={(e) => patchDraft("sort_order", e.target.value)}
                data-testid="sales-master-sort"
              />
              <p className="hint">Angka lebih kecil akan tampil lebih atas.</p>
            </label>
          </section>

          <section className="sales-master-section">
            <h4 className="sales-master-section-title">
              <Link2 size={16} aria-hidden="true" />
              Akun Eternal (opsional)
            </h4>
            <label className="sales-master-field">
              <span className="sales-master-field-label">Akun Eternal</span>
              <select
                value={draft.user_id || ""}
                onChange={(e) => patchDraft("user_id", e.target.value)}
                data-testid="sales-master-user"
              >
                <option value="">— tidak dihubungkan —</option>
                {(Array.isArray(users) ? users : []).filter((u) => u.status !== "inactive").map((u) => (
                  <option key={u.id} value={u.id}>{u.name} · {u.email} · {u.role}</option>
                ))}
              </select>
              <p className="hint">Hubungkan ke akun user Eternal jika ada.</p>
            </label>
          </section>

          <section className="sales-master-section">
            <h4 className="sales-master-section-title">
              <Building2 size={16} aria-hidden="true" />
              Cabang (multi)
            </h4>
            <fieldset className="sales-master-branch-box">
              <legend className="small">Cabang penugasan</legend>
              {branches.length === 0 ? (
                <p className="muted small">Belum ada master cabang / tidak dapat dimuat.</p>
              ) : (
                branches.filter((b) => b.active !== false).map((b) => (
                  <label key={b.id}>
                    <input
                      type="checkbox"
                      checked={(draft.branch_ids || []).includes(b.id)}
                      onChange={() => toggleBranch(b.id)}
                    />
                    <span>{b.name || b.code || b.id}</span>
                  </label>
                ))
              )}
            </fieldset>
            <p className="hint" style={{ margin: 0 }}>Pilih satu atau lebih cabang penugasan. Kosong = semua / belum ditugaskan.</p>
          </section>

          <div className="sales-master-active-row">
            <label className="sales-master-switch">
              <input
                type="checkbox"
                role="switch"
                checked={Boolean(draft.active)}
                onChange={(e) => patchDraft("active", e.target.checked)}
                data-testid="sales-master-active"
                aria-label="Aktif"
              />
              <span className="sales-master-switch-ui" aria-hidden="true" />
            </label>
            <div className="sales-master-active-copy">
              <strong>Aktif</strong>
              <p className="hint">Sales yang nonaktif tidak dapat dipilih untuk transaksi baru.</p>
            </div>
          </div>
        </div>

        <footer className="sales-master-drawer-foot">
          <button type="button" className="ghost sm" onClick={closeDraft} disabled={busy}>Batal</button>
          <button type="button" className="primary sm" onClick={save} disabled={busy} data-testid="sales-master-save">
            <Save size={14} aria-hidden="true" />
            {busy ? "Menyimpan…" : "Simpan"}
          </button>
        </footer>
      </div>
    </div>
  ) : null;

  return (
    <div className="sales-master-panel" data-testid="sales-master-tab">
      <p className="muted small sales-master-intro">
        Katalog Sales operasional. Nama harus identik dengan label Accurate.
        Mapping Accurate → user Eternal tetap di tab terpisah.
      </p>
      <p className="muted small sales-master-intro" data-testid="sales-master-filter-scope-note">
        Filter dan pencarian berlaku pada data katalog yang sudah dimuat (hingga batas daftar server).
      </p>
      {err && !draft ? <p className="sales-master-banner-error" data-testid="sales-master-error">{err}</p> : null}

      <div className="sales-master-toolbar" data-testid="sales-master-toolbar">
        <div className="sales-master-toolbar-actions">
          {canEdit ? (
            <button type="button" className="primary sm" onClick={openCreate} data-testid="sales-master-add">
              <Plus size={14} aria-hidden="true" />
              Tambah Sales
            </button>
          ) : null}
          <button type="button" className="ghost sm" onClick={load} disabled={loading} data-testid="sales-master-reload">
            <RefreshCw size={14} className={loading ? "spin" : ""} aria-hidden="true" />
            Muat ulang
          </button>
          <button
            type="button"
            className="ghost sm"
            onClick={resetFilters}
            disabled={activeFilterCount === 0}
            data-testid="sales-master-reset-filters"
          >
            Reset Filter{activeFilterCount ? ` (${activeFilterCount})` : ""}
          </button>
          <button
            type="button"
            className="ghost sm"
            onClick={resetLayout}
            data-testid="sales-master-reset-layout"
          >
            Reset Tampilan
          </button>
        </div>
        <label className="sales-master-search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Cari nama sales…"
            data-testid="sales-master-search"
            autoComplete="off"
          />
        </label>
      </div>

      <div className="sales-master-table-wrap" data-testid="sales-master-table-scroll">
        {loading ? (
          <p className="muted" style={{ padding: 16, margin: 0 }} data-testid="sales-master-loading">Memuat…</p>
        ) : items.length === 0 ? (
          <div className="sales-master-empty" data-testid="sales-master-empty">
            <strong>Belum ada Master Sales</strong>
            <span>Tambahkan secara manual dengan tombol Tambah Sales.</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="sales-master-empty" data-testid="sales-master-search-empty">
            <strong>Tidak ada hasil</strong>
            <span>Tidak ada sales yang cocok dengan pencarian atau filter aktif.</span>
          </div>
        ) : (
          <table
            className="ship-table sales-master-table"
            data-testid="sales-master-table"
            style={{ width: "100%", minWidth: tableMinWidth, tableLayout: "fixed" }}
          >
            <colgroup>
              {colOrder.map((key) => (
                <col
                  key={key}
                  data-col={key}
                  style={{ width: colWidths[key] || SALES_MASTER_DEFAULT_COL_WIDTHS[key] }}
                />
              ))}
            </colgroup>
            <thead>
              <tr>
                {colOrder.map((key) => {
                  const label = SALES_MASTER_COL_LABELS[key] || key;
                  const filterable = key !== "actions";
                  const filterOn = filterable && isSalesMasterColFilterActive(key, colFilters);
                  return (
                    <th
                      key={key}
                      className={`sm-th${draggingKey === key ? " is-dragging" : ""}${dropTargetKey === key ? " is-drop-target" : ""}${filterOn ? " is-filtered" : ""}`}
                      data-testid={`sm-th-${key}`}
                      data-col={key}
                      draggable={key !== "actions"}
                      onDragStart={(e) => onDragStart(e, key)}
                      onDragOver={(e) => onDragOver(e, key)}
                      onDrop={(e) => onDrop(e, key)}
                      onDragEnd={onDragEnd}
                    >
                      <div className="sm-th-inner">
                        {key !== "actions" ? (
                          <button
                            type="button"
                            className="sm-th-drag"
                            title={`${label} — geser atau ←/→ untuk pindah kolom`}
                            aria-label={`Pindah kolom ${label}`}
                            data-testid={`sm-move-${key}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              moveColumnKeyboard(key, e.shiftKey ? 1 : -1);
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
                            onMouseDown={(e) => e.stopPropagation()}
                          >
                            <GripVertical size={12} aria-hidden="true" />
                          </button>
                        ) : null}
                        <span className="sm-th-label" title={label}>{label}</span>
                        {filterable ? (
                          <button
                            type="button"
                            className={`sm-th-filter${filterOn ? " is-active" : ""}`}
                            ref={(el) => { filterBtnRefs.current[key] = el; }}
                            title={`Filter ${label}`}
                            aria-label={`Filter ${label}`}
                            aria-expanded={openFilterKey === key}
                            data-testid={`sm-filter-${key}`}
                            onClick={(e) => openFilter(key, e)}
                            onPointerDown={(e) => e.stopPropagation()}
                            onMouseDown={(e) => e.stopPropagation()}
                          >
                            <Filter size={12} aria-hidden="true" />
                          </button>
                        ) : null}
                        <SmColResizeHandle colKey={key} onPointerDown={onColResizePointerDown} />
                      </div>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => (
                <tr key={row.id} data-testid={`sm-row-${row.id}`}>
                  {colOrder.map((key) => (
                    <td
                      key={key}
                      data-col={key}
                      className={key === "actions" ? "sales-master-actions" : undefined}
                    >
                      {renderCell(key, row)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {openFilterKey ? (
        <ColumnFilterPopover
          colKey={openFilterKey}
          anchorRect={filterAnchor}
          filters={draftFilters}
          branches={branches}
          users={users}
          onChange={setDraftFilters}
          onClose={() => setOpenFilterKey("")}
          onApply={applyFilters}
        />
      ) : null}

      {drawer && typeof document !== "undefined" ? createPortal(drawer, document.body) : null}
    </div>
  );
}
