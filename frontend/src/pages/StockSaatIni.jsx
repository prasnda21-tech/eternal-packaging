import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Download, Search, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import { useDebouncedLiveSearch, liveSearchTableView } from "../hooks/useDebouncedLiveSearch";
import { openingReady, stockEmptyMessage, warehouseChoices } from "../lib/physicalStockView";

function newKey() {
  return globalThis.crypto?.randomUUID?.() || `saldo-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function StockSaatIni() {
  const { hasPerm } = useAuth();
  const canOpening = hasPerm("current_stock.update") || hasPerm("permissions.manage");
  const canExport = hasPerm("current_stock.export");
  const [data, setData] = useState(null);
  const [branches, setBranches] = useState([]);
  const [warehouses, setWarehouses] = useState([]);
  const [branchId, setBranchId] = useState("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery, pending: searchPending } = useDebouncedLiveSearch();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [openingOpen, setOpeningOpen] = useState(false);
  const loadSeq = useRef(0);

  useEffect(() => { setPage(1); }, [q, branchId]);

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
        branch_id: branchId || undefined,
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
  }, [q, branchId, page, pageSize]);

  useEffect(() => { load(); }, [load]);

  const clearSearch = () => {
    clearQuery();
    setPage(1);
  };

  const items = data?.items || [];
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
    branchId,
  });

  const download = () => {
    const header = ["Kode", "Nama Barang", "Satuan Dasar", "Cabang", "Stock Fisik", "Qty Pesanan", "Belum Dicetak", "Sudah Dicetak", "Stock Saat Ini", "Terakhir Update"];
    const lines = [header.join(",")];
    items.forEach((row) => {
      const cells = [
        row.item_code,
        row.item_name,
        row.base_unit,
        row.branch_name || row.branch_code,
        row.physical_qty,
        row.order_qty,
        row.belum_dicetak,
        row.printed_qty,
        row.stock_current,
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
          <p className="subhead" data-testid="stock-subtitle">Stock Saat Ini = stok fisik cabang − qty pesanan outstanding. Progress cetak tidak mengurangi stok.</p>
          {data?.operational_branch && (
            <p className="subhead" data-testid="stock-operational-branch">
              Cabang operasional: {data.operational_branch.code} — {data.operational_branch.name}. Qty pesanan dihitung sekali pada cabang ini.
            </p>
          )}
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
            value={branchId}
            onChange={(e) => { setBranchId(e.target.value); setPage(1); }}
            data-testid="stock-filter-branch"
            aria-label="Cabang"
          >
            <option value="">Semua Cabang</option>
            {branches.map((branch) => (
              <option key={branch.id} value={branch.id}>{branch.code} — {branch.name}</option>
            ))}
          </select>
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
              <th className="col-fit">Cabang</th>
              <th className="col-fit num">Stock Fisik</th>
              <th className="col-fit num">Qty Pesanan</th>
              <th className="col-fit num">Belum Dicetak</th>
              <th className="col-fit num">Sudah Dicetak</th>
              <th className="col-fit num">Stock Saat Ini</th>
              <th className="col-fit">Terakhir Update</th>
            </tr>
          </thead>
          <tbody data-testid="stock-tbody" data-search-state={tableView}>
            {tableView === "loading" && (
              <tr data-testid="stock-loading"><td colSpan={10} className="muted small">Memuat…</td></tr>
            )}
            {tableView === "empty" && (
              <tr>
                <td colSpan={10} className="muted small" data-testid="stock-empty">{emptyText}</td>
              </tr>
            )}
            {tableView === "results" && items.map((it) => (
              <tr key={`${it.master_item_id}-${it.branch_id}`} data-testid={`stock-row-${it.master_item_id}-${it.branch_id}`}>
                <td className="col-fit mono">{it.item_code || "—"}</td>
                <td className="col-text">{it.item_name || "—"}</td>
                <td className="col-fit">{it.base_unit || "—"}</td>
                <td className="col-fit">{it.branch_code || it.branch_name || "—"}</td>
                <td className="col-fit num mono" data-testid={`stock-physical-${it.master_item_id}`}>{fmtQty(it.physical_qty)}</td>
                <td className="col-fit num mono" data-testid={`stock-order-${it.master_item_id}`}>{fmtQty(it.order_qty)}</td>
                <td className="col-fit num mono" data-testid={`stock-belum-${it.master_item_id}`}>{fmtQty(it.belum_dicetak)}</td>
                <td className="col-fit num mono" data-testid={`stock-printed-${it.master_item_id}`}>{fmtQty(it.printed_qty)}</td>
                <td className="col-fit num mono" data-testid={`stock-current-${it.master_item_id}`}>{fmtQty(it.stock_current)}</td>
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
    </div>
  );
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
      <form onSubmit={submit} data-testid="saldo-awal-form" style={{ width: "min(560px, 100%)", background: "#fff", borderRadius: 12, padding: 20, display: "grid", gap: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
          <h2 style={{ margin: 0 }}>Saldo Awal</h2>
          <button type="button" className="ghost sm" onClick={onClose} aria-label="Tutup">Tutup</button>
        </div>
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
  );
}
