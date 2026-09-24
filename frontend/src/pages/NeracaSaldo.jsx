import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertCircle, BookOpen } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { NAV_KEYS } from "../lib/navAccess";
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

const monthStart = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
};
const todayIso = () => new Date().toISOString().slice(0, 10);

export default function NeracaSaldo() {
  const navigate = useNavigate();
  const { hasPerm } = useAuth();
  const [startDate, setStartDate] = useState(monthStart);
  const [endDate, setEndDate] = useState(todayIso);
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery } = useDebouncedLiveSearch();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");

  const canOpenLedger = hasPerm(NAV_KEYS.ledgerGeneralLedger) || hasPerm("general_ledger.view");

  const load = useCallback(async () => {
    if (!startDate || !endDate) {
      setData(null);
      setLoadError("");
      setLoading(false);
      return;
    }
    setLoading(true);
    setLoadError("");
    try {
      const res = await api.trialBalance({
        start_date: startDate,
        end_date: endDate,
        q: q || undefined,
      });
      setData(res);
    } catch (ex) {
      setLoadError(errText(ex));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [startDate, endDate, q]);

  useEffect(() => { load(); }, [load]);

  const items = useMemo(() => (Array.isArray(data?.items) ? data.items : []), [data]);
  const totals = data?.totals || {};

  const openLedger = (row) => {
    if (!row?.account_id || !startDate || !endDate) return;
    const params = new URLSearchParams({
      account_id: row.account_id,
      start_date: startDate,
      end_date: endDate,
    });
    navigate(`/buku-besar/buku-besar?${params.toString()}`);
  };

  return (
    <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="neraca-saldo-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Buku Besar</p>
          <h1 data-testid="neraca-saldo-title">Neraca Saldo</h1>
          <p className="muted">Saldo per akun dari jurnal posted — saldo awal, mutasi, dan saldo akhir periode.</p>
        </div>
      </div>

      {loadError && (
        <div className="form-error" role="alert">
          <AlertCircle size={16} /> {loadError}
        </div>
      )}

      <div className="kb-toolbar kb-tb-toolbar" data-testid="neraca-saldo-toolbar">
        <div className="kb-tb-filters">
          <KbField label="Dari Tanggal" required className="kb-tb-start">
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} data-testid="neraca-saldo-start" />
          </KbField>
          <KbField label="Sampai Tanggal" required className="kb-tb-end">
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} data-testid="neraca-saldo-end" />
          </KbField>
          <KbField label="Cari akun" className="kb-tb-search">
            <KbSearchField
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              onSubmit={doSearch}
              onClear={clearQuery}
              placeholder="Cari kode / nama / tipe akun…"
              testid="neraca-saldo-search"
              ariaLabel="Cari kode, nama, atau tipe akun"
            />
          </KbField>
        </div>
      </div>

      {data?.completeness_status === "incomplete" && (
        <div className="form-error" role="status" data-testid="neraca-saldo-completeness">
          <AlertCircle size={16} /> Reversal belum lengkap ({data.blocker_code || "REVERSAL_INCOMPLETE"}). Angka pada ringkasan ini belum final.
        </div>
      )}

      {data && (
        <div className="kb-tb-summary" data-testid="neraca-saldo-summary">
          <div className="kb-tb-summary-group" data-testid="neraca-saldo-summary-opening">
            <span className="kb-tb-summary-title">Saldo Awal</span>
            <div className="kb-tb-summary-row">
              <span>Debit</span>
              <strong className="num kb-money">{formatRp(totals.opening_debit)}</strong>
            </div>
            <div className="kb-tb-summary-row">
              <span>Kredit</span>
              <strong className="num kb-money">{formatRp(totals.opening_credit)}</strong>
            </div>
            <div className="kb-tb-summary-row kb-tb-summary-diff">
              <span>Selisih</span>
              <strong className="num kb-money">{formatRp(totals.opening_diff)}</strong>
            </div>
          </div>
          <div className="kb-tb-summary-group" data-testid="neraca-saldo-summary-period">
            <span className="kb-tb-summary-title">Mutasi</span>
            <div className="kb-tb-summary-row">
              <span>Debit</span>
              <strong className="num kb-money">{formatRp(totals.period_debit)}</strong>
            </div>
            <div className="kb-tb-summary-row">
              <span>Kredit</span>
              <strong className="num kb-money">{formatRp(totals.period_credit)}</strong>
            </div>
            <div className="kb-tb-summary-row kb-tb-summary-diff">
              <span>Selisih</span>
              <strong className="num kb-money">{formatRp(totals.period_diff)}</strong>
            </div>
          </div>
          <div className="kb-tb-summary-group kb-summary-emphasis" data-testid="neraca-saldo-summary-ending">
            <span className="kb-tb-summary-title">Saldo Akhir</span>
            <div className="kb-tb-summary-row">
              <span>Debit</span>
              <strong className="num kb-money">{formatRp(totals.ending_debit)}</strong>
            </div>
            <div className="kb-tb-summary-row">
              <span>Kredit</span>
              <strong className="num kb-money">{formatRp(totals.ending_credit)}</strong>
            </div>
            <div className="kb-tb-summary-row kb-tb-summary-diff">
              <span>Selisih</span>
              <strong className="num kb-money">{formatRp(totals.ending_diff)}</strong>
            </div>
          </div>
        </div>
      )}

      <div className="table-wrap">
        <table className="prod-table kas-bank-table" data-testid="neraca-saldo-table">
          <thead>
            <tr>
              <th>Kode</th>
              <th>Nama Akun</th>
              <th>Tipe</th>
              <th className="num">Saldo Awal Debit</th>
              <th className="num">Saldo Awal Kredit</th>
              <th className="num">Mutasi Debit</th>
              <th className="num">Mutasi Kredit</th>
              <th className="num">Saldo Akhir Debit</th>
              <th className="num">Saldo Akhir Kredit</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {(!startDate || !endDate) && (
              <tr><td colSpan={10} className="empty-state" data-testid="neraca-saldo-dates-required">Isi rentang tanggal untuk menampilkan Neraca Saldo.</td></tr>
            )}
            {startDate && endDate && loading && (
              <tr><td colSpan={10} className="empty-state">Memuat Neraca Saldo…</td></tr>
            )}
            {startDate && endDate && !loading && !items.length && (
              <tr><td colSpan={10} className="empty-state" data-testid="neraca-saldo-empty">Tidak ada akun untuk ditampilkan.</td></tr>
            )}
            {startDate && endDate && !loading && items.map((row) => (
              <tr key={row.account_id} data-testid={`neraca-saldo-row-${row.account_id}`}>
                <td className="kb-doc-number">{row.code || "—"}</td>
                <td className="kb-ellipsis" title={row.name || ""}>{row.name || "—"}</td>
                <td>{row.account_type || "—"}</td>
                <td className="num kb-money">{row.opening_debit ? formatRp(row.opening_debit) : ""}</td>
                <td className="num kb-money">{row.opening_credit ? formatRp(row.opening_credit) : ""}</td>
                <td className="num kb-money">{row.period_debit ? formatRp(row.period_debit) : ""}</td>
                <td className="num kb-money">{row.period_credit ? formatRp(row.period_credit) : ""}</td>
                <td className="num kb-money">{row.ending_debit ? formatRp(row.ending_debit) : ""}</td>
                <td className="num kb-money">{row.ending_credit ? formatRp(row.ending_credit) : ""}</td>
                <td>
                  {canOpenLedger && row.account_id && startDate && endDate ? (
                    <button
                      type="button"
                      className="ghost sm kb-action-primary"
                      onClick={() => openLedger(row)}
                      title="Lihat Buku Besar"
                      data-testid={`neraca-saldo-view-gl-${row.account_id}`}
                    >
                      <BookOpen size={14} /> Lihat Buku Besar
                    </button>
                  ) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
