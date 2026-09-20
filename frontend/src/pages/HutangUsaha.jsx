import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle } from "lucide-react";
import api from "../lib/api";
import { useDebouncedLiveSearch } from "../hooks/useDebouncedLiveSearch";
import { KbField, KbSearchField } from "../components/CashBankFormPrimitives";

const errText = (ex) => {
  const det = ex?.response?.data?.detail;
  if (typeof det === "string") return det;
  if (det && typeof det === "object" && det.message) return det.message;
  return ex?.message || "Permintaan gagal";
};

const formatMoney = (n) => {
  const v = Number(n || 0);
  return v.toLocaleString("id-ID", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
};
const formatRp = (n) => `Rp ${formatMoney(n)}`;

const INTEGRITY_WARNING_LABELS = {
  PAYMENT_AP_ACCOUNT_MISMATCH: "Akun AP pembayaran tidak sesuai faktur",
  NEGATIVE_ACCOUNTING_AP: "Saldo Accounting AP negatif",
  OVER_APPLIED_ACCOUNTING_PAYMENT: "Pembayaran accounting melebihi nilai AP",
  MULTIPLE_INVOICE_APAY_ACCOUNTS: "Faktur menggunakan lebih dari satu akun AP",
  MULTIPLE_PAYMENT_APAY_ACCOUNTS: "Pembayaran menggunakan lebih dari satu akun AP",
  missing_purchase_invoice_document: "Jurnal faktur tidak memiliki dokumen Faktur Pembelian",
  missing_purchase_payment_document: "Jurnal pembayaran tidak memiliki dokumen Pembayaran Pembelian",
};

export function integrityWarningLabel(code) {
  return INTEGRITY_WARNING_LABELS[code] || String(code || "Anomali integritas");
}

export function wibCalendarDate(now = new Date()) {
  const instant = now instanceof Date ? now : new Date(now);
  const wib = new Date(instant.getTime() + 7 * 60 * 60 * 1000);
  const y = wib.getUTCFullYear();
  const m = String(wib.getUTCMonth() + 1).padStart(2, "0");
  const d = String(wib.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function wibMonthStart(now = new Date()) {
  return `${wibCalendarDate(now).slice(0, 7)}-01`;
}

export function IntegrityMarker({ warnings, testid }) {
  const codes = Array.isArray(warnings) ? warnings.filter(Boolean) : [];
  if (!codes.length) {
    return <span data-testid={testid}>—</span>;
  }
  const labels = codes.map(integrityWarningLabel);
  return (
    <span className="kb-ar-integrity" title={labels.join(" · ")} data-testid={testid}>
      Perhatian
    </span>
  );
}

const TABS = [
  { id: "ringkasan", label: "Ringkasan" },
  { id: "aging", label: "Aging" },
  { id: "suppliers", label: "Per Supplier" },
  { id: "statement", label: "Statement" },
];

const BUCKET_OPTIONS = [
  { value: "", label: "Semua bucket" },
  { value: "NOT_DUE", label: "Not Due" },
  { value: "1_30", label: "1–30" },
  { value: "31_60", label: "31–60" },
  { value: "61_90", label: "61–90" },
  { value: "OVER_90", label: ">90" },
];

const bucketLabel = (bucket) => ({
  NOT_DUE: "Not Due",
  "1_30": "1–30",
  "31_60": "31–60",
  "61_90": "61–90",
  OVER_90: ">90",
}[bucket] || bucket || "—");

export default function HutangUsaha() {
  const [tab, setTab] = useState("ringkasan");
  const [agingRef, setAgingRef] = useState(wibCalendarDate);
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery } = useDebouncedLiveSearch();
  const [bucket, setBucket] = useState("");
  const [apAccountId, setApAccountId] = useState("");
  const [supplierKey, setSupplierKey] = useState("");
  const [startDate, setStartDate] = useState(wibMonthStart);
  const [endDate, setEndDate] = useState(wibCalendarDate);

  const [summary, setSummary] = useState(null);
  const [aging, setAging] = useState(null);
  const [suppliers, setSuppliers] = useState(null);
  const [statement, setStatement] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");

  const commonParams = useMemo(() => ({
    aging_reference_date: agingRef || undefined,
    q: q || undefined,
    ap_account_id: apAccountId || undefined,
    aging_bucket: bucket || undefined,
  }), [agingRef, q, apAccountId, bucket]);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      if (tab === "ringkasan") {
        setSummary(await api.accountsPayableSummary(commonParams));
      } else if (tab === "aging") {
        const [agingRes, summaryRes] = await Promise.all([
          api.accountsPayableAging(commonParams),
          api.accountsPayableSummary(commonParams),
        ]);
        setAging(agingRes);
        setSummary(summaryRes);
      } else if (tab === "suppliers") {
        setSuppliers(await api.accountsPayableSuppliers(commonParams));
      } else if (tab === "statement") {
        setSuppliers(await api.accountsPayableSuppliers(commonParams));
        if (!supplierKey) {
          setStatement(null);
        } else {
          setStatement(await api.accountsPayableStatement({
            supplier_key: supplierKey,
            start_date: startDate,
            end_date: endDate,
          }));
        }
      }
    } catch (ex) {
      setLoadError(errText(ex));
    } finally {
      setLoading(false);
    }
  }, [tab, commonParams, supplierKey, startDate, endDate]);

  useEffect(() => { load(); }, [load]);

  const accountOptions = useMemo(() => {
    const rows = Array.isArray(summary?.account_breakdown) ? summary.account_breakdown : [];
    return rows.map((a) => ({
      id: a.account_id,
      label: [a.account_code, a.account_name].filter(Boolean).join(" · ") || a.account_id,
    }));
  }, [summary]);

  const supplierOptions = useMemo(() => {
    const rows = Array.isArray(suppliers?.items) ? suppliers.items : [];
    return rows.map((c) => ({
      key: c.supplier_key,
      label: c.supplier_name || c.supplier_key || "—",
    }));
  }, [suppliers]);

  const openSupplierStatement = (key) => {
    setSupplierKey(key || "");
    setTab("statement");
  };

  const agingItems = Array.isArray(aging?.items) ? aging.items : [];
  const supplierItems = Array.isArray(suppliers?.items) ? suppliers.items : [];
  const statementItems = Array.isArray(statement?.items) ? statement.items : [];
  const pendingPayments = Array.isArray(statement?.pending_payments) ? statement.pending_payments : [];
  const breakdown = Array.isArray(summary?.account_breakdown) ? summary.account_breakdown : [];
  const integrityAnomalies = Array.isArray(summary?.integrity_anomalies) ? summary.integrity_anomalies : [];
  const reconNote = summary?.reconciliation_note
    || "Rekonsiliasi GL menggunakan seluruh Hutang Accounting saat ini dan tidak dipersempit oleh filter supplier atau aging.";

  return (
    <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="hutang-usaha-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Buku Besar</p>
          <h1 data-testid="hutang-usaha-title">Hutang Usaha</h1>
          <p className="muted">Hutang accounting dari jurnal posted. Operational outstanding tetap ditampilkan terpisah.</p>
        </div>
      </div>

      {loadError && (
        <div className="form-error" role="alert">
          <AlertCircle size={16} /> {loadError}
        </div>
      )}

      <div className="kb-toolbar kb-gl-toolbar" data-testid="hutang-usaha-toolbar">
        <div className="kb-gl-filters">
          <KbField label="Tanggal Acuan Aging" className="kb-gl-start">
            <input
              type="date"
              value={agingRef}
              onChange={(e) => setAgingRef(e.target.value)}
              data-testid="hutang-aging-reference-date"
            />
          </KbField>
          <KbField label="Cari" className="kb-gl-search">
            <KbSearchField
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              onSubmit={doSearch}
              onClear={clearQuery}
              placeholder="Cari faktur / supplier…"
              testid="hutang-search"
              ariaLabel="Cari faktur atau supplier"
            />
          </KbField>
          <KbField label="Bucket" className="kb-gl-source">
            <select value={bucket} onChange={(e) => setBucket(e.target.value)} data-testid="hutang-bucket">
              {BUCKET_OPTIONS.map((o) => (
                <option key={o.value || "all"} value={o.value}>{o.label}</option>
              ))}
            </select>
          </KbField>
          <KbField label="Akun AP" className="kb-gl-account">
            <select value={apAccountId} onChange={(e) => setApAccountId(e.target.value)} data-testid="hutang-ap-account">
              <option value="">Semua akun AP</option>
              {accountOptions.map((a) => (
                <option key={a.id} value={a.id}>{a.label}</option>
              ))}
            </select>
          </KbField>
        </div>
      </div>

      <div className="kb-ar-tabs" role="tablist" aria-label="Hutang Usaha" data-testid="hutang-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            className={"kb-ar-tab" + (tab === t.id ? " is-active" : "")}
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            data-testid={`hutang-tab-${t.id}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab !== "statement" && (
        <p className="kb-ar-note" data-testid="hutang-aging-disclaimer">
          Hutang Accounting dihitung dari jurnal yang sudah diposting. Tanggal Acuan Aging hanya menentukan umur hutang, bukan historical accounting as-of.
        </p>
      )}

      {loading && <p className="muted">Memuat…</p>}

      {tab === "ringkasan" && summary && (
        <div data-testid="hutang-ringkasan">
          <div className="kb-summary-strip" data-testid="hutang-summary-cards">
            <div className="kb-summary-card kb-summary-emphasis">
              <span>Accounting AP</span>
              <strong className="num kb-money">{formatRp(summary.total_accounting_ap)}</strong>
            </div>
            <div className="kb-summary-card">
              <span>Operational Outstanding</span>
              <strong className="num kb-money">{formatRp(summary.total_operational_outstanding)}</strong>
            </div>
            <div className="kb-summary-card">
              <span>Pending Payment</span>
              <strong className="num kb-money">{formatRp(summary.total_pending_payments)}</strong>
            </div>
            <div className="kb-summary-card">
              <span>Overdue</span>
              <strong className="num kb-money">{formatRp(
                Number(summary.d1_30 || 0) + Number(summary.d31_60 || 0) + Number(summary.d61_90 || 0) + Number(summary.over_90 || 0)
              )}</strong>
            </div>
          </div>
          <div className="kb-summary-strip kb-ar-aging-strip" data-testid="hutang-aging-cards">
            <div className="kb-summary-card"><span>Not Due</span><strong className="num kb-money">{formatRp(summary.not_due)}</strong></div>
            <div className="kb-summary-card"><span>1–30</span><strong className="num kb-money">{formatRp(summary.d1_30)}</strong></div>
            <div className="kb-summary-card"><span>31–60</span><strong className="num kb-money">{formatRp(summary.d31_60)}</strong></div>
            <div className="kb-summary-card"><span>61–90</span><strong className="num kb-money">{formatRp(summary.d61_90)}</strong></div>
            <div className="kb-summary-card"><span>&gt;90</span><strong className="num kb-money">{formatRp(summary.over_90)}</strong></div>
          </div>

          {integrityAnomalies.length > 0 && (
            <div className="kb-ar-note" data-testid="hutang-integrity-panel" role="status">
              {integrityAnomalies.length} anomali integritas accounting terdeteksi.
              <ul data-testid="hutang-integrity-list" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {integrityAnomalies.map((item, idx) => (
                  <li key={`${item.code}-${item.invoice_id || item.journal_id || item.source_id || idx}`}>
                    {item.label || integrityWarningLabel(item.code)}
                    {item.invoice_number ? ` · ${item.invoice_number}` : ""}
                    {item.supplier_name ? ` · ${item.supplier_name}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <h2 className="muted" style={{ fontSize: 13, margin: "8px 0" }}>Rekonsiliasi GL</h2>
          <p className="kb-ar-note" data-testid="hutang-recon-scope">{reconNote}</p>
          <div className="prod-table-wrap kb-workspace-table-wrap kb-ar-table-scroll">
            <table className="prod-table kas-bank-table" data-testid="hutang-gl-table">
              <thead>
                <tr>
                  <th>Akun</th>
                  <th className="num">Purchase AP</th>
                  <th className="num">GL Balance</th>
                  <th className="num">Selisih non-Faktur / jurnal lain</th>
                </tr>
              </thead>
              <tbody>
                {breakdown.length === 0 ? (
                  <tr><td colSpan={4} className="muted">Tidak ada akun APAY untuk direkonsiliasi.</td></tr>
                ) : breakdown.map((row) => (
                  <tr key={row.account_id}>
                    <td>{[row.account_code, row.account_name].filter(Boolean).join(" · ")}</td>
                    <td className="num kb-money">{formatRp(row.purchase_ap_balance)}</td>
                    <td className="num kb-money">{formatRp(row.gl_apay_balance)}</td>
                    <td className="num kb-money">
                      {formatRp(row.difference)}
                      {row.difference_has_integrity_anomaly ? (
                        <span className="kb-ar-integrity" title="Selisih ini juga mengandung anomali integritas jurnal/akun."> · anomali</span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "aging" && aging && (
        <div data-testid="hutang-aging">
          <div className="prod-table-wrap kb-workspace-table-wrap kb-ar-table-scroll">
            <table className="prod-table kas-bank-table" data-testid="hutang-aging-table">
              <thead>
                <tr>
                  <th>Invoice</th>
                  <th>Supplier</th>
                  <th>Tanggal Faktur</th>
                  <th>Jatuh Tempo</th>
                  <th className="num">Days</th>
                  <th>Bucket</th>
                  <th>AP Account</th>
                  <th className="num">Original AP</th>
                  <th className="num">Posted Payment</th>
                  <th className="num">Pending</th>
                  <th className="num">Accounting Outstanding</th>
                  <th className="num">Operational Outstanding</th>
                  <th>Integritas</th>
                </tr>
              </thead>
              <tbody>
                {agingItems.length === 0 ? (
                  <tr><td colSpan={13} className="muted" data-testid="hutang-aging-empty">Tidak ada hutang accounting outstanding.</td></tr>
                ) : agingItems.map((row) => (
                  <tr key={row.invoice_id} data-testid={`hutang-aging-row-${row.invoice_id}`}>
                    <td className="mono">{row.invoice_number}</td>
                    <td>{row.supplier_name || "—"}</td>
                    <td>{row.invoice_date || "—"}</td>
                    <td>{row.due_date || "—"}{row.due_date_fallback ? " *" : ""}</td>
                    <td className="num">{row.days_overdue ?? "—"}</td>
                    <td>{bucketLabel(row.aging_bucket)}</td>
                    <td>{[row.actual_ap_account_code, row.actual_ap_account_name].filter(Boolean).join(" · ") || "—"}</td>
                    <td className="num kb-money">{formatRp(row.original_ap_amount)}</td>
                    <td className="num kb-money">{formatRp(row.posted_payment_amount)}</td>
                    <td className="num kb-money">{formatRp(row.pending_payment_amount)}</td>
                    <td className="num kb-money">{formatRp(row.accounting_outstanding)}</td>
                    <td className="num kb-money">{formatRp(row.operational_outstanding)}</td>
                    <td>
                      <IntegrityMarker
                        warnings={row.integrity_warnings}
                        testid={`hutang-aging-integrity-${row.invoice_id}`}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "suppliers" && suppliers && (
        <div data-testid="hutang-suppliers">
          <div className="prod-table-wrap kb-workspace-table-wrap kb-ar-table-scroll">
            <table className="prod-table kas-bank-table" data-testid="hutang-suppliers-table">
              <thead>
                <tr>
                  <th>Supplier</th>
                  <th className="num">Accounting AP</th>
                  <th className="num">Pending</th>
                  <th className="num">Operational</th>
                  <th className="num">Not Due</th>
                  <th className="num">1–30</th>
                  <th className="num">31–60</th>
                  <th className="num">61–90</th>
                  <th className="num">&gt;90</th>
                  <th>Oldest Due</th>
                  <th className="num">Invoices</th>
                </tr>
              </thead>
              <tbody>
                {supplierItems.length === 0 ? (
                  <tr><td colSpan={11} className="muted">Tidak ada saldo supplier.</td></tr>
                ) : supplierItems.map((row) => (
                  <tr key={row.supplier_key || row.supplier_name}>
                    <td>
                      <button
                        type="button"
                        className="linkish"
                        onClick={() => openSupplierStatement(row.supplier_key)}
                        data-testid={`hutang-supplier-${row.supplier_key}`}
                      >
                        {row.supplier_name || row.supplier_key || "—"}
                      </button>
                    </td>
                    <td className="num kb-money">{formatRp(row.accounting_outstanding)}</td>
                    <td className="num kb-money">{formatRp(row.pending_payment_amount)}</td>
                    <td className="num kb-money">{formatRp(row.operational_outstanding)}</td>
                    <td className="num kb-money">{formatRp(row.not_due)}</td>
                    <td className="num kb-money">{formatRp(row.d1_30)}</td>
                    <td className="num kb-money">{formatRp(row.d31_60)}</td>
                    <td className="num kb-money">{formatRp(row.d61_90)}</td>
                    <td className="num kb-money">{formatRp(row.over_90)}</td>
                    <td>{row.oldest_due_date || "—"}</td>
                    <td className="num">{row.invoice_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "statement" && (
        <div data-testid="hutang-statement">
          <p className="kb-ar-note" data-testid="hutang-statement-disclaimer">
            Statement mengikuti tanggal jurnal. Riwayat mengikuti tanggal jurnal. Sistem belum menyediakan historical accounting as-of sebelum reversal yang dibukukan pada tanggal asli.
          </p>
          <div className="kb-toolbar kb-gl-toolbar">
            <div className="kb-gl-filters">
              <KbField label="Supplier" required className="kb-gl-account">
                <select
                  value={supplierKey}
                  onChange={(e) => setSupplierKey(e.target.value)}
                  data-testid="hutang-statement-supplier"
                >
                  <option value="">— Pilih supplier —</option>
                  {supplierOptions.map((c) => (
                    <option key={c.key} value={c.key}>{c.label}</option>
                  ))}
                </select>
              </KbField>
              <KbField label="Dari Tanggal" required className="kb-gl-start">
                <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} data-testid="hutang-statement-start" />
              </KbField>
              <KbField label="Sampai Tanggal" required className="kb-gl-end">
                <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} data-testid="hutang-statement-end" />
              </KbField>
            </div>
          </div>

          {!supplierKey && <p className="muted">Pilih supplier untuk melihat statement.</p>}

          {statement && (
            <>
              <div className="kb-summary-strip" data-testid="hutang-statement-summary">
                <div className="kb-summary-card"><span>Opening</span><strong className="num kb-money">{formatRp(statement.opening_balance)}</strong></div>
                <div className="kb-summary-card"><span>AP Increase</span><strong className="num kb-money">{formatRp(statement.period_ap_increase)}</strong></div>
                <div className="kb-summary-card"><span>AP Decrease</span><strong className="num kb-money">{formatRp(statement.period_ap_decrease)}</strong></div>
                <div className="kb-summary-card kb-summary-emphasis"><span>Ending</span><strong className="num kb-money">{formatRp(statement.ending_balance)}</strong></div>
              </div>
              <div className="prod-table-wrap kb-workspace-table-wrap kb-ar-table-scroll">
                <table className="prod-table kas-bank-table" data-testid="hutang-statement-table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Journal</th>
                      <th>Type</th>
                      <th>Reference</th>
                      <th>Description</th>
                      <th className="num">AP Increase</th>
                      <th className="num">AP Decrease</th>
                      <th className="num">Running AP</th>
                    </tr>
                  </thead>
                  <tbody>
                    {statementItems.length === 0 ? (
                      <tr><td colSpan={8} className="muted">Tidak ada mutasi pada periode ini.</td></tr>
                    ) : statementItems.map((row) => (
                      <tr key={row.journal_id || `${row.date}-${row.journal_number}`}>
                        <td>{row.date || "—"}</td>
                        <td className="mono">{row.journal_number || "—"}</td>
                        <td>{row.source_label || row.source_type}</td>
                        <td>{row.reference || "—"}</td>
                        <td>{row.description || "—"}</td>
                        <td className="num kb-money">{formatRp(row.ap_increase)}</td>
                        <td className="num kb-money">{formatRp(row.ap_decrease)}</td>
                        <td className="num kb-money">{formatRp(row.running_ap)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <h2 className="muted" style={{ fontSize: 13, margin: "12px 0 6px" }}>Pending payments not yet posted</h2>
              <div className="prod-table-wrap kb-workspace-table-wrap kb-ar-table-scroll">
                <table className="prod-table kas-bank-table" data-testid="hutang-pending-payments">
                  <thead>
                    <tr>
                      <th>Payment</th>
                      <th>Tanggal</th>
                      <th>Invoice</th>
                      <th className="num">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pendingPayments.length === 0 ? (
                      <tr><td colSpan={4} className="muted">Tidak ada pembayaran pending.</td></tr>
                    ) : pendingPayments.map((row) => (
                      <tr key={row.payment_id}>
                        <td className="mono">{row.payment_number || row.payment_id}</td>
                        <td>{row.payment_date || "—"}</td>
                        <td>{row.invoice_number || row.invoice_id || "—"}</td>
                        <td className="num kb-money">{formatRp(row.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
