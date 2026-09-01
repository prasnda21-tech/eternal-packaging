import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Plus, Save, Trash2 } from "lucide-react";
import api from "../lib/api";
import { EA_FIELD_TYPES, EA_SETTINGS_TABS, emptyEligibility, emptyField, emptyStep } from "../lib/employeeAdmin";

function errDetail(ex) {
  return ex?.response?.data?.detail || ex?.message || "Gagal menyimpan";
}

function moveItem(list, idx, dir) {
  const next = list.slice();
  const j = idx + dir;
  if (j < 0 || j >= next.length) return next;
  const tmp = next[idx];
  next[idx] = next[j];
  next[j] = tmp;
  return next.map((row, i) => ({ ...row, order: i + 1, level: i + 1 }));
}

export default function EmployeeAdminSettings({ notify, canEdit }) {
  const [types, setTypes] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [tab, setTab] = useState("type");
  const [form, setForm] = useState(null);
  const [meta, setMeta] = useState({ roles: [], users: [], divisions: [] });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);

  const selected = useMemo(() => types.find((t) => t.id === selectedId) || null, [types, selectedId]);

  const load = async (keepId) => {
    const [t, m] = await Promise.all([api.employeeAdminTypes(true), api.employeeAdminMeta().catch(() => ({ roles: [], users: [], divisions: [] }))]);
    const rows = t.types || [];
    setTypes(rows);
    setMeta(m);
    const id = keepId || selectedId || rows[0]?.id || "";
    setSelectedId(id);
    const cur = rows.find((r) => r.id === id);
    if (cur) setForm(hydrate(cur));
  };

  useEffect(() => {
    load().catch((ex) => setErr(errDetail(ex)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setForm(hydrate(selected));
  }, [selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    if (!form) return;
    setBusy(true);
    setErr("");
    try {
      const payload = serialize(form);
      const res = form.id
        ? await api.employeeAdminUpdateType(form.id, payload)
        : await api.employeeAdminCreateType(payload);
      notify?.(res.message || "Tersimpan");
      setCreating(false);
      await load(res.type.id);
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  const startCreate = () => {
    setCreating(true);
    setSelectedId("");
    setForm({
      name: "Pengajuan Baru",
      code: "",
      description: "",
      category: "",
      active: true,
      generates_pdf: true,
      eligibility: emptyEligibility(),
      form_fields: [emptyField(1)],
      approval_steps: [emptyStep(1)],
      document_template: { title: "Surat Keterangan", numbering_prefix: "SKY", header_text: "ETERNAL PACKAGING", footer_text: "Dokumen ini disetujui secara elektronik.", sections: [{ heading: "Isi Pengajuan", body: "{{submitted_fields}}" }] },
    });
    setTab("type");
  };

  return (
    <div className="ea-settings" data-testid="ea-settings">
      <h3 style={{ marginTop: 0 }}>Administrasi Karyawan</h3>
      <p className="muted small">Pilih satu jenis pengajuan, lalu atur form, hak pengajuan, alur persetujuan, dan dokumen di tab yang sama.</p>
      {err && <div className="error" data-testid="ea-settings-error"><AlertCircle size={14} /> {err}</div>}
      <div className="ea-settings-layout">
        <aside className="settings-card ea-type-list" data-testid="ea-type-list">
          <div className="ea-type-list-head">
            <strong>Jenis Pengajuan</strong>
            {canEdit && <button type="button" className="ghost" onClick={startCreate} data-testid="ea-type-add"><Plus size={14} /> Tambah</button>}
          </div>
          <ul>
            {types.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  className={t.id === selectedId && !creating ? "active" : ""}
                  onClick={() => { setCreating(false); setSelectedId(t.id); }}
                  data-testid={`ea-type-${t.code}`}
                >
                  {t.name}
                  <span className={`ea-badge ${t.active === false ? "ea-badge-ditolak" : "ea-badge-disetujui"}`}>{t.active === false ? "Nonaktif" : "Aktif"}</span>
                </button>
              </li>
            ))}
          </ul>
        </aside>
        <div className="settings-card" data-testid="ea-type-config">
          {!form && <p className="muted">Pilih jenis pengajuan.</p>}
          {form && (
            <>
              <div className="settings-tabs ea-inner-tabs" role="tablist">
                {EA_SETTINGS_TABS.map((t) => (
                  <button key={t.id} type="button" role="tab" className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)} data-testid={`ea-tab-${t.id}`}>{t.label}</button>
                ))}
              </div>
              {tab === "type" && <TypeTab form={form} setForm={setForm} canEdit={canEdit} />}
              {tab === "form" && <FormTab form={form} setForm={setForm} canEdit={canEdit} />}
              {tab === "eligibility" && <EligibilityTab form={form} setForm={setForm} canEdit={canEdit} meta={meta} />}
              {tab === "approval" && <ApprovalTab form={form} setForm={setForm} canEdit={canEdit} meta={meta} />}
              {tab === "document" && <DocumentTab form={form} setForm={setForm} canEdit={canEdit} />}
              {tab === "numbering" && <NumberingTab form={form} setForm={setForm} canEdit={canEdit} />}
              {canEdit && (
                <div style={{ marginTop: 16 }}>
                  <button type="button" className="primary" onClick={save} disabled={busy} data-testid="ea-settings-save">
                    <Save size={14} /> {busy ? "Menyimpan…" : "Simpan konfigurasi"}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function hydrate(doc) {
  return {
    id: doc.id,
    name: doc.name || "",
    code: doc.code || "",
    description: doc.description || "",
    category: doc.category || "",
    active: doc.active !== false,
    generates_pdf: !!doc.generates_pdf,
    eligibility: { ...emptyEligibility(), ...(doc.eligibility || {}) },
    form_fields: (doc.form_fields || []).length ? doc.form_fields : [emptyField(1)],
    approval_steps: (doc.approval_steps || []).length ? doc.approval_steps : [emptyStep(1)],
    document_template: {
      title: "Surat Keterangan",
      numbering_prefix: "SKY",
      header_text: "ETERNAL PACKAGING",
      footer_text: "Dokumen ini disetujui secara elektronik.",
      sections: [{ heading: "Isi Pengajuan", body: "{{submitted_fields}}" }],
      ...(doc.document_template || {}),
    },
    config_version: doc.config_version,
  };
}

function serialize(form) {
  return {
    name: form.name,
    code: form.code,
    description: form.description,
    category: form.category,
    active: form.active,
    generates_pdf: form.generates_pdf,
    eligibility: form.eligibility,
    form_fields: (form.form_fields || []).map((f, i) => ({
      ...f,
      order: i + 1,
      options: typeof f.options === "string" ? f.options.split("\n").map((s) => s.trim()).filter(Boolean) : (f.options || []),
      minimum: f.minimum === "" ? null : f.minimum,
      maximum: f.maximum === "" ? null : f.maximum,
    })),
    approval_steps: (form.approval_steps || []).map((s, i) => ({ ...s, level: i + 1 })),
    document_template: form.document_template,
  };
}

function TypeTab({ form, setForm, canEdit }) {
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  return (
    <div className="emp-grid" data-testid="ea-tab-type-panel">
      <label>Nama
        <input value={form.name} disabled={!canEdit} onChange={(e) => set({ name: e.target.value })} data-testid="ea-type-name" />
      </label>
      <label>Kode internal
        <input value={form.code} disabled={!canEdit} onChange={(e) => set({ code: e.target.value.toUpperCase() })} data-testid="ea-type-code" />
      </label>
      <label>Kategori
        <input value={form.category} disabled={!canEdit} onChange={(e) => set({ category: e.target.value })} data-testid="ea-type-category" />
      </label>
      <label>Deskripsi
        <input value={form.description} disabled={!canEdit} onChange={(e) => set({ description: e.target.value })} data-testid="ea-type-desc" />
      </label>
      <label className="ea-check"><input type="checkbox" checked={!!form.active} disabled={!canEdit} onChange={(e) => set({ active: e.target.checked })} data-testid="ea-type-active" /> Aktif</label>
      <label className="ea-check"><input type="checkbox" checked={!!form.generates_pdf} disabled={!canEdit} onChange={(e) => set({ generates_pdf: e.target.checked })} data-testid="ea-type-pdf" /> Hasilkan PDF setelah persetujuan akhir</label>
    </div>
  );
}

function FormTab({ form, setForm, canEdit }) {
  const fields = form.form_fields || [];
  const patch = (idx, upd) => setForm((f) => ({ ...f, form_fields: f.form_fields.map((row, i) => (i === idx ? { ...row, ...upd } : row)) }));
  return (
    <div data-testid="ea-form-builder">
      <p className="muted small">Tidak ada formula/kode yang bisa dijalankan. Schema divalidasi backend.</p>
      {fields.map((field, idx) => (
        <div className="ea-builder-row" key={field.key + idx} data-testid={`ea-field-row-${idx}`}>
          <input placeholder="Label" value={field.label} disabled={!canEdit} onChange={(e) => patch(idx, { label: e.target.value })} data-testid={`ea-field-label-${idx}`} />
          <input placeholder="kunci" value={field.key} disabled={!canEdit} onChange={(e) => patch(idx, { key: e.target.value })} data-testid={`ea-field-key-${idx}`} />
          <select value={field.type} disabled={!canEdit} onChange={(e) => patch(idx, { type: e.target.value })} data-testid={`ea-field-type-${idx}`}>
            {EA_FIELD_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
          <label className="ea-check"><input type="checkbox" checked={!!field.required} disabled={!canEdit} onChange={(e) => patch(idx, { required: e.target.checked })} /> Wajib</label>
          <label className="ea-check"><input type="checkbox" checked={field.active !== false} disabled={!canEdit} onChange={(e) => patch(idx, { active: e.target.checked })} /> Aktif</label>
          {["number", "currency"].includes(field.type) && (
            <>
              <input placeholder="Min" value={field.minimum ?? ""} disabled={!canEdit} onChange={(e) => patch(idx, { minimum: e.target.value })} data-testid={`ea-field-min-${idx}`} />
              <input placeholder="Max" value={field.maximum ?? ""} disabled={!canEdit} onChange={(e) => patch(idx, { maximum: e.target.value })} data-testid={`ea-field-max-${idx}`} />
            </>
          )}
          {field.type === "select" && (
            <textarea className="ea-span-2" placeholder="Satu pilihan per baris" value={Array.isArray(field.options) ? field.options.join("\n") : (field.options || "")} disabled={!canEdit} onChange={(e) => patch(idx, { options: e.target.value })} data-testid={`ea-field-options-${idx}`} />
          )}
          <div className="ea-row-actions">
            <button type="button" disabled={!canEdit} onClick={() => setForm((f) => ({ ...f, form_fields: moveItem(f.form_fields, idx, -1) }))}>↑</button>
            <button type="button" disabled={!canEdit} onClick={() => setForm((f) => ({ ...f, form_fields: moveItem(f.form_fields, idx, 1) }))}>↓</button>
            <button type="button" disabled={!canEdit} onClick={() => setForm((f) => ({ ...f, form_fields: f.form_fields.filter((_, i) => i !== idx) }))} data-testid={`ea-field-del-${idx}`}><Trash2 size={14} /></button>
          </div>
        </div>
      ))}
      {canEdit && <button type="button" className="ghost" onClick={() => setForm((f) => ({ ...f, form_fields: [...f.form_fields, emptyField(f.form_fields.length + 1)] }))} data-testid="ea-field-add"><Plus size={14} /> Tambah field</button>}
    </div>
  );
}

function MultiChecks({ options, values, onChange, testid, disabled }) {
  const set = new Set(values || []);
  return (
    <div className="ea-checks" data-testid={testid}>
      {options.map((opt) => (
        <label key={opt.value} className="ea-check">
          <input
            type="checkbox"
            disabled={disabled}
            checked={set.has(opt.value)}
            onChange={(e) => {
              const next = new Set(set);
              if (e.target.checked) next.add(opt.value);
              else next.delete(opt.value);
              onChange([...next]);
            }}
          />
          {opt.label}
        </label>
      ))}
    </div>
  );
}

function EligibilityTab({ form, setForm, canEdit, meta }) {
  const el = form.eligibility || emptyEligibility();
  const setEl = (patch) => setForm((f) => ({ ...f, eligibility: { ...f.eligibility, ...patch } }));
  return (
    <div data-testid="ea-eligibility-builder">
      <label className="ea-check">
        <input type="checkbox" checked={!!el.all_employees} disabled={!canEdit} onChange={(e) => setEl({ all_employees: e.target.checked })} data-testid="ea-elig-all" />
        Semua Karyawan
      </label>
      <label>Mode aturan
        <select value={el.mode || "any"} disabled={!canEdit} onChange={(e) => setEl({ mode: e.target.value })} data-testid="ea-elig-mode">
          <option value="any">Cocok Salah Satu</option>
          <option value="all">Cocok Semua</option>
        </select>
      </label>
      <h4>Role / Jabatan</h4>
      <MultiChecks
        testid="ea-elig-roles"
        disabled={!canEdit}
        values={el.roles}
        onChange={(roles) => setEl({ roles })}
        options={(meta.roles || []).map((r) => ({ value: r.role, label: r.label || r.role }))}
      />
      <h4>Divisi</h4>
      <MultiChecks
        testid="ea-elig-divs"
        disabled={!canEdit}
        values={el.divisions}
        onChange={(divisions) => setEl({ divisions })}
        options={(meta.divisions || []).map((d) => ({ value: d.id, label: d.label }))}
      />
      <h4>User tertentu</h4>
      <MultiChecks
        testid="ea-elig-users"
        disabled={!canEdit}
        values={el.user_ids}
        onChange={(user_ids) => setEl({ user_ids })}
        options={(meta.users || []).map((u) => ({ value: u.id, label: `${u.name} (${u.role})` }))}
      />
    </div>
  );
}

function ApprovalTab({ form, setForm, canEdit, meta }) {
  const steps = form.approval_steps || [];
  const patch = (idx, upd) => setForm((f) => ({ ...f, approval_steps: f.approval_steps.map((row, i) => (i === idx ? { ...row, ...upd } : row)) }));
  return (
    <div data-testid="ea-approval-builder">
      <p className="muted small">Persetujuan berurutan. Satu approval dari approver yang eligible menyelesaikan langkah.</p>
      {steps.map((step, idx) => (
        <div className="ea-builder-row" key={step.id || idx} data-testid={`ea-step-${idx}`}>
          <strong>Level {idx + 1}</strong>
          <input value={step.label} disabled={!canEdit} onChange={(e) => patch(idx, { label: e.target.value })} data-testid={`ea-step-label-${idx}`} />
          <label className="ea-check"><input type="checkbox" checked={step.active !== false} disabled={!canEdit} onChange={(e) => patch(idx, { active: e.target.checked })} /> Aktif</label>
          <div className="ea-span-2">
            <span className="muted small">Role / Jabatan</span>
            <MultiChecks testid={`ea-step-roles-${idx}`} disabled={!canEdit} values={step.roles} onChange={(roles) => patch(idx, { roles })} options={(meta.roles || []).map((r) => ({ value: r.role, label: r.label || r.role }))} />
          </div>
          <div className="ea-span-2">
            <span className="muted small">Divisi</span>
            <MultiChecks testid={`ea-step-divs-${idx}`} disabled={!canEdit} values={step.divisions} onChange={(divisions) => patch(idx, { divisions })} options={(meta.divisions || []).map((d) => ({ value: d.id, label: d.label }))} />
          </div>
          <div className="ea-span-2">
            <span className="muted small">User tertentu</span>
            <MultiChecks testid={`ea-step-users-${idx}`} disabled={!canEdit} values={step.user_ids} onChange={(user_ids) => patch(idx, { user_ids })} options={(meta.users || []).map((u) => ({ value: u.id, label: `${u.name} (${u.role})` }))} />
          </div>
          <div className="ea-row-actions">
            <button type="button" disabled={!canEdit} onClick={() => setForm((f) => ({ ...f, approval_steps: moveItem(f.approval_steps, idx, -1) }))}>↑</button>
            <button type="button" disabled={!canEdit} onClick={() => setForm((f) => ({ ...f, approval_steps: moveItem(f.approval_steps, idx, 1) }))}>↓</button>
            <button type="button" disabled={!canEdit} onClick={() => setForm((f) => ({ ...f, approval_steps: f.approval_steps.filter((_, i) => i !== idx) }))}><Trash2 size={14} /></button>
          </div>
        </div>
      ))}
      {canEdit && <button type="button" className="ghost" onClick={() => setForm((f) => ({ ...f, approval_steps: [...f.approval_steps, emptyStep(f.approval_steps.length + 1)] }))} data-testid="ea-step-add"><Plus size={14} /> Tambah langkah</button>}
    </div>
  );
}

function DocumentTab({ form, setForm, canEdit }) {
  const tpl = form.document_template || {};
  const setTpl = (patch) => setForm((f) => ({ ...f, document_template: { ...f.document_template, ...patch } }));
  const placeholders = (form.form_fields || []).filter((f) => f.active !== false).map((f) => `{{field.${f.key}}}`).join("  ");
  return (
    <div className="emp-grid" data-testid="ea-document-settings">
      <label className="ea-span-2">Judul surat
        <input value={tpl.title || ""} disabled={!canEdit} onChange={(e) => setTpl({ title: e.target.value })} data-testid="ea-doc-title" />
      </label>
      <label className="ea-span-2">Header
        <textarea value={tpl.header_text || ""} disabled={!canEdit} onChange={(e) => setTpl({ header_text: e.target.value })} data-testid="ea-doc-header" />
      </label>
      <label className="ea-span-2">Footer
        <textarea value={tpl.footer_text || ""} disabled={!canEdit} onChange={(e) => setTpl({ footer_text: e.target.value })} data-testid="ea-doc-footer" />
      </label>
      <label className="ea-span-2">Isi (placeholder aman)
        <textarea value={(tpl.sections && tpl.sections[0]?.body) || ""} disabled={!canEdit} onChange={(e) => setTpl({ sections: [{ heading: tpl.sections?.[0]?.heading || "Isi Pengajuan", body: e.target.value }] })} data-testid="ea-doc-body" />
      </label>
      <p className="muted small ea-span-2">Placeholder: {`{{employee.name}} {{request.number}} {{submitted_fields}} ${placeholders}`}</p>
    </div>
  );
}

function NumberingTab({ form, setForm, canEdit }) {
  const tpl = form.document_template || {};
  return (
    <div data-testid="ea-numbering-settings">
      <p className="muted small">Nomor pengajuan memakai pola PKY-YYYYMMDD-NNN. Nomor surat memakai prefix di bawah, unik dan tidak berubah setelah terbit.</p>
      <label>Prefix nomor dokumen
        <input value={tpl.numbering_prefix || "SKY"} disabled={!canEdit} onChange={(e) => setForm((f) => ({ ...f, document_template: { ...f.document_template, numbering_prefix: e.target.value.toUpperCase() } }))} data-testid="ea-doc-prefix" />
      </label>
      {form.config_version ? <p className="muted small">Versi konfigurasi saat ini: {form.config_version}</p> : null}
    </div>
  );
}
