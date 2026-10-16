import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Check, Download, Search, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import { useDebouncedLiveSearch, liveSearchTableView } from "../hooks/useDebouncedLiveSearch";
import {
  bookPhysicalDisplay,
  isOperationalWarehouse,
  openingReady,
  operationalOpeningWarehouses,
  preselectedWarehouseId,
  stockEmptyMessage,
  warehouseChoices,
  warehouseLocationLabel,
} from "../lib/physicalStockView";
import BranchScopeSelect from "../components/BranchScopeSelect";

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
  const [branchId, setBranchId] = useState("");
  const [warehouseId, setWarehouseId] = useState(""); // "" = Semua Gudang
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery, pending: searchPending } = useDebouncedLiveSearch();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [openingOpen, setOpeningOpen] = useState(false);
  const [adjustItem, setAdjustItem] = useState(null);
  const [inlineItemId, setInlineItemId] = useState(null);
  const [savingAdjust, setSavingAdjust] = useState(false);
  const [expandedRows, setExpandedRows] = useState(() => ({}));
  const saveLock = useRef(false);
  const loadSeq = useRef(0);

  useEffect(() => { setPage(1); }, [q, branchId, warehouseId]);

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
        const branchRows = branchRes.items || [];
        setBranches(branchRows);
        setWarehouses(warehouseRes.items || []);
        // Controlled <select> with value="" can visually show the first option
        // while state stays empty — pin the sole active branch immediately.
        setBranchId((current) => {
          if (current) return current;
          const active = branchRows.filter((row) => row?.active !== false && row?.id);
          return active.length === 1 ? active[0].id : current;
        });
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
      const params = { q, page, limit: pageSize };
      if (branchId) params.branch_id = branchId;
      if (warehouseId) params.warehouse_id = warehouseId;
      const d = await api.physicalStockCatalog(params);
      if (seq !== loadSeq.current) return;
      setData(d);
      if (!branchId && d?.effective_branch_id) {
        setBranchId(d.effective_branch_id);
      }
    } catch (ex) {
      if (seq !== loadSeq.current) return;
      const det = ex.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Stock Saat Ini"));
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [q, page, pageSize, branchId, warehouseId]);

  useEffect(() => { load(); }, [load]);

  const clearSearch = () => {
    clearQuery();
    setPage(1);
  };

  const items = data?.items || [];
  const effectiveBranchId = branchId || data?.effective_branch_id || "";
  const canSwitchBranches = Boolean(data?.can_switch_branches);
  const filterWarehouses = effectiveBranchId
    ? warehouseChoices(warehouses, effectiveBranchId).filter(
      (row) => row.active !== false && isOperationalWarehouse(row),
    )
    : [];
  const selectedFilterWarehouse = filterWarehouses.find((row) => row.id === warehouseId) || null;
  // Adjust targets the selected warehouse when filtered; otherwise all active
  // warehouses on the selected branch (ID-scoped — no YGY name/code hardcode).
  const branchAdjustWarehouses = effectiveBranchId
    ? warehouseChoices(warehouses, effectiveBranchId).filter((row) => row.active !== false)
    : [];
  const adjustWarehouses = selectedFilterWarehouse
    ? [selectedFilterWarehouse]
    : branchAdjustWarehouses;
  const singleWarehouse = adjustWarehouses.length === 1 ? adjustWarehouses[0] : null;
  const opnameHref = warehouseId
    ? `/persediaan/stock-opname?warehouse_id=${encodeURIComponent(warehouseId)}`
    : "/persediaan/stock-opname";

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
  const readMode = data?.stock_read_mode || (data?.r2_authority_active ? "r2" : "compatibility");
  const r2Active = Boolean(data?.r2_authority_active);
  // Warehouse-scoped UI when R2 OR explicit warehouse_stock_standalone is on.
  const warehouseActive = Boolean(
    r2Active
    || data?.warehouse_stock_standalone
    || readMode === "warehouse_standalone"
    || readMode === "r2"
  );
  const emptyText = stockEmptyMessage({
    masterItemCount: data?.master_item_count || 0,
    warehouseCount: data?.warehouse_count || 0,
    readMode,
  });
  const modeChipLabel = r2Active
    ? "R2"
    : warehouseActive
      ? "Gudang"
      : "Kompatibilitas";
  const modeFullLabel = r2Active
    ? "Mode R2 — stok fisik per cabang/gudang."
    : warehouseActive
      ? (data?.stock_read_mode_label
        || "Mode warehouse standalone — stok fisik per cabang/gudang (tanpa Replica Set).")
      : (data?.stock_read_mode_label
        || "Mode kompatibilitas — stok fisik legacy perusahaan (bukan per gudang).");
  const showBranchAggregate = Boolean(warehouseActive && !selectedFilterWarehouse);

  const physicalHeader = warehouseActive
    ? "Stock Fisik"
    : "Stock Fisik (perusahaan)";

  const toggleRowExpand = (masterItemId) => {
    setExpandedRows((prev) => ({ ...prev, [masterItemId]: !prev[masterItemId] }));
  };
  const download = () => {
    const header = [
      "Kode",
      "Nama Barang",
      "Satuan Dasar",
      "Lokasi",
      physicalHeader,
      "Qty Pesanan",
      "Qty Belum Terkirim",
      "Qty Tersedia",
      "Baseline",
      "Terakhir Update",
    ];
    const lines = [header.join(",")];
    items.forEach((row) => {
      const location = selectedFilterWarehouse
        ? warehouseLocationLabel(selectedFilterWarehouse, branches)
        : (data?.effective_branch_code || "Cabang");
      const book = bookPhysicalDisplay(row, { warehouseId: selectedFilterWarehouse?.id || "" });
      const cells = [
        row.item_code,
        row.item_name,
        row.base_unit,
        location,
        book.present ? book.qty : "",
        row.qty_pesanan,
        row.qty_belum_terkirim,
        row.qty_tersedia_valid === false ? "" : row.qty_tersedia,
        row.baseline_status || "",
        row.updated_at || "",
      ].map((value) => `"${String(value ?? "").replace(/"/g, '""')}"`);
      lines.push(cells.join(","));
      if (!selectedFilterWarehouse && Array.isArray(row.warehouse_balances)) {
        row.warehouse_balances.forEach((wb) => {
          const whLabel = warehouseLocationLabel({
            id: wb.warehouse_id,
            code: wb.warehouse_code,
            name: wb.warehouse_name,
            branch_id: effectiveBranchId,
          }, branches);
          const qty = wb.balance_present ? wb.physical_qty : "";
          const detail = [
            row.item_code,
            row.item_name,
            row.base_unit,
            whLabel,
            qty,
            "",
            "",
            "",
            wb.baseline_status || (wb.balance_present ? "" : "unreconciled"),
            "",
          ].map((value) => `"${String(value ?? "").replace(/"/g, '""')}"`);
          lines.push(detail.join(","));
        });
      }
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
      <div className="page-heading prod-heading stock-page-heading">
        <div className="prod-heading-copy">
          <div className="stock-title-row">
            <h1 data-testid="stock-title">Stock Saat Ini</h1>
            {data && (
              <span
                className={`stock-mode-chip stock-mode-chip--${r2Active ? "r2" : warehouseActive ? "warehouse" : "compat"}`}
                data-testid="stock-mode-chip"
                title={modeFullLabel}
              >
                {modeChipLabel}
              </span>
            )}
          </div>
          <p className="subhead" data-testid="stock-subtitle">
            Qty Tersedia = Stock Fisik − Qty Pesanan − Qty Belum Terkirim.
          </p>
          {data && (
            <details className="stock-context-info" data-testid="stock-context-info">
              <summary>Aturan stok &amp; mode baca</summary>
              <p className="muted small" data-testid="stock-read-mode">{modeFullLabel}</p>
              {!warehouseActive && (
                <p className="muted small" data-testid="stock-compat-branch-note">
                  {data.physical_qty_note
                    || "Angka Stock Fisik adalah gabungan legacy perusahaan — bukan saldo gudang."}
                </p>
              )}
              {!warehouseActive && !canAdjust && (
                <p className="muted small" data-testid="stock-adjust-blocked-reason">
                  {data.adjust_blocked_reason
                    || "Koreksi/Saldo Awal/Opname gudang memerlukan r2_authority_active atau warehouse_stock_standalone."}
                </p>
              )}
              {warehouseActive && (
                <p className="muted small" data-testid="stock-book-vs-count-note">
                  Qty Pesanan / Belum Terkirim lingkup cabang. Saldo belum ada = Belum tersedia; belum direkonsiliasi = Belum valid — bukan klaim nol siap jual. Agregat cabang tidak menyembunyikan defisit gudang atau saldo belum valid.
                </p>
              )}
            </details>
          )}
        </div>
        <div className="stock-heading-actions">
          <button
            type="button"
            className="ghost"
            onClick={() => setOpeningOpen(true)}
            disabled={!canAdjust}
            title={!canAdjust ? (data?.adjust_blocked_reason || "Saldo Awal belum tersedia pada mode ini") : undefined}
            data-testid="saldo-awal-button"
          >
            Saldo Awal
          </button>
          {warehouseActive ? (
            <Link className="ghost" to={opnameHref} data-testid="stock-opname-button">
              Stock Opname
            </Link>
          ) : (
            <button
              type="button"
              className="ghost"
              disabled
              title={data?.adjust_blocked_reason || "Stock Opname memerlukan mesin gudang (R2 atau warehouse standalone)."}
              data-testid="stock-opname-button"
            >
              Stock Opname
            </button>
          )}
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
          <BranchScopeSelect
            branches={branches}
            value={effectiveBranchId}
            canSwitch={Boolean(canSwitchBranches && warehouseActive)}
            effectiveCode={data?.effective_branch_code}
            onChange={(next) => {
              setBranchId(next);
              setWarehouseId("");
              setPage(1);
              setInlineItemId(null);
              setAdjustItem(null);
            }}
            testId="stock-branch-select"
          />
          {warehouseActive && (
            <label className="field stock-warehouse-filter" data-testid="stock-warehouse-filter">
              <span className="muted small">Gudang</span>
              <select
                value={warehouseId}
                onChange={(e) => {
                  setWarehouseId(e.target.value);
                  setPage(1);
                  setInlineItemId(null);
                  setAdjustItem(null);
                }}
                data-testid="stock-warehouse-select"
                aria-label="Filter gudang"
              >
                <option value="">Semua Gudang</option>
                {filterWarehouses.map((wh) => (
                  <option key={wh.id} value={wh.id}>
                    {wh.code ? `${wh.code} — ${wh.name || wh.code}` : (wh.name || wh.id)}
                  </option>
                ))}
              </select>
            </label>
          )}
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

      <div className="prod-table-wrap master-table-wrap stock-table-wrap">
        <table className="prod-table master-items-table stock-dense-table" data-testid="stock-table">
          <thead>
            <tr>
              {showBranchAggregate && <th className="col-expand" aria-label="Rincian gudang" />}
              <th className="col-text">Barang</th>
              <th className="col-fit" data-testid="stock-location-header">Lokasi</th>
              <th
                className="col-fit num"
                data-testid="stock-physical-header"
                data-physical-scope={warehouseActive ? (selectedFilterWarehouse ? "WAREHOUSE" : "BRANCH") : "COMPANY"}
                title={warehouseActive
                  ? (selectedFilterWarehouse ? "Stock fisik gudang terpilih" : "Stock fisik cabang (agregat gudang)")
                  : "Legacy perusahaan — bukan saldo per gudang"}
              >
                {physicalHeader}
              </th>
              <th className="col-fit num" title="Lingkup cabang — bukan alokasi per gudang">
                Qty Pesanan
              </th>
              <th className="col-fit num" title="Lingkup cabang — bukan alokasi per gudang">
                Qty Belum Terkirim
              </th>
              <th className="col-fit num">Qty Tersedia</th>
              <th className="col-fit">Terakhir Update</th>
            </tr>
          </thead>
          <tbody data-testid="stock-tbody" data-search-state={tableView}>
            {tableView === "loading" && (
              <tr data-testid="stock-loading">
                <td colSpan={showBranchAggregate ? 8 : 7} className="muted small">Memuat…</td>
              </tr>
            )}
            {tableView === "empty" && (
              <tr>
                <td colSpan={showBranchAggregate ? 8 : 7} className="muted small" data-testid="stock-empty">{emptyText}</td>
              </tr>
            )}
            {tableView === "results" && items.map((it) => {
              const book = bookPhysicalDisplay(it, { warehouseId: selectedFilterWarehouse?.id || "" });
              const physicalText = book.present ? fmtQty(book.qty) : book.text;
              const whBalances = Array.isArray(it.warehouse_balances) ? it.warehouse_balances : [];
              const canExpand = showBranchAggregate && whBalances.length > 0;
              const expanded = Boolean(expandedRows[it.master_item_id]);
              const hasWhDeficit = whBalances.some((wb) => wb.saldo_buku_negatif);
              const hasWhMissing = whBalances.some((wb) => wb.balance_present === false);
              const hasWhUnreconciled = whBalances.some((wb) => wb.baseline_status === "unreconciled");
              return (
              <Fragment key={it.master_item_id}>
              <tr
                data-testid={`stock-row-${it.master_item_id}`}
                className={[
                  expanded ? "stock-row-expanded" : "",
                  hasWhDeficit ? "stock-row-has-deficit" : "",
                ].filter(Boolean).join(" ") || undefined}
              >
                {showBranchAggregate && (
                  <td className="col-expand">
                    {canExpand ? (
                      <button
                        type="button"
                        className="ghost sm stock-expand-btn"
                        aria-expanded={expanded}
                        aria-label={expanded ? "Sembunyikan rincian gudang" : "Tampilkan rincian gudang"}
                        data-testid={`stock-expand-${it.master_item_id}`}
                        onClick={() => toggleRowExpand(it.master_item_id)}
                      >
                        {expanded ? "▾" : "▸"}
                      </button>
                    ) : null}
                  </td>
                )}
                <td className="col-text" data-testid={`stock-item-${it.master_item_id}`}>
                  <div className="stock-item-primary">{it.item_name || "—"}</div>
                  <div className="stock-item-secondary muted small">
                    <span className="mono" data-testid={`stock-code-${it.master_item_id}`}>{it.item_code || "—"}</span>
                    <span aria-hidden="true"> · </span>
                    <span>{it.base_unit || "—"}</span>
                  </div>
                </td>
                <td className="col-fit small" data-testid={`stock-location-${it.master_item_id}`}>
                  {selectedFilterWarehouse
                    ? warehouseLocationLabel(selectedFilterWarehouse, branches)
                    : (data?.effective_branch_code || "Cabang")}
                </td>
                <td
                  className={`col-fit num mono${book.present && it.saldo_buku_negatif ? " stock-book-negative" : ""}`}
                  data-testid={`stock-physical-${it.master_item_id}`}
                  data-negative={book.present && it.saldo_buku_negatif ? "true" : "false"}
                  data-balance-present={book.present ? "true" : "false"}
                >
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
                      {physicalText}
                    </button>
                  ) : (
                    <span className={book.present ? undefined : "muted"}>{physicalText}</span>
                  )}
                  {it.baseline_status === "unreconciled" && (
                    <div className="muted small" data-testid={`stock-baseline-${it.master_item_id}`}>
                      {it.baseline_label || "Belum valid"}
                    </div>
                  )}
                  {book.present && it.saldo_buku_negatif && (
                    <div className="stock-book-negative-note" data-testid={`stock-book-negative-${it.master_item_id}`}>
                      Saldo negatif
                    </div>
                  )}
                  {showBranchAggregate && (hasWhDeficit || hasWhMissing || hasWhUnreconciled) && !expanded && (
                    <div className="muted small stock-aggregate-hint" data-testid={`stock-aggregate-hint-${it.master_item_id}`}>
                      {hasWhDeficit ? "Ada gudang defisit" : hasWhMissing ? "Ada gudang belum saldo" : "Ada gudang belum valid"}
                    </div>
                  )}
                </td>
                <td className="col-fit num mono" data-testid={`stock-order-${it.master_item_id}`}>{fmtQty(it.qty_pesanan)}</td>
                <td
                  className="col-fit num mono"
                  data-testid={`stock-undelivered-${it.master_item_id}`}
                  data-belum-status={it.belum_terkirim_status || "READY"}
                >
                  {(it.belum_terkirim_status === "BELUM_TERVERIFIKASI"
                    || it.belum_terkirim_status === "CUTOVER_PENDING")
                    ? "Belum terverifikasi"
                    : fmtQty(it.qty_belum_terkirim)}
                </td>
                <td
                  className="col-fit num mono"
                  data-testid={`stock-available-${it.master_item_id}`}
                  data-tersedia-valid={it.qty_tersedia_valid === false ? "false" : "true"}
                  title={
                    it.qty_tersedia_valid === false
                      ? (it.qty_tersedia_note || "Qty Tersedia belum valid — saldo fisik belum direkonsiliasi")
                      : selectedFilterWarehouse
                        ? (it.qty_tersedia_note || "Permintaan cabang vs saldo buku gudang — bukan alokasi per gudang")
                        : (Number(it.qty_tersedia) < 0 ? "Kebutuhan stok (informasi) — tidak memblokir pesanan" : undefined)
                  }
                >
                  {it.qty_tersedia_valid === false ? (
                    <span className="muted" data-testid={`stock-available-invalid-${it.master_item_id}`}>
                      Belum valid
                    </span>
                  ) : fmtQty(it.qty_tersedia)}
                </td>
                <td className="col-fit small nowrap">{it.updated_at ? formatDate(it.updated_at) : "—"}</td>
              </tr>
              {canExpand && expanded && (
                <tr className="stock-expand-row" data-testid={`stock-expand-row-${it.master_item_id}`}>
                  <td colSpan={8}>
                    <ul className="stock-warehouse-breakdown" data-testid={`stock-warehouse-breakdown-${it.master_item_id}`}>
                      {whBalances.map((wb) => (
                        <li
                          key={wb.warehouse_id}
                          data-testid={`stock-wh-bal-${it.master_item_id}-${wb.warehouse_code || wb.warehouse_id}`}
                          className={wb.saldo_buku_negatif ? "stock-book-negative" : undefined}
                          data-negative={wb.saldo_buku_negatif ? "true" : "false"}
                          data-balance-present={wb.balance_present ? "true" : "false"}
                        >
                          <span className="mono">{wb.warehouse_code || wb.warehouse_name}</span>
                          {": "}
                          <span className={wb.balance_present ? "mono" : "muted"}>
                            {wb.balance_present ? fmtQty(wb.physical_qty) : "Belum tersedia"}
                          </span>
                          {" "}
                          <span className="muted">{it.base_unit || "PCS"}</span>
                          {wb.baseline_status === "unreconciled" && (
                            <span className="muted small"> · Belum valid</span>
                          )}
                          {wb.saldo_buku_negatif && (
                            <span className="stock-book-negative-note"> · Saldo negatif</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </td>
                </tr>
              )}
              </Fragment>
              );
            })}
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
          branches={branches}
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

function PhysicalAdjustModal({ item, branches = [], warehouses, onClose, onSaved }) {
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
              <option key={row.id} value={row.id}>{warehouseLocationLabel(row, branches)}</option>
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
  const options = warehouseChoices(warehouses, branchId).filter(isOperationalWarehouse);
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

  const templateWarehouses = operationalOpeningWarehouses(branches, warehouses);
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
                <option key={row.id} value={row.id}>{warehouseLocationLabel(row, branches)}</option>
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
              <option key={row.id} value={row.id}>{warehouseLocationLabel(row, branches)}</option>
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
