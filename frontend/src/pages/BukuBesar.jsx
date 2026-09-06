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

const SOURCE_OPTIONS = [
  { value: "", label: "Semua sumber" },
  { value: "cash_bank_receipt", label: "Penerimaan" },
  { value: "cash_bank_payment", label: "Pembayaran" },
  { value: "cash_bank_transfer", label: "Transfer" },
  { value: "cash_bank_reversal", label: "Reversal Kas & Bank" },
  { value: "general_journal", label: "Jurnal Umum" },
  { value: "general_journal_reversal", label: "Reversal Jurnal Umum" },
  { value: "opening_balance", label: "Saldo Awal" },
  { value: "opening_balance_reversal", label: "Reversal Saldo Awal" },
];

const monthStart = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
};
const todayIso = () => new Date().toISOString().slice(0, 10);

const accountOptionLabel = (row) => {
  const code = String(row?.code || "").trim();
  const name = String(row?.name || "").trim();
  const type = String(row?.account_type || "").trim();
  const core = [code, name].filter(Boolean).join(" · ");
  return type ? `${core} (${type})` : core || row?.id || "—";
};

export default function BukuBesar() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [accounts, setAccounts] = useState([]);
  const [accountId, setAccountId] = useState(() => searchParams.get("account_id") || "");
  const [startDate, setStartDate] = useState(() => searchParams.get("start_date") || monthStart());
  const [endDate, setEndDate] = useState(() => searchParams.get("end_date") || todayIso());
  const [sourceType, setSourceType] = useState("");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery } = useDebouncedLiveSearch();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [accountsError, setAccountsError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.ledgerAccounts();
        if (cancelled) return;
        setAccounts(Array.isArray(res?.items) ? res.items : []);
      } catch (ex) {
        if (!cancelled) setAccountsError(errText(ex));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const load = useCallback(async () => {
    if (!accountId || !startDate || !endDate) {
      setData(null);
      setLoadError("");
      setLoading(false);
      return;
    }
    setLoading(true);
    setLoadError("");
    try {
      const res = await api.generalLedger({
        account_id: accountId,
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
  }, [accountId, startDate, endDate, q, sourceType]);

  useEffect(() => { load(); }, [load]);

  const items = useMemo(() => (Array.isArray(data?.items) ? data.items : []), [data]);

  const openSource = (row) => {
    if (!row?.source_path || !row?.source_id) return;
    navigate(`${row.source_path}?id=${encodeURIComponent(row.source_id)}`);
  };

  return (
    <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="buku-besar-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Buku Besar</p>
          <h1 data-testid="buku-besar-title">Buku Besar</h1>
          <p className="muted">Ledger per akun dari jurnal posted — saldo berjalan dari baris jurnal.</p>
        </div>
      </div>

      {(accountsError || loadError) && (
        <div className="form-error" role="alert">
          <AlertCircle size={16} /> {accountsError || loadError}
        </div>
      )}

      <div className="kb-toolbar kb-gl-toolbar" data-testid="buku-besar-toolbar">
        <div className="kb-gl-filters">
          <KbField label="Akun Perkiraan" required className="kb-gl-account">
            <select
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              data-testid="buku-besar-account"
            >
              <option value="">— Pilih akun —</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{accountOptionLabel(a)}</option>
              ))}
            </select>
          </KbField>
          <KbField label="Sumber" className="kb-gl-source">
            <select
              value={sourceType}
              onChange={(e) => setSourceType(e.target.value)}
              data-testid="buku-besar-source"
            >
              {SOURCE_OPTIONS.map((o) => (
                <option key={o.value || "all"} value={o.value}>{o.label}</option>
              ))}
            </select>
          </KbField>
          <KbField label="Dari Tanggal" required className="kb-gl-start">
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} data-testid="buku-besar-start" />
          </KbField>
          <KbField label="Sampai Tanggal" required className="kb-gl-end">
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} data-testid="buku-besar-end" />
          </KbField>
          <KbField label="Cari" className="kb-gl-search">
            <KbSearchField
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              onSubmit={doSearch}
              onClear={clearQuery}
              placeholder="Cari nomor jurnal / keterangan…"
              testid="buku-besar-search"
              ariaLabel="Cari nomor jurnal atau keterangan"
            />
          </KbField>
        </div>
      </div>

      {data && (
        <div className="kb-summary-strip" data-testid="buku-besar-summary">
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
        <table className="prod-table kas-bank-table" data-testid="buku-besar-table">
          <thead>
            <tr>
              <th>Tanggal</th>
              <th>No. Jurnal</th>
              <th>Sumber</th>
              <th>Keterangan</th>
              <th className="num">Debit</th>
              <th className="num">Kredit</th>
              <th className="num">Saldo Berjalan</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {!accountId && (
              <tr><td colSpan={8} className="empty-state" data-testid="buku-besar-account-required">Pilih Akun Perkiraan untuk menampilkan Buku Besar.</td></tr>
            )}
            {accountId && loading && (
              <tr><td colSpan={8} className="empty-state">Memuat Buku Besar…</td></tr>
            )}
            {accountId && !loading && !items.length && (
              <tr><td colSpan={8} className="empty-state" data-testid="buku-besar-empty">Tidak ada mutasi pada periode ini.</td></tr>
            )}
            {accountId && !loading && items.map((row) => {
              const ket = row.line_description || row.description || "—";
              return (
                <tr key={row.journal_line_id} data-testid={`buku-besar-row-${row.journal_line_id}`}>
                  <td>{row.date}</td>
                  <td className="kb-doc-number">{row.journal_number || "—"}</td>
                  <td>{row.source_label || row.source_type || "—"}</td>
                  <td className="kb-ellipsis" title={ket}>{ket}</td>
                  <td className="num kb-money">{row.debit ? formatRp(row.debit) : ""}</td>
                  <td className="num kb-money">{row.credit ? formatRp(row.credit) : ""}</td>
                  <td className="num kb-money">{formatRp(row.running_balance)}</td>
                  <td>
                    {row.source_path && row.source_id ? (
                      <button
                        type="button"
                        className="ghost sm kb-action-primary"
                        onClick={() => openSource(row)}
                        title="Lihat sumber"
                        data-testid={`buku-besar-view-${row.journal_line_id}`}
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
