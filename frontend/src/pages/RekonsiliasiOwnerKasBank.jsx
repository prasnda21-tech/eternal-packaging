import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Eye, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { useDebouncedLiveSearch } from "../hooks/useDebouncedLiveSearch";
import { KbField, KbSearchField } from "../components/CashBankFormPrimitives";

const errText = (ex) => {
  const det = ex?.response?.data?.detail;
  if (typeof det === "string") return det;
  if (det && typeof det === "object" && det.message) return det.message;
  return ex?.message || "Permintaan gagal";
};

const formatMoney = (n) => {
  if (n == null || n === "") return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  return v.toLocaleString("id-ID", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
};
const formatRp = (n) => (n == null || n === "" ? "—" : `Rp ${formatMoney(n)}`);

const STATUS_OPTIONS = [
  { value: "", label: "Semua" },
  { value: "SESUAI", label: "Sesuai" },
  { value: "BELUM_DICOCOKKAN", label: "Belum Cocok" },
  { value: "STATEMENT_TANPA_JURNAL", label: "Statement Tanpa Jurnal" },
  { value: "JURNAL_TANPA_STATEMENT", label: "Jurnal Tanpa Statement" },
  { value: "PERLU_REVIEW", label: "Perlu Review" },
  { value: "DUPLIKAT", label: "Duplikat" },
  { value: "REVERSED", label: "Reversed" },
];

const STATUS_CLASS = {
  SESUAI: "kb-status-posted",
  BELUM_DICOCOKKAN: "kb-status-draft",
  STATEMENT_TANPA_JURNAL: "kb-status-draft",
  JURNAL_TANPA_STATEMENT: "kb-status-draft",
  PERLU_REVIEW: "kb-owner-status-review",
  DUPLIKAT: "kb-owner-status-dup",
  REVERSED: "kb-status-reversed",
};

const monthStart = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
};
const todayIso = () => new Date().toISOString().slice(0, 10);

function Prop({ label, children }) {
  return (
    <div className="drawer-prop-row">
      <span className="prop-label">{label}</span>
      <span className="prop-value">{children ?? "—"}</span>
    </div>
  );
}

export default function RekonsiliasiOwnerKasBank() {
  const { hasPerm } = useAuth();
  const canView = hasPerm("cash_bank.view") || hasPerm("nav.cash_bank.reconciliation_owner");
  const canReconcile = hasPerm("cash_bank.reconcile");
  const [banks, setBanks] = useState([]);
  const [cashBankId, setCashBankId] = useState("");
  const [startDate, setStartDate] = useState(monthStart);
  const [endDate, setEndDate] = useState(todayIso);
  const [status, setStatus] = useState("");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery } = useDebouncedLiveSearch();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [banksError, setBanksError] = useState("");
  const [selected, setSelected] = useState(null);
  const [suggestions, setSuggestions] = useState(null);
  const [suggestLoading, setSuggestLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.cashBankAccounts({ active_only: false });
        if (cancelled) return;
        const items = Array.isArray(res?.items) ? res.items : [];
        setBanks(items);
        if (items.length) setCashBankId((prev) => prev || items[0].id);
      } catch (ex) {
        if (!cancelled) setBanksError(errText(ex));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const load = useCallback(async () => {
    if (!cashBankId || !startDate || !endDate) return;
    setLoading(true);
    setLoadError("");
    try {
      const res = await api.cashBankOwnerReconView({
        cash_bank_id: cashBankId,
        start_date: startDate,
        end_date: endDate,
        status: status || undefined,
        q: q || undefined,
      });
      setData(res);
    } catch (ex) {
      setData(null);
      setLoadError(errText(ex));
    } finally {
      setLoading(false);
    }
  }, [cashBankId, startDate, endDate, status, q]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!selected?.statement?.id || !startDate || !endDate) {
      setSuggestions(null);
      return undefined;
    }
    let cancelled = false;
    setSuggestLoading(true);
    (async () => {
      try {
        const res = await api.cashBankPairSuggestions({
          statement_line_id: selected.statement.id,
          start_date: startDate,
          end_date: endDate,
        });
        if (!cancelled) setSuggestions(res);
      } catch {
        if (!cancelled) setSuggestions(null);
      } finally {
        if (!cancelled) setSuggestLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [selected, startDate, endDate]);

  const summary = data?.summary || {};
  const items = useMemo(() => (Array.isArray(data?.items) ? data.items : []), [data]);

  const openRow = (row) => setSelected(row);
  const closeDrawer = () => {
    setSelected(null);
    setSuggestions(null);
  };

  if (!canView) {
    return (
      <div className="prod-page kas-bank-page" data-testid="kas-bank-owner-recon-page">
        <p className="kb-pair-note">Izin cash_bank.view diperlukan.</p>
      </div>
    );
  }

  return (
    <div className="prod-page kas-bank-page kb-owner-recon" data-testid="kas-bank-owner-recon-page" data-module="cashBank">
      <header className="page-header">
        <div>
          <h1>Kas &amp; Bank Rekonsiliasi</h1>
          <p className="page-sub">Tampilan baca saja — pencocokan &amp; impor tidak diubah di halaman ini.</p>
        </div>
        {canReconcile ? (
          <Link className="ghost sm" to="/kas-bank/rekonsiliasi" data-testid="kas-bank-owner-link-pair">
            Buka pencocokan baris
          </Link>
        ) : null}
      </header>

      <p className="kb-pair-note" data-testid="kas-bank-owner-legacy-note">
        {data?.legacy_rk_note
          || "Dokumen rekonsiliasi saldo lama (RK) terpisah. Finalized RK bukan berarti saldo cocok."}
      </p>

      <div className="kb-toolbar kb-recon-toolbar kb-owner-toolbar" data-testid="kas-bank-owner-toolbar">
        <KbField label="Bank">
          <select
            aria-label="Bank"
            value={cashBankId}
            onChange={(e) => setCashBankId(e.target.value)}
            data-testid="kas-bank-owner-bank"
          >
            {!banks.length && <option value="">—</option>}
            {banks.map((b) => (
              <option key={b.id} value={b.id}>{b.name}</option>
            ))}
          </select>
        </KbField>
        <KbField label="Mulai">
          <input type="date" aria-label="Tanggal mulai" value={startDate} onChange={(e) => setStartDate(e.target.value)} data-testid="kas-bank-owner-start" />
        </KbField>
        <KbField label="Akhir">
          <input type="date" aria-label="Tanggal akhir" value={endDate} onChange={(e) => setEndDate(e.target.value)} data-testid="kas-bank-owner-end" />
        </KbField>
        <KbField label="Status">
          <select
            aria-label="Filter status"
            className="kb-toolbar-status"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            data-testid="kas-bank-owner-status"
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value || "all"} value={o.value}>{o.label}</option>
            ))}
          </select>
        </KbField>
        <KbSearchField
          value={qInput}
          onChange={setQInput}
          onSubmit={doSearch}
          onClear={clearQuery}
          placeholder="Cari"
          ariaLabel="Cari"
          testid="kas-bank-owner-search"
        />
      </div>

      {banksError && <p className="form-error" role="alert">{banksError}</p>}
      {loadError && <p className="form-error" role="alert">{loadError}</p>}

      <div className="kb-owner-summary" data-testid="kas-bank-owner-summary">
        <div><span>Saldo Buku</span><strong className="kb-money">{formatRp(summary.saldo_buku)}</strong></div>
        <div>
          <span>Saldo Statement</span>
          <strong className="kb-money">
            {data?.statement_closing_complete ? formatRp(summary.saldo_statement) : "Belum lengkap"}
          </strong>
        </div>
        <div>
          <span>Selisih</span>
          <strong className="kb-money">
            {data?.statement_closing_complete ? formatRp(summary.selisih) : "—"}
          </strong>
        </div>
        <div><span>Cocok</span><strong>{summary.cocok ?? 0}</strong></div>
        <div><span>Belum Cocok</span><strong>{summary.belum_cocok ?? 0}</strong></div>
        <div><span>Perlu Review</span><strong>{summary.perlu_review ?? 0}</strong></div>
      </div>

      {!data?.statement_closing_complete && (
        <p className="kb-pair-note" data-testid="kas-bank-owner-closing-note">
          {data?.statement_closing_note || "Statement closing belum lengkap"}
        </p>
      )}

      <div className="kb-owner-table-wrap" data-testid="kas-bank-owner-table-wrap">
        <table className="prod-table kas-bank-table kb-owner-table" data-testid="kas-bank-owner-table">
          <thead>
            <tr>
              <th>Tanggal</th>
              <th>Bank</th>
              <th>Statement</th>
              <th>Buku</th>
              <th className="num">Nominal</th>
              <th>Source</th>
              <th>Status</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={8}>Memuat…</td></tr>
            )}
            {!loading && !items.length && (
              <tr><td colSpan={8}>Tidak ada baris untuk filter ini.</td></tr>
            )}
            {!loading && items.map((row) => (
              <tr key={row.id} data-testid="kas-bank-owner-row" data-status={row.status}>
                <td>{row.date || "—"}</td>
                <td className="kb-ellipsis">{row.bank || "—"}</td>
                <td className="kb-ellipsis">{row.statement_text || "—"}</td>
                <td className="kb-ellipsis">{row.book_text || "—"}</td>
                <td className="num kb-money">{formatRp(row.nominal)}</td>
                <td>
                  <span className="kb-primary-text">{row.source_label || "—"}</span>
                  {row.voucher_number ? <small className="kb-coa-name"> · {row.voucher_number}</small> : null}
                </td>
                <td>
                  <span className={`pill ${STATUS_CLASS[row.status] || "kb-status-draft"}`}>
                    {row.status_label || row.status}
                  </span>
                </td>
                <td>
                  <button
                    type="button"
                    className="ghost sm"
                    onClick={() => openRow(row)}
                    data-testid="kas-bank-owner-open"
                  >
                    <Eye size={14} /> Detail
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <div className="kb-owner-drawer-overlay" onClick={closeDrawer} data-testid="kas-bank-owner-drawer-backdrop">
          <aside
            className="kb-owner-drawer"
            onClick={(e) => e.stopPropagation()}
            data-testid="kas-bank-owner-drawer"
            role="dialog"
            aria-label="Detail rekonsiliasi"
          >
            <div className="drawer-header">
              <h2>Detail</h2>
              <button type="button" className="ghost sm" onClick={closeDrawer} aria-label="Tutup" data-testid="kas-bank-owner-drawer-close">
                <X size={16} />
              </button>
            </div>
            <div className="drawer-body">
              <h3>Statement</h3>
              <div className="drawer-prop-list">
                <Prop label="Tanggal">{selected.statement?.date}</Prop>
                <Prop label="Nominal">{formatRp(selected.statement?.amount)}</Prop>
                <Prop label="Arah">{selected.statement?.direction}</Prop>
                <Prop label="Memo">{selected.statement?.description}</Prop>
                <Prop label="Referensi">{selected.statement?.reference}</Prop>
                <Prop label="Bank Txn">{selected.statement?.bank_txn_id}</Prop>
                <Prop label="Duplikat">{selected.statement?.duplicate_status}</Prop>
              </div>

              <h3>Book</h3>
              <div className="drawer-prop-list">
                <Prop label="Jurnal">{selected.book?.journal_number}</Prop>
                <Prop label="Tanggal">{selected.book?.date}</Prop>
                <Prop label="Nominal">{formatRp(selected.book?.absolute_amount)}</Prop>
                <Prop label="Arah">{selected.book?.direction}</Prop>
                <Prop label="Source type">{selected.book?.source_type || selected.source_type}</Prop>
                <Prop label="Deskripsi">{selected.book?.description}</Prop>
                <Prop label="Status jurnal">{selected.book?.status || selected.journal_status}</Prop>
              </div>

              <h3>Source</h3>
              <div className="drawer-prop-list">
                <Prop label="Label">{selected.source_label}</Prop>
                <Prop label="Dokumen">{selected.voucher_number}</Prop>
                <Prop label="Tautan">
                  {selected.source_path && selected.source_id ? (
                    <Link to={`${selected.source_path}?id=${encodeURIComponent(selected.source_id)}`} data-testid="kas-bank-owner-source-link">
                      Buka dokumen
                    </Link>
                  ) : "—"}
                </Prop>
              </div>

              <h3>Match</h3>
              <div className="drawer-prop-list">
                <Prop label="Pair id">{selected.match?.id}</Prop>
                <Prop label="Jenis">{selected.match?.match_kind === "pair_group" ? (selected.match?.label || "Grup 1:N") : (selected.match ? "1:1" : "—")}</Prop>
                {selected.match?.match_kind === "pair_group" ? (
                  <Prop label="Anggota grup">{selected.match?.member_count ?? (selected.match?.members || []).length ?? "—"}</Prop>
                ) : null}
                <Prop label="Matched">{selected.match?.claim_active ? "Ya" : "Tidak"}</Prop>
                <Prop label="Review">{selected.match?.review_state}</Prop>
                <Prop label="Saran">
                  {suggestLoading
                    ? "Memuat…"
                    : suggestions?.suggestions?.length
                      ? suggestions.suggestions.map((s) => s.reasons?.join(", ") || s.journal_number).join(" · ")
                      : "—"}
                </Prop>
                {suggestions?.ambiguous ? <Prop label="Ambigu">Ya — saran hanya advisory</Prop> : null}
              </div>

              <h3>Balance Context</h3>
              <div className="drawer-prop-list">
                <Prop label="Saldo buku">{formatRp(data?.book_ending_balance)}</Prop>
                <Prop label="Statement closing">
                  {data?.statement_closing_complete
                    ? formatRp(data?.statement_closing?.amount)
                    : (data?.statement_closing_note || "Statement closing belum lengkap")}
                </Prop>
                <Prop label="Selisih">
                  {data?.statement_closing_complete ? formatRp(data?.difference) : "—"}
                </Prop>
              </div>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
