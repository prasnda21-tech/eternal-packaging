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
  const v = Number(n);
  const safe = Number.isFinite(v) ? v : 0;
  return safe.toLocaleString("id-ID", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
};
const formatRp = (n) => `Rp ${formatMoney(n)}`;
const moneyClass = (n) => `num kb-money${Number(n) < 0 ? " kb-pl-neg" : ""}`;

const monthStart = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
};
const todayIso = () => new Date().toISOString().slice(0, 10);

const GROUP_SECTION_LABELS = {
  revenue: "PENDAPATAN",
  cogs: "BEBAN POKOK PENJUALAN",
  expense: "BEBAN",
  other_income: "PENDAPATAN LAIN-LAIN",
  other_expense: "BEBAN LAIN-LAIN",
};

const GROUP_TOTAL_LABELS = {
  revenue: "Total Pendapatan",
  cogs: "Total HPP",
  expense: "Total Beban",
  other_income: "Total Pendapatan Lain",
  other_expense: "Total Beban Lain",
};

export default function LabaRugi() {
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
      const res = await api.profitLoss({
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

  const groups = useMemo(() => (Array.isArray(data?.groups) ? data.groups : []), [data]);
  const totals = data?.totals || {};

  const openLedger = (row) => {
    const accountId = row?.drilldown?.account_id || row?.account_id;
    const start = row?.drilldown?.start_date || startDate;
    const end = row?.drilldown?.end_date || endDate;
    if (!accountId || !start || !end) return;
    const params = new URLSearchParams({
      account_id: accountId,
      start_date: start,
      end_date: end,
    });
    navigate(`/buku-besar/buku-besar?${params.toString()}`);
  };

  const renderAccountRow = (row) => (
    <tr key={row.account_id} className="kb-pl-account" data-testid={`laba-rugi-row-${row.account_id}`}>
      <td className="kb-doc-number">{row.code || "—"}</td>
      <td className="kb-ellipsis" title={row.name || ""}>{row.name || "—"}</td>
      <td className={moneyClass(row.debit)}>{formatRp(row.debit)}</td>
      <td className={moneyClass(row.credit)}>{formatRp(row.credit)}</td>
      <td className={moneyClass(row.amount)} data-testid={`laba-rugi-amount-${row.account_id}`}>{formatRp(row.amount)}</td>
      <td>
        {canOpenLedger && row.account_id && startDate && endDate ? (
          <button
            type="button"
            className="ghost sm kb-action-primary"
            onClick={() => openLedger(row)}
            title="Lihat Buku Besar"
            data-testid={`laba-rugi-view-gl-${row.account_id}`}
          >
            <BookOpen size={14} /> Lihat Buku Besar
          </button>
        ) : "—"}
      </td>
    </tr>
  );

  const renderGroup = (group) => (
    <tbody key={group.report_group} data-testid={`laba-rugi-group-${group.report_group}`}>
      <tr className="kb-pl-section">
        <td colSpan={6}>{GROUP_SECTION_LABELS[group.report_group] || group.label}</td>
      </tr>
      {(group.items || []).map(renderAccountRow)}
      <tr className="kb-pl-group-total" data-testid={`laba-rugi-group-total-${group.report_group}`}>
        <td colSpan={2}>{GROUP_TOTAL_LABELS[group.report_group] || "Total"}</td>
        <td className="num kb-money" />
        <td className="num kb-money" />
        <td className={moneyClass(group.total)}>{formatRp(group.total)}</td>
        <td />
      </tr>
    </tbody>
  );

  const byKey = Object.fromEntries(groups.map((g) => [g.report_group, g]));

  const renderSubtotal = (key, label, amount, extraClass) => (
    <tbody>
      <tr className={`kb-pl-subtotal ${extraClass || ""}`} data-testid={`laba-rugi-${key}`}>
        <td colSpan={2}>{label}</td>
        <td className="num kb-money" />
        <td className="num kb-money" />
        <td className={moneyClass(amount)}>{formatRp(amount)}</td>
        <td />
      </tr>
    </tbody>
  );

  return (
    <div className="prod-page kas-bank-page kas-bank-workspace" data-testid="laba-rugi-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Buku Besar</p>
          <h1 data-testid="laba-rugi-title">Laba/Rugi</h1>
          <p className="muted">Laporan laba rugi per periode dari jurnal posted — tanpa pajak penghasilan.</p>
        </div>
      </div>

      {loadError && (
        <div className="form-error" role="alert">
          <AlertCircle size={16} /> {loadError}
        </div>
      )}

      <div className="kb-toolbar kb-tb-toolbar" data-testid="laba-rugi-toolbar">
        <div className="kb-tb-filters">
          <KbField label="Dari Tanggal" required className="kb-tb-start">
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} data-testid="laba-rugi-start" />
          </KbField>
          <KbField label="Sampai Tanggal" required className="kb-tb-end">
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} data-testid="laba-rugi-end" />
          </KbField>
          <KbField label="Cari akun" className="kb-tb-search">
            <KbSearchField
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              onSubmit={doSearch}
              onClear={clearQuery}
              placeholder="Cari akun..."
              testid="laba-rugi-search"
              ariaLabel="Cari kode, nama, atau tipe akun"
            />
          </KbField>
        </div>
      </div>

      <div className="table-wrap kb-pl-wrap">
        <table className="prod-table kas-bank-table kb-pl-table" data-testid="laba-rugi-table">
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
          {(!startDate || !endDate) && (
            <tbody>
              <tr><td colSpan={6} className="empty-state" data-testid="laba-rugi-dates-required">Isi rentang tanggal untuk menampilkan Laba/Rugi.</td></tr>
            </tbody>
          )}
          {startDate && endDate && loading && (
            <tbody>
              <tr><td colSpan={6} className="empty-state">Memuat Laba/Rugi…</td></tr>
            </tbody>
          )}
          {startDate && endDate && !loading && !data && (
            <tbody>
              <tr><td colSpan={6} className="empty-state" data-testid="laba-rugi-empty">Tidak ada data untuk ditampilkan.</td></tr>
            </tbody>
          )}
          {startDate && endDate && !loading && data && (
            <>
              {byKey.revenue && renderGroup(byKey.revenue)}
              {byKey.cogs && renderGroup(byKey.cogs)}
              {renderSubtotal("gross-profit", "LABA KOTOR", totals.gross_profit)}
              {byKey.expense && renderGroup(byKey.expense)}
              {renderSubtotal("operating-profit", "LABA OPERASIONAL", totals.operating_profit)}
              {byKey.other_income && renderGroup(byKey.other_income)}
              {byKey.other_expense && renderGroup(byKey.other_expense)}
              {renderSubtotal("net-profit", "LABA BERSIH", totals.net_profit, "kb-pl-net")}
            </>
          )}
        </table>
      </div>
    </div>
  );
}
