import { useCallback, useEffect, useState } from "react";
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
  const v = Number(n);
  const safe = Number.isFinite(v) ? v : 0;
  return safe.toLocaleString("id-ID", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
};
const formatRp = (n) => `Rp ${formatMoney(n)}`;
const moneyClass = (n) => `num kb-money${Number(n) < 0 ? " kb-pl-neg" : ""}`;

const todayIso = () => new Date().toISOString().slice(0, 10);

const SECTION_LABELS = {
  assets: "ASET",
  liabilities: "KEWAJIBAN",
  equity: "EKUITAS",
};

const GROUP_TOTAL_LABELS = {
  assets: "Total Aset",
  liabilities: "Total Kewajiban",
  equity: "Total Ekuitas",
};

export default function Neraca() {
  const navigate = useNavigate();
  const { hasPerm } = useAuth();
  const [asOfDate, setAsOfDate] = useState(todayIso);
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery } = useDebouncedLiveSearch();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");

  const canOpenLedger = hasPerm(NAV_KEYS.ledgerGeneralLedger) || hasPerm("general_ledger.view");

  const load = useCallback(async () => {
    if (!asOfDate) {
      setData(null);
      setLoadError("");
      setLoading(false);
      return;
    }
    setLoading(true);
    setLoadError("");
    try {
      const res = await api.balanceSheet({
        as_of_date: asOfDate,
        q: q || undefined,
      });
      setData(res);
    } catch (ex) {
      setLoadError(errText(ex));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [asOfDate, q]);

  useEffect(() => { load(); }, [load]);

  const groups = data?.groups || {};
  const summary = data?.summary || {};
  const currentEarnings = data?.current_earnings || {};

  const openLedger = (row) => {
    const accountId = row?.drilldown?.account_id || row?.account_id;
    const end = row?.drilldown?.end_date || asOfDate;
    if (!accountId || !end) return;
    const params = new URLSearchParams({
      account_id: accountId,
      end_date: end,
    });
    navigate(`/buku-besar/buku-besar?${params.toString()}`);
  };

  const renderAccountRow = (row) => (
    <tr key={row.account_id} className="kb-pl-account" data-testid={`neraca-row-${row.account_id}`}>
      <td className="kb-doc-number">{row.code || "—"}</td>
      <td className="kb-ellipsis" title={row.name || ""}>{row.name || "—"}</td>
      <td className={moneyClass(row.debit)}>{formatRp(row.debit)}</td>
      <td className={moneyClass(row.credit)}>{formatRp(row.credit)}</td>
      <td className={moneyClass(row.amount)} data-testid={`neraca-amount-${row.account_id}`}>{formatRp(row.amount)}</td>
      <td>
        {canOpenLedger && row.account_id ? (
          <button
            type="button"
            className="ghost sm kb-action-primary"
            onClick={() => openLedger(row)}
            title="Lihat Buku Besar"
            data-testid={`neraca-view-gl-${row.account_id}`}
          >
            <BookOpen size={14} /> Lihat Buku Besar
          </button>
        ) : "—"}
      </td>
    </tr>
  );

  const groupTotalAmount = (key, group) => {
    if (key === "assets") return summary.total_assets;
    if (key === "liabilities") return summary.total_liabilities;
    if (key === "equity") return summary.total_equity;
    return group.total;
  };

  const renderGroup = (key, extraRows) => {
    const group = groups[key] || { accounts: [], total: 0 };
    const total = groupTotalAmount(key, group);
    return (
      <tbody key={key} data-testid={`neraca-group-${key}`}>
        <tr className="kb-pl-section">
          <td colSpan={6}>{SECTION_LABELS[key] || group.label}</td>
        </tr>
        {(group.accounts || []).map(renderAccountRow)}
        {extraRows}
        <tr className="kb-pl-group-total" data-testid={`neraca-group-total-${key}`}>
          <td colSpan={2}>{GROUP_TOTAL_LABELS[key] || "Total"}</td>
          <td className="num kb-money" />
          <td className="num kb-money" />
          <td className={moneyClass(total)}>{formatRp(total)}</td>
          <td />
        </tr>
      </tbody>
    );
  };

  const earningsRow = (
    <tr className="kb-bs-derived" data-testid="neraca-current-earnings">
      <td className="kb-doc-number">—</td>
      <td className="kb-ellipsis">{currentEarnings.label || "Laba/Rugi Berjalan"}</td>
      <td className="num kb-money" />
      <td className="num kb-money" />
      <td className={moneyClass(currentEarnings.amount)} data-testid="neraca-current-earnings-amount">
        {formatRp(currentEarnings.amount)}
      </td>
      <td>—</td>
    </tr>
  );

  return (
    <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="neraca-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Buku Besar</p>
          <h1 data-testid="neraca-title">Neraca</h1>
          <p className="muted">Laporan posisi keuangan per tanggal dari jurnal posted — tanpa penyesuaian otomatis.</p>
        </div>
      </div>

      {loadError && (
        <div className="form-error" role="alert">
          <AlertCircle size={16} /> {loadError}
        </div>
      )}

      <div className="kb-toolbar kb-tb-toolbar" data-testid="neraca-toolbar">
        <div className="kb-tb-filters kb-bs-filters">
          <KbField label="Tanggal Neraca" required className="kb-bs-asof">
            <input type="date" value={asOfDate} onChange={(e) => setAsOfDate(e.target.value)} data-testid="neraca-as-of" />
          </KbField>
          <KbField label="Cari akun" className="kb-tb-search">
            <KbSearchField
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              onSubmit={doSearch}
              onClear={clearQuery}
              placeholder="Cari akun..."
              testid="neraca-search"
              ariaLabel="Cari kode, nama, atau tipe akun"
            />
          </KbField>
        </div>
      </div>

      <div className="table-wrap kb-pl-wrap">
        <table className="prod-table kas-bank-table kb-pl-table" data-testid="neraca-table">
          <thead>
            <tr>
              <th>Kode</th>
              <th>Nama Akun</th>
              <th className="num">Debit</th>
              <th className="num">Kredit</th>
              <th className="num">Jumlah</th>
              <th>Aksi</th>
            </tr>
          </thead>
          {!asOfDate && (
            <tbody>
              <tr><td colSpan={6} className="empty-state" data-testid="neraca-date-required">Isi tanggal neraca untuk menampilkan laporan.</td></tr>
            </tbody>
          )}
          {asOfDate && loading && (
            <tbody>
              <tr><td colSpan={6} className="empty-state">Memuat Neraca…</td></tr>
            </tbody>
          )}
          {asOfDate && !loading && !data && (
            <tbody>
              <tr><td colSpan={6} className="empty-state" data-testid="neraca-empty">Tidak ada data untuk ditampilkan.</td></tr>
            </tbody>
          )}
          {asOfDate && !loading && data && (
            <>
              {renderGroup("assets")}
              {renderGroup("liabilities")}
              {renderGroup("equity", earningsRow)}
              <tbody>
                <tr className="kb-pl-subtotal kb-pl-net" data-testid="neraca-total-liabilities-and-equity">
                  <td colSpan={2}>TOTAL KEWAJIBAN & EKUITAS</td>
                  <td className="num kb-money" />
                  <td className="num kb-money" />
                  <td className={moneyClass(summary.total_liabilities_and_equity)}>{formatRp(summary.total_liabilities_and_equity)}</td>
                  <td />
                </tr>
              </tbody>
            </>
          )}
        </table>
      </div>

      {asOfDate && !loading && data && (
        <div
          className={`kb-bs-status${summary.is_balanced ? " is-balanced" : " is-imbalanced"}`}
          data-testid="neraca-status"
        >
          {summary.is_balanced ? (
            <span data-testid="neraca-balanced">NERACA SEIMBANG</span>
          ) : (
            <span data-testid="neraca-imbalance">
              SELISIH NERACA: {formatRp(summary.balance_difference)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
