import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, Edit3, Plus, Save, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { canTransition, moneyFormat, statusLabel } from "../lib/hrPayroll";
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";

const MODE_META = {
  employees: ["Daftar Karyawan", "hr-employees-page"],
  employment: ["Data Kepegawaian", "hr-employment-page"],
  payroll: ["Penggajian", "hr-payroll-page"],
  payslips: ["Slip Gaji", "hr-payslips-page"],
  components: ["Komponen Gaji", "hr-components-page"],
  history: ["Riwayat Payroll", "hr-history-page"],
};

const emptyEmployee = {
  name: "", employee_number: "", role: "", division: "", employment_status: "tetap",
  join_date: "", end_date: "", worker_type: "monthly", email: "", phone: "", address: "",
  bank_name: "", bank_account: "", bank_account_name: "", user_id: "", active: true,
  salary_profile: { base_salary: 0, allowances: [], effective_from: "" },
};
const emptyComponent = {
  code: "", name: "", kind: "earning", calc_mode: "fixed", amount: 0,
  percentage_of: "base", recurring: true, active: true, order: 1,
};

const errorText = (ex) => ex?.response?.data?.detail || ex?.message || "Permintaan gagal";
const employeeName = (row) => row?.employee_snapshot?.name || row?.employee?.name || row?.name || "-";

function PageHead({ title, action }) {
  return <div className="page-heading"><div><p className="eyebrow">HR &amp; Payroll</p><h1>{title}</h1></div>{action}</div>;
}

function EmployeeForm({ initial, onClose, onSaved }) {
  const [form, setForm] = useState(() => ({ ...emptyEmployee, ...initial, salary_profile: { ...emptyEmployee.salary_profile, ...(initial?.salary_profile || {}) } }));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (key, value) => setForm((cur) => ({ ...cur, [key]: value }));
  const save = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const payload = { ...form, user_id: form.user_id || null, salary_profile: { ...form.salary_profile, base_salary: Number(form.salary_profile.base_salary) || 0 } };
      if (initial?.id) await api.hrPayrollUpdateEmployee(initial.id, payload);
      else await api.hrPayrollCreateEmployee(payload);
      await onSaved();
      onClose();
    } catch (ex) { setErr(errorText(ex)); } finally { setBusy(false); }
  };
  return (
    <div className="modal" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-inner modal-wide">
        <header className="modal-head"><h3>{initial?.id ? "Edit Karyawan" : "Tambah Karyawan"}</h3><button className="icon-button" onClick={onClose}><X size={16} /></button></header>
        <form className="emp-form" onSubmit={save} data-testid="hr-employee-form">
          <FormProgress
            percentage={Math.round(([form.name, form.employee_number, form.role, form.division, form.join_date].filter(v => v).length / 5) * 100)}
            completed={[form.name, form.employee_number, form.role, form.division, form.join_date].filter(v => v).length}
            total={5}
          />
          <FormSection
            id="hr-employee-info"
            title="Informasi Karyawan"
            description="Data dasar dan kepegawaian."
            status={computeStatus([
              { value: form.name, isRequired: true },
              { value: form.employee_number, isRequired: true },
            ])}
          >
            <div className="hr-form-grid">
              {[["name", "Nama lengkap"], ["employee_number", "Nomor karyawan"], ["role", "Jabatan"], ["division", "Divisi"], ["employment_status", "Status kerja"], ["join_date", "Tanggal bergabung"], ["end_date", "Tanggal berakhir"], ["worker_type", "Tipe pekerja"], ["email", "Email"], ["phone", "Telepon"], ["bank_name", "Nama bank"], ["bank_account", "Nomor rekening"], ["bank_account_name", "Nama pemilik rekening"], ["user_id", "User ID (opsional)"]].map(([key, label]) => (
                <FormField key={key} id={`hr-emp-${key}`} label={label} isRequired={["name", "employee_number"].includes(key)} value={form[key] || ""}>
                  <input type={key.includes("date") ? "date" : "text"} value={form[key] || ""} onChange={(e) => set(key, e.target.value)} required={["name", "employee_number"].includes(key)} />
                </FormField>
              ))}
              <FormField id="hr-emp-gaji" label="Gaji pokok" value={form.salary_profile.base_salary}>
                <input type="number" min="0" value={form.salary_profile.base_salary || 0} onChange={(e) => setForm((cur) => ({ ...cur, salary_profile: { ...cur.salary_profile, base_salary: e.target.value } }))} />
              </FormField>
              <FormField id="hr-emp-efektif" label="Efektif gaji sejak" value={form.salary_profile.effective_from}>
                <input type="date" value={form.salary_profile.effective_from || ""} onChange={(e) => setForm((cur) => ({ ...cur, salary_profile: { ...cur.salary_profile, effective_from: e.target.value } }))} />
              </FormField>
              <FormField id="hr-emp-alamat" label="Alamat" className="hr-span-2" value={form.address}>
                <textarea value={form.address || ""} onChange={(e) => set("address", e.target.value)} />
              </FormField>
            </div>
          </FormSection>
          {err && <div className="error"><AlertCircle size={14} /> {err}</div>}
          <div className="emp-actions"><button type="button" className="ghost" onClick={onClose}>Batal</button><button className="primary" disabled={busy}><Save size={14} /> Simpan</button></div>
        </form>
      </div>
    </div>
  );
}

function Employees({ employment = false }) {
  const { hasPerm } = useAuth();
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState(null);
  const [editing, setEditing] = useState(null);
  const [err, setErr] = useState("");
  const load = useCallback(async () => {
    try {
      const data = await api.hrPayrollEmployees();
      setRows(data.employees || []);
      setSelected((cur) => (cur ? (data.employees || []).find((r) => r.id === cur.id) || null : null));
    } catch (ex) { setErr(errorText(ex)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  if (employment) {
    return (
      <div data-testid="hr-employment-page">
        <PageHead title="Data Kepegawaian" />
        {err && <div className="error">{err}</div>}
        <div className="hr-master-detail">
          <div className="settings-card"><h3>Karyawan</h3>{rows.map((row) => <button type="button" className={`hr-list-button ${selected?.id === row.id ? "active" : ""}`} key={row.id} onClick={() => setSelected(row)}>{row.employee_number} · {row.name}</button>)}</div>
          <div className="settings-card">
            {!selected ? <p className="muted">Pilih karyawan untuk melihat detail.</p> : <>
              <div className="hr-card-head"><h3>{selected.name}</h3>{hasPerm("hr.employee.manage") && <button className="ghost sm" onClick={() => setEditing(selected)}><Edit3 size={13} /> Edit</button>}</div>
              <dl className="hr-detail-list">
                <dt>Status kerja</dt><dd>{selected.employment_status || "-"}</dd><dt>Jabatan / Divisi</dt><dd>{selected.role || "-"} / {selected.division || "-"}</dd>
                <dt>Masa kerja</dt><dd>{selected.join_date || "-"} s.d. {selected.end_date || "sekarang"}</dd><dt>Gaji pokok</dt><dd>{moneyFormat(selected.salary_profile?.base_salary)}</dd>
                <dt>Bank</dt><dd>{selected.bank_name || "-"} · {selected.bank_account || "-"}</dd>
              </dl>
              <h4>Riwayat Kepegawaian</h4>
              {(selected.employment_history || []).length ? (selected.employment_history || []).map((item, i) => <div key={i} className="hr-history-item">{item.title || item.role || "Perubahan"} · {item.effective_date || item.date || "-"}</div>) : <p className="muted">Belum ada riwayat.</p>}
            </>}
          </div>
        </div>
        {editing && <EmployeeForm initial={editing} onClose={() => setEditing(null)} onSaved={load} />}
      </div>
    );
  }
  return (
    <div data-testid="hr-employees-page">
      <PageHead title="Daftar Karyawan" action={hasPerm("hr.employee.manage") && <button className="primary" onClick={() => setEditing({})}><Plus size={14} /> Tambah Karyawan</button>} />
      {err && <div className="error">{err}</div>}
      <div className="table-wrap"><table className="ship-table"><thead><tr><th>No.</th><th>Nama</th><th>Jabatan</th><th>Divisi</th><th>Status</th><th>Gaji Pokok</th><th></th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.id}><td>{row.employee_number}</td><td><strong>{row.name}</strong></td><td>{row.role || "-"}</td><td>{row.division || "-"}</td><td>{row.active === false ? "Nonaktif" : "Aktif"}</td><td>{moneyFormat(row.salary_profile?.base_salary)}</td><td>{hasPerm("hr.employee.manage") && <button className="ghost sm" onClick={() => setEditing(row)}>Edit</button>}</td></tr>)}</tbody>
      </table></div>
      {editing && <EmployeeForm initial={editing.id ? editing : null} onClose={() => setEditing(null)} onSaved={load} />}
    </div>
  );
}

function Components() {
  const { hasPerm } = useAuth();
  const [rows, setRows] = useState([]);
  const [editing, setEditing] = useState(null);
  const [err, setErr] = useState("");
  const load = useCallback(async () => { try { setRows((await api.hrPayrollComponents()).components || []); } catch (ex) { setErr(errorText(ex)); } }, []);
  useEffect(() => { load(); }, [load]);
  const save = async (e) => {
    e.preventDefault();
    try {
      const payload = { ...editing, amount: Number(editing.amount) || 0, order: Number(editing.order) || 0 };
      if (editing.id) await api.hrPayrollUpdateComponent(editing.id, payload); else await api.hrPayrollCreateComponent(payload);
      setEditing(null); await load();
    } catch (ex) { setErr(errorText(ex)); }
  };
  return <div data-testid="hr-components-page"><PageHead title="Komponen Gaji" action={hasPerm("payroll.settings") && <button className="primary" onClick={() => setEditing(emptyComponent)}><Plus size={14} /> Tambah Komponen</button>} />{err && <div className="error">{err}</div>}
    <div className="table-wrap"><table className="ship-table"><thead><tr><th>Kode</th><th>Nama</th><th>Jenis</th><th>Perhitungan</th><th>Nilai</th><th>Status</th><th></th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{row.code}</td><td>{row.name}</td><td>{row.kind === "earning" ? "Pendapatan" : "Potongan"}</td><td>{row.calc_mode}</td><td>{moneyFormat(row.amount)}</td><td>{row.active === false ? "Nonaktif" : "Aktif"}</td><td>{hasPerm("payroll.settings") && <button className="ghost sm" onClick={() => setEditing(row)}>Edit</button>}</td></tr>)}</tbody></table></div>
    {editing && <div className="modal" onClick={(e) => e.target === e.currentTarget && setEditing(null)}><div className="modal-inner"><header className="modal-head"><h3>Komponen Gaji</h3><button className="icon-button" onClick={() => setEditing(null)}><X size={16} /></button></header><form className="emp-form" onSubmit={save}><div className="hr-form-grid">
      <label>Kode<input value={editing.code} onChange={(e) => setEditing({ ...editing, code: e.target.value.toUpperCase() })} required /></label><label>Nama<input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} required /></label>
      <label>Jenis<select value={editing.kind} onChange={(e) => setEditing({ ...editing, kind: e.target.value })}><option value="earning">Pendapatan</option><option value="deduction">Potongan</option></select></label>
      <label>Mode<select value={editing.calc_mode} onChange={(e) => setEditing({ ...editing, calc_mode: e.target.value })}>{["fixed", "percentage", "per_day", "per_hour", "per_minute"].map((v) => <option key={v}>{v}</option>)}</select></label>
      <label>Nilai<input type="number" value={editing.amount} onChange={(e) => setEditing({ ...editing, amount: e.target.value })} /></label><label className="hr-check"><input type="checkbox" checked={editing.active !== false} onChange={(e) => setEditing({ ...editing, active: e.target.checked })} /> Aktif</label>
    </div><button className="primary"><Save size={14} /> Simpan</button></form></div></div>}
  </div>;
}

function AttendanceEditor({ period, employees, attendance, onSaved }) {
  const [draft, setDraft] = useState(() => Object.fromEntries((attendance || []).map((row) => [row.employee_id, row])));
  const save = async (employeeId) => {
    await api.hrPayrollUpdateAttendance(period.id, employeeId, draft[employeeId] || {});
    onSaved();
  };
  if (period.status !== "DRAFT") return null;
  return <div className="settings-card"><h3>Kehadiran</h3><div className="table-wrap"><table className="ship-table"><thead><tr><th>Karyawan</th><th>Hari kerja</th><th>Terlambat (menit)</th><th>Alpha</th><th>Lembur hari kerja (jam)</th><th></th></tr></thead><tbody>{employees.map((emp) => {
    const row = draft[emp.id] || { employee_id: emp.id, work_days: 0, late_minutes: 0, alpha_days: 0, overtime_weekday_hours: 0, overtime_weekend_hours: 0, overtime_holiday_hours: 0, unpaid_leave_days: 0, unpaid_sick_days: 0 };
    const field = (key) => <input className="hr-number" type="number" min="0" value={row[key] || 0} onChange={(e) => setDraft((cur) => ({ ...cur, [emp.id]: { ...row, [key]: Number(e.target.value) } }))} />;
    return <tr key={emp.id}><td>{emp.name}</td><td>{field("work_days")}</td><td>{field("late_minutes")}</td><td>{field("alpha_days")}</td><td>{field("overtime_weekday_hours")}</td><td><button className="ghost sm" onClick={() => save(emp.id)}>Simpan</button></td></tr>;
  })}</tbody></table></div></div>;
}

function Payroll() {
  const { hasPerm } = useAuth();
  const [periods, setPeriods] = useState([]);
  const [selected, setSelected] = useState(null);
  const [lines, setLines] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [attendance, setAttendance] = useState([]);
  const [kasbon, setKasbon] = useState([]);
  const [form, setForm] = useState({ year: new Date().getFullYear(), month: new Date().getMonth() + 1, start_date: "", end_date: "" });
  const [err, setErr] = useState("");
  const loadPeriods = useCallback(async () => { const data = await api.hrPayrollPeriods(); setPeriods(data.periods || []); }, []);
  const loadDetail = useCallback(async (period) => {
    if (!period?.id) return;
    const [p, l, a, e, k] = await Promise.all([api.hrPayrollPeriod(period.id), api.hrPayrollLines(period.id), api.hrPayrollAttendance(period.id), api.hrPayrollEmployees(), api.hrPayrollKasbonSchedules({ active_only: true })]);
    setSelected(p.period); setLines(l.lines || []); setAttendance(a.attendance || []); setEmployees(e.employees || []); setKasbon(k.schedules || []);
  }, []);
  useEffect(() => { loadPeriods().catch((ex) => setErr(errorText(ex))); }, [loadPeriods]);
  const create = async (e) => { e.preventDefault(); try { const res = await api.hrPayrollCreatePeriod({ ...form, year: Number(form.year), month: Number(form.month) }); await loadPeriods(); await loadDetail(res.period); } catch (ex) { setErr(errorText(ex)); } };
  const calculate = async () => { try { await api.hrPayrollCalculate(selected.id); await loadDetail(selected); } catch (ex) { setErr(errorText(ex)); } };
  const transition = async (target) => { try { await api.hrPayrollTransition(selected.id, target); await loadPeriods(); await loadDetail(selected); } catch (ex) { setErr(errorText(ex)); } };
  const reopen = async () => { const reason = window.prompt("Alasan buka ulang payroll:"); if (!reason) return; try { const res = await api.hrPayrollReopen(selected.id, reason); await loadPeriods(); await loadDetail(res.period); } catch (ex) { setErr(errorText(ex)); } };
  const transitionButtons = [
    ["DRAFT", "payroll.review", "hr-transition-draft", "Kembalikan ke Draft"],
    ["REVIEW", "payroll.review", "hr-transition-review", "Kirim Review"],
    ["APPROVAL", "payroll.approve", "hr-transition-approval", "Kirim Persetujuan"],
    ["FINAL", "payroll.finalize", "hr-transition-final", "Finalkan"],
  ];
  return <div data-testid="hr-payroll-page"><PageHead title="Penggajian" />{err && <div className="error"><AlertCircle size={14} /> {err}</div>}
    {hasPerm("payroll.calculate") && <form className="settings-card hr-period-form" onSubmit={create}><h3>Buat Periode</h3>{[["year", "Tahun", "number"], ["month", "Bulan", "number"], ["start_date", "Tanggal mulai", "date"], ["end_date", "Tanggal akhir", "date"]].map(([key, label, type]) => <label key={key}>{label}<input type={type} value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} required /></label>)}<button className="primary"><Plus size={14} /> Buat</button></form>}
    <div className="hr-master-detail"><div className="settings-card"><h3>Periode</h3>{periods.map((p) => <button type="button" className={`hr-list-button ${selected?.id === p.id ? "active" : ""}`} key={p.id} onClick={() => loadDetail(p).catch((ex) => setErr(errorText(ex)))}><span>{p.period_number || `${p.month}/${p.year}`}</span><span className={`hr-status hr-status-${String(p.status).toLowerCase()}`}>{statusLabel(p.status)}</span></button>)}</div>
      <div className="hr-payroll-detail">{!selected ? <div className="settings-card"><p className="muted">Pilih periode payroll.</p></div> : <>
        <div className="settings-card"><div className="hr-card-head"><div><h3>{selected.period_number || `${selected.month}/${selected.year}`}</h3><span className={`hr-status hr-status-${String(selected.status).toLowerCase()}`}>{statusLabel(selected.status)}</span></div><div className="hr-actions">
          {selected.status === "DRAFT" && hasPerm("payroll.calculate") && <button className="primary" onClick={calculate} data-testid="hr-calc-btn">Hitung Payroll</button>}
          {transitionButtons.map(([target, perm, testid, label]) => canTransition(selected.status, target) && hasPerm(perm) ? <button key={target} className="primary" onClick={() => transition(target)} data-testid={testid}>{label}</button> : null)}
          {selected.status === "FINAL" && hasPerm("payroll.reopen") && <button className="ghost" onClick={reopen} data-testid="hr-reopen-btn">Buka Ulang</button>}
        </div></div></div>
        <AttendanceEditor period={selected} employees={employees} attendance={attendance} onSaved={() => loadDetail(selected)} />
        <div className="settings-card" data-testid="hr-breakdown"><h3>Rincian Payroll</h3><div className="table-wrap"><table className="ship-table"><thead><tr><th>Karyawan</th><th>Pendapatan</th><th>Potongan</th><th>Gaji Bersih</th></tr></thead><tbody>{lines.map((line) => <tr key={line.id}><td>{employeeName(line)}</td><td>{moneyFormat(line.gross)}</td><td>{moneyFormat(line.total_deductions)}</td><td><strong>{moneyFormat(line.net)}</strong><div className="muted small">{(line.earnings || []).map((r) => `${r.name}: ${moneyFormat(r.amount)}`).join(" · ")}</div></td></tr>)}</tbody></table></div></div>
        <div className="settings-card"><h3>Jadwal Kasbon</h3><div className="table-wrap"><table className="ship-table"><tbody>{kasbon.map((row) => <tr key={row.id} data-testid="hr-kasbon-row"><td>{row.employee_name || row.employee_id}</td><td>{row.request_number || row.request_id}</td><td>{moneyFormat(row.outstanding)}</td></tr>)}</tbody></table></div></div>
      </>}</div>
    </div>
  </div>;
}

function History({ payslips = false }) {
  const [data, setData] = useState({ periods: [], lines: [] });
  const [err, setErr] = useState("");
  useEffect(() => { api.hrPayrollHistory().then(setData).catch((ex) => setErr(errorText(ex))); }, []);
  if (payslips) return <div data-testid="hr-payslips-page"><PageHead title="Slip Gaji" />{err && <div className="error">{err}</div>}<div className="table-wrap"><table className="ship-table"><thead><tr><th>Periode</th><th>Karyawan</th><th>Gaji Bersih</th><th>Slip</th></tr></thead><tbody>{(data.lines || []).map((line) => <tr key={line.id}><td>{line.period_number || line.period_id}</td><td>{employeeName(line)}</td><td>{moneyFormat(line.net)}</td><td><a className="linkish" href={api.hrPayrollPayslipUrl(line.period_id, line.id)} target="_blank" rel="noreferrer">PDF</a></td></tr>)}</tbody></table></div></div>;
  return <div data-testid="hr-history-page"><PageHead title="Riwayat Payroll" />{err && <div className="error">{err}</div>}<div className="table-wrap"><table className="ship-table"><thead><tr><th>Periode</th><th>Tahun</th><th>Bulan</th><th>Revisi</th><th>Status</th><th>Jumlah Karyawan</th></tr></thead><tbody>{(data.periods || []).map((p) => <tr key={p.id}><td>{p.period_number}</td><td>{p.year}</td><td>{p.month}</td><td>{p.revision || 1}</td><td>{statusLabel(p.status)}</td><td>{p.summary?.line_count || "-"}</td></tr>)}</tbody></table></div></div>;
}

export default function HrPayroll({ mode }) {
  const [title, testid] = MODE_META[mode] || MODE_META.employees;
  const content = useMemo(() => {
    if (mode === "employees") return <Employees />;
    if (mode === "employment") return <Employees employment />;
    if (mode === "payroll") return <Payroll />;
    if (mode === "payslips") return <History payslips />;
    if (mode === "components") return <Components />;
    if (mode === "history") return <History />;
    return <div data-testid={testid}><PageHead title={title} /></div>;
  }, [mode, testid, title]);
  return <div className="hr-page">{content}</div>;
}
