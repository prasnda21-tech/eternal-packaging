import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Check, Download, Search, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import { useDebouncedLiveSearch, liveSearchTableView } from "../hooks/useDebouncedLiveSearch";
import { openingReady, preselectedWarehouseId, stockEmptyMessage, warehouseChoices, yogyakartaWarehouses } from "../lib/physicalStockView";

const INLINE_ADJUST_REASON = "Koreksi manual dari Stock Saat Ini";

function newKey() {
  return globalThis.crypto?.randomUUID?.() || `saldo-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function StockSaatIni() {
  const { hasPerm } = useAuth();
  const canOpening = hasPerm("current_stock.update") || hasPerm("permissions.manage");
  const canExport = hasPerm("current_stock.export");
  const [data, setData] = useState(null);
  const canAdjust = Boolean(data?.can_adjust_physical_stock);
  const [branches, setBranches] = useState([]);
  const [warehouses, setWarehouses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery, pending: searchPending } = useDebouncedLiveSearch();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [openingOpen, setOpeningOpen] = useState(false);
  const [adjustItem, setAdjustItem] = useState(null);
  const [inlineItemId, setInlineItemId] = useState(null);
  const [savingAdjust, setSavingAdjust] = useState(false);
  const saveLock = useRef(false);
  const loadSeq = useRef(0);

  useEffect(() => { setPage(1); }, [q]);

  useEffect(() => {
    if (!searchPending) return;
    setLoading(true);
    setData((prev) => (prev ? { ...prev, items: [] } : prev));
  }, [searchPending]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.branches(), api.warehouses()])
      .then(([branchRes, warehouseRes]) => {
        if (cancelled) return;
        setBranches(branchRes.items || []);
        setWarehouses(warehouseRes.items || []);
      })
      .catch(() => {
        if (!cancelled) setErr("Gagal memuat cabang dan gudang");
      });
    return () => { cancelled = true; };
  }, []);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setErr("");
    setData((prev) => (prev ? { ...prev, items: [] } : prev));
    try {
      const d = await api.physicalStockCatalog({
        q,
        page,
        limit: pageSize,
      });
      if (seq !== loadSeq.current) return;
      setData(d);
    } catch (ex) {
      if (seq !== loadSeq.current) return;
      const det = ex.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Stock Saat Ini"));
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [q, page, pageSize]);

  useEffect(() => { load(); }, [load]);

  const clearSearch = () => {
    clearQuery();
    setPage(1);
  };

  const items = data?.items || [];
  const adjustWarehouses = yogyakartaWarehouses(branches, warehouses);
  const singleWarehouse = adjustWarehouses.length === 1 ? adjustWarehouses[0] : null;

  const beginAdjust = (item) => {
    if (savingAdjust || saveLock.current) return;
    if (adjustWarehouses.length === 0) {
      setInlineItemId(null);
      setAdjustItem(null);
      setErr("Koreksi tidak tersedia: belum ada gudang aktif.");
      return;
    }
    if (adjustWarehouses.length > 1) {
      setInlineItemId(null);
      setAdjustItem(item);
      return;
    }
    setAdjustItem(null);
    setErr("");
    setInlineItemId(item.master_item_id);
  };

  const saveInline = async (item, target, setCellError, close) => {
    if (saveLock.current || !singleWarehouse) return;
    const current = Number(item.physical_qty);
    if (target === current) {
      close();
      return;
    }
    saveLock.current = true;
    setSavingAdjust(true);
    setCellError("");
    try {
      await api.createPhysicalAdjustment(item.master_item_id, {
        branch_id: singleWarehouse.branch_id,
        warehouse_id: singleWarehouse.id,
        target_physical_qty: target,
        reason: INLINE_ADJUST_REASON,
        idempotency_key: newKey(),
      });
      close();
      await load();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setCellError(typeof det === "object" ? det.message : (det || "Gagal menyimpan koreksi"));
    } finally {
      saveLock.current = false;
      setSavingAdjust(false);
    }
  };
  const pageCount = data?.page_count || 1;
  const tableBusy = searchPending || loading;
  const tableView = liveSearchTableView({
    pending: searchPending,
    loading,
    itemCount: items.length,
  });
  const emptyText = stockEmptyMessage({
    masterItemCount: data?.master_item_count || 0,
    warehouseCount: data?.warehouse_count || 0,
  });

  const download = () => {
    const header = ["Kode", "Nama Barang", "Satuan Dasar", "Stock Fisik", "Qty Pesanan", "Qty Belum Terkirim", "Qty Tersedia", "Terakhir Update"];
    const lines = [header.join(",")];
    items.forEach((row) => {
      const cells = [
        row.item_code,
        row.item_name,
        row.base_unit,
        row.physical_qty,
        row.qty_pesanan,
        row.qty_belum_terkirim,
        row.qty_tersedia,
        row.updated_at || "",
      ].map((value) => `"${String(value ?? "").replace(/"/g, '""')}"`);
      lines.push(cells.join(","));
    });
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `stock-saat-ini-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="prod-page master-items-page stock-page" data-testid="stock-saat-ini-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1 data-testid="stock-title">Stock Saat Ini</h1>
          <p className="subhead" data-testid="stock-subtitle">Qty Tersedia = Stock Fisik - Qty Pesanan - Qty Belum Terkirim. Nilai negatif menunjukkan kebutuhan stock.</p>
        </div>
        <div className="stock-heading-actions">
          <button
            type="button"
            className="ghost"
            onClick={() => setOpeningOpen(true)}
            data-testid="saldo-awal-button"
          >
            Saldo Awal
          </button>
          <button
            type="button"
            className="ghost"
            disabled
            title="Belum tersedia untuk stok baru"
            data-testid="stock-opname-disabled"
          >
            Stock Opname
          </button>
          {canExport && (
            <button className="primary" onClick={download} data-testid="stock-export-button">
              <Download size={14} /> Export
            </button>
          )}
        </div>
      </div>

      {err && (
        <div className="signal-strip" data-testid="stock-error" role="alert">
          <div><AlertCircle size={16} /><span>{err}</span></div>
        </div>
      )}

      <div className="prod-filters" data-testid="stock-filters">
        <div className="prod-filter-row">
          <form className="search-inline" onSubmit={doSearch}>
            <Search size={14} />
            <input
              type="text"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Cari kode / nama / Accurate ID (live)"
              data-testid="stock-search"
              aria-label="Cari kode / nama / Accurate ID"
            />
            {q && (
              <button type="button" className="ghost sm" onClick={clearSearch} aria-label="Bersihkan">
                <X size={12} />
              </button>
            )}
          </form>
          <select
            value={pageSize}
            onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
            data-testid="stock-page-size"
          >
            <option value={25}>25 / halaman</option>
            <option value={50}>50 / halaman</option>
            <option value={100}>100 / halaman</option>
          </select>
        </div>
      </div>

      <div className="prod-table-wrap master-table-wrap">
        <table className="prod-table master-items-table" data-testid="stock-table">
          <thead>
            <tr>
              <th className="col-fit">Kode</th>
              <th className="col-text">Nama Barang</th>
              <th className="col-fit">Satuan Dasar</th>
              <th className="col-fit num">Stock Fisik</th>
              <th className="col-fit num">Qty Pesanan</th>
              <th className="col-fit num">Qty Belum Terkirim</th>
              <th className="col-fit num">Qty Tersedia</th>
              <th className="col-fit">Terakhir Update</th>
            </tr>
          </thead>
          <tbody data-testid="stock-tbody" data-search-state={tableView}>
            {tableView === "loading" && (
              <tr data-testid="stock-loading"><td colSpan={8} className="muted small">Memuat…</td></tr>
            )}
            {tableView === "empty" && (
              <tr>
                <td colSpan={8} className="muted small" data-testid="stock-empty">{emptyText}</td>
              </tr>
            )}
            {tableView === "results" && items.map((it) => (
              <tr key={it.master_item_id} data-testid={`stock-row-${it.master_item_id}`}>
                <td className="col-fit mono">{it.item_code || "—"}</td>
                <td className="col-text">{it.item_name || "—"}</td>
                <td className="col-fit">{it.base_unit || "—"}</td>
                <td className="col-fit num mono" data-testid={`stock-physical-${it.master_item_id}`}>
                  {canAdjust && inlineItemId === it.master_item_id && singleWarehouse ? (
                    <InlinePhysicalEditor
                      item={it}
                      saving={savingAdjust}
                      onCancel={() => setInlineItemId(null)}
                      onSave={(target, setCellError, close) => saveInline(it, target, setCellError, close)}
                    />
                  ) : canAdjust ? (
                    <button
                      type="button"
                      className="ghost sm"
                      data-testid={`stock-adjust-${it.master_item_id}`}
                      onClick={() => beginAdjust(it)}
                    >
                      {fmtQty(it.physical_qty)}
                    </button>
                  ) : fmtQty(it.physical_qty)}
                </td>
                <td className="col-fit num mono" data-testid={`stock-order-${it.master_item_id}`}>{fmtQty(it.qty_pesanan)}</td>
                <td className="col-fit num mono" data-testid={`stock-undelivered-${it.master_item_id}`}>{fmtQty(it.qty_belum_terkirim)}</td>
                <td className="col-fit num mono" data-testid={`stock-available-${it.master_item_id}`}>{fmtQty(it.qty_tersedia)}</td>
                <td className="col-fit small nowrap">{it.updated_at ? formatDate(it.updated_at) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pageCount > 1 && (
        <div className="master-pager" data-testid="stock-pager">
          <button className="ghost sm" disabled={page <= 1 || tableBusy} onClick={() => setPage((p) => p - 1)}>Sebelumnya</button>
          <span className="muted small">Halaman {data?.page || page} dari {pageCount} · {data?.total || 0} baris</span>
          <button className="ghost sm" disabled={page >= pageCount || tableBusy} onClick={() => setPage((p) => p + 1)}>Berikutnya</button>
        </div>
      )}

      {openingOpen && (
        <SaldoAwalModal
          canSubmit={canOpening}
          branches={branches}
          warehouses={warehouses}
          onClose={() => setOpeningOpen(false)}
          onSaved={load}
        />
      )}
      {adjustItem && adjustWarehouses.length > 1 && (
        <PhysicalAdjustModal
          item={adjustItem}
          warehouses={adjustWarehouses}
          onClose={() => setAdjustItem(null)}
          onSaved={load}
        />
      )}
    </div>
  );
}

function InlinePhysicalEditor({ item, saving, onCancel, onSave }) {
  const [draft, setDraft] = useState(() => String(item.physical_qty ?? 0));
  const [error, setError] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const save = () => {
    if (saving) return;
    const raw = String(draft).trim();
    if (!/^\d+$/.test(raw)) {
      setError("Target stok fisik harus bilangan 0 atau lebih.");
      return;
    }
    onSave(Number(raw), setError, onCancel);
  };

  return (
    <span data-testid={`stock-inline-${item.master_item_id}`} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <input
        ref={inputRef}
        value={draft}
        inputMode="numeric"
        aria-label="Target Stock Fisik"
        data-testid={`stock-inline-input-${item.master_item_id}`}
        disabled={saving}
        style={{ width: 72 }}
        onChange={(e) => {
          const next = e.target.value;
          if (next === "" || /^\d+$/.test(next)) {
            setDraft(next);
            setError("");
          }
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            save();
          } else if (e.key === "Escape") {
            e.preventDefault();
            if (!saving) onCancel();
          }
        }}
      />
      <button type="button" className="ghost sm" aria-label="Simpan" data-testid={`stock-inline-save-${item.master_item_id}`} disabled={saving} onClick={save}>
        {saving ? "…" : <Check size={14} />}
      </button>
      <button type="button" className="ghost sm" aria-label="Batal" data-testid={`stock-inline-cancel-${item.master_item_id}`} disabled={saving} onClick={onCancel}>
        <X size={14} />
      </button>
      {error && <span role="alert" data-testid={`stock-inline-error-${item.master_item_id}`}>{error}</span>}
    </span>
  );
}

function PhysicalAdjustModal({ item, warehouses, onClose, onSaved }) {
  const [warehouseId, setWarehouseId] = useState(() => preselectedWarehouseId(warehouses));
  const [currentQty, setCurrentQty] = useState(null);
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [idempotencyKey] = useState(newKey);
  const warehouse = warehouses.find((row) => row.id === warehouseId);
  const parsed = Number(String(target).replace(",", "."));
  const targetOk = target !== "" && Number.isFinite(parsed) && parsed >= 0 && Number.isInteger(parsed);
  const ready = Boolean(warehouseId && targetOk && reason.trim() && !saving);

  useEffect(() => {
    if (!warehouseId) {
      setCurrentQty(null);
      return undefined;
    }
    let cancelled = false;
    api.physicalStock(item.master_item_id, { branch_id: warehouse?.branch_id, warehouse_id: warehouseId })
      .then((res) => {
        if (cancelled) return;
        const row = (res.items || []).find((entry) => entry.warehouse_id === warehouseId);
        setCurrentQty(row ? row.physical_qty : 0);
      })
      .catch(() => {
        if (!cancelled) setError("Gagal memuat stok fisik gudang");
      });
    return () => { cancelled = true; };
  }, [item.master_item_id, warehouseId, warehouse?.branch_id]);

  const submit = async (event) => {
    event.preventDefault();
    if (!ready) return;
    setSaving(true);
    setError("");
    try {
      await api.createPhysicalAdjustment(item.master_item_id, {
        branch_id: warehouse.branch_id,
        warehouse_id: warehouseId,
        target_physical_qty: parsed,
        reason: reason.trim(),
        idempotency_key: idempotencyKey,
      });
      await onSaved();
      onClose();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setError(typeof det === "object" ? det.message : (det || "Gagal menyimpan koreksi"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mutation-drawer-overlay" data-testid="stock-adjust-modal" style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,.45)", zIndex: 40, display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <form onSubmit={submit} style={{ width: "min(560px, 100%)", background: "#fff", borderRadius: 12, padding: 20, display: "grid", gap: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
          <h2 style={{ margin: 0 }}>Koreksi Stock Fisik</h2>
          <button type="button" className="ghost sm" onClick={onClose}>Tutup</button>
        </div>
        <p>{item.item_code || "—"} — {item.item_name || "—"}</p>
        <p>Target ini adalah stok fisik gudang yang dipilih, bukan jumlah seluruh gudang.</p>
        {error && <div className="signal-strip" role="alert"><div><AlertCircle size={16} /><span>{error}</span></div></div>}
        <label>
          Gudang
          <select value={warehouseId} onChange={(e) => setWarehouseId(e.target.value)} data-testid="stock-adjust-warehouse">
            <option value="">Pilih gudang</option>
            {warehouses.map((row) => (
              <option key={row.id} value={row.id}>{row.code} — {row.name}</option>
            ))}
          </select>
        </label>
        <p data-testid="stock-adjust-current">Stok fisik gudang saat ini: {currentQty == null ? "—" : fmtQty(currentQty)}</p>
        <label>
          Target Stock Fisik
          <input value={target} onChange={(e) => setTarget(e.target.value)} inputMode="numeric" data-testid="stock-adjust-target" />
        </label>
        <label>
          Alasan
          <input value={reason} onChange={(e) => setReason(e.target.value)} data-testid="stock-adjust-reason" />
        </label>
        <button type="submit" className="primary" disabled={!ready} data-testid="stock-adjust-submit">
          {saving ? "Menyimpan…" : "Simpan Koreksi"}
        </button>
      </form>
    </div>
  );
}

function importMessage(ex, fallback) {
  const det = ex?.response?.data?.detail;
  if (typeof det === "string" && det.trim()) return det;
  if (det && typeof det.message === "string" && det.message.trim()) return det.message;
  return fallback;
}

function SaldoAwalModal({ canSubmit, branches, warehouses, onClose, onSaved }) {
  const [items, setItems] = useState([]);
  const [itemId, setItemId] = useState("");
  const [branchId, setBranchId] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [qty, setQty] = useState("");
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState(newKey);
  const [preview, setPreview] = useState(null);
  const [importKey, setImportKey] = useState("");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const [templateWarehouseId, setTemplateWarehouseId] = useState("");
  const options = warehouseChoices(warehouses, branchId);
  const selected = items.find((row) => row.id === itemId);
  const baseUnit = (selected?.unit || "PCS").toUpperCase();

  useEffect(() => {
    let cancelled = false;
    api.masterItems({ page_size: 200 })
      .then((res) => { if (!cancelled) setItems(res.items || []); })
      .catch(() => { if (!cancelled) setError("Gagal memuat barang"); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (warehouseId && !options.some((row) => row.id === warehouseId)) setWarehouseId("");
  }, [options, warehouseId]);

  useEffect(() => {
    if (!itemId || !branchId || !warehouseId) {
      setStatus(null);
      return undefined;
    }
    let cancelled = false;
    setStatusLoading(true);
    setError("");
    api.physicalOpeningBalance(itemId, { branch_id: branchId, warehouse_id: warehouseId })
      .then((res) => { if (!cancelled) setStatus(res); })
      .catch((ex) => {
        if (cancelled) return;
        const det = ex.response?.data?.detail;
        setStatus(null);
        setError(typeof det === "object" ? det.message : (det || "Gagal memeriksa saldo awal"));
      })
      .finally(() => { if (!cancelled) setStatusLoading(false); });
    return () => { cancelled = true; };
  }, [itemId, branchId, warehouseId]);

  const established = Boolean(status?.established);
  const ready = canSubmit && !established && !statusLoading && openingReady({ itemId, branchId, warehouseId, qty });
  const branch = branches.find((row) => row.id === branchId);
  const warehouse = options.find((row) => row.id === warehouseId);

  const templateWarehouses = yogyakartaWarehouses(branches, warehouses);
  const downloadTemplate = async () => {
    setImportError("");
    if (!templateWarehouseId) {
      setImportError("Pilih gudang sebelum mengunduh template.");
      return;
    }
    try {
      const blob = await api.physicalOpeningImportTemplate({ warehouse_id: templateWarehouseId });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "saldo-awal-stock-fisik.xlsx";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (ex) {
      setImportError(importMessage(ex, "Gagal mengunduh template"));
    }
  };

  const onImportFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setImportError("");
    setPreview(null);
    try {
      const result = await api.physicalOpeningImportPreview(file);
      setPreview(result);
      setImportKey(newKey());
    } catch (ex) {
      setImportError(importMessage(ex, "File tidak dapat diperiksa"));
    }
  };

  const confirmImport = async () => {
    if (!preview?.can_confirm || !importKey || importing) return;
    setImporting(true);
    setImportError("");
    try {
      await api.physicalOpeningImportConfirm({
        idempotency_key: importKey,
        lines: (preview.rows || []).filter((row) => row.status === "Siap").map((row) => ({
          master_item_id: row.master_item_id,
          branch_id: row.branch_id,
          warehouse_id: row.warehouse_id,
          physical_qty: row.physical_qty,
          item_code: row.item_code,
        })),
      });
      setPreview(null);
      setImportKey("");
      await onSaved();
    } catch (ex) {
      setImportError(importMessage(ex, "Impor saldo awal gagal"));
    } finally {
      setImporting(false);
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    if (!ready || saving) return;
    setSaving(true);
    setError("");
    try {
      await api.createPhysicalOpeningBalance(itemId, {
        branch_id: branchId,
        warehouse_id: warehouseId,
        physical_qty: Number(String(qty).replace(",", ".")),
        reason: reason.trim(),
        idempotency_key: idempotencyKey,
      });
      const next = await api.physicalOpeningBalance(itemId, { branch_id: branchId, warehouse_id: warehouseId });
      setStatus(next);
      setIdempotencyKey(newKey());
      await onSaved();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setError(typeof det === "object" ? det.message : (det || "Gagal menetapkan saldo awal"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mutation-drawer-overlay" data-testid="saldo-awal-modal" style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,.45)", zIndex: 40, display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div data-testid="saldo-awal-panel" style={{ width: "min(960px, 100%)", maxHeight: "90vh", overflow: "auto", background: "#fff", borderRadius: 12, padding: 20, display: "grid", gap: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
          <h2 style={{ margin: 0 }} data-testid="saldo-awal-title">Saldo Awal Stock Fisik</h2>
          <button type="button" className="ghost sm" onClick={onClose} aria-label="Tutup">Tutup</button>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
          <label>
            Gudang
            <select value={templateWarehouseId} onChange={(e) => setTemplateWarehouseId(e.target.value)} data-testid="opening-import-warehouse">
              <option value="">Pilih gudang</option>
              {templateWarehouses.map((row) => (
                <option key={row.id} value={row.id}>{row.name || row.code}</option>
              ))}
            </select>
          </label>
          <button type="button" className="ghost" onClick={downloadTemplate} disabled={!templateWarehouseId} data-testid="opening-import-template">Download Template</button>
          <label className="ghost" style={{ display: "inline-flex", alignItems: "center", cursor: "pointer" }}>
            Import Excel
            <input type="file" accept=".xlsx,.csv,text/csv" onChange={onImportFile} data-testid="opening-import-file" style={{ display: "none" }} />
          </label>
        </div>
        {importError && <div className="signal-strip" role="alert" data-testid="opening-import-error"><div><AlertCircle size={16} /><span>{importError}</span></div></div>}
        {preview && (
          <div style={{ overflowX: "auto" }}>
            <table className="prod-table" data-testid="opening-import-preview">
              <thead>
                <tr>
                  <th>Kode</th>
                  <th>Nama Barang</th>
                  <th>Satuan</th>
                  <th>Gudang</th>
                  <th>Stock Fisik Excel</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {(preview.rows || []).map((row) => (
                  <tr key={`${row.row_number}-${row.item_code}`} data-testid={`opening-import-row-${row.row_number}`}>
                    <td>{row.item_code || "—"}</td>
                    <td>{row.item_name || "—"}</td>
                    <td>{row.unit || "—"}</td>
                    <td>{row.warehouse_name || "—"}</td>
                    <td>{row.physical_qty == null ? "—" : fmtQty(row.physical_qty)}</td>
                    <td data-testid={`opening-import-status-${row.row_number}`}>
                      {row.status}{row.reason ? ` — ${row.reason}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <button
          type="button"
          className="primary"
          disabled={!preview?.can_confirm || importing}
          onClick={confirmImport}
          data-testid="opening-import-confirm"
        >
          {importing ? "Menyimpan…" : "Konfirmasi Saldo Awal"}
        </button>
        <form onSubmit={submit} data-testid="saldo-awal-form" style={{ display: "grid", gap: 12 }}>
        {items.length === 0 && (
          <p data-testid="saldo-awal-empty-items">Belum ada Barang/Jasa pada environment preview.</p>
        )}
        {error && <div className="signal-strip" role="alert"><div><AlertCircle size={16} /><span>{error}</span></div></div>}
        <label>
          Barang
          <select value={itemId} onChange={(e) => setItemId(e.target.value)} data-testid="saldo-awal-item" disabled={established}>
            <option value="">Pilih barang</option>
            {items.map((row) => (
              <option key={row.id} value={row.id}>{row.item_code || "—"} — {row.item_name || row.name || row.id}</option>
            ))}
          </select>
        </label>
        <label>
          Cabang
          <select value={branchId} onChange={(e) => setBranchId(e.target.value)} data-testid="saldo-awal-branch" disabled={established}>
            <option value="">Pilih cabang</option>
            {branches.map((row) => (
              <option key={row.id} value={row.id}>{row.code} — {row.name}</option>
            ))}
          </select>
        </label>
        <label>
          Gudang
          <select value={warehouseId} onChange={(e) => setWarehouseId(e.target.value)} data-testid="saldo-awal-warehouse" disabled={established}>
            <option value="">Pilih gudang</option>
            {options.map((row) => (
              <option key={row.id} value={row.id}>{row.code} — {row.name}</option>
            ))}
          </select>
        </label>
        {established ? (
          <div data-testid="saldo-awal-established">
            <strong>SALDO AWAL SUDAH DITETAPKAN</strong>
            <p>Barang: {selected?.item_name || selected?.item_code || itemId}</p>
            <p>Cabang: {branch?.name || branch?.code || branchId}</p>
            <p>Gudang: {warehouse?.name || warehouse?.code || warehouseId}</p>
            <p>Saldo Awal: {fmtQty(status.opening_qty)} {baseUnit}</p>
            <p>Satuan: {baseUnit}</p>
            <p>Waktu Penetapan: {status.occurred_at ? formatDate(status.occurred_at) : "—"}</p>
          </div>
        ) : (
          <>
            <label>
              Qty Stok Fisik
              <input value={qty} onChange={(e) => setQty(e.target.value)} inputMode="decimal" data-testid="saldo-awal-qty" />
            </label>
            <label>
              Satuan Dasar
              <input value={baseUnit} readOnly data-testid="saldo-awal-unit" />
            </label>
            <label>
              Catatan
              <input value={reason} onChange={(e) => setReason(e.target.value)} data-testid="saldo-awal-reason" />
            </label>
            <button type="submit" className="primary" disabled={!ready || saving} data-testid="saldo-awal-submit">
              {saving ? "Menyimpan…" : "Tetapkan Saldo Awal"}
            </button>
          </>
        )}
        </form>
      </div>
    </div>
  );
}
