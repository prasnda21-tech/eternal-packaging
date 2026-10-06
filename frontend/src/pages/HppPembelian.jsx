import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, Download, RefreshCw, Search, X } from "lucide-react";
import api from "../lib/api";
import { friendlyLoadError } from "../lib/purchaseUi";
import "./purchasePages.css";

const STATUS_FILTERS = [
  { id: "", label: "Semua status" },
  { id: "ADA_HPP", label: "Ada HPP" },
  { id: "BELUM_ADA_HPP", label: "Belum ada HPP" },
  { id: "PENDING_VALUATION", label: "Menunggu valuasi" },
  { id: "NEEDS_COST", label: "Butuh harga PO" },
  { id: "NEEDS_UOM", label: "Butuh satuan" },
  { id: "FAILED", label: "Gagal valuasi" },
];

const MOVER_LIMIT = 10;

function formatMoney(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("id-ID", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function formatQty(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("id-ID", { maximumFractionDigits: 4 });
}

function formatDelta(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "0";
  const abs = Math.abs(n).toLocaleString("id-ID", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  return n > 0 ? `+${abs}` : `-${abs}`;
}

function formatPct(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "0%";
  const abs = Math.abs(n).toLocaleString("id-ID", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  return n > 0 ? `+${abs}%` : `-${abs}%`;
}

function deltaClass(value) {
  if (value == null || value === "") return "";
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return "is-flat";
  return n > 0 ? "is-up" : "is-down";
}

function receiptLabel(row) {
  const parts = [
    row?.latest_gr_date,
    row?.latest_gr_number,
    row?.latest_supplier,
    row?.latest_po_number,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "—";
}

function statusClass(code) {
  const key = String(code || "").toLowerCase().replace(/_/g, "-");
  return `hpp-status is-${key}`;
}

function hasComparableMove(row) {
  return row?.price_vs_hpp_delta != null && Number.isFinite(Number(row.price_vs_hpp_delta));
}

function topMovers(rows, direction) {
  const scored = (rows || []).filter(hasComparableMove).map((row) => ({
    ...row,
    _delta: Number(row.price_vs_hpp_delta),
  }));
  if (direction === "up") {
    return scored
      .filter((row) => row._delta > 0)
      .sort((a, b) => b._delta - a._delta || String(a.item_name || "").localeCompare(String(b.item_name || "")))
      .slice(0, MOVER_LIMIT);
  }
  return scored
    .filter((row) => row._delta < 0)
    .sort((a, b) => a._delta - b._delta || String(a.item_name || "").localeCompare(String(b.item_name || "")))
    .slice(0, MOVER_LIMIT);
}

function MoverList({ items, emptyLabel, testId }) {
  return (
    <div className="hpp-mover-panel" data-testid={testId}>
      <ul className="hpp-mover-list">
        {items.length === 0 && <li className="muted small">{emptyLabel}</li>}
        {items.map((row) => (
          <li key={row.master_item_id} data-testid={`${testId}-row`}>
            <div className="hpp-mover-name">{row.item_name || "—"}</div>
            <div className="hpp-mover-meta">
              <span className="hpp-mover-range">
                {formatMoney(row.hpp_before_latest_gr)} → {formatMoney(row.latest_purchase_unit_cost)}
              </span>
              <span className={`hpp-mover-delta ${deltaClass(row.price_vs_hpp_delta)}`}>
                {formatDelta(row.price_vs_hpp_delta)} ({formatPct(row.price_vs_hpp_pct)})
              </span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function HppPembelian() {
  const [rows, setRows] = useState([]);
  const [suppliers, setSuppliers] = useState([]);
  const [q, setQ] = useState("");
  const [supplier, setSupplier] = useState("");
  const [status, setStatus] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [err, setErr] = useState("");
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await api.purchaseHpp({
        q,
        supplier,
        status,
        date_from: dateFrom,
        date_to: dateTo,
      });
      setRows(res.rows || []);
      setSuppliers(res.suppliers || []);
    } catch (ex) {
      setErr(friendlyLoadError(ex, "Tidak dapat memuat HPP Pembelian."));
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [q, supplier, status, dateFrom, dateTo]);

  useEffect(() => {
    load();
  }, [load]);

  const topUp = useMemo(() => topMovers(rows, "up"), [rows]);
  const topDown = useMemo(() => topMovers(rows, "down"), [rows]);

  const closeDrawer = () => {
    setDrawerOpen(false);
    setDetail(null);
    setSelectedId("");
    setDetailLoading(false);
  };

  const openDetail = async (row) => {
    const id = row?.master_item_id;
    if (!id) return;
    setSelectedId(id);
    setDrawerOpen(true);
    setDetailLoading(true);
    setErr("");
    try {
      const item = await api.purchaseHppItem(id);
      setDetail(item);
    } catch (ex) {
      setErr(friendlyLoadError(ex, "Tidak dapat memuat detail HPP barang."));
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const download = async () => {
    if (exporting) return;
    setExporting(true);
    setErr("");
    try {
      const blob = await api.purchaseHppExport({
        q,
        supplier,
        status,
        date_from: dateFrom,
        date_to: dateTo,
      });
      const url = URL.createObjectURL(new Blob([blob], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `hpp-pembelian-${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (ex) {
      setErr(friendlyLoadError(ex, "Gagal export HPP Pembelian."));
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="prod-page purchase-page" data-testid="hpp-pembelian-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1 data-testid="hpp-pembelian-title">HPP Pembelian</h1>
          <p className="subhead">
            Pantau HPP persediaan dari penerimaan barang (rata-rata tertimbang).
            Bukan jurnal, bukan HPP penjualan, bukan harga customer.
          </p>
        </div>
        <div className="stock-heading-actions purchase-toolbar-actions">
          <button type="button" className="ghost" onClick={load} data-testid="hpp-pembelian-refresh">
            <RefreshCw size={14} className={loading ? "spin" : ""} /> Segarkan
          </button>
          <button type="button" className="ghost" onClick={download} disabled={exporting} data-testid="hpp-pembelian-export">
            <Download size={14} /> {exporting ? "Menyiapkan…" : "Export Excel"}
          </button>
        </div>
      </div>

      {err && (
        <div className="purchase-error-panel" role="alert" data-testid="hpp-pembelian-error">
          <AlertCircle size={16} color="#a53f2b" /><span>{err}</span>
        </div>
      )}

      <div className="hpp-summary-row" data-testid="hpp-summary-row">
        <section className="hpp-summary-box" data-testid="hpp-top-up">
          <h2>Top Naik</h2>
          <MoverList items={topUp} emptyLabel="Belum ada kenaikan." testId="hpp-top-up" />
        </section>
        <section className="hpp-summary-box" data-testid="hpp-top-down">
          <h2>Top Turun</h2>
          <MoverList items={topDown} emptyLabel="Belum ada penurunan." testId="hpp-top-down" />
        </section>
      </div>

      <div className="purchase-search-bar is-toolbar hpp-filter-bar" data-testid="hpp-pembelian-filters">
        <form className="search-inline" onSubmit={(e) => e.preventDefault()}>
          <Search size={14} />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            data-testid="hpp-search"
            placeholder="Cari kode / nama barang"
          />
        </form>
        <select value={supplier} onChange={(e) => setSupplier(e.target.value)} data-testid="hpp-filter-supplier">
          <option value="">Semua supplier</option>
          {suppliers.map((name) => (
            <option key={name} value={name}>{name}</option>
          ))}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} data-testid="hpp-filter-status">
          {STATUS_FILTERS.map((item) => (
            <option key={item.id || "all"} value={item.id}>{item.label}</option>
          ))}
        </select>
        <label className="hpp-date-field">
          <span>Dari</span>
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} data-testid="hpp-date-from" />
        </label>
        <label className="hpp-date-field">
          <span>Sampai</span>
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} data-testid="hpp-date-to" />
        </label>
      </div>

      {loading && <p className="muted small" data-testid="hpp-loading">Memuat HPP pembelian…</p>}

      <div className="purchase-table-wrap">
        <table className="prod-table purchase-table hpp-table" data-testid="hpp-pembelian-table">
          <thead>
            <tr>
              <th>Kode Barang</th>
              <th>Nama Barang</th>
              <th className="num">Stock Fisik</th>
              <th>Status HPP</th>
            </tr>
          </thead>
          <tbody>
            {!loading && rows.length === 0 && (
              <tr>
                <td colSpan={4} className="muted small" data-testid="hpp-empty">Belum ada data HPP pembelian.</td>
              </tr>
            )}
            {rows.map((row) => (
              <tr
                key={row.master_item_id}
                className={`is-row-clickable${selectedId === row.master_item_id ? " is-selected" : ""}`}
                data-testid="hpp-list-row"
                tabIndex={0}
                onClick={() => openDetail(row)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    openDetail(row);
                  }
                }}
              >
                <td className="mono" data-testid="hpp-item-code">{row.item_code || "—"}</td>
                <td data-testid="hpp-item-name">{row.item_name || "—"}</td>
                <td className="num" data-testid="hpp-stock">{formatQty(row.stock_fisik)}</td>
                <td><span className={statusClass(row.hpp_status)} data-testid="hpp-status">{row.hpp_status_label || "—"}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {drawerOpen && (
        <div className="hpp-drawer-root" data-testid="hpp-drawer-root">
          <button type="button" className="hpp-drawer-scrim" aria-label="Tutup detail" onClick={closeDrawer} data-testid="hpp-drawer-scrim" />
          <aside className="hpp-drawer" role="dialog" aria-label="Detail HPP Pembelian" data-testid="hpp-detail">
            <div className="hpp-detail-head">
              <div>
                <p className="eyebrow">Detail HPP Pembelian</p>
                <h2 data-testid="hpp-detail-title">
                  {detailLoading && !detail ? "Memuat…" : `${detail?.item_code || "—"} · ${detail?.item_name || "—"}`}
                </h2>
              </div>
              <button type="button" className="ghost sm" data-testid="hpp-detail-close" onClick={closeDrawer}>
                <X size={14} /> Tutup
              </button>
            </div>

            {detailLoading && <p className="muted small" data-testid="hpp-detail-loading">Memuat detail…</p>}

            {detail && (
              <>
                <div className="hpp-detail-grid" data-testid="hpp-detail-identity">
                  <div><span className="label">Kode Barang</span><span className="value mono">{detail.item_code || "—"}</span></div>
                  <div><span className="label">Nama Barang</span><span className="value">{detail.item_name || "—"}</span></div>
                  <div><span className="label">Satuan</span><span className="value">{detail.unit || "—"}</span></div>
                  <div><span className="label">Stock Fisik</span><span className="value" data-testid="hpp-detail-stock">{formatQty(detail.stock_fisik)}</span></div>
                  <div><span className="label">Status HPP</span><span className={`value ${statusClass(detail.hpp_status)}`} data-testid="hpp-detail-status">{detail.hpp_status_label || "—"}</span></div>
                  <div><span className="label">Supplier</span><span className="value">{detail.latest_supplier || "—"}</span></div>
                  <div className="hpp-detail-span"><span className="label">Penerimaan Terakhir</span><span className="value" data-testid="hpp-detail-receipt">{receiptLabel(detail)}</span></div>
                </div>

                <h3>Analisis HPP</h3>
                <div className="hpp-analysis-stack" data-testid="hpp-detail-current">
                  <div className="hpp-analysis-row">
                    <span className="label">HPP Sebelum Pembelian Terakhir</span>
                    <span className="value" data-testid="hpp-detail-before">{formatMoney(detail.hpp_before_latest_gr)}</span>
                  </div>
                  <div className="hpp-analysis-row">
                    <span className="label">Harga Pembelian Terbaru</span>
                    <span className="value" data-testid="hpp-detail-price">{formatMoney(detail.latest_purchase_unit_cost)}</span>
                  </div>
                  <div className="hpp-analysis-row">
                    <span className="label">Selisih</span>
                    <span className={`value ${deltaClass(detail.price_vs_hpp_delta)}`} data-testid="hpp-detail-delta">{formatDelta(detail.price_vs_hpp_delta)}</span>
                  </div>
                  <div className="hpp-analysis-row">
                    <span className="label">Persentase</span>
                    <span className={`value ${deltaClass(detail.price_vs_hpp_pct)}`} data-testid="hpp-detail-pct">{formatPct(detail.price_vs_hpp_pct)}</span>
                  </div>
                  <div className="hpp-analysis-row">
                    <span className="label">HPP Setelah Pembelian Terakhir</span>
                    <span className="value" data-testid="hpp-detail-after">{formatMoney(detail.hpp_after_latest_gr)}</span>
                  </div>
                  <div className="hpp-analysis-row">
                    <span className="label">HPP Saat Ini</span>
                    <span className="value" data-testid="hpp-detail-current-hpp">{formatMoney(detail.current_hpp)}</span>
                  </div>
                  <div className="hpp-analysis-row">
                    <span className="label">Nilai Persediaan</span>
                    <span className="value" data-testid="hpp-detail-value">{formatMoney(detail.inventory_value)}</span>
                  </div>
                </div>

                <h3>Riwayat valuasi</h3>
                <div className="purchase-table-wrap hpp-history-wrap">
                  <table className="prod-table" data-testid="hpp-history-table">
                    <thead>
                      <tr>
                        <th>Tanggal</th>
                        <th>Transaksi</th>
                        <th className="num">Qty</th>
                        <th>Harga / Cost</th>
                        <th className="num">HPP Sebelum</th>
                        <th className="num">HPP Sesudah</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(detail.history || []).length === 0 && (
                        <tr><td colSpan={7} className="muted small">Belum ada pengakuan HPP untuk barang ini.</td></tr>
                      )}
                      {(detail.history || []).map((row, index) => {
                        const meta = [row.supplier, row.po_number, row.gr_number].filter(Boolean).join(" · ");
                        return (
                          <tr key={`${row.source_id || "h"}-${index}`} data-testid="hpp-history-row">
                            <td className="nowrap">{row.document_date || (row.recognized_at || "").slice(0, 10) || "—"}</td>
                            <td data-testid="hpp-history-type">
                              <span className="hpp-history-txn">{row.source_type_label || row.source_type || "—"}</span>
                              {meta ? <span className="hpp-history-meta">{meta}</span> : null}
                            </td>
                            <td className="num">{formatQty(row.qty)}</td>
                            <td>
                              <span className="hpp-cost-label">{row.incoming_cost_label || "Cost Event"}</span>
                              <span className="hpp-cost-value">{formatMoney(row.incoming_unit_cost)}</span>
                            </td>
                            <td className="num">{formatMoney(row.hpp_before)}</td>
                            <td className="num">{formatMoney(row.hpp_after)}</td>
                            <td>{row.status || "—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
