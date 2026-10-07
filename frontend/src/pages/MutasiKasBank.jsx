import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AlertCircle, Eye } from "lucide-react";
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

const STATUS_META = {
  posted: { className: "kb-status-posted", label: "Posted" },
  reversed: { className: "kb-status-reversed", label: "Reversed" },
};

const SOURCE_OPTIONS = [
  { value: "", label: "Semua sumber" },
  { value: "cash_bank_receipt", label: "Penerimaan" },
  { value: "cash_bank_payment", label: "Pembayaran" },
  { value: "cash_bank_transfer", label: "Transfer" },
  { value: "cash_bank_reversal", label: "Reversal" },
  { value: "payroll_payment", label: "Pembayaran Payroll" },
];

const monthStart = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
};
const todayIso = () => new Date().toISOString().slice(0, 10);

export default function MutasiKasBank() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const urlBank = searchParams.get("cash_bank_id") || "";
  const urlStart = searchParams.get("start_date") || "";
  const urlEnd = searchParams.get("end_date") || "";
  const urlSource = searchParams.get("source_type") || "";
  const [banks, setBanks] = useState([]);
  const [cashBankId, setCashBankId] = useState(urlBank);
  const [startDate, setStartDate] = useState(urlStart || monthStart);
  const [endDate, setEndDate] = useState(urlEnd || todayIso);
  const [sourceType, setSourceType] = useState(urlSource);
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery } = useDebouncedLiveSearch();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [banksError, setBanksError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.cashBankAccounts({ active_only: false });
        if (cancelled) return;
        const items = Array.isArray(res?.items) ? res.items : [];
        setBanks(items);
        if (!cashBankId && items.length) setCashBankId(urlBank || items[0].id);
      } catch (ex) {
        if (!cancelled) setBanksError(errText(ex));
      }
    })();
    return () => { cancelled = true; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async () => {
    if (!cashBankId || !startDate || !endDate) return;
    setLoading(true);
    setLoadError("");
    try {
      const res = await api.cashBankMutasi({
        cash_bank_id: cashBankId,
        start_date: startDate,
        end_date: endDate,
        q: q || undefined,
        source_type: sourceType || undefined,
      });
      setData(res);
    } catch (ex) {
      setLoadError(errText(ex));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [cashBankId, startDate, endDate, q, sourceType]);

  useEffect(() => { load(); }, [load]);

  const items = useMemo(() => (Array.isArray(data?.items) ? data.items : []), [data]);

  const openSource = (row) => {
    if (!row?.source_path || !row?.source_id) return;
    navigate(`${row.source_path}?id=${encodeURIComponent(row.source_id)}`);
  };

  return (
    <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="kas-bank-mutasi-page">
      <div className="page-heading">
        <div>
          <h1>Mutasi Kas &amp; Bank</h1>
          <p className="muted">Ledger dari jurnal posted — saldo berjalan dari baris jurnal.</p>
        </div>
      </div>

      {(banksError || loadError) && (
        <div className="form-error" role="alert">
          <AlertCircle size={16} /> {banksError || loadError}
        </div>
      )}

      <div className="kb-toolbar kb-mutasi-toolbar" data-testid="kas-bank-mutasi-toolbar">
        <div className="kb-mutasi-filters">
          <KbField label="Kas/Bank" required className="kb-mutasi-bank">
            <select
              value={cashBankId}
              onChange={(e) => setCashBankId(e.target.value)}
              data-testid="kas-bank-mutasi-bank"
            >
              <option value="">— Pilih —</option>
              {banks.map((b) => (
                <option key={b.id} value={b.id}>{b.name}</option>
              ))}
            </select>
          </KbField>
          <KbField label="Sumber" className="kb-mutasi-source">
            <select value={sourceType} onChange={(e) => setSourceType(e.target.value)} data-testid="kas-bank-mutasi-source">
              {SOURCE_OPTIONS.map((o) => (
                <option key={o.value || "all"} value={o.value}>{o.label}</option>
              ))}
            </select>
          </KbField>
          <KbField label="Dari tanggal" required className="kb-mutasi-start">
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} data-testid="kas-bank-mutasi-start" />
          </KbField>
          <KbField label="Sampai tanggal" required className="kb-mutasi-end">
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} data-testid="kas-bank-mutasi-end" />
          </KbField>
          <KbField label="Cari" className="kb-mutasi-search">
            <KbSearchField
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              onSubmit={doSearch}
              onClear={clearQuery}
              placeholder="Cari nomor / keterangan…"
              testid="kas-bank-mutasi-search"
              ariaLabel="Cari nomor atau keterangan"
            />
          </KbField>
        </div>
      </div>

      {data && (
        <div className="kb-summary-strip" data-testid="kas-bank-mutasi-summary">
          <div className="kb-summary-card">
            <span>Saldo Awal</span>
            <strong className="num kb-money">{formatRp(data.opening_balance)}</strong>
          </div>
          <div className="kb-summary-card">
            <span>Total Debit</span>
            <strong className="num kb-money">{formatRp(data.total_debit)}</strong>
          </div>
          <div className="kb-summary-card">
            <span>Total Kredit</span>
            <strong className="num kb-money">{formatRp(data.total_credit)}</strong>
          </div>
          <div className="kb-summary-card kb-summary-emphasis">
            <span>Saldo Akhir</span>
            <strong className="num kb-money">{formatRp(data.ending_balance)}</strong>
          </div>
        </div>
      )}

      <div className="table-wrap">
        <table className="prod-table kas-bank-table" data-testid="kas-bank-mutasi-table">
          <thead>
            <tr>
              <th>Tanggal</th>
              <th>No. Transaksi</th>
              <th>Sumber</th>
              <th>Keterangan</th>
              <th className="num">Debit</th>
              <th className="num">Kredit</th>
              <th className="num">Saldo Berjalan</th>
              <th>Status</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={9} className="empty-state">Memuat mutasi…</td></tr>
            )}
            {!loading && !items.length && (
              <tr><td colSpan={9} className="empty-state">Tidak ada mutasi pada periode ini.</td></tr>
            )}
            {!loading && items.map((row) => {
              const st = STATUS_META[row.status] || STATUS_META.posted;
              return (
                <tr key={row.journal_line_id} data-testid={`kas-bank-mutasi-row-${row.journal_line_id}`}>
                  <td>{row.date}</td>
                  <td className="kb-doc-number">{row.transaction_number || row.journal_number || "—"}</td>
                  <td>{row.source_label}</td>
                  <td className="kb-ellipsis" title={row.description || ""}>{row.description || "—"}</td>
                  <td className="num kb-money">{row.debit ? formatRp(row.debit) : ""}</td>
                  <td className="num kb-money">{row.credit ? formatRp(row.credit) : ""}</td>
                  <td className="num kb-money">{formatRp(row.running_balance)}</td>
                  <td><span className={`pill ${st.className}`}>{st.label}</span></td>
                  <td>
                    {row.source_path && row.source_id ? (
                      <button
                        type="button"
                        className="ghost sm kb-action-primary"
                        onClick={() => openSource(row)}
                        title="Lihat sumber"
                        data-testid={`kas-bank-mutasi-view-${row.journal_line_id}`}
                      >
                        <Eye size={14} /> Lihat
                      </button>
                    ) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
