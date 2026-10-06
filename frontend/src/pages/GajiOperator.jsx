import { useCallback, useEffect, useMemo, useState, Fragment } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Download, RefreshCw, Settings2, ChevronDown, ChevronRight, SlidersHorizontal } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate, formatTrackingPageDate } from "../lib/constants";
import { NAV_KEYS } from "../lib/navAccess";
import { exportOperatorIds } from "../lib/operatorFilter";
import OperatorMultiSelect from "../components/OperatorMultiSelect";
import FacetSearchSelect from "../components/FacetSearchSelect";

function todayIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function monthStartIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  return `${y}-${m}-01`;
}

function fmtQty(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

function fmtMoney(n) {
  if (n === "" || n == null) return "Belum Disetel";
  return Number(n || 0).toLocaleString("id-ID");
}

function formatIdDate(iso) {
  return formatTrackingPageDate(iso) || formatDisplayDate(iso);
}

const PAYROLL_STATUS = {
  BELUM_MASUK_PAYROLL: ["Siap Payroll", "gaji-badge-ready"],
  HELD: ["Dalam Proses", "gaji-badge-progress"],
  POSTED: ["Sudah Payroll", "gaji-badge-done"],
  NOT_ELIGIBLE: ["Perlu Perbaikan", "gaji-badge-fix"],
};

const STATUS_TABS = [
  ["BELUM_MASUK_PAYROLL", "Siap Payroll"],
  ["HELD", "Dalam Proses"],
  ["POSTED", "Sudah Payroll"],
  ["NOT_ELIGIBLE", "Perlu Perbaikan"],
];

const KNOWN_ELIG_LABELS = [
  "Tanpa tanggal kerja",
  "Tanpa snapshot tarif",
  "Profil HR belum piece",
  "Sebelum batas integrasi",
];

function payrollBadge(status, testId) {
  const [label, tone] = PAYROLL_STATUS[status] || [status || "—", "hr-status-draft"];
  return <span className={`hr-status ${tone}`} data-testid={testId || "gaji-payroll-status"}>{label}</span>;
}

/** Backend matches line item_code / name — never send accurate_item_id alone. */
export function gajiFilterItemValue(it) {
  if (!it) return "";
  return String(it.item_code || it.item_name || "").trim();
}

export function gajiFilterItemLabel(it) {
  if (!it) return "";
  const code = String(it.item_code || "").trim();
  const name = String(it.item_name || "").trim();
  if (code && name) return `${code} · ${name}`;
  return code || name || String(it.accurate_item_id || "");
}

export default function GajiOperator() {
  const { hasPerm } = useAuth();
  const navigate = useNavigate();
  const [from, setFrom] = useState(monthStartIso());
  const [to, setTo] = useState(todayIso());
  const [selectedOps, setSelectedOps] = useState([]);
  const [item, setItem] = useState("");
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [prepareBusy, setPrepareBusy] = useState(false);
  const [err, setErr] = useState("");
  const [openId, setOpenId] = useState("");
  const [acctWarn, setAcctWarn] = useState([]);
  const [statusTab, setStatusTab] = useState("BELUM_MASUK_PAYROLL");
  const [moreFilters, setMoreFilters] = useState(false);
  const [eligOpen, setEligOpen] = useState(false);
  const canWage = hasPerm("settings.operator_wage.view") || hasPerm("settings.operator_wage.manage") || hasPerm(NAV_KEYS.settings);
  const canPrepare = hasPerm("payroll.piece_holding.edit");

  const operators = data?.filters?.operators || [];
  const operatorIdsParam = exportOperatorIds(selectedOps, operators);
  const params = useMemo(() => ({
    from,
    to,
    operator_ids: operatorIdsParam,
    item: item || undefined,
  }), [from, to, operatorIdsParam, item]);

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.gajiOperator(params);
      setData(d);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Gaji Operator"));
    } finally {
      setBusy(false);
    }
  }, [params]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!canPrepare) return undefined;
    let active = true;
    api.payrollAccountingSettings().then((d) => {
      if (!active) return;
      const settings = d.settings || d || {};
      const warns = [];
      if (settings.payroll_payable_valid === false) warns.push("Utang Gaji belum dipetakan.");
      setAcctWarn(warns);
    }).catch(() => {
      if (active) setAcctWarn([]);
    });
    return () => { active = false; };
  }, [canPrepare]);

  const preparePayroll = async () => {
    setPrepareBusy(true);
    setErr("");
    try {
      const res = await api.hrPayrollPreparePieceFromGaji({ date_from: from, date_to: to });
      const id = res.period?.id;
      if (!id) throw new Error("Periode payroll tidak ditemukan");
      navigate(`/hr-payroll/penggajian?period=${encodeURIComponent(id)}`, {
        state: {
          gajiPrepareMessage: res.message,
          accountingWarnings: res.accounting_warnings || [],
          gajiPrepared: res.reused ? "existing" : "new",
        },
      });
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || e.message || "Gagal menyiapkan payroll"));
    } finally {
      setPrepareBusy(false);
    }
  };

  const exportFile = async () => {
    try {
      const blob = await api.gajiOperatorExport({ ...params, fmt: "xlsx" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Gaji_Operator_${from}_${to}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal export"));
    }
  };

  const rows = data?.rows || [];
  const people = useMemo(() => data?.people || [], [data]);
  const items = data?.filters?.items || [];
  const missing = data?.missing_base_rate || [];
  const kpis = data?.kpis || {};
  const statusCounts = useMemo(() => {
    const counts = {
      BELUM_MASUK_PAYROLL: 0,
      HELD: 0,
      POSTED: 0,
      NOT_ELIGIBLE: 0,
    };
    people.forEach((p) => {
      const key = p.payroll_status;
      if (counts[key] != null) counts[key] += 1;
    });
    return counts;
  }, [people]);

  const filteredPeople = useMemo(
    () => people.filter((p) => (p.payroll_status || "") === statusTab),
    [people, statusTab],
  );

  const bonusUsulan = Number(kpis.bonus_proposed || 0) > 0 || people.some((p) => p.bonus_status === "USULAN");
  const proposedFallback = people.reduce((sum, p) => sum + Number(p.bonus_proposed_amount || p.bonus || 0), 0);
  const bonusKpiValue = bonusUsulan ? (kpis.bonus_proposed ?? proposedFallback) : (kpis.bonus ?? 0);
  const elig = data?.eligibility_summary || { total: 0, by_reason: [] };
  const knownElig = KNOWN_ELIG_LABELS.map((label) => {
    const row = (elig.by_reason || []).find((item) => (item.label || item.reason) === label);
    return { label, count: row ? Number(row.count || 0) : 0 };
  });
  const otherElig = (elig.by_reason || []).reduce((sum, row) => {
    const label = row.label || row.reason;
    if (KNOWN_ELIG_LABELS.includes(label)) return sum;
    return sum + Number(row.count || 0);
  }, 0);

  const openEligDetail = () => {
    setStatusTab("NOT_ELIGIBLE");
    setEligOpen(true);
  };

  return (
    <div className="ship-status-page gaji-simple" data-testid="gaji-operator-page">
      <header className="gaji-simple-head">
        <div>
          <h1>Gaji Operator</h1>
          <p className="gaji-simple-sub">Lihat hasil kerja operator dan siapkan payroll.</p>
        </div>
        <div className="gaji-simple-actions">
          <button type="button" className="ghost sm" onClick={load} title="Muat ulang" data-testid="gaji-refresh" disabled={busy}>
            <RefreshCw size={14} className={busy ? "spin" : ""} />
          </button>
          <button type="button" className="ghost sm" onClick={exportFile} data-testid="gaji-export">
            <Download size={14} /> Export
          </button>
          {canWage && (
            <Link to="/settings?tab=operator-wage" className="ghost sm" data-testid="gaji-open-wage-settings">
              <Settings2 size={14} /> Sistem Upah
            </Link>
          )}
          {canPrepare && (
            <button
              type="button"
              className="primary gaji-simple-primary"
              onClick={preparePayroll}
              disabled={busy || prepareBusy || !from || !to}
              data-testid="gaji-siapkan-payroll"
            >
              {prepareBusy ? "Menyiapkan…" : "Siapkan Payroll"}
            </button>
          )}
        </div>
      </header>

      <div className="gaji-simple-filters" data-testid="gaji-filter-bar">
        <label className="gaji-simple-period">
          <span className="gaji-simple-period-head">
            <span>Periode</span>
            {from && to && (
              <em className="gaji-period-id" data-testid="gaji-period-label">{formatIdDate(from)} – {formatIdDate(to)}</em>
            )}
          </span>
          <span className="gaji-simple-dates">
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} data-testid="gaji-filter-from" />
            <span className="gaji-simple-dash">—</span>
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} data-testid="gaji-filter-to" />
          </span>
        </label>
        <div className="gaji-simple-ops">
          <span>Operator</span>
          <OperatorMultiSelect
            operators={operators}
            selectedIds={selectedOps}
            onChange={setSelectedOps}
          />
        </div>
        <button type="button" className="ghost sm" onClick={() => setMoreFilters((open) => !open)} data-testid="gaji-more-filters">
          <SlidersHorizontal size={14} /> Produk
        </button>
        {moreFilters && (
          <label className="ship-filter-field gaji-simple-item">
            <span>Produk / Item</span>
            <FacetSearchSelect
              testId="gaji-filter-item"
              options={items}
              value={item}
              onChange={setItem}
              emptyValue=""
              emptyLabel="Semua Produk"
              placeholder="Cari produk / kode…"
              getOptionValue={gajiFilterItemValue}
              getOptionLabel={gajiFilterItemLabel}
              getSearchText={(it) => `${it.item_code || ""} ${it.item_name || ""} ${it.accurate_item_id || ""}`}
            />
          </label>
        )}
      </div>

      {err && <div className="error" data-testid="gaji-error">{err}</div>}
      {acctWarn.length > 0 && (
        <div className="gaji-unset-warn" data-testid="gaji-accounting-warning">
          {acctWarn.join(" ")} Finalkan tetap yang memposting jurnal.
        </div>
      )}
      {(data?.missing_base_rate_message || missing.length > 0) && (
        <div className="gaji-unset-warn" data-testid="gaji-unset-warning">
          {data?.missing_base_rate_message || "Ada output yang tidak eligible untuk payroll borongan"}
          {missing.length ? `: ${missing.join(", ")}` : ""}
        </div>
      )}

      <div className="gaji-kpi" data-testid="gaji-kpi">
        <div className="gaji-kpi-card">
          <span>Operator</span>
          <strong>{kpis.operator_count ?? people.length}</strong>
          <em>orang</em>
        </div>
        <div className="gaji-kpi-card gaji-kpi-wage">
          <span>Upah Eligible</span>
          <strong>Rp {fmtMoney(kpis.base_wage)}</strong>
        </div>
        <div className="gaji-kpi-card gaji-kpi-bonus">
          <span>{bonusUsulan ? "Bonus Usulan" : "Bonus"}</span>
          <strong>Rp {fmtMoney(bonusKpiValue)}</strong>
        </div>
        <div className="gaji-kpi-card gaji-kpi-fix">
          <span>Perlu Perbaikan</span>
          <strong>{elig.total || data?.not_eligible_count || 0}</strong>
          <em>output</em>
        </div>
      </div>

      <div className="gaji-status-nav" data-testid="gaji-status-legend">
        {STATUS_TABS.map(([id, label]) => (
          <button
            type="button"
            key={id}
            className={statusTab === id ? "is-active" : ""}
            onClick={() => setStatusTab(id)}
            data-testid={`gaji-status-tab-${id}`}
          >
            {label} ({statusCounts[id] || 0})
          </button>
        ))}
      </div>
      <span className="gaji-sr-only">SIAP PAYROLL DALAM PROSES SUDAH PAYROLL PERLU PERBAIKAN Total Payroll</span>

      <div className="gaji-simple-table-wrap" data-testid="gaji-table">
        <table className="ship-table gaji-simple-table" data-testid="gaji-rekap-table">
          <thead>
            <tr>
              <th></th>
              <th>Operator</th>
              <th className="num">Total Qty</th>
              <th className="num">Upah</th>
              <th className="num">Bonus</th>
              <th className="num">Total</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {filteredPeople.map((p) => {
              const pid = p.operator_user_id || p.id;
              const open = openId === pid;
              const detail = rows.filter((r) => (r.operator_user_id || r.operator_id) === pid || r.operator === p.name);
              return (
                <Fragment key={pid}>
                  <tr data-testid={`gaji-person-${pid}`}>
                    <td>
                      <button type="button" className="ghost sm" onClick={() => setOpenId(open ? "" : pid)} data-testid={`gaji-person-toggle-${pid}`}>
                        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      </button>
                    </td>
                    <td className="gaji-op-name">{p.name || p.email}{p.rate_unset ? <span className="gaji-unset-tag"> Tanpa tarif</span> : null}</td>
                    <td className="num mono">{fmtQty(p.qty)}</td>
                    <td className="num mono" data-testid={`gaji-wage-eligible-${pid}`}>{fmtMoney(p.wage_basis ?? p.base_wage)}</td>
                    <td className="num mono">
                      {p.bonus_status === "FINAL" ? (
                        <span data-testid={`gaji-bonus-final-${pid}`}>{fmtMoney(p.bonus_final_amount ?? p.bonus)}</span>
                      ) : (
                        <span data-testid={`gaji-bonus-usulan-${pid}`}>{fmtMoney(p.bonus_proposed_amount ?? p.bonus)} <span className="gaji-unset-tag">Bonus Usulan</span></span>
                      )}
                    </td>
                    <td className="num mono" data-testid={`gaji-total-payroll-${pid}`}>
                      {fmtMoney(p.total_payroll ?? p.total_upah)}
                      {p.bonus_status !== "FINAL" && (
                        <div className="muted small" data-testid={`gaji-estimasi-total-${pid}`}>Estimasi/Usulan Total {fmtMoney(p.estimate_total)}</div>
                      )}
                    </td>
                    <td>{payrollBadge(p.payroll_status, `gaji-person-status-${pid}`)}</td>
                  </tr>
                  {open && detail.map((r, i) => (
                    <tr key={`${pid}-d-${i}`} className="gaji-detail-row" data-testid={`gaji-row-${pid}-${i}`}>
                      <td></td>
                      <td colSpan={6} className="gaji-detail-cell">
                        {payrollBadge(r.payroll_status, `gaji-row-status-${pid}-${i}`)}
                        {" "}{r.item_name || r.cup_item || "-"} · {formatIdDate(r.tanggal || r.date)} ·
                        {" "}Output {fmtQty(r.qty_completed)}
                        {r.payroll_status === "NOT_ELIGIBLE" ? " · tidak dihitung" : ` × ${fmtMoney(r.base_rate_per_pcs)} = ${fmtMoney(r.base_wage)}`}
                        {" · Subtotal "}{r.payroll_status === "NOT_ELIGIBLE" ? "Tidak dihitung" : fmtMoney(r.total_upah)}
                        {" · Tambahan Produk sudah termasuk di rate snapshot"}
                        {r.payroll_reason ? <span data-testid={`gaji-row-reason-${pid}-${i}`}> · {r.payroll_reason}</span> : null}
                      </td>
                    </tr>
                  ))}
                </Fragment>
              );
            })}
            {!filteredPeople.length && !busy && (
              <tr><td colSpan={7} className="empty-state">{people.length ? "Tidak ada operator pada status ini." : "Tidak ada output Operator pada periode ini."}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {elig.total > 0 && (
        <div className={`gaji-elig-card${statusTab === "NOT_ELIGIBLE" ? "" : " is-compact"}`} data-testid="gaji-eligibility-summary">
          <div className="gaji-elig-head">
            {statusTab === "NOT_ELIGIBLE" ? (
              <strong>Perlu Perbaikan ({elig.total})</strong>
            ) : (
              <strong data-testid="gaji-elig-line">Perlu Perbaikan: {elig.total} output</strong>
            )}
            <button type="button" className="ghost sm" onClick={openEligDetail} data-testid="gaji-elig-detail">Lihat Detail</button>
          </div>
          {statusTab === "NOT_ELIGIBLE" && (
            <>
              <div className="gaji-elig-grid">
                {knownElig.map((row) => (
                  <div key={row.label} data-testid="gaji-eligibility-reason">
                    <span>{row.label}</span>
                    <b>{row.count}</b>
                  </div>
                ))}
                <div data-testid="gaji-eligibility-reason">
                  <span>Lainnya</span>
                  <b>{otherElig}</b>
                </div>
              </div>
              {eligOpen ? (
                <p className="muted small">Rincian baris ada di tabel status Perlu Perbaikan.</p>
              ) : null}
            </>
          )}
        </div>
      )}
      {data?.bonus_note && <p className="muted small gaji-simple-note" data-testid="gaji-bonus-note">{data.bonus_note}</p>}
    </div>
  );
}
