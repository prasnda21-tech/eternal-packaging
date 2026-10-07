import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { moneyFormat } from "../lib/hrPayroll";
import "./PayrollReconciliation.css";

const STATUS_CLASS = {
  BELUM_FINAL: "pr-badge-muted",
  FINAL_BELUM_JURNAL: "pr-badge-warn",
  BELUM_ADA_PAYMENT: "pr-badge-warn",
  MENUNGGU_PERSETUJUAN: "pr-badge-info",
  SIAP_DIPOSTING: "pr-badge-info",
  SESUAI: "pr-badge-ok",
  SESUAI_TANPA_PEMBAYARAN: "pr-badge-ok",
  MISMATCH_NOMINAL: "pr-badge-bad",
  MISSING_BANK_JOURNAL: "pr-badge-bad",
};

const FILTER_OPTIONS = [
  { value: "", label: "Semua status" },
  { value: "BELUM_FINAL", label: "Belum Final" },
  { value: "FINAL_BELUM_JURNAL", label: "Final, Jurnal Belum Ada" },
  { value: "BELUM_ADA_PAYMENT", label: "Belum Dibayar" },
  { value: "MENUNGGU_PERSETUJUAN", label: "Menunggu Persetujuan" },
  { value: "SIAP_DIPOSTING", label: "Siap Diposting" },
  { value: "SESUAI", label: "Sesuai" },
  { value: "SESUAI_TANPA_PEMBAYARAN", label: "Selesai Tanpa Pembayaran" },
  { value: "MISMATCH_NOMINAL", label: "Nominal Tidak Sesuai" },
  { value: "MISSING_BANK_JOURNAL", label: "Jurnal Bank Bermasalah" },
];

const SCOPE_OPTIONS = [
  { value: "", label: "Semua jenis" },
  { value: "monthly", label: "Bulanan" },
  { value: "piece", label: "Borongan" },
];

function errText(ex) {
  const det = ex?.response?.data?.detail;
  if (typeof det === "string") return det;
  return ex?.message || "Gagal memuat rekonsiliasi";
}

function StatusBadge({ code, label }) {
  const cls = STATUS_CLASS[code] || "pr-badge-muted";
  return (
    <span className={`pr-badge ${cls}`} data-testid="pr-status-badge" data-status={code}>
      {label || code}
    </span>
  );
}

function RuleRow({ rule }) {
  const left = rule.left != null ? moneyFormat(rule.left) : "—";
  const right = rule.right != null ? moneyFormat(rule.right) : "—";
  const showAmounts = rule.left != null || rule.right != null;
  return (
    <li className={rule.ok ? "pr-rule-ok" : "pr-rule-bad"} data-testid={`pr-rule-${rule.code}`}>
      <span className="pr-rule-mark">{rule.ok ? "✓" : "✗"}</span>
      <span className="pr-rule-label">{rule.label}</span>
      {showAmounts && (
        <span className="pr-rule-amounts">
          {left} = {right}
        </span>
      )}
      {!showAmounts && rule.detail ? <span className="pr-rule-detail muted">{rule.detail}</span> : null}
    </li>
  );
}

function DetailDrawer({ item, onClose, canMutasi, canGl }) {
  if (!item) return null;
  const links = item.links || {};
  return (
    <div className="drawer pr-drawer" role="dialog" aria-modal="true" data-testid="pr-detail-drawer" onClick={onClose}>
      <div className="drawer-panel pr-drawer-panel" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head">
          <div>
            <h3>Rekonsiliasi Payroll</h3>
            <p className="muted small">{item.period_label}</p>
          </div>
          <button type="button" className="ghost" onClick={onClose} data-testid="pr-drawer-close">Tutup</button>
        </header>
        <div className="drawer-body pr-drawer-body">
          <section className="pr-section" data-testid="pr-section-payroll">
            <h4>PAYROLL</h4>
            <dl className="pr-dl">
              <div><dt>Periode</dt><dd>{item.period_number || item.period_id}</dd></div>
              <div><dt>Tanggal</dt><dd>{item.start_date} – {item.end_date}</dd></div>
              <div><dt>Jenis</dt><dd>{item.period_scope_label}</dd></div>
              <div><dt>Status</dt><dd>{item.payroll_status}</dd></div>
              <div><dt>Total Bersih</dt><dd>{moneyFormat(item.net_total)}</dd></div>
              <div><dt>Jumlah Baris</dt><dd>{item.line_count}</dd></div>
            </dl>
          </section>
          <section className="pr-section" data-testid="pr-section-accrual">
            <h4>AKRUAL</h4>
            <dl className="pr-dl">
              <div><dt>Jurnal</dt><dd>{item.accrual?.journal_number || item.accrual?.journal_id || "—"}</dd></div>
              <div><dt>Tanggal</dt><dd>{item.accrual?.date || "—"}</dd></div>
              <div><dt>Utang Gaji</dt><dd>{item.accrual?.payable_amount != null ? moneyFormat(item.accrual.payable_amount) : "—"}</dd></div>
              <div><dt>Status</dt><dd>{item.accrual?.posted ? "Posted" : "Belum ada"}</dd></div>
            </dl>
          </section>
          <section className="pr-section" data-testid="pr-section-payment">
            <h4>PAYMENT</h4>
            <dl className="pr-dl">
              <div><dt>Nomor</dt><dd>{item.payment?.payment_number || "—"}</dd></div>
              <div><dt>Status</dt><dd>{item.payment?.payment_status_label || item.payment?.status || "—"}</dd></div>
              <div><dt>Tanggal</dt><dd>{item.payment?.payment_date || "—"}</dd></div>
              <div><dt>Nominal</dt><dd>{item.payment?.total_amount != null ? moneyFormat(item.payment.total_amount) : "—"}</dd></div>
              <div><dt>Bank</dt><dd>{item.payment?.bank_name || "—"}</dd></div>
            </dl>
            {item.history_marker ? <p className="muted small" data-testid="pr-history-marker">{item.history_marker}</p> : null}
          </section>
          <section className="pr-section" data-testid="pr-section-bank">
            <h4>BANK / JOURNAL</h4>
            <dl className="pr-dl">
              <div><dt>Jurnal Payment</dt><dd>{item.payment_journal?.journal_number || item.payment_journal?.journal_id || "—"}</dd></div>
              <div><dt>Bank COA</dt><dd>{item.payment?.bank_coa_id || "—"}</dd></div>
              <div><dt>Debit Utang Gaji</dt><dd>{item.payment_journal?.payable_debit != null ? moneyFormat(item.payment_journal.payable_debit) : "—"}</dd></div>
              <div><dt>Kredit Bank</dt><dd>{item.payment_journal?.bank_credit != null ? moneyFormat(item.payment_journal.bank_credit) : "—"}</dd></div>
            </dl>
          </section>
          <section className="pr-section" data-testid="pr-section-rules">
            <h4>REKONSILIASI</h4>
            <div className="pr-status-line">
              <StatusBadge code={item.reconciliation_status} label={item.reconciliation_label} />
            </div>
            <ul className="pr-rules">
              {(item.rules || []).map((rule) => <RuleRow key={rule.code} rule={rule} />)}
            </ul>
            {(item.mismatches || []).length > 0 && (
              <p className="pr-mismatch-list" data-testid="pr-mismatch-list">
                Flag: {(item.mismatches || []).join(", ")}
              </p>
            )}
          </section>
          <section className="pr-actions" data-testid="pr-actions">
            <Link className="btn ghost" to={links.payroll || "/hr-payroll/penggajian"} data-testid="pr-link-payroll">Buka Payroll</Link>
            {item.payment?.id ? (
              <Link className="btn ghost" to={links.payment || links.payroll} data-testid="pr-link-payment">Buka Payment</Link>
            ) : null}
            {canMutasi ? (
              <Link className="btn ghost" to={links.mutasi || "/kas-bank/mutasi"} data-testid="pr-link-mutasi">Buka Mutasi</Link>
            ) : null}
            {canGl ? (
              <Link className="btn ghost" to={links.general_ledger || "/buku-besar/buku-besar"} data-testid="pr-link-gl">Buka GL</Link>
            ) : null}
          </section>
          <p className="muted small pr-readonly-note" data-testid="pr-readonly-note">
            Halaman ini hanya rekonsiliasi baca. Tidak ada aksi Create / Approve / Post / Reverse.
          </p>
        </div>
      </div>
    </div>
  );
}

export default function PayrollReconciliation() {
  const { hasPerm } = useAuth();
  const canMutasi = hasPerm("cash_bank.view") || hasPerm("nav.cash_bank.mutasi");
  const canGl = hasPerm("general_ledger.view") || hasPerm("nav.ledger.general_ledger");
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [scope, setScope] = useState("");
  const [reconStatus, setReconStatus] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.hrPayrollReconciliation({
        limit: 50,
        period_scope: scope || undefined,
        reconciliation_status: reconStatus || undefined,
      });
      setItems(Array.isArray(res?.items) ? res.items : []);
    } catch (ex) {
      setError(errText(ex));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [scope, reconStatus]);

  useEffect(() => { load(); }, [load]);

  const openDetail = async (periodId) => {
    setSelectedId(periodId);
    setDetailLoading(true);
    try {
      const res = await api.hrPayrollReconciliationDetail(periodId);
      setDetail(res?.item || null);
    } catch (ex) {
      setError(errText(ex));
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const selectedSummary = useMemo(
    () => items.find((row) => row.period_id === selectedId) || detail,
    [items, selectedId, detail],
  );

  return (
    <div className="prod-page pr-page" data-testid="payroll-reconciliation-page">
      <div className="page-head pr-head">
        <div>
          <h2>Payroll Rekonsiliasi</h2>
          <p className="muted small">Ringkasan baca: periode → jurnal akrual → payment → jurnal bank</p>
        </div>
        <div className="pr-filters" data-testid="pr-filters">
          <select value={scope} onChange={(e) => setScope(e.target.value)} data-testid="pr-filter-scope" aria-label="Jenis periode">
            {SCOPE_OPTIONS.map((opt) => <option key={opt.value || "all"} value={opt.value}>{opt.label}</option>)}
          </select>
          <select value={reconStatus} onChange={(e) => setReconStatus(e.target.value)} data-testid="pr-filter-status" aria-label="Status rekonsiliasi">
            {FILTER_OPTIONS.map((opt) => <option key={opt.value || "all"} value={opt.value}>{opt.label}</option>)}
          </select>
          <button type="button" className="ghost" onClick={load} data-testid="pr-refresh">Muat ulang</button>
        </div>
      </div>
      {error ? <div className="error" data-testid="pr-error">{error}</div> : null}
      <div className="table-wrap pr-table-wrap" data-testid="pr-table-wrap">
        <table className="ship-table pr-table" data-testid="pr-table">
          <thead>
            <tr>
              <th>Periode</th>
              <th>Jenis</th>
              <th>Total Bersih</th>
              <th>Payroll</th>
              <th>Jurnal</th>
              <th>Payment</th>
              <th>Bank</th>
              <th>Rekonsiliasi</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={9} className="muted">Memuat…</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={9} className="muted" data-testid="pr-empty">Tidak ada periode</td></tr>
            ) : items.map((row) => (
              <tr key={row.period_id} data-testid="pr-row" data-status={row.reconciliation_status}>
                <td>
                  <div className="pr-period-cell">
                    <strong>{row.period_number || row.period_id}</strong>
                    <span className="muted small">{row.start_date} – {row.end_date}</span>
                  </div>
                </td>
                <td>{row.period_scope_label}</td>
                <td>{moneyFormat(row.net_total)}</td>
                <td>{row.payroll_status}</td>
                <td>{row.accrual?.posted ? (row.accrual.journal_number || "OK") : "—"}</td>
                <td>{row.payment?.payment_status_label || row.payment?.status || "—"}</td>
                <td>{row.payment?.bank_name || "—"}</td>
                <td><StatusBadge code={row.reconciliation_status} label={row.reconciliation_label} /></td>
                <td>
                  <button
                    type="button"
                    className="linkish"
                    onClick={() => openDetail(row.period_id)}
                    data-testid="pr-open-detail"
                  >
                    Detail
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {detailLoading ? <p className="muted small" data-testid="pr-detail-loading">Memuat detail…</p> : null}
      {detail ? (
        <DetailDrawer
          item={detail || selectedSummary}
          onClose={() => { setDetail(null); setSelectedId(""); }}
          canMutasi={canMutasi}
          canGl={canGl}
        />
      ) : null}
    </div>
  );
}
