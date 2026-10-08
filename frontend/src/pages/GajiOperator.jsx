import { useCallback, useEffect, useMemo, useState, Fragment } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Download, RefreshCw, Settings2, ChevronDown, ChevronRight, SlidersHorizontal, X } from "lucide-react";
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

const PAYMENT_STATUS = {
  BELUM_DIBAYAR: ["Belum Dibayar", "gaji-badge-pay-unpaid"],
  MENUNGGU_PERSETUJUAN: ["Menunggu Persetujuan", "gaji-badge-pay-wait"],
  SIAP_DIPOSTING: ["Siap Diposting", "gaji-badge-pay-ready"],
  SUDAH_DIBAYAR: ["Sudah Dibayar", "gaji-badge-pay-done"],
};

const STATUS_TABS = [
  ["BELUM_MASUK_PAYROLL", "Siap Payroll"],
  ["HELD", "Dalam Proses"],
  ["POSTED", "Sudah Payroll"],
  ["NOT_ELIGIBLE", "Perlu Perbaikan"],
];

function payrollBadge(status, testId) {
  const [label, tone] = PAYROLL_STATUS[status] || [status || "—", "hr-status-draft"];
  return <span className={`hr-status ${tone}`} data-testid={testId || "gaji-payroll-status"}>{label}</span>;
}

function paymentBadge(status, testId) {
  if (!status) return null;
  const [label, tone] = PAYMENT_STATUS[status] || [status, "hr-status-draft"];
  return <span className={`hr-status ${tone}`} data-testid={testId || "gaji-payment-status"}>{label}</span>;
}

function statusStack(payrollStatus, paymentStatus, testIdBase) {
  return (
    <div className="gaji-status-stack" data-testid={testIdBase}>
      <span className="gaji-status-stack-row">
        <em>Payroll</em>
        {payrollBadge(payrollStatus, `${testIdBase}-payroll`)}
      </span>
      {paymentStatus ? (
        <span className="gaji-status-stack-row">
          <em>Pembayaran</em>
          {paymentBadge(paymentStatus, `${testIdBase}-payment`)}
        </span>
      ) : null}
    </div>
  );
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

function RepairDrawer({ issue, mode, onClose, onSaved, canRepairDate, canRepairRate }) {
  const [workDate, setWorkDate] = useState("");
  const [rate, setRate] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [localErr, setLocalErr] = useState("");

  if (!issue) return null;
  const repair = issue.repair || {};
  const locked = repair.action_type === "locked" || issue.issue_bucket === "locked";
  const isDate = mode === "work_date";
  const isRate = mode === "rate";
  const canEdit = isDate ? canRepairDate : isRate ? canRepairRate : false;

  const save = async () => {
    if (!canEdit || locked) return;
    setBusy(true);
    setLocalErr("");
    try {
      if (isDate) {
        await api.gajiOperatorRepairWorkDate({
          order_id: issue.order_id,
          split_id: issue.split_id,
          action_id: issue.action_id,
          work_date: workDate,
          expected_work_date: issue.expected_work_date || issue.work_date || "",
          reason,
        });
      } else {
        await api.gajiOperatorRepairRate({
          order_id: issue.order_id,
          split_id: issue.split_id,
          action_id: issue.action_id,
          rate_per_pcs: Number(rate),
          expected_rate_per_pcs: issue.expected_rate_per_pcs == null || issue.expected_rate_per_pcs === ""
            ? null
            : Number(issue.expected_rate_per_pcs),
          reason,
        });
      }
      onSaved();
      onClose();
    } catch (e) {
      const det = e.response?.data?.detail;
      setLocalErr(typeof det === "object" ? (det.message || JSON.stringify(det)) : (det || e.message || "Gagal menyimpan"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal" data-testid="gaji-repair-drawer" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-inner gaji-repair-modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h3>{isDate ? "Perbaiki Tanggal Kerja" : isRate ? "Perbaiki Snapshot Tarif" : "Detail Masalah"}</h3>
          <button type="button" className="icon-button" onClick={onClose} data-testid="gaji-repair-close"><X size={16} /></button>
        </header>
        <div className="gaji-repair-body">
          <dl className="gaji-repair-meta">
            <div><dt>Operator</dt><dd>{issue.operator || issue.operator_name || "—"}</dd></div>
            <div><dt>PO</dt><dd>{issue.po_number || "—"}</dd></div>
            <div><dt>Qty</dt><dd>{fmtQty(issue.qty_completed || issue.qty)}</dd></div>
            <div><dt>Alasan</dt><dd data-testid="gaji-repair-reason-label">{repair.reason_label || issue.payroll_reason || "—"}</dd></div>
            <div><dt>Field</dt><dd>{repair.target_field || "—"}</dd></div>
            <div><dt>Tindakan</dt><dd>{repair.required_action || "—"}</dd></div>
          </dl>
          {locked && (
            <p className="gaji-repair-locked" data-testid="gaji-repair-locked">{repair.lock_reason || "Output terkunci."}</p>
          )}
          {!locked && isDate && canEdit && (
            <label className="gaji-repair-field">
              <span>Tanggal kerja (progress_entries.work_date)</span>
              <input type="date" value={workDate} onChange={(e) => setWorkDate(e.target.value)} data-testid="gaji-repair-work-date" autoFocus />
            </label>
          )}
          {!locked && isRate && canEdit && (
            <label className="gaji-repair-field">
              <span>Tarif per pcs (manual)</span>
              <input type="number" min="0" step="0.01" value={rate} onChange={(e) => setRate(e.target.value)} data-testid="gaji-repair-rate" autoFocus />
            </label>
          )}
          {!locked && canEdit && (isDate || isRate) && (
            <label className="gaji-repair-field">
              <span>Alasan koreksi</span>
              <input value={reason} onChange={(e) => setReason(e.target.value)} data-testid="gaji-repair-note" placeholder="Minimal 5 karakter" />
            </label>
          )}
          {!canEdit && !locked && (isDate || isRate) && (
            <p className="muted small" data-testid="gaji-repair-no-perm">Anda tidak punya permission untuk koreksi ini.</p>
          )}
          {localErr && <div className="error" data-testid="gaji-repair-error">{localErr}</div>}
        </div>
        <footer className="emp-actions gaji-repair-foot">
          <button type="button" className="ghost" onClick={onClose}>Tutup</button>
          {!locked && canEdit && (isDate || isRate) && (
            <button type="button" className="primary" disabled={busy} onClick={save} data-testid="gaji-repair-save">
              {busy ? "Menyimpan…" : "Simpan & Validasi Ulang"}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

function IssueActions({ issue, canRepairDate, canRepairRate, canHr, onRepair }) {
  const repair = issue.repair || {};
  const type = repair.action_type || "inspect";
  if (type === "locked") {
    return repair.target_route ? (
      <Link className="ghost sm" to={repair.target_route} data-testid={`gaji-fix-locked-${issue.source_key}`}>Lihat Payroll Terkunci</Link>
    ) : <span className="muted small">Terkunci</span>;
  }
  if (type === "repair_work_date") {
    return canRepairDate ? (
      <button type="button" className="primary sm" onClick={() => onRepair(issue, "work_date")} data-testid={`gaji-fix-date-${issue.source_key}`}>
        Perbaiki Tanggal Kerja
      </button>
    ) : <span className="muted small">Butuh permission koreksi tanggal</span>;
  }
  if (type === "repair_rate") {
    return canRepairRate ? (
      <button type="button" className="primary sm" onClick={() => onRepair(issue, "rate")} data-testid={`gaji-fix-rate-${issue.source_key}`}>
        Perbaiki Snapshot Tarif
      </button>
    ) : <span className="muted small">Butuh permission koreksi tarif</span>;
  }
  if (type === "open_hr") {
    return canHr && repair.target_route ? (
      <Link className="primary sm" to={repair.target_route} data-testid={`gaji-fix-hr-${issue.source_key}`}>Buka Profil HR</Link>
    ) : <span className="muted small">Butuh permission HR</span>;
  }
  if (type === "inspect_identity" && repair.target_route) {
    return <Link className="ghost sm" to={repair.target_route} data-testid={`gaji-fix-uid-${issue.source_key}`}>Periksa Identitas Operator</Link>;
  }
  if (repair.target_route) {
    return <Link className="ghost sm" to={repair.target_route} data-testid={`gaji-fix-inspect-${issue.source_key}`}>{repair.button_label || "Lihat Data Historis"}</Link>;
  }
  return <button type="button" className="ghost sm" onClick={() => onRepair(issue, "inspect")} data-testid={`gaji-fix-detail-${issue.source_key}`}>Lihat Detail</button>;
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
  const [historicOpen, setHistoricOpen] = useState(false);
  const [repairIssue, setRepairIssue] = useState(null);
  const [repairMode, setRepairMode] = useState("");
  const canWage = hasPerm("settings.operator_wage.view") || hasPerm("settings.operator_wage.manage") || hasPerm(NAV_KEYS.settings);
  const canPrepare = hasPerm("payroll.piece_holding.edit");
  const canRepairDate = hasPerm("reports.gaji_operator.repair_work_date");
  const canRepairRate = hasPerm("reports.gaji_operator.repair_rate");
  const canHr = hasPerm("hr.employee.manage") || hasPerm("hr.employee.view");

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
  const historicRows = data?.historic_rows || [];
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
  const elig = data?.eligibility_summary || { total: 0, by_reason: [], by_code: [] };
  const historicSummary = data?.historic_summary || { total: 0, by_reason: [] };
  const actionableIssues = useMemo(
    () => rows.filter((r) => r.payroll_status === "NOT_ELIGIBLE" && r.issue_bucket === "actionable"),
    [rows],
  );

  const openEligDetail = () => {
    setStatusTab("NOT_ELIGIBLE");
    setEligOpen(true);
  };

  const openRepair = (issue, mode) => {
    setRepairIssue(issue);
    setRepairMode(mode);
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
        {(Number(kpis.sudah_dibayar) > 0 || Number(kpis.belum_dibayar) > 0) && (
          <span className="gaji-pay-chips" data-testid="gaji-payment-summary">
            <span className="gaji-pay-chip is-paid" data-testid="gaji-pay-chip-paid">Sudah Dibayar: {kpis.sudah_dibayar || 0}</span>
            <span className="gaji-pay-chip is-unpaid" data-testid="gaji-pay-chip-unpaid">Belum Dibayar: {kpis.belum_dibayar || 0}</span>
          </span>
        )}
      </div>
      <span className="gaji-sr-only">SIAP PAYROLL DALAM PROSES SUDAH PAYROLL PERLU PERBAIKAN Total Payroll Belum Dibayar Sudah Dibayar</span>

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
                    <td className="gaji-status-cell">
                      {statusStack(p.payroll_status, p.payment_status, `gaji-person-status-${pid}`)}
                    </td>
                  </tr>
                  {open && detail.map((r, i) => (
                    <tr key={`${pid}-d-${i}`} className="gaji-detail-row" data-testid={`gaji-row-${pid}-${i}`}>
                      <td></td>
                      <td colSpan={6} className="gaji-detail-cell">
                        {statusStack(r.payroll_status, r.payment_status, `gaji-row-status-${pid}-${i}`)}
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

      {(elig.total > 0 || historicSummary.total > 0) && (
        <div className={`gaji-elig-card${statusTab === "NOT_ELIGIBLE" || eligOpen ? "" : " is-compact"}`} data-testid="gaji-eligibility-summary">
          <div className="gaji-elig-head">
            <strong data-testid="gaji-elig-line">Perlu Perbaikan: {elig.total || 0} output</strong>
            <button type="button" className="ghost sm" onClick={openEligDetail} data-testid="gaji-elig-detail">Lihat Detail</button>
          </div>
          {(statusTab === "NOT_ELIGIBLE" || eligOpen) && (
            <>
              <div className="gaji-elig-grid" data-testid="gaji-elig-by-code">
                {(elig.by_code || elig.by_reason || []).map((row) => (
                  <div key={row.reason_code || row.reason || row.label} data-testid="gaji-eligibility-reason">
                    <span>{row.label || row.reason_code || row.reason}</span>
                    <b>{row.count}</b>
                  </div>
                ))}
              </div>
              <div className="gaji-issue-list" data-testid="gaji-issue-list">
                {actionableIssues.map((issue) => (
                  <div key={issue.source_key} className="gaji-issue-row" data-testid={`gaji-issue-${issue.source_key}`}>
                    <div className="gaji-issue-main">
                      <strong>{issue.operator || issue.operator_name}</strong>
                      <span>{issue.po_number} · Qty {fmtQty(issue.qty_completed)}</span>
                      <span className="gaji-issue-reason">{issue.repair?.reason_label || issue.payroll_reason}</span>
                      <em className="muted">{issue.repair?.target_field || ""}</em>
                    </div>
                    <IssueActions
                      issue={issue}
                      canRepairDate={canRepairDate}
                      canRepairRate={canRepairRate}
                      canHr={canHr}
                      onRepair={openRepair}
                    />
                  </div>
                ))}
                {!actionableIssues.length && <p className="muted small">Tidak ada masalah actionable pada periode ini.</p>}
              </div>
            </>
          )}
        </div>
      )}

      {historicSummary.total > 0 && (
        <div className="gaji-historic-card" data-testid="gaji-historic-summary">
          <div className="gaji-elig-head">
            <strong>Historis / Legacy: {historicSummary.total} output</strong>
            <button type="button" className="ghost sm" onClick={() => setHistoricOpen((v) => !v)} data-testid="gaji-historic-toggle">
              {historicOpen ? "Sembunyikan" : "Lihat Data Historis"}
            </button>
          </div>
          <p className="muted small">Tidak dihitung sebagai Perlu Perbaikan periode aktif. Keterkaitan tanggal ke periode ini belum terbukti.</p>
          {historicOpen && (
            <div className="gaji-issue-list" data-testid="gaji-historic-list">
              {historicRows.slice(0, 100).map((issue) => (
                <div key={issue.source_key} className="gaji-issue-row is-historic" data-testid={`gaji-historic-${issue.source_key}`}>
                  <div className="gaji-issue-main">
                    <strong>{issue.operator || issue.operator_name}</strong>
                    <span>{issue.po_number} · Qty {fmtQty(issue.qty_completed)}</span>
                    <span className="gaji-issue-reason">{issue.repair?.reason_label || issue.payroll_reason}</span>
                  </div>
                  <IssueActions
                    issue={issue}
                    canRepairDate={canRepairDate}
                    canRepairRate={canRepairRate}
                    canHr={canHr}
                    onRepair={openRepair}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {data?.bonus_note && <p className="muted small gaji-simple-note" data-testid="gaji-bonus-note">{data.bonus_note}</p>}

      {repairIssue && (
        <RepairDrawer
          issue={repairIssue}
          mode={repairMode}
          onClose={() => { setRepairIssue(null); setRepairMode(""); }}
          onSaved={load}
          canRepairDate={canRepairDate}
          canRepairRate={canRepairRate}
        />
      )}
    </div>
  );
}
