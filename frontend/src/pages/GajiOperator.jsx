import { useCallback, useEffect, useMemo, useState, Fragment } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Download, RefreshCw, Settings2, ChevronDown, ChevronRight } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate } from "../lib/constants";
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

const PAYROLL_STATUS = {
  BELUM_MASUK_PAYROLL: ["Siap Payroll", "hr-status-draft"],
  HELD: ["Dalam Proses", "hr-status-review"],
  POSTED: ["Sudah Payroll", "hr-status-final"],
  NOT_ELIGIBLE: ["Perlu Perbaikan", "hr-status-approval"],
};

const PAYROLL_STATUS_GROUP = [
  ["SIAP PAYROLL", "Siap Payroll", "hr-status-draft"],
  ["DALAM PROSES", "Dalam Proses", "hr-status-review"],
  ["SUDAH PAYROLL", "Sudah Payroll", "hr-status-final"],
  ["PERLU PERBAIKAN", "Perlu Perbaikan", "hr-status-approval"],
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
  const people = data?.people || [];
  const items = data?.filters?.items || [];
  const missing = data?.missing_base_rate || [];

  return (
    <div className="ship-status-page" data-testid="gaji-operator-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading">
          <h1>Gaji Operator</h1>
        </header>
        <div className="ship-status-toolbar" data-testid="gaji-filter-bar">
          <label className="ship-filter-field">
            <span>Tanggal Mulai</span>
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} data-testid="gaji-filter-from" />
          </label>
          <label className="ship-filter-field">
            <span>Tanggal Selesai</span>
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} data-testid="gaji-filter-to" />
          </label>
          <OperatorMultiSelect
            operators={operators}
            selectedIds={selectedOps}
            onChange={setSelectedOps}
          />
          <label className="ship-filter-field">
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
          <button type="button" className="ship-icon-btn" onClick={load} title="Muat ulang" data-testid="gaji-refresh" disabled={busy}>
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
              className="primary sm"
              onClick={preparePayroll}
              disabled={busy || prepareBusy || !from || !to}
              data-testid="gaji-siapkan-payroll"
            >
              {prepareBusy ? "Menyiapkan…" : "Siapkan Payroll"}
            </button>
          )}
        </div>
        {err && <div className="error" data-testid="gaji-error">{err}</div>}
        {acctWarn.length > 0 && (
          <div className="gaji-unset-warn" data-testid="gaji-accounting-warning">
            {acctWarn.join(" ")} Finalkan tetap yang memposting jurnal.
          </div>
        )}
        {(data?.missing_base_rate_message || missing.length > 0 || data?.not_eligible_count > 0) && (
          <div className="gaji-unset-warn" data-testid="gaji-unset-warning">
            {data?.missing_base_rate_message || "Ada output yang tidak eligible untuk payroll borongan"}
            {missing.length ? `: ${missing.join(", ")}` : ""}
            {data?.not_eligible_count ? ` · ${data.not_eligible_count} output Tidak Eligible` : ""}
          </div>
        )}
        {data?.eligibility_summary?.total > 0 && (
          <div className="gaji-elig-summary" data-testid="gaji-eligibility-summary">
            <strong>Tidak Eligible: {data.eligibility_summary.total}</strong>
            <ul>
              {(data.eligibility_summary.by_reason || []).map((row) => (
                <li key={row.reason} data-testid="gaji-eligibility-reason">
                  {row.label || row.reason}: {row.count}
                </li>
              ))}
            </ul>
            <p className="muted small">Rincian baris tetap dapat dibuka di tabel.</p>
          </div>
        )}
        <div className="gaji-status-legend" data-testid="gaji-status-legend">
          {PAYROLL_STATUS_GROUP.map(([group, label, tone]) => (
            <span key={group} className={`hr-status ${tone}`}>{group} · {label}</span>
          ))}
        </div>
        {data?.bonus_note && <p className="muted small" data-testid="gaji-bonus-note">{data.bonus_note}</p>}
      </div>
      <div className="ship-status-grid" data-testid="gaji-table">
        <table className="ship-table" data-testid="gaji-rekap-table">
          <thead>
            <tr>
              <th></th>
              <th>Nama Operator</th>
              <th className="num">Total Output</th>
              <th className="num">Upah Eligible</th>
              <th className="num">Tambahan Produk</th>
              <th className="num">Bonus</th>
              <th className="num">Total Payroll</th>
            </tr>
          </thead>
          <tbody>
            {people.map((p) => {
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
                    <td>{p.name || p.email} {payrollBadge(p.payroll_status, `gaji-person-status-${pid}`)}{p.rate_unset ? <span className="gaji-unset-tag"> Tanpa tarif</span> : null}</td>
                    <td className="num mono">{fmtQty(p.qty)}</td>
                    <td className="num mono" data-testid={`gaji-wage-eligible-${pid}`}>{fmtMoney(p.wage_basis ?? p.base_wage)}</td>
                    <td className="num mono" title="Sudah termasuk di rate snapshot payroll">—</td>
                    <td className="num mono">
                      {p.bonus_status === "FINAL" ? (
                        <span data-testid={`gaji-bonus-final-${pid}`}>{fmtMoney(p.bonus_final_amount ?? p.bonus)} <span className="gaji-unset-tag">Final</span></span>
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
                  </tr>
                  {open && detail.map((r, i) => (
                    <tr key={`${pid}-d-${i}`} className="gaji-detail-row" data-testid={`gaji-row-${pid}-${i}`}>
                      <td></td>
                      <td colSpan={6} className="gaji-detail-cell">
                        {payrollBadge(r.payroll_status, `gaji-row-status-${pid}-${i}`)}
                        {" "}{r.item_name || r.cup_item || "-"} · {formatDisplayDate(r.tanggal || r.date)} ·
                        {" "}Output {fmtQty(r.qty_completed)}
                        {r.payroll_status === "NOT_ELIGIBLE" ? " · tidak dihitung" : ` × ${fmtMoney(r.base_rate_per_pcs)} = ${fmtMoney(r.base_wage)}`}
                        {" · Subtotal "}{r.payroll_status === "NOT_ELIGIBLE" ? "Tidak dihitung" : fmtMoney(r.total_upah)}
                        {r.payroll_reason ? <span data-testid={`gaji-row-reason-${pid}-${i}`}> · {r.payroll_reason}</span> : null}
                      </td>
                    </tr>
                  ))}
                </Fragment>
              );
            })}
            {!people.length && !busy && (
              <tr><td colSpan={7} className="empty-state">Tidak ada output Operator pada periode ini.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
