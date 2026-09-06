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

const PREVIEW_TREATMENT_LABELS = {
  expense_debit: "Beban / Cost",
  expense_credit: "Pengurang Beban",
  liability_credit: "Kewajiban",
  asset_credit: "Pengurang Aset",
};

function previewIsPayable(row) {
  const keys = row?.source_keys || [];
  return keys.includes("payroll_payable") || row?.code === "PAYROLL_PAYABLE";
}

function previewSourceLabel(row) {
  if (previewIsPayable(row)) return "Utang Gaji (net)";
  const keys = row?.source_keys || [];
  return keys[0] || row?.code || "—";
}

function previewTreatmentLabel(treatment) {
  return PREVIEW_TREATMENT_LABELS[treatment] || treatment || "—";
}

function previewAccountLabel(row) {
  const acc = row?.account || {};
  const code = String(acc.code || "").trim();
  const name = String(acc.name || "").trim();
  if (code && name) return `${code} · ${name}`;
  if (code) return code;
  if (name) return name;
  return "—";
}

function PageHead({ title, action }) {
  return <div className="page-heading"><div><p className="eyebrow">HR &amp; Payroll</p><h1>{title}</h1></div><div className="page-heading-actions">{action}</div></div>;
}

function EmployeeForm({ initial, onClose, onSaved, roles = [], canCreateEternalAccount = false, startWithAccountCreate = false }) {
  const isCreateProfileFromUser = Boolean(initial?.has_hr_profile === false && initial?.user_id);
  const [form, setForm] = useState(() => {
    if (isCreateProfileFromUser) {
      return {
        ...emptyEmployee,
        name: initial?.name || "",
        email: initial?.email || "",
        role: initial?.role || "",
        role_label: initial?.role_label || "",
        phone: initial?.phone || "",
        user_id: initial?.user_id || "",
        salary_profile: { ...emptyEmployee.salary_profile },
      };
    }
    return {
      ...emptyEmployee,
      ...initial,
      salary_profile: { ...emptyEmployee.salary_profile, ...(initial?.salary_profile || {}) },
    };
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [createAccount, setCreateAccount] = useState(() => Boolean(startWithAccountCreate && !isCreateProfileFromUser));
  const [account, setAccount] = useState({
    name: initial?.name || "",
    email: initial?.email || "",
    role: "",
    password: "",
    nickname: "",
    phone: initial?.phone || "",
  });
  const linkedFromSettings = Boolean(form.user_id || initial?.linked_from_settings);
  const isVirtualDirectory = initial?.has_hr_profile === false && !isCreateProfileFromUser;
  const isCreate = !initial?.id;
  const isHrOnlyUnlinked = Boolean(initial?.id && !initial?.user_id && initial?.has_hr_profile !== false);
  const accountMode = isCreate && !isCreateProfileFromUser ? createAccount : (isHrOnlyUnlinked && createAccount);
  const hasHrProfile = Boolean(initial?.id) || isCreate;
  const set = (key, value) => setForm((cur) => ({ ...cur, [key]: value }));
  const setAccountField = (key, value) => setAccount((cur) => ({ ...cur, [key]: value }));
  const activeRoles = useMemo(() => (roles || []).filter((r) => r.status !== "inactive"), [roles]);

  useEffect(() => {
    if (!createAccount) return;
    setAccount((cur) => ({
      ...cur,
      name: cur.name || form.name || "",
      email: cur.email || form.email || "",
      phone: cur.phone || form.phone || "",
      role: cur.role || (activeRoles[0]?.role || ""),
    }));
  }, [createAccount, form.name, form.email, form.phone, activeRoles]);

  useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const scrollY = window.scrollY;
    const previous = {
      htmlOverflow: html.style.overflow,
      bodyOverflow: body.style.overflow,
      bodyPosition: body.style.position,
      bodyTop: body.style.top,
      bodyWidth: body.style.width,
    };
    html.style.overflow = "hidden";
    body.style.overflow = "hidden";
    body.style.position = "fixed";
    body.style.top = `-${scrollY}px`;
    body.style.width = "100%";
    return () => {
      html.style.overflow = previous.htmlOverflow;
      body.style.overflow = previous.bodyOverflow;
      body.style.position = previous.bodyPosition;
      body.style.top = previous.bodyTop;
      body.style.width = previous.bodyWidth;
      window.scrollTo(0, scrollY);
    };
  }, []);

  const save = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      if (isVirtualDirectory) {
        setErr("Profil HR belum ada. Gunakan Tambah Karyawan, atau kelola nama/role di Settings → Karyawan.");
        return;
      }
      if (accountMode) {
        if (!canCreateEternalAccount) {
          setErr("Anda tidak berhak membuat akun Eternal (users.manage).");
          return;
        }
        if (!account.email?.trim() || !account.role || !account.password || account.password.length < 6) {
          setErr("Akun Eternal membutuhkan email, role, dan password minimal 6 karakter.");
          return;
        }
      }
      if (isHrOnlyUnlinked && createAccount) {
        await api.hrPayrollCreateEternalAccount(initial.id, {
          name: account.name || form.name,
          email: account.email.trim().toLowerCase(),
          role: account.role,
          password: account.password,
          nickname: account.nickname || null,
          phone: account.phone || form.phone || null,
        });
      } else {
        const payload = {
          ...form,
          user_id: form.user_id || initial?.user_id || null,
          salary_profile: { ...form.salary_profile, base_salary: Number(form.salary_profile.base_salary) || 0 },
        };
        if (linkedFromSettings && initial?.id) {
          delete payload.name;
          delete payload.role;
          delete payload.email;
          delete payload.active;
        }
        if (isCreate && createAccount && !isCreateProfileFromUser) {
          payload.create_account = {
            name: account.name || form.name,
            email: account.email.trim().toLowerCase(),
            role: account.role,
            password: account.password,
            nickname: account.nickname || null,
            phone: account.phone || form.phone || null,
          };
          delete payload.user_id;
        } else if (isCreate) {
          payload.create_account = false;
        }
        if (initial?.id) await api.hrPayrollUpdateEmployee(initial.id, payload);
        else await api.hrPayrollCreateEmployee(payload);
      }
      await onSaved();
      onClose();
    } catch (ex) {
      setErr(errorText(ex));
      if (ex?.response?.status === 409) {
        onSaved().catch(() => {});
      }
    } finally { setBusy(false); }
  };
  const fields = [
    ["name", "Nama lengkap"], ["employee_number", "Nomor karyawan"], ["role", "Jabatan HR"], ["division", "Divisi"],
    ["employment_status", "Status kerja"], ["join_date", "Tanggal bergabung"], ["end_date", "Tanggal berakhir"],
    ["worker_type", "Tipe pekerja"], ["email", "Email (HR)"], ["phone", "Telepon"], ["bank_name", "Nama bank"],
    ["bank_account", "Nomor rekening"], ["bank_account_name", "Nama pemilik rekening"],
  ];
  if (!accountMode && !linkedFromSettings) fields.push(["user_id", "User ID (opsional)"]);
  const lockedKeys = linkedFromSettings ? new Set(["name", "role", "email", "user_id"]) : new Set();
  const showAccountToggle = (isCreate || isHrOnlyUnlinked) && canCreateEternalAccount && !isCreateProfileFromUser;
  return (
    <div className="modal" data-testid="hr-employee-modal" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-inner modal-wide hr-employee-modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h3>{isCreateProfileFromUser ? `Buat Profil HR · ${form.name || initial?.name}` : isCreate ? "Tambah Karyawan" : isHrOnlyUnlinked && createAccount ? "Buat Akun Eternal" : hasHrProfile ? "Edit Karyawan" : "Detail Karyawan (Settings)"}</h3>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Tutup"><X size={16} /></button>
        </header>
        <form className="emp-form hr-employee-form" onSubmit={save} data-testid="hr-employee-form">
          <div className="hr-employee-form-body" data-testid="hr-employee-form-body">
            <FormProgress
              percentage={Math.round(([form.name, form.employee_number, form.role, form.division, form.join_date].filter(v => v).length / 5) * 100)}
              completed={[form.name, form.employee_number, form.role, form.division, form.join_date].filter(v => v).length}
              total={5}
            />
            {linkedFromSettings && (
              <p className="muted small" data-testid="hr-employee-settings-authority">
                Nama, email, role, dan status akun dikelola melalui Settings → Karyawan (canonical user ID: {form.user_id}).
                {isCreateProfileFromUser && " Melengkapi form ini akan membuat profil HR persisten untuk akun tersebut."}
                {!hasHrProfile && !isCreateProfileFromUser && " Profil HR belum dibuat — tidak ada mutasi HR dari baris directory ini."}
              </p>
            )}
            {showAccountToggle && (
              <label className="hr-check" data-testid="hr-create-eternal-toggle">
                <input
                  type="checkbox"
                  checked={createAccount}
                  onChange={(e) => setCreateAccount(e.target.checked)}
                />
                Buat Akun Eternal
              </label>
            )}
            {showAccountToggle && !createAccount && (
              <p className="muted small" data-testid="hr-create-eternal-hint">Default OFF — menambah karyawan HR tidak memberi akses login aplikasi.</p>
            )}
            {accountMode && (
              <FormSection
                id="hr-eternal-account"
                title="Akun Eternal"
                description="Membuat akun login canonical di Settings → Karyawan. Role = hak akses aplikasi, bukan jabatan HR."
                className="fs-section-scroll-safe"
                status={computeStatus([
                  { value: account.email, isRequired: true },
                  { value: account.role, isRequired: true },
                  { value: account.password, isRequired: true },
                ])}
              >
                <div className="hr-form-grid" data-testid="hr-eternal-account-fields">
                  <FormField id="hr-acc-name" label="Nama akun" isRequired value={account.name}>
                    <input value={account.name} onChange={(e) => setAccountField("name", e.target.value)} required data-testid="hr-acc-name" />
                  </FormField>
                  <FormField id="hr-acc-email" label="Email login" isRequired value={account.email}>
                    <input type="email" value={account.email} onChange={(e) => setAccountField("email", e.target.value)} required data-testid="hr-acc-email" />
                  </FormField>
                  <FormField id="hr-acc-role" label="Role / Hak Akses" isRequired value={account.role}>
                    <select value={account.role} onChange={(e) => setAccountField("role", e.target.value)} required data-testid="hr-acc-role">
                      <option value="">Pilih role</option>
                      {activeRoles.map((r) => (
                        <option key={r.role} value={r.role}>{r.label || r.role}</option>
                      ))}
                    </select>
                  </FormField>
                  <FormField id="hr-acc-password" label="Password" isRequired value={account.password}>
                    <input type="password" value={account.password} onChange={(e) => setAccountField("password", e.target.value)} required minLength={6} autoComplete="new-password" data-testid="hr-acc-password" placeholder="Wajib diisi (min. 6)" />
                  </FormField>
                  <FormField id="hr-acc-nickname" label="Nama panggilan" value={account.nickname}>
                    <input value={account.nickname} onChange={(e) => setAccountField("nickname", e.target.value)} data-testid="hr-acc-nickname" />
                  </FormField>
                  <FormField id="hr-acc-phone" label="Nomor HP" value={account.phone}>
                    <input value={account.phone} onChange={(e) => setAccountField("phone", e.target.value)} data-testid="hr-acc-phone" />
                  </FormField>
                </div>
              </FormSection>
            )}
            {!(isHrOnlyUnlinked && createAccount) && (
            <FormSection
              id="hr-employee-info"
              title="Informasi Karyawan"
              description="Data dasar dan kepegawaian."
              className="fs-section-scroll-safe"
              status={computeStatus([
                { value: form.name, isRequired: true },
                { value: form.employee_number, isRequired: hasHrProfile },
              ])}
            >
              <div className="hr-form-grid">
                {fields.map(([key, label]) => (
                  <FormField key={key} id={`hr-emp-${key}`} label={label} isRequired={["name", "employee_number"].includes(key) && hasHrProfile && !lockedKeys.has(key)} value={form[key] || ""}>
                    <input
                      type={key.includes("date") ? "date" : "text"}
                      value={key === "role" && lockedKeys.has("role") && form.role_label ? form.role_label : (form[key] || "")}
                      onChange={(e) => set(key, e.target.value)}
                      required={["name", "employee_number"].includes(key) && hasHrProfile && !lockedKeys.has(key)}
                      readOnly={lockedKeys.has(key) || !hasHrProfile}
                      disabled={lockedKeys.has(key) || !hasHrProfile}
                      data-testid={lockedKeys.has(key) || !hasHrProfile ? `hr-emp-${key}-readonly` : undefined}
                    />
                  </FormField>
                ))}
                <FormField id="hr-emp-gaji" label="Gaji pokok" value={form.salary_profile?.base_salary}>
                  <input type="number" min="0" value={form.salary_profile?.base_salary || 0} disabled={!hasHrProfile} onChange={(e) => setForm((cur) => ({ ...cur, salary_profile: { ...cur.salary_profile, base_salary: e.target.value } }))} />
                </FormField>
                <FormField id="hr-emp-efektif" label="Efektif gaji sejak" value={form.salary_profile?.effective_from}>
                  <input type="date" value={form.salary_profile?.effective_from || ""} disabled={!hasHrProfile} onChange={(e) => setForm((cur) => ({ ...cur, salary_profile: { ...cur.salary_profile, effective_from: e.target.value } }))} />
                </FormField>
                <FormField id="hr-emp-alamat" label="Alamat" className="hr-span-2" value={form.address}>
                  <textarea value={form.address || ""} disabled={!hasHrProfile} onChange={(e) => set("address", e.target.value)} />
                </FormField>
              </div>
            </FormSection>
            )}
            {err && <div className="error"><AlertCircle size={14} /> {err}</div>}
          </div>
          <div className="emp-actions hr-employee-form-footer" data-testid="hr-employee-form-footer">
            <button type="button" className="ghost" onClick={onClose}>Batal</button>
            {hasHrProfile && <button className="primary" disabled={busy} data-testid="hr-employee-save"><Save size={14} /> Simpan</button>}
          </div>
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
  const [accountFor, setAccountFor] = useState(null);
  const [roles, setRoles] = useState([]);
  const [err, setErr] = useState("");
  const canCreateEternalAccount = hasPerm("users.manage") && hasPerm("hr.employee.manage");
  const rowKey = (row) => row?.directory_key || row?.id || row?.user_id;
  const canEditHr = (row) => Boolean(row?.has_hr_profile !== false && row?.id);
  const canCreateHrProfile = (row) => Boolean(row?.has_hr_profile === false && row?.user_id);
  const canCreateAccountFor = (row) => Boolean(canCreateEternalAccount && canEditHr(row) && !row?.user_id);
  const load = useCallback(async () => {
    try {
      const [data, meta] = await Promise.all([api.hrPayrollEmployees(), api.hrPayrollMeta().catch(() => ({ roles: [] }))]);
      setRows(data.employees || []);
      setRoles(meta.roles || []);
      setSelected((cur) => {
        if (!cur) return null;
        return (data.employees || []).find((r) => rowKey(r) === rowKey(cur)) || null;
      });
    } catch (ex) { setErr(errorText(ex)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  if (employment) {
    return (
      <div data-testid="hr-employment-page">
        <PageHead title="Data Kepegawaian" />
        {err && <div className="error">{err}</div>}
        <div className="hr-master-detail">
          <div className="settings-card"><h3>Karyawan</h3>{rows.map((row) => <button type="button" className={`hr-list-button ${rowKey(selected) === rowKey(row) ? "active" : ""}`} key={rowKey(row)} onClick={() => setSelected(row)}>{row.employee_number || "—"} · {row.name}</button>)}</div>
          <div className="settings-card">
            {!selected ? <p className="muted">Pilih karyawan untuk melihat detail.</p> : <>
              <div className="hr-card-head">
                <h3>{selected.name}</h3>
                <div className="hr-actions">
                  {hasPerm("hr.employee.manage") && canEditHr(selected) && <button className="ghost sm" onClick={() => setEditing(selected)}><Edit3 size={13} /> Edit</button>}
                  {hasPerm("hr.employee.manage") && canCreateHrProfile(selected) && (
                    <button className="ghost sm" data-testid="hr-create-profile-action" onClick={() => setEditing(selected)}>
                      Buat Profil HR
                    </button>
                  )}
                  {hasPerm("hr.employee.manage") && canCreateAccountFor(selected) && <button className="ghost sm" data-testid="hr-create-account-action" onClick={() => setAccountFor(selected)}>Buat Akun Eternal</button>}
                </div>
              </div>
              <dl className="hr-detail-list">
                <dt>Status kerja</dt><dd>{selected.employment_status || "-"}</dd><dt>Jabatan / Divisi</dt><dd>{selected.role_label || selected.role || "-"} / {selected.division || "-"}</dd>
                <dt>Masa kerja</dt><dd>{selected.join_date || "-"} s.d. {selected.end_date || "sekarang"}</dd><dt>Gaji pokok</dt><dd>{moneyFormat(selected.salary_profile?.base_salary)}</dd>
                <dt>Bank</dt><dd>{selected.bank_name || "-"} · {selected.bank_account || "-"}</dd>
                {selected.user_id && <><dt>User ID</dt><dd data-testid="hr-employment-user-id">{selected.user_id}</dd></>}
                {selected.has_hr_profile === false && <><dt>Profil HR</dt><dd data-testid="hr-virtual-directory-note">Directory Settings saja — belum ada profil HR</dd></>}
              </dl>
              <h4>Riwayat Kepegawaian</h4>
              {(selected.employment_history || []).length ? (selected.employment_history || []).map((item, i) => <div key={i} className="hr-history-item">{item.title || item.role || "Perubahan"} · {item.effective_date || item.date || "-"}</div>) : <p className="muted">Belum ada riwayat.</p>}
            </>}
          </div>
        </div>
        {editing && <EmployeeForm initial={editing} onClose={() => setEditing(null)} onSaved={load} roles={roles} canCreateEternalAccount={canCreateEternalAccount} />}
        {accountFor && canCreateAccountFor(accountFor) && <EmployeeForm initial={accountFor} onClose={() => setAccountFor(null)} onSaved={load} roles={roles} canCreateEternalAccount={canCreateEternalAccount} startWithAccountCreate />}
      </div>
    );
  }
  return (
    <div data-testid="hr-employees-page">
      <PageHead title="Daftar Karyawan" action={hasPerm("hr.employee.manage") && <button className="primary" onClick={() => setEditing({})}><Plus size={14} /> Tambah Karyawan</button>} />
      {err && <div className="error">{err}</div>}
      <div className="table-wrap"><table className="ship-table" data-testid="hr-employees-table"><thead><tr><th>No.</th><th>Nama</th><th>Jabatan</th><th>Divisi</th><th>Status</th><th>Gaji Pokok</th><th></th></tr></thead>
        <tbody>{rows.map((row) => (
          <tr
            key={rowKey(row)}
            data-testid="hr-employee-row"
            data-user-id={row.user_id || ""}
            data-employee-id={row.id || ""}
            data-directory-key={rowKey(row)}
            data-has-hr-profile={row.has_hr_profile === false ? "false" : "true"}
          >
            <td>{row.employee_number || "—"}</td>
            <td><strong>{row.name}</strong>{row.has_hr_profile === false && <div className="muted small" data-testid="hr-directory-no-profile">Belum ada profil HR</div>}</td>
            <td>{row.role_label || row.role || "-"}</td>
            <td>{row.division || "-"}</td>
            <td>{row.active === false ? "Nonaktif" : "Aktif"}</td>
            <td>{moneyFormat(row.salary_profile?.base_salary)}</td>
            <td>
              {hasPerm("hr.employee.manage") && canEditHr(row) && <button className="ghost sm" onClick={() => setEditing(row)}>Edit</button>}
              {hasPerm("hr.employee.manage") && canCreateHrProfile(row) && (
                <button className="ghost sm" data-testid="hr-create-profile-action" onClick={() => setEditing(row)}>
                  Buat Profil HR
                </button>
              )}
              {hasPerm("hr.employee.manage") && canCreateAccountFor(row) && <button className="ghost sm" data-testid="hr-create-account-action" onClick={() => setAccountFor(row)}>Buat Akun Eternal</button>}
            </td>
          </tr>
        ))}</tbody>
      </table></div>
      {editing && (
        <EmployeeForm initial={editing} onClose={() => setEditing(null)} onSaved={load} roles={roles} canCreateEternalAccount={canCreateEternalAccount} />
      )}
      {accountFor && canCreateAccountFor(accountFor) && (
        <EmployeeForm initial={accountFor} onClose={() => setAccountFor(null)} onSaved={async () => { setAccountFor(null); await load(); }} roles={roles} canCreateEternalAccount={canCreateEternalAccount} startWithAccountCreate />
      )}
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
  const hrEmployees = useMemo(
    () => (employees || []).filter((emp) => emp?.id && emp?.has_hr_profile !== false),
    [employees],
  );
  const [draft, setDraft] = useState(() => Object.fromEntries((attendance || []).map((row) => [row.employee_id, row])));
  const save = async (employeeId) => {
    if (!employeeId) return;
    await api.hrPayrollUpdateAttendance(period.id, employeeId, draft[employeeId] || {});
    onSaved();
  };
  if (period.status !== "DRAFT") return null;
  return <div className="settings-card"><h3>Kehadiran</h3><div className="table-wrap"><table className="ship-table"><thead><tr><th>Karyawan</th><th>Hari kerja</th><th>Terlambat (menit)</th><th>Alpha</th><th>Lembur hari kerja (jam)</th><th></th></tr></thead><tbody>{hrEmployees.map((emp) => {
    const row = draft[emp.id] || { employee_id: emp.id, work_days: 0, late_minutes: 0, alpha_days: 0, overtime_weekday_hours: 0, overtime_weekend_hours: 0, overtime_holiday_hours: 0, unpaid_leave_days: 0, unpaid_sick_days: 0 };
    const field = (key) => <input className="hr-number" type="number" min="0" value={row[key] || 0} onChange={(e) => setDraft((cur) => ({ ...cur, [emp.id]: { ...row, [key]: Number(e.target.value) } }))} />;
    return <tr key={emp.id} data-testid="hr-attendance-employee-row"><td>{emp.name}</td><td>{field("work_days")}</td><td>{field("late_minutes")}</td><td>{field("alpha_days")}</td><td>{field("overtime_weekday_hours")}</td><td><button className="ghost sm" onClick={() => save(emp.id)}>Simpan</button></td></tr>;
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
  const [readiness, setReadiness] = useState(null);
  const [showPreview, setShowPreview] = useState(false);
  const [form, setForm] = useState({ year: new Date().getFullYear(), month: new Date().getMonth() + 1, start_date: "", end_date: "" });
  const [err, setErr] = useState("");
  const loadPeriods = useCallback(async () => { const data = await api.hrPayrollPeriods(); setPeriods(data.periods || []); }, []);
  const loadDetail = useCallback(async (period) => {
    if (!period?.id) return;
    const [p, l, a, e, k] = await Promise.all([api.hrPayrollPeriod(period.id), api.hrPayrollLines(period.id), api.hrPayrollAttendance(period.id), api.hrPayrollEmployees({ hr_profile_only: true }), api.hrPayrollKasbonSchedules({ active_only: true })]);
    setSelected(p.period); setLines(l.lines || []); setAttendance(a.attendance || []); setEmployees((e.employees || []).filter((emp) => emp?.id && emp?.has_hr_profile !== false)); setKasbon(k.schedules || []);
    try {
      const ready = await api.hrPayrollAccountingReadiness(period.id);
      setReadiness(ready.readiness || null);
    } catch {
      setReadiness(null);
    }
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
        <div className="settings-card"><div className="hr-card-head"><div><h3>{selected.period_number || `${selected.month}/${selected.year}`}</h3><span className={`hr-status hr-status-${String(selected.status).toLowerCase()}`}>{statusLabel(selected.status)}</span>
          {readiness && <span className="muted small" data-testid="hr-payroll-accounting-ready">Accounting: {readiness.accounting_ready ? "Siap" : "Belum Siap"}</span>}
        </div><div className="hr-actions">
          {selected.status === "DRAFT" && hasPerm("payroll.calculate") && <button className="primary" onClick={calculate} data-testid="hr-calc-btn">Hitung Payroll</button>}
          {transitionButtons.map(([target, perm, testid, label]) => canTransition(selected.status, target) && hasPerm(perm) ? <button key={target} className="primary" onClick={() => transition(target)} data-testid={testid}>{label}</button> : null)}
          {selected.status === "FINAL" && hasPerm("payroll.reopen") && <button className="ghost" onClick={reopen} data-testid="hr-reopen-btn">Buka Ulang</button>}
          {readiness && <button type="button" className="ghost" onClick={() => setShowPreview(true)} data-testid="hr-payroll-accounting-preview-btn">Lihat Preview Accounting</button>}
        </div></div></div>
        {showPreview && readiness && (
          <div className="settings-card hr-payroll-accounting-preview" data-testid="hr-payroll-accounting-preview">
            <h3>Preview Accounting</h3>
            <p className="muted small">PREVIEW — BELUM MEMBUAT JURNAL</p>
            <p>Status: {readiness.accounting_ready ? "Siap" : "Belum Siap"}</p>
            <p className="muted small">{(readiness.reasons || []).join(" · ")}</p>
            <div className="table-wrap"><table className="ship-table"><thead><tr><th>Sumber</th><th>Treatment</th><th>Akun</th><th className="num">Debit</th><th className="num">Kredit</th></tr></thead><tbody>
              {(readiness.planned_lines || []).map((row, idx) => (
                <tr key={`${row.account_id}-${(row.source_keys || []).join(",")}-${idx}`} data-testid={previewIsPayable(row) ? "hr-payroll-accounting-preview-payable" : "hr-payroll-accounting-preview-line"}>
                  <td>
                    <div>{previewSourceLabel(row)}</div>
                    {previewIsPayable(row)
                      ? <div className="muted small">Saldo utang dari net payroll — bukan komponen mapped</div>
                      : row.name && row.name !== previewSourceLabel(row) ? <div className="muted small">{row.name}</div> : null}
                  </td>
                  <td>
                    <div>{previewTreatmentLabel(row.treatment)}</div>
                    {row.treatment ? <div className="muted small">{row.treatment}</div> : null}
                  </td>
                  <td className="mono">{previewAccountLabel(row)}</td>
                  <td className="num">{moneyFormat(row.debit)}</td>
                  <td className="num">{moneyFormat(row.credit)}</td>
                </tr>
              ))}
            </tbody></table></div>
            <p>Gross {moneyFormat(readiness.summary?.gross)} · Potongan {moneyFormat(readiness.summary?.deductions)} · Net {moneyFormat(readiness.summary?.net)}</p>
            <p>Total debit {moneyFormat(readiness.summary?.total_debit)} · Total kredit {moneyFormat(readiness.summary?.total_credit)} · Selisih {moneyFormat(readiness.summary?.difference)}</p>
            <button type="button" className="ghost" onClick={() => setShowPreview(false)}>Tutup</button>
          </div>
        )}
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
