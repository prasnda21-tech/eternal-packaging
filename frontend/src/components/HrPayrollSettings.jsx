import { useEffect, useState } from "react";
import { AlertCircle, Plus, Save } from "lucide-react";
import api from "../lib/api";
import { moneyFormat } from "../lib/hrPayroll";

const TABS = [
  ["general", "Umum"], ["components", "Komponen"], ["attendance", "Kehadiran"],
  ["overtime", "Lembur"], ["deductions", "Potongan"], ["kasbon", "Kasbon"],
  ["workflow", "Workflow info"], ["payslip", "Slip Gaji"],
];
const errorText = (ex) => ex?.response?.data?.detail || ex?.message || "Gagal memuat";

function NumberField({ label, value, onChange, disabled, step = "any" }) {
  return <label>{label}<input type="number" min="0" step={step} value={value ?? 0} onChange={(e) => onChange(Number(e.target.value))} disabled={disabled} /></label>;
}

export default function HrPayrollSettings({ notify, canEdit }) {
  const [tab, setTab] = useState("general");
  const [settings, setSettings] = useState(null);
  const [components, setComponents] = useState([]);
  const [newComponent, setNewComponent] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const load = async () => {
    const [s, c] = await Promise.all([api.hrPayrollSettings(), api.hrPayrollComponents()]);
    setSettings(s.settings);
    setComponents(c.components || []);
  };
  useEffect(() => { load().catch((ex) => setErr(errorText(ex))); }, []);
  const patch = (section, key, value) => setSettings((cur) => ({ ...cur, [section]: { ...cur[section], [key]: value } }));
  const save = async () => {
    setBusy(true); setErr("");
    try {
      const result = await api.hrPayrollUpdateSettings(settings);
      setSettings(result.settings);
      notify?.(result.message || "Pengaturan HR & Payroll disimpan");
    } catch (ex) { setErr(errorText(ex)); } finally { setBusy(false); }
  };
  const createComponent = async () => {
    try {
      await api.hrPayrollCreateComponent({ ...newComponent, amount: Number(newComponent.amount) || 0, recurring: true, active: true });
      setNewComponent(null);
      await load();
      notify?.("Komponen gaji disimpan");
    } catch (ex) { setErr(errorText(ex)); }
  };
  return (
    <div className="hr-settings" data-testid="settings-hr-payroll">
      <h3 style={{ marginTop: 0 }}>HR &amp; Payroll</h3>
      <p className="muted small">Atur aturan penggajian. Setiap periode menyimpan snapshot konfigurasi agar histori tetap konsisten.</p>
      {err && <div className="error"><AlertCircle size={14} /> {err}</div>}
      <div className="settings-tabs hr-inner-tabs" role="tablist">
        {TABS.map(([id, label]) => <button type="button" role="tab" key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)} data-testid={`settings-hr-payroll-tab-${id}`}>{label}</button>)}
      </div>
      {!settings ? <p className="muted">Memuat…</p> : <div className="settings-card" data-testid={`settings-hr-payroll-panel-${tab}`}>
        {tab === "general" && <><h4>Konfigurasi Umum</h4><p>Versi konfigurasi: <strong>{settings.config_version}</strong></p><p className="muted small">Perhitungan memakai profil gaji karyawan, komponen aktif, kehadiran, lembur, potongan, dan kasbon.</p></>}
        {tab === "components" && <><div className="hr-card-head"><h4>Komponen Gaji</h4>{canEdit && <button className="ghost sm" onClick={() => setNewComponent({ code: "", name: "", kind: "earning", calc_mode: "fixed", amount: 0 })}><Plus size={13} /> Tambah</button>}</div>
          <div className="table-wrap"><table className="ship-table"><thead><tr><th>Kode</th><th>Nama</th><th>Jenis</th><th>Mode</th><th>Nilai</th></tr></thead><tbody>{components.map((row) => <tr key={row.id}><td>{row.code}</td><td>{row.name}</td><td>{row.kind}</td><td>{row.calc_mode}</td><td>{moneyFormat(row.amount)}</td></tr>)}</tbody></table></div>
          {newComponent && <div className="hr-form-grid hr-inline-form"><label>Kode<input value={newComponent.code} onChange={(e) => setNewComponent({ ...newComponent, code: e.target.value.toUpperCase() })} /></label><label>Nama<input value={newComponent.name} onChange={(e) => setNewComponent({ ...newComponent, name: e.target.value })} /></label><label>Jenis<select value={newComponent.kind} onChange={(e) => setNewComponent({ ...newComponent, kind: e.target.value })}><option value="earning">Pendapatan</option><option value="deduction">Potongan</option></select></label><NumberField label="Nilai" value={newComponent.amount} onChange={(amount) => setNewComponent({ ...newComponent, amount })} /><button className="primary" onClick={createComponent}>Simpan Komponen</button></div>}</>}
        {tab === "attendance" && <div className="hr-form-grid"><NumberField label="Toleransi terlambat (menit)" value={settings.attendance.late_grace_minutes} onChange={(v) => patch("attendance", "late_grace_minutes", v)} disabled={!canEdit} /><NumberField label="Potongan per menit" value={settings.attendance.late_rate_per_minute} onChange={(v) => patch("attendance", "late_rate_per_minute", v)} disabled={!canEdit} /><NumberField label="Batas potongan terlambat" value={settings.attendance.late_cap} onChange={(v) => patch("attendance", "late_cap", v)} disabled={!canEdit} /></div>}
        {tab === "overtime" && <div className="hr-form-grid"><NumberField label="Pengali hari kerja" value={settings.overtime.weekday_multiplier} onChange={(v) => patch("overtime", "weekday_multiplier", v)} disabled={!canEdit} /><NumberField label="Pengali akhir pekan" value={settings.overtime.weekend_multiplier} onChange={(v) => patch("overtime", "weekend_multiplier", v)} disabled={!canEdit} /><NumberField label="Pengali hari libur" value={settings.overtime.holiday_multiplier} onChange={(v) => patch("overtime", "holiday_multiplier", v)} disabled={!canEdit} /><NumberField label="Pembagi dasar per jam" value={settings.overtime.hourly_base_divisor} onChange={(v) => patch("overtime", "hourly_base_divisor", v)} disabled={!canEdit} /></div>}
        {tab === "deductions" && <div className="hr-form-grid"><NumberField label="Alpha per hari" value={settings.attendance.alpha_rate_per_day} onChange={(v) => patch("attendance", "alpha_rate_per_day", v)} disabled={!canEdit} /><NumberField label="Cuti tidak dibayar per hari" value={settings.attendance.unpaid_leave_rate_per_day} onChange={(v) => patch("attendance", "unpaid_leave_rate_per_day", v)} disabled={!canEdit} /><NumberField label="Sakit tidak dibayar per hari" value={settings.attendance.unpaid_sick_rate_per_day} onChange={(v) => patch("attendance", "unpaid_sick_rate_per_day", v)} disabled={!canEdit} /></div>}
        {tab === "kasbon" && <div className="hr-form-grid"><label>Mode default<select value={settings.kasbon.default_mode} onChange={(e) => patch("kasbon", "default_mode", e.target.value)} disabled={!canEdit}><option value="full">Potong penuh</option><option value="installment_fixed">Cicilan tetap</option><option value="installment_periods">Jumlah periode</option></select></label><NumberField label="Cicilan default" value={settings.kasbon.default_installment_amount} onChange={(v) => patch("kasbon", "default_installment_amount", v)} disabled={!canEdit} /><NumberField label="Jumlah periode" value={settings.kasbon.default_periods} onChange={(v) => patch("kasbon", "default_periods", v)} disabled={!canEdit} /><NumberField label="Rasio potongan maksimal" step="0.01" value={settings.kasbon.max_deduction_ratio} onChange={(v) => patch("kasbon", "max_deduction_ratio", v)} disabled={!canEdit} /></div>}
        {tab === "workflow" && <div><h4>Alur Payroll</h4><ol><li>Draft — input kehadiran dan hitung payroll</li><li>Review — pemeriksaan HR</li><li>Persetujuan — persetujuan pejabat berwenang</li><li>Final — data dibekukan dan slip tersedia</li></ol><p className="muted small">Buka ulang membuat revisi Draft baru dan tidak mengubah periode Final lama.</p></div>}
        {tab === "payslip" && <div className="hr-form-grid"><label>Nama perusahaan<input value={settings.payslip.company_name} onChange={(e) => patch("payslip", "company_name", e.target.value)} disabled={!canEdit} /></label><label>Judul slip<input value={settings.payslip.header_text} onChange={(e) => patch("payslip", "header_text", e.target.value)} disabled={!canEdit} /></label><label className="hr-span-2">Footer<textarea value={settings.payslip.footer_text} onChange={(e) => patch("payslip", "footer_text", e.target.value)} disabled={!canEdit} /></label></div>}
        {canEdit && !["components", "workflow", "general"].includes(tab) && <button className="primary hr-settings-save" onClick={save} disabled={busy} data-testid="settings-hr-payroll-save"><Save size={14} /> Simpan Pengaturan</button>}
      </div>}
    </div>
  );
}
