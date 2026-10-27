import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, X } from "lucide-react";
import api from "../lib/api";
import { formatDate, formatTrackingPageDateTime } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import { warehouseLocationLabel } from "../lib/physicalStockView";
import {
  SSI_REASON_LABEL,
  resolveDapatDijualDisplay,
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

function DetailQty({ row, field, physicalPrimary }) {
  if (field.physical) {
    const present = physicalPrimary?.present;
    const qty = present ? physicalPrimary.qty : null;
    if (!present) {
      const text = physicalPrimary?.text || "Belum saldo";
      const badgeClass = text === "Belum lengkap"
        ? "stock-status-badge--incomplete"
        : "stock-status-badge--missing";
      return (
        <div className="ssi-detail-qty" data-testid={`ssi-detail-${field.key}`}>
          <span className={`stock-status-badge ${badgeClass}`}>{text}</span>
          <p className="muted small">
            {text === "Belum lengkap"
              ? "Ada gudang operasional yang belum saldo / belum valid."
              : "Belum ada saldo fisik untuk gudang/lingkup ini."}
          </p>
        </div>
      );
    }
    const neg = Number(qty) < 0;
    return (
      <div className={`ssi-detail-qty${neg ? " is-negative" : ""}`} data-testid={`ssi-detail-${field.key}`}>
        <strong className={`mono${neg ? " stock-book-negative" : ""}`}>{fmtQty(qty)}</strong>
        <span className="muted small">PCS</span>
        {physicalPrimary?.flag ? (
          <span className="stock-status-badge stock-status-badge--deficit">{physicalPrimary.flag}</span>
        ) : null}
      </div>
    );
  }
  if (field.contract === "dapat") {
    const disp = resolveDapatDijualDisplay(row, physicalPrimary);
    if (disp.mode === "unverified") {
      return (
        <div className="ssi-detail-qty" data-testid={`ssi-detail-${field.key}`}>
          <span className="stock-status-badge stock-status-badge--unverified">Belum terverifikasi</span>
          <p className="muted small" data-testid={`ssi-detail-reason-${field.key}`}>{disp.title}</p>
        </div>
      );
    }
    const neg = Number(disp.qty) < 0;
    return (
      <div className={`ssi-detail-qty${neg ? " is-negative" : ""}`} data-testid={`ssi-detail-${field.key}`}>
        <strong className={`mono${neg ? " stock-book-negative" : ""}`}>
          {disp.qty == null ? "—" : fmtQty(disp.qty)}
        </strong>
        <span className="muted small">PCS</span>
        {disp.mode === "indicative" ? (
          <span className="stock-status-badge stock-status-badge--indicative">Indikatif</span>
        ) : null}
        <p className="muted small" data-testid={`ssi-detail-reason-${field.key}`}>{disp.title}</p>
      </div>
    );
  }
  if (field.contract === "polos") {
    const disp = resolvePolosDisplay(row, physicalPrimary);
    if (disp.mode === "unverified") {
      return (
        <div className="ssi-detail-qty" data-testid={`ssi-detail-${field.key}`}>
          <span className="stock-status-badge stock-status-badge--unverified">Belum terverifikasi</span>
          <p className="muted small" data-testid={`ssi-detail-reason-${field.key}`}>{disp.title}</p>
        </div>
      );
    }
    const neg = Number(disp.qty) < 0;
    return (
      <div className={`ssi-detail-qty${neg ? " is-negative" : ""}`} data-testid={`ssi-detail-${field.key}`}>
        <strong className={`mono${neg ? " stock-book-negative" : ""}`}>{fmtQty(disp.qty)}</strong>
        <span className="muted small">PCS</span>
        <span className="stock-status-badge stock-status-badge--indicative">Indikatif</span>
        <p className="muted small" data-testid={`ssi-detail-reason-${field.key}`}>{disp.title}</p>
      </div>
    );
  }
  const qty = row?.[field.key];
  const status = fieldStatus(row, field);
  if (qty == null || status === "BELUM_TERSEDIA" || status === "UNVERIFIED") {
    const reason = fieldReason(row, field);
    return (
      <div className="ssi-detail-qty" data-testid={`ssi-detail-${field.key}`}>
        <span className="stock-status-badge stock-status-badge--unverified">Belum terverifikasi</span>
        {reason ? <p className="muted small" data-testid={`ssi-detail-reason-${field.key}`}>{reason}</p> : null}
      </div>
    );
  }
  if (status === "INDICATIVE") {
    const neg = Number(qty) < 0;
    const reason = fieldReason(row, field);
    return (
      <div className={`ssi-detail-qty${neg ? " is-negative" : ""}`} data-testid={`ssi-detail-${field.key}`}>
        <strong className={`mono${neg ? " stock-book-negative" : ""}`}>{fmtQty(qty)}</strong>
        <span className="muted small">PCS</span>
        <span className="stock-status-badge stock-status-badge--indicative">Indikatif</span>
        {reason ? <p className="muted small" data-testid={`ssi-detail-reason-${field.key}`}>{reason}</p> : null}
      </div>
    );
  }
  const neg = Number(qty) < 0;
  return (
    <div className={`ssi-detail-qty${neg ? " is-negative" : ""}`} data-testid={`ssi-detail-${field.key}`}>
      <strong className={`mono${neg ? " stock-book-negative" : ""}`}>{fmtQty(qty)}</strong>
      <span className="muted small">PCS</span>
    </div>
  );
}

export default function StockSaatIniDetailDrawer({
  item,
  physicalPrimary,
  branches,
  branchId,
  warehouseId,
  canAdjust,
  canOpening,
  onClose,
  onOpenOpening,
  onOpenAdjust,
  adjustBlockedReason,
}) {
  const [lane, setLane] = useState("SEMUA");
  const [activity, setActivity] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const loadActivity = useCallback(async () => {
    if (!item?.master_item_id) return;
    setLoading(true);
    setErr("");
    try {
      const params = { lane, limit: 100 };
      if (branchId) params.branch_id = branchId;
      if (warehouseId) params.warehouse_id = warehouseId;
      const res = await api.ssiItemActivity(item.master_item_id, params);
      setActivity(res);
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat riwayat"));
      setActivity(null);
    } finally {
      setLoading(false);
    }
  }, [item?.master_item_id, lane, branchId, warehouseId]);

  useEffect(() => { loadActivity(); }, [loadActivity]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!item) return null;
  const whBalances = Array.isArray(item.warehouse_balances) ? item.warehouse_balances : [];

  return (
    <div className="ssi-detail-overlay" data-testid="ssi-detail-drawer" role="dialog" aria-modal="true">
      <button type="button" className="ssi-detail-backdrop" aria-label="Tutup detail" onClick={onClose} />
      <aside className="ssi-detail-panel" data-testid="ssi-detail-panel">
        <header className="ssi-detail-head">
          <div>
            <h2 data-testid="ssi-detail-title">{item.item_name || "—"}</h2>
            <p className="muted small mono" data-testid="ssi-detail-code">{item.item_code || "—"} · PCS</p>
          </div>
          <button type="button" className="ghost sm" onClick={onClose} aria-label="Tutup" data-testid="ssi-detail-close">
            <X size={16} />
          </button>
        </header>

        <div className="ssi-detail-actions">
          {canOpening && (
            <button
              type="button"
              className="stock-btn-outline"
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
              className="stock-btn-outline"
              data-testid="ssi-detail-adjust"
              onClick={() => onOpenAdjust?.(item)}
            >
              Koreksi Stok
            </button>
          )}
          <Link
            className="stock-btn-outline"
            to={`/persediaan/master-barang-jasa/${encodeURIComponent(item.master_item_id)}?tab=mutasi`}
            data-testid="ssi-detail-mutasi-link"
          >
            Kartu Mutasi
          </Link>
        </div>

        <section className="ssi-detail-section" data-testid="ssi-detail-excel">
          <h3>Sembilan kolom Excel Owner</h3>
          <div className="ssi-detail-excel-grid">
            <div className="ssi-detail-excel-row">
              <span>Produk</span>
              <div>
                <strong>{item.item_name || "—"}</strong>
                <div className="muted small mono">{item.item_code || "—"}</div>
              </div>
            </div>
            {EXCEL_DETAIL_FIELDS.map((field) => (
              <div key={field.key} className="ssi-detail-excel-row">
                <span>{field.label}</span>
                <DetailQty row={item} field={field} physicalPrimary={physicalPrimary} />
              </div>
            ))}
          </div>
        </section>

        <section className="ssi-detail-section" data-testid="ssi-detail-warehouses">
          <h3>Rincian gudang</h3>
          {whBalances.length === 0 ? (
            <p className="muted small">Tidak ada rincian gudang pada lingkup ini.</p>
          ) : (
            <table className="ssi-detail-wh-table">
              <thead>
                <tr>
                  <th>Gudang</th>
                  <th className="num">Stock</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {whBalances.map((wb) => (
                  <tr key={wb.warehouse_id} data-testid={`ssi-detail-wh-${wb.warehouse_id}`}>
                    <td className="mono">{wb.warehouse_code || wb.warehouse_name || wb.warehouse_id}</td>
                    <td className={`num mono${wb.saldo_buku_negatif ? " stock-book-negative" : ""}`}>
                      {wb.balance_present ? (
                        fmtQty(wb.physical_qty)
                      ) : (
                        <span className="stock-status-badge stock-status-badge--missing">Belum saldo</span>
                      )}
                    </td>
                    <td className="small">
                      {wb.balance_present
                        ? (wb.baseline_status || "—")
                        : "Belum ada saldo fisik"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="ssi-detail-section" data-testid="ssi-detail-history">
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
              {branchId ? ` · cabang aktif` : ""}
              {warehouseId ? ` · ${warehouseLocationLabel({ id: warehouseId }, branches)}` : ""}
            </p>
          ) : null}
        </section>
      </aside>
    </div>
  );
}
