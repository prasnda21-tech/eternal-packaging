/**
 * Stock Saat Ini item detail — centered modal (SSI module only).
 * Filename kept for stable imports; not a right-side drawer.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, X } from "lucide-react";
import api from "../lib/api";
import { formatDate, formatTrackingPageDateTime } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import { warehouseLocationLabel } from "../lib/physicalStockView";
import {
  SSI_REASON_LABEL,
  resolveDapatDijualDisplay,
  resolvePhysicalDisplay,
  resolvePolosDisplay,
} from "../lib/ssiStatusDisplay";
import "./StockSaatIni.status.css";

const EXCEL_DETAIL_FIELDS = [
  { key: "saldo_awal", label: "Saldo Awal" },
  { key: "pembelian", label: "Pembelian" },
  { key: "stock", label: "Stock", physical: true },
  {
    key: "stock_dapat_dijual",
    label: "Stock Dapat Dijual",
    statusKey: "qty_siap_jual_status",
    reasonKey: "qty_siap_jual_reason",
    noteKey: "qty_siap_jual_note",
    contract: "dapat",
  },
  {
    key: "qty_tercetak",
    label: "Qty Tercetak",
    statusKey: "qty_tercetak_status",
    legacyStatusKey: "qty_sudah_cetak_gudang_status",
    reasonKey: "qty_tercetak_reason",
    legacyReasonKey: "qty_sudah_cetak_gudang_reason",
    noteKey: "qty_sudah_cetak_gudang_note",
  },
  {
    key: "qty_polos",
    label: "Qty Polos",
    statusKey: "qty_polos_status",
    legacyStatusKey: "qty_polos_gudang_status",
    reasonKey: "qty_polos_reason",
    legacyReasonKey: "qty_polos_gudang_reason",
    noteKey: "qty_polos_gudang_note",
    contract: "polos",
  },
  {
    key: "sisa_belum_cetak",
    label: "Sisa Belum Cetak",
    statusKey: "sisa_belum_cetak_status",
    reasonKey: "sisa_belum_cetak_reason",
    noteKey: "sisa_belum_cetak_note",
  },
  {
    key: "blank_open_commitment",
    label: "Komitmen Blank",
    statusKey: "blank_open_commitment_status",
    reasonKey: "blank_open_commitment_reason",
    noteKey: "blank_open_commitment_note",
  },
  {
    key: "stock_polos_dapat_dijual",
    label: "Stock Polos Dapat Dijual",
    statusKey: "stock_polos_dapat_dijual_status",
    reasonKey: "stock_polos_dapat_dijual_reason",
    noteKey: "stock_polos_dapat_dijual_note",
  },
  {
    key: "tercetak_belum_kirim",
    label: "Barang Tercetak Belum Kirim",
    statusKey: "tercetak_belum_kirim_status",
    reasonKey: "tercetak_belum_kirim_reason",
  },
  {
    key: "cetak_sudah_dikirim",
    label: "Barang Cetak Sudah Dikirim",
    statusKey: "cetak_sudah_dikirim_status",
    reasonKey: "cetak_sudah_dikirim_reason",
  },
];

const HISTORY_FILTERS = [
  { id: "SEMUA", label: "Semua" },
  { id: "FISIK", label: "Fisik" },
  { id: "SO", label: "SO" },
  { id: "CETAK", label: "Cetak" },
];

function nestedDialogOpen() {
  return Boolean(
    document.querySelector(
      '[data-testid="stock-adjust-modal"], [data-testid="saldo-awal-modal"], [data-testid="saldo-awal-dialog"], [data-testid="stock-opening-modal"]',
    ),
  );
}

function fieldStatus(row, field) {
  return String(
    (field.statusKey && row?.[field.statusKey])
    || (field.legacyStatusKey && row?.[field.legacyStatusKey])
    || "",
  ).toUpperCase();
}

function fieldReason(row, field) {
  const code = String(
    (field.reasonKey && row?.[field.reasonKey])
    || (field.legacyReasonKey && row?.[field.legacyReasonKey])
    || "",
  ).trim();
  const note = String((field.noteKey && row?.[field.noteKey]) || "").trim();
  return SSI_REASON_LABEL[code] || note || (code || "");
}

function DetailCell({ row, field, physicalPrimary }) {
  if (field.physical) {
    const phys = resolvePhysicalDisplay(physicalPrimary);
    const neg = Number(phys.qty) < 0;
    return {
      qtyNode: (
        <span
          className={`mono${neg ? " stock-book-negative" : ""}`}
          data-presentation-fallback={phys.presentationFallback ? "true" : undefined}
        >
          {fmtQty(phys.qty)}
        </span>
      ),
      note: "PCS",
      neg,
    };
  }
  if (field.contract === "dapat") {
    const disp = resolveDapatDijualDisplay(row, physicalPrimary);
    const neg = Number(disp.qty) < 0;
    return {
      qtyNode: (
        <span
          className={`ssi-doc-qty${neg ? " is-neg" : ""}`}
          data-presentation-fallback={disp.presentationFallback ? "true" : undefined}
        >
          <span className={`mono${neg ? " stock-book-negative" : ""}`}>
            {fmtQty(disp.qty)}
          </span>
        </span>
      ),
      note: disp.title || "PCS",
      neg,
    };
  }
  if (field.contract === "polos") {
    const disp = resolvePolosDisplay(row, physicalPrimary);
    const neg = Number(disp.qty) < 0;
    return {
      qtyNode: (
        <span
          className={`ssi-doc-qty${neg ? " is-neg" : ""}`}
          data-presentation-fallback={disp.presentationFallback ? "true" : undefined}
        >
          <span className={`mono${neg ? " stock-book-negative" : ""}`}>{fmtQty(disp.qty)}</span>
        </span>
      ),
      note: disp.title || "PCS",
      neg,
    };
  }
  const qty = row?.[field.key];
  const status = fieldStatus(row, field);
  if (qty == null || status === "BELUM_TERSEDIA" || status === "UNVERIFIED") {
    return {
      qtyNode: (
        <span className="mono" data-presentation-fallback="true">{fmtQty(0)}</span>
      ),
      note: fieldReason(row, field) || "PCS",
      neg: false,
    };
  }
  if (status === "INDICATIVE") {
    const neg = Number(qty) < 0;
    return {
      qtyNode: (
        <span className="ssi-doc-qty">
          <span className={`mono${neg ? " stock-book-negative" : ""}`}>{fmtQty(qty)}</span>
        </span>
      ),
      note: fieldReason(row, field) || "PCS",
      neg,
    };
  }
  const neg = Number(qty) < 0;
  return {
    qtyNode: <span className={`mono${neg ? " stock-book-negative" : ""}`}>{fmtQty(qty)}</span>,
    note: "PCS",
    neg,
  };
}

function focusableIn(root) {
  if (!root) return [];
  return Array.from(
    root.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  );
}

export default function StockSaatIniDetailDrawer({
  item,
  physicalPrimary,
  branches,
  branchId,
  warehouseId,
  scopeLabel,
  canAdjust,
  canOpening,
  canManageMinimum = false,
  onClose,
  onOpenOpening,
  onOpenAdjust,
  adjustBlockedReason,
  onMinimumSaved,
}) {
  const [lane, setLane] = useState("SEMUA");
  const [activity, setActivity] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [minimumQty, setMinimumQty] = useState("");
  const [minimumConfigured, setMinimumConfigured] = useState(false);
  const [minimumBusy, setMinimumBusy] = useState(false);
  const [minimumMsg, setMinimumMsg] = useState("");
  const [minimumErr, setMinimumErr] = useState("");
  const panelRef = useRef(null);
  const closeBtnRef = useRef(null);
  const itemId = item?.master_item_id || "";

  useEffect(() => {
    setLane("SEMUA");
    setActivity(null);
    setErr("");
    setLoading(Boolean(itemId));
    setMinimumMsg("");
    setMinimumErr("");
  }, [itemId]);

  useEffect(() => {
    let cancelled = false;
    if (!itemId || !branchId || !warehouseId) {
      setMinimumQty("");
      setMinimumConfigured(false);
      return undefined;
    }
    api.listStockMinimums({ branch_id: branchId, warehouse_id: warehouseId })
      .then((res) => {
        if (cancelled) return;
        const rows = Array.isArray(res?.items) ? res.items : [];
        const hit = rows.find((row) => String(row.master_item_id || "") === String(itemId));
        if (hit && hit.configured) {
          setMinimumConfigured(true);
          setMinimumQty(hit.minimum_qty === 0 || hit.minimum_qty === "0" ? "0" : String(hit.minimum_qty ?? ""));
        } else {
          setMinimumConfigured(false);
          setMinimumQty("");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMinimumConfigured(false);
          setMinimumQty("");
        }
      });
    return () => { cancelled = true; };
  }, [itemId, branchId, warehouseId]);

  const loadActivity = useCallback(async () => {
    if (!itemId) return;
    setLoading(true);
    setErr("");
    try {
      const params = { lane, limit: 100 };
      if (branchId) params.branch_id = branchId;
      if (warehouseId) params.warehouse_id = warehouseId;
      const res = await api.ssiItemActivity(itemId, params);
      setActivity(res);
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat riwayat"));
      setActivity(null);
    } finally {
      setLoading(false);
    }
  }, [itemId, lane, branchId, warehouseId]);

  useEffect(() => { loadActivity(); }, [loadActivity]);

  // Body scroll lock + focus trap + Escape (skip when nested dialogs are open).
  useEffect(() => {
    if (!itemId) return undefined;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const t = window.setTimeout(() => {
      closeBtnRef.current?.focus?.();
    }, 0);
    const onKey = (e) => {
      if (nestedDialogOpen()) return;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onClose?.();
        return;
      }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      const list = focusableIn(panel);
      if (!list.length) return;
      const first = list[0];
      const last = list[list.length - 1];
      if (e.shiftKey) {
        if (document.activeElement === first || !panel?.contains(document.activeElement)) {
          e.preventDefault();
          last.focus();
        }
      } else if (document.activeElement === last || !panel?.contains(document.activeElement)) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.clearTimeout(t);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [itemId, onClose]);

  if (!item) return null;
  const whBalances = Array.isArray(item.warehouse_balances) ? item.warehouse_balances : [];

  return (
    <div className="ssi-detail-modal-root" data-testid="ssi-detail-drawer" data-ssi-detail-mode="centered-modal">
      <button
        type="button"
        className="ssi-detail-modal-backdrop"
        aria-label="Tutup detail"
        data-testid="ssi-detail-backdrop"
        onMouseDown={(e) => {
          // Close on mousedown so the same click cannot fall through to the table row.
          e.preventDefault();
          e.stopPropagation();
          if (nestedDialogOpen()) return;
          onClose?.();
        }}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
      />
      <div
        ref={panelRef}
        className="ssi-detail-modal-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ssi-detail-title"
        data-testid="ssi-detail-panel"
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="ssi-detail-modal-head">
          <div className="ssi-detail-modal-head-text">
            <h2 id="ssi-detail-title" data-testid="ssi-detail-title">{item.item_name || "—"}</h2>
            <p className="muted small" data-testid="ssi-detail-code">
              <span className="mono">{item.item_code || "—"}</span>
              {" · PCS"}
              {scopeLabel ? (
                <>
                  {" · "}
                  <span data-testid="ssi-detail-scope">{scopeLabel}</span>
                </>
              ) : null}
            </p>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            className="ssi-detail-modal-close"
            onClick={onClose}
            aria-label="Tutup"
            data-testid="ssi-detail-close"
          >
            <X size={18} aria-hidden />
          </button>
        </header>

        <div className="ssi-detail-modal-actions">
          {canOpening && (
            <button
              type="button"
              className="stock-btn-outline sm"
              data-testid="ssi-detail-saldo-awal"
              disabled={!canAdjust}
              title={!canAdjust ? (adjustBlockedReason || "Saldo Awal belum tersedia") : undefined}
              onClick={() => onOpenOpening?.(item)}
            >
              Saldo Awal
            </button>
          )}
          {canAdjust && (
            <button
              type="button"
              className="stock-btn-outline sm"
              data-testid="ssi-detail-adjust"
              onClick={() => onOpenAdjust?.(item)}
            >
              Koreksi Stok
            </button>
          )}
          <Link
            className="stock-btn-outline sm"
            to={`/persediaan/master-barang-jasa/${encodeURIComponent(item.master_item_id)}?tab=mutasi`}
            data-testid="ssi-detail-mutasi-link"
          >
            Kartu Mutasi
          </Link>
        </div>

        <div className="ssi-detail-modal-body">
          <section className="ssi-item-detail-section" data-testid="ssi-detail-excel">
            <h3>Sembilan kolom Excel Owner</h3>
            <div className="ssi-doc-table-wrap">
              <table className="ssi-doc-table" data-testid="ssi-detail-excel-table">
                <thead>
                  <tr>
                    <th scope="col">Kolom</th>
                    <th scope="col" className="num">Kuantitas</th>
                    <th scope="col">Keterangan</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th scope="row">Produk</th>
                    <td colSpan={2}>
                      <strong>{item.item_name || "—"}</strong>
                      {" "}
                      <span className="muted small mono">{item.item_code || "—"}</span>
                    </td>
                  </tr>
                  {EXCEL_DETAIL_FIELDS.map((field) => {
                    const cell = DetailCell({ row: item, field, physicalPrimary });
                    return (
                      <tr key={field.key} data-testid={`ssi-detail-row-${field.key}`}>
                        <th scope="row">{field.label}</th>
                        <td
                          className={`num${cell.neg ? " is-negative" : ""}`}
                          data-testid={`ssi-detail-${field.key}`}
                        >
                          {cell.qtyNode}
                        </td>
                        <td className="small muted" data-testid={`ssi-detail-reason-${field.key}`}>
                          {cell.note || "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <section className="ssi-item-detail-section" data-testid="ssi-detail-minimum">
            <h3>Batas Stok Minimum</h3>
            {!warehouseId ? (
              <p className="muted small" data-testid="ssi-minimum-need-warehouse">
                Pilih satu gudang di filter Stock Saat Ini untuk mengatur minimum per barang × cabang × gudang.
              </p>
            ) : (
              <div className="ssi-minimum-form" data-testid="ssi-minimum-form">
                <p className="muted small">
                  Kosong = belum diatur. 0 = batas minimum sah. Hanya mengubah pengaturan; tidak memutasi stok.
                </p>
                <div className="ssi-minimum-row">
                  <label>
                    Batas minimum ({item.base_unit || "PCS"})
                    <input
                      type="number"
                      min="0"
                      step="any"
                      className="so-line-input"
                      value={minimumQty}
                      disabled={!canManageMinimum || minimumBusy}
                      placeholder="Belum diatur"
                      data-testid="ssi-minimum-input"
                      onChange={(e) => setMinimumQty(e.target.value)}
                    />
                  </label>
                  {canManageMinimum ? (
                    <div className="ssi-minimum-actions">
                      <button
                        type="button"
                        className="stock-btn-outline sm"
                        disabled={minimumBusy}
                        data-testid="ssi-minimum-save"
                        onClick={async () => {
                          setMinimumBusy(true);
                          setMinimumErr("");
                          setMinimumMsg("");
                          try {
                            const res = await api.upsertStockMinimum({
                              master_item_id: itemId,
                              branch_id: branchId,
                              warehouse_id: warehouseId,
                              minimum_qty: minimumQty,
                              unit: item.base_unit || "PCS",
                            });
                            const configured = Boolean(res?.minimum?.configured);
                            setMinimumConfigured(configured);
                            setMinimumQty(
                              configured
                                ? (res.minimum.minimum_qty === 0 ? "0" : String(res.minimum.minimum_qty ?? ""))
                                : "",
                            );
                            setMinimumMsg(configured ? "Minimum disimpan." : "Minimum dikosongkan.");
                            onMinimumSaved?.(res?.minimum);
                          } catch (ex) {
                            const det = ex.response?.data?.detail;
                            setMinimumErr(typeof det === "object" ? (det.message || JSON.stringify(det)) : (det || "Gagal menyimpan"));
                          } finally {
                            setMinimumBusy(false);
                          }
                        }}
                      >
                        Simpan
                      </button>
                      <button
                        type="button"
                        className="stock-btn-outline sm"
                        disabled={minimumBusy || !minimumConfigured}
                        data-testid="ssi-minimum-clear"
                        onClick={async () => {
                          setMinimumBusy(true);
                          setMinimumErr("");
                          setMinimumMsg("");
                          try {
                            await api.upsertStockMinimum({
                              master_item_id: itemId,
                              branch_id: branchId,
                              warehouse_id: warehouseId,
                              clear: true,
                              unit: item.base_unit || "PCS",
                            });
                            setMinimumConfigured(false);
                            setMinimumQty("");
                            setMinimumMsg("Minimum dikosongkan.");
                            onMinimumSaved?.(null);
                          } catch (ex) {
                            const det = ex.response?.data?.detail;
                            setMinimumErr(typeof det === "object" ? (det.message || JSON.stringify(det)) : (det || "Gagal mengosongkan"));
                          } finally {
                            setMinimumBusy(false);
                          }
                        }}
                      >
                        Kosongkan
                      </button>
                    </div>
                  ) : (
                    <p className="muted small" data-testid="ssi-minimum-readonly">
                      {minimumConfigured
                        ? `Minimum terpasang: ${minimumQty}`
                        : "Minimum belum diatur. Izin Atur Stok Minimum diperlukan untuk mengubah."}
                    </p>
                  )}
                </div>
                {minimumMsg ? <p className="muted small" data-testid="ssi-minimum-msg">{minimumMsg}</p> : null}
                {minimumErr ? <p className="stock-err small" data-testid="ssi-minimum-err">{minimumErr}</p> : null}
              </div>
            )}
          </section>

          <section className="ssi-item-detail-section" data-testid="ssi-detail-warehouses">
            <h3>Rincian gudang</h3>
            {whBalances.length === 0 ? (
              <p className="muted small">Tidak ada rincian gudang pada lingkup ini.</p>
            ) : (
              <div className="ssi-doc-table-wrap">
                <table className="ssi-doc-table ssi-doc-table--wh">
                  <thead>
                    <tr>
                      <th>Gudang</th>
                      <th className="num">Stock</th>
                      <th className="num">Stock Dapat Dijual</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {whBalances.map((wb) => (
                      <tr key={wb.warehouse_id} data-testid={`ssi-detail-wh-${wb.warehouse_id}`}>
                        <td className="mono">{wb.warehouse_code || wb.warehouse_name || wb.warehouse_id}</td>
                        <td
                          className={`num mono${wb.saldo_buku_negatif ? " stock-book-negative" : ""}`}
                          data-presentation-fallback={
                            !(wb.balance_present || wb.operational_qty_proven || wb.no_relevant_activity)
                              ? "true"
                              : undefined
                          }
                        >
                          {fmtQty(
                            (wb.balance_present || wb.operational_qty_proven || wb.no_relevant_activity)
                              ? (wb.physical_qty ?? 0)
                              : 0,
                          )}
                        </td>
                        <td
                          className={`num mono${Number(wb.stock_dapat_dijual) < 0 ? " stock-book-negative" : ""}`}
                          data-testid={`ssi-detail-wh-sellable-${wb.warehouse_id}`}
                          title={
                            wb.stock_dapat_dijual_status === "INCOMPLETE"
                              || wb.qty_polos_note
                              ? (wb.stock_dapat_dijual_provisional
                                ? "Provisional — alokasi belum lengkap"
                                : (wb.qty_polos_note || "INCOMPLETE"))
                              : undefined
                          }
                        >
                          {wb.stock_dapat_dijual == null
                            ? "—"
                            : fmtQty(wb.stock_dapat_dijual)}
                        </td>
                        <td className="small">
                          {(wb.balance_present || wb.operational_qty_proven || wb.no_relevant_activity)
                            ? (wb.baseline_status || "—")
                            : "Belum ada saldo fisik"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="ssi-item-detail-section" data-testid="ssi-detail-history">
            <div className="ssi-detail-history-head">
              <h3>Riwayat aktivitas</h3>
              <div className="ssi-history-filters" role="tablist" aria-label="Filter riwayat">
                {HISTORY_FILTERS.map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    role="tab"
                    aria-selected={lane === f.id}
                    className={lane === f.id ? "is-active" : ""}
                    data-testid={`ssi-history-filter-${f.id}`}
                    onClick={() => setLane(f.id)}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            </div>
            <p className="muted small">
              Fisik = mutasi stok. SO = komitmen penjualan (bukan stok). Cetak = status produksi / referensi SJ cetak (bukan pengurangan stok).
            </p>
            {activity?.history_incomplete || activity?.coverage?.limitation ? (
              <div className="signal-strip" data-testid="ssi-history-incomplete" role="status">
                <div>
                  <AlertCircle size={16} />
                  <span>
                    {activity?.coverage?.limitation
                      || "Riwayat mungkin tidak lengkap — hanya event berbukti yang ditampilkan."}
                  </span>
                </div>
              </div>
            ) : null}
            {err && (
              <div className="signal-strip" role="alert" data-testid="ssi-history-error">
                <div><AlertCircle size={16} /><span>{err}</span></div>
              </div>
            )}
            {loading ? (
              <p className="muted small" data-testid="ssi-history-loading">Memuat riwayat…</p>
            ) : (
              <ul className="ssi-history-list" data-testid="ssi-history-list">
                {(activity?.items || []).length === 0 ? (
                  <li className="muted small" data-testid="ssi-history-empty">Tidak ada event berbukti pada filter ini.</li>
                ) : (activity.items.map((ev, idx) => (
                  <li
                    key={`${ev.lane}-${ev.activity_code}-${ev.source_id || idx}-${ev.occurred_at || idx}`}
                    className="ssi-history-item"
                    data-lane={ev.lane}
                    data-testid={`ssi-history-row-${idx}`}
                    data-affects-physical={ev.affects_physical_stock ? "true" : "false"}
                  >
                    <div className="ssi-history-meta">
                      <span className={`ssi-lane-pill ssi-lane-pill--${String(ev.lane || "").toLowerCase()}`}>{ev.lane}</span>
                      <span className="muted small">
                        {ev.occurred_at
                          ? (formatTrackingPageDateTime(ev.occurred_at) || formatDate(ev.occurred_at))
                          : "Waktu tidak tercatat"}
                      </span>
                    </div>
                    <div className="ssi-history-body">
                      <strong>{ev.activity}</strong>
                      <div className="ssi-history-grid">
                        <span>Qty</span>
                        <span className="mono">{ev.qty_pcs == null ? "—" : `${fmtQty(ev.qty_pcs)} PCS`}</span>
                        <span>Metrik</span>
                        <span>{ev.metric_affected || "—"}</span>
                        <span>Dokumen</span>
                        <span>
                          {ev.source_path && ev.source_label ? (
                            <Link to={ev.source_path}>{ev.source_label}</Link>
                          ) : (ev.source_label || ev.source_id || "—")}
                        </span>
                        <span>Actor</span>
                        <span>{ev.actor_name || "Tidak tercatat"}</span>
                        {ev.before_after_supported ? (
                          <>
                            <span>Sebelum/Sesudah</span>
                            <span className="mono">
                              {ev.balance_before == null ? "—" : fmtQty(ev.balance_before)}
                              {" → "}
                              {ev.balance_after == null ? "—" : fmtQty(ev.balance_after)}
                            </span>
                          </>
                        ) : null}
                      </div>
                      {ev.notes ? <p className="muted small">{ev.notes}</p> : null}
                      {ev.affects_physical_stock ? null : (
                        <p className="muted small">Bukan pengurangan stok fisik.</p>
                      )}
                    </div>
                  </li>
                )))}
              </ul>
            )}
            {activity?.counts ? (
              <p className="muted small" data-testid="ssi-history-counts">
                Fisik {activity.counts.fisik ?? 0} · SO {activity.counts.so ?? 0} · Cetak {activity.counts.cetak ?? 0}
                {branchId ? " · cabang aktif" : ""}
                {warehouseId ? ` · ${warehouseLocationLabel({ id: warehouseId }, branches)}` : ""}
              </p>
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}
