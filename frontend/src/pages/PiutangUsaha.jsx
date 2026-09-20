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

const todayIso = () => new Date().toISOString().slice(0, 10);
const monthStart = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
};

const TABS = [
  { id: "ringkasan", label: "Ringkasan" },
  { id: "aging", label: "Aging" },
  { id: "customers", label: "Per Pelanggan" },
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

export default function PiutangUsaha() {
  const [tab, setTab] = useState("ringkasan");
  const [agingRef, setAgingRef] = useState(todayIso);
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery } = useDebouncedLiveSearch();
  const [bucket, setBucket] = useState("");
  const [arAccountId, setArAccountId] = useState("");
  const [sales, setSales] = useState("");
  const [customerKey, setCustomerKey] = useState("");
  const [startDate, setStartDate] = useState(monthStart);
  const [endDate, setEndDate] = useState(todayIso);

  const [summary, setSummary] = useState(null);
  const [aging, setAging] = useState(null);
  const [customers, setCustomers] = useState(null);
  const [statement, setStatement] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");

  const commonParams = useMemo(() => ({
    aging_reference_date: agingRef || undefined,
    q: q || undefined,
    sales: sales || undefined,
    ar_account_id: arAccountId || undefined,
    aging_bucket: bucket || undefined,
  }), [agingRef, q, sales, arAccountId, bucket]);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      if (tab === "ringkasan") {
        setSummary(await api.accountsReceivableSummary(commonParams));
      } else if (tab === "aging") {
        const [agingRes, summaryRes] = await Promise.all([
          api.accountsReceivableAging(commonParams),
          api.accountsReceivableSummary(commonParams),
        ]);
        setAging(agingRes);
        setSummary(summaryRes);
      } else if (tab === "customers") {
        setCustomers(await api.accountsReceivableCustomers(commonParams));
      } else if (tab === "statement") {
        setCustomers(await api.accountsReceivableCustomers(commonParams));
        if (!customerKey) {
          setStatement(null);
        } else {
          setStatement(await api.accountsReceivableStatement({
            customer_key: customerKey,
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
  }, [tab, commonParams, customerKey, startDate, endDate]);

  useEffect(() => { load(); }, [load]);

  const accountOptions = useMemo(() => {
    const rows = Array.isArray(summary?.account_breakdown) ? summary.account_breakdown : [];
    return rows.map((a) => ({
      id: a.account_id,
      label: [a.account_code, a.account_name].filter(Boolean).join(" · ") || a.account_id,
    }));
  }, [summary]);

  const customerOptions = useMemo(() => {
    const rows = Array.isArray(customers?.items) ? customers.items : [];
    return rows.map((c) => ({
      key: c.customer_key,
      label: c.customer_name || c.customer_key || "—",
    }));
  }, [customers]);

  const openCustomerStatement = (key) => {
    setCustomerKey(key || "");
    setTab("statement");
  };

  const agingItems = Array.isArray(aging?.items) ? aging.items : [];
  const customerItems = Array.isArray(customers?.items) ? customers.items : [];
  const statementItems = Array.isArray(statement?.items) ? statement.items : [];
  const pendingReceipts = Array.isArray(statement?.pending_receipts) ? statement.pending_receipts : [];
  const breakdown = Array.isArray(summary?.account_breakdown) ? summary.account_breakdown : [];

  return (
    <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="piutang-usaha-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Buku Besar</p>
          <h1 data-testid="piutang-usaha-title">Piutang Usaha</h1>
          <p className="muted">Piutang accounting dari jurnal posted. Operational outstanding tetap ditampilkan terpisah.</p>
        </div>
      </div>

      {loadError && (
        <div className="form-error" role="alert">
          <AlertCircle size={16} /> {loadError}
        </div>
      )}

      <div className="kb-toolbar kb-gl-toolbar" data-testid="piutang-usaha-toolbar">
        <div className="kb-gl-filters">
          <KbField label="Tanggal Acuan Aging" className="kb-gl-start">
            <input
              type="date"
              value={agingRef}
              onChange={(e) => setAgingRef(e.target.value)}
              data-testid="piutang-aging-reference-date"
            />
          </KbField>
          <KbField label="Cari" className="kb-gl-search">
            <KbSearchField
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              onSubmit={doSearch}
              onClear={clearQuery}
              placeholder="Cari faktur / pelanggan / sales…"
              testid="piutang-search"
              ariaLabel="Cari faktur atau pelanggan"
            />
          </KbField>
          <KbField label="Bucket" className="kb-gl-source">
            <select value={bucket} onChange={(e) => setBucket(e.target.value)} data-testid="piutang-bucket">
              {BUCKET_OPTIONS.map((o) => (
                <option key={o.value || "all"} value={o.value}>{o.label}</option>
              ))}
            </select>
          </KbField>
          <KbField label="Akun AR" className="kb-gl-account">
            <select value={arAccountId} onChange={(e) => setArAccountId(e.target.value)} data-testid="piutang-ar-account">
              <option value="">Semua akun AR</option>
              {accountOptions.map((a) => (
                <option key={a.id} value={a.id}>{a.label}</option>
              ))}
            </select>
          </KbField>
          <KbField label="Sales" className="kb-gl-source">
            <input
              value={sales}
              onChange={(e) => setSales(e.target.value)}
              placeholder="Nama sales"
              data-testid="piutang-sales"
            />
          </KbField>
        </div>
      </div>

      <div className="kb-ar-tabs" role="tablist" aria-label="Piutang Usaha" data-testid="piutang-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            className={"kb-ar-tab" + (tab === t.id ? " is-active" : "")}
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            data-testid={`piutang-tab-${t.id}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab !== "statement" && (
        <p className="kb-ar-note" data-testid="piutang-aging-disclaimer">
          Piutang Accounting dihitung dari jurnal yang sudah diposting. Tanggal Acuan Aging hanya menentukan umur piutang, bukan historical accounting as-of.
        </p>
      )}

      {loading && <p className="muted">Memuat…</p>}

      {tab === "ringkasan" && summary && (
        <div data-testid="piutang-ringkasan">
          <div className="kb-summary-strip" data-testid="piutang-summary-cards">
            <div className="kb-summary-card kb-summary-emphasis">
              <span>Accounting AR</span>
              <strong className="num kb-money">{formatRp(summary.total_accounting_ar)}</strong>
            </div>
            <div className="kb-summary-card">
              <span>Operational Outstanding</span>
              <strong className="num kb-money">{formatRp(summary.total_operational_outstanding)}</strong>
            </div>
            <div className="kb-summary-card">
              <span>Pending Receipt</span>
              <strong className="num kb-money">{formatRp(summary.total_pending_receipts)}</strong>
            </div>
            <div className="kb-summary-card">
              <span>Overdue</span>
              <strong className="num kb-money">{formatRp(
                Number(summary.d1_30 || 0) + Number(summary.d31_60 || 0) + Number(summary.d61_90 || 0) + Number(summary.over_90 || 0)
              )}</strong>
            </div>
          </div>
          <div className="kb-summary-strip kb-ar-aging-strip" data-testid="piutang-aging-cards">
            <div className="kb-summary-card"><span>Not Due</span><strong className="num kb-money">{formatRp(summary.not_due)}</strong></div>
            <div className="kb-summary-card"><span>1–30</span><strong className="num kb-money">{formatRp(summary.d1_30)}</strong></div>
            <div className="kb-summary-card"><span>31–60</span><strong className="num kb-money">{formatRp(summary.d31_60)}</strong></div>
            <div className="kb-summary-card"><span>61–90</span><strong className="num kb-money">{formatRp(summary.d61_90)}</strong></div>
            <div className="kb-summary-card"><span>&gt;90</span><strong className="num kb-money">{formatRp(summary.over_90)}</strong></div>
          </div>

          <h2 className="muted" style={{ fontSize: 13, margin: "8px 0" }}>Rekonsiliasi GL</h2>
          <div className="prod-table-wrap kb-workspace-table-wrap kb-ar-table-scroll">
            <table className="prod-table kas-bank-table" data-testid="piutang-gl-table">
              <thead>
                <tr>
                  <th>Akun</th>
                  <th className="num">Sales AR</th>
                  <th className="num">GL Balance</th>
                  <th className="num">Selisih non-Faktur / jurnal lain</th>
                </tr>
              </thead>
              <tbody>
                {breakdown.length === 0 ? (
                  <tr><td colSpan={4} className="muted">Tidak ada akun AREC untuk direkonsiliasi.</td></tr>
                ) : breakdown.map((row) => (
                  <tr key={row.account_id}>
                    <td>{[row.account_code, row.account_name].filter(Boolean).join(" · ")}</td>
                    <td className="num kb-money">{formatRp(row.sales_ar_balance)}</td>
                    <td className="num kb-money">{formatRp(row.gl_arec_balance)}</td>
                    <td className="num kb-money">{formatRp(row.difference)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "aging" && aging && (
        <div data-testid="piutang-aging">
          <div className="prod-table-wrap kb-workspace-table-wrap kb-ar-table-scroll">
            <table className="prod-table kas-bank-table" data-testid="piutang-aging-table">
              <thead>
                <tr>
                  <th>Invoice</th>
                  <th>Customer</th>
                  <th>Tanggal Faktur</th>
                  <th>Jatuh Tempo</th>
                  <th className="num">Days</th>
                  <th>Bucket</th>
                  <th>AR Account</th>
                  <th className="num">Original AR</th>
                  <th className="num">Posted Receipt</th>
                  <th className="num">Pending</th>
                  <th className="num">Accounting Outstanding</th>
                  <th className="num">Operational Outstanding</th>
                </tr>
              </thead>
              <tbody>
                {agingItems.length === 0 ? (
                  <tr><td colSpan={12} className="muted" data-testid="piutang-aging-empty">Tidak ada piutang accounting outstanding.</td></tr>
                ) : agingItems.map((row) => (
                  <tr key={row.invoice_id} data-testid={`piutang-aging-row-${row.invoice_id}`}>
                    <td className="mono">{row.invoice_number}</td>
                    <td>{row.customer_name}{row.customer_identity_ambiguous ? " (ambigu)" : ""}</td>
                    <td>{row.invoice_date || "—"}</td>
                    <td>{row.due_date || "—"}{row.due_date_fallback ? " *" : ""}</td>
                    <td className="num">{row.days_overdue ?? "—"}</td>
                    <td>{bucketLabel(row.aging_bucket)}</td>
                    <td>{[row.actual_ar_account_code, row.actual_ar_account_name].filter(Boolean).join(" · ") || "—"}</td>
                    <td className="num kb-money">{formatRp(row.original_ar_amount)}</td>
                    <td className="num kb-money">{formatRp(row.posted_receipt_amount)}</td>
                    <td className="num kb-money">{formatRp(row.pending_receipt_amount)}</td>
                    <td className="num kb-money">{formatRp(row.accounting_outstanding)}</td>
                    <td className="num kb-money">{formatRp(row.operational_outstanding)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "customers" && customers && (
        <div data-testid="piutang-customers">
          <div className="prod-table-wrap kb-workspace-table-wrap kb-ar-table-scroll">
            <table className="prod-table kas-bank-table" data-testid="piutang-customers-table">
              <thead>
                <tr>
                  <th>Customer</th>
                  <th className="num">Accounting AR</th>
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
                {customerItems.length === 0 ? (
                  <tr><td colSpan={11} className="muted">Tidak ada saldo pelanggan.</td></tr>
                ) : customerItems.map((row) => (
                  <tr key={row.customer_key || row.customer_name}>
                    <td>
                      <button
                        type="button"
                        className="linkish"
                        onClick={() => openCustomerStatement(row.customer_key)}
                        data-testid={`piutang-customer-${row.customer_key}`}
                      >
                        {row.customer_name || row.customer_key || "—"}
                        {row.ambiguous ? " (ambigu)" : ""}
                      </button>
                    </td>
                    <td className="num kb-money">{formatRp(row.accounting_outstanding)}</td>
                    <td className="num kb-money">{formatRp(row.pending_receipt_amount)}</td>
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
        <div data-testid="piutang-statement">
          <p className="kb-ar-note" data-testid="piutang-statement-disclaimer">
            Statement mengikuti tanggal jurnal. Riwayat mengikuti tanggal jurnal. Sistem belum menyediakan historical accounting as-of sebelum reversal yang dibukukan pada tanggal asli.
          </p>
          <div className="kb-toolbar kb-gl-toolbar">
            <div className="kb-gl-filters">
              <KbField label="Pelanggan" required className="kb-gl-account">
                <select
                  value={customerKey}
                  onChange={(e) => setCustomerKey(e.target.value)}
                  data-testid="piutang-statement-customer"
                >
                  <option value="">— Pilih pelanggan —</option>
                  {customerOptions.map((c) => (
                    <option key={c.key} value={c.key}>{c.label}</option>
                  ))}
                </select>
              </KbField>
              <KbField label="Dari Tanggal" required className="kb-gl-start">
                <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} data-testid="piutang-statement-start" />
              </KbField>
              <KbField label="Sampai Tanggal" required className="kb-gl-end">
                <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} data-testid="piutang-statement-end" />
              </KbField>
            </div>
          </div>

          {!customerKey && <p className="muted">Pilih pelanggan untuk melihat statement.</p>}

          {statement && (
            <>
              <div className="kb-summary-strip" data-testid="piutang-statement-summary">
                <div className="kb-summary-card"><span>Opening</span><strong className="num kb-money">{formatRp(statement.opening_balance)}</strong></div>
                <div className="kb-summary-card"><span>Debit</span><strong className="num kb-money">{formatRp(statement.period_debit)}</strong></div>
                <div className="kb-summary-card"><span>Credit</span><strong className="num kb-money">{formatRp(statement.period_credit)}</strong></div>
                <div className="kb-summary-card kb-summary-emphasis"><span>Ending</span><strong className="num kb-money">{formatRp(statement.ending_balance)}</strong></div>
              </div>
              <div className="prod-table-wrap kb-workspace-table-wrap kb-ar-table-scroll">
                <table className="prod-table kas-bank-table" data-testid="piutang-statement-table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Journal</th>
                      <th>Type</th>
                      <th>Reference</th>
                      <th>Description</th>
                      <th className="num">Debit AR</th>
                      <th className="num">Credit AR</th>
                      <th className="num">Running Balance</th>
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
                        <td className="num kb-money">{formatRp(row.debit)}</td>
                        <td className="num kb-money">{formatRp(row.credit)}</td>
                        <td className="num kb-money">{formatRp(row.running_balance)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <h2 className="muted" style={{ fontSize: 13, margin: "12px 0 6px" }}>Pending receipts not yet posted</h2>
              <div className="prod-table-wrap kb-workspace-table-wrap kb-ar-table-scroll">
                <table className="prod-table kas-bank-table" data-testid="piutang-pending-receipts">
                  <thead>
                    <tr>
                      <th>Receipt</th>
                      <th>Tanggal</th>
                      <th>Invoice</th>
                      <th className="num">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pendingReceipts.length === 0 ? (
                      <tr><td colSpan={4} className="muted">Tidak ada penerimaan pending.</td></tr>
                    ) : pendingReceipts.map((row) => (
                      <tr key={row.receipt_id}>
                        <td className="mono">{row.receipt_number || row.receipt_id}</td>
                        <td>{row.receipt_date || "—"}</td>
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
