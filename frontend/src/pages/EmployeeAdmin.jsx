import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AlertCircle, Plus, Search } from "lucide-react";
import api from "../lib/api";
import { currentStepLabel, eligibleTypeOptions, statusLabel } from "../lib/employeeAdmin";
import { formatDisplayDate } from "../lib/constants";

const MODE_META = {
  mine: { title: "Pengajuan Saya", eyebrow: "Administrasi Karyawan", scope: "mine", testid: "ea-mine-page" },
  approve: { title: "Persetujuan", eyebrow: "Administrasi Karyawan", scope: "approve", testid: "ea-approve-page" },
  archive: { title: "Arsip Dokumen", eyebrow: "Administrasi Karyawan", scope: "archive", testid: "ea-archive-page" },
  create: { title: "Buat Pengajuan", eyebrow: "Administrasi Karyawan", scope: "mine", testid: "ea-create-page" },
};

function errDetail(ex) {
  return ex?.response?.data?.detail || ex?.message || "Gagal memuat";
}

function DynamicEmployeeForm({ fields, values, setValues, onUpload, busy }) {
  const active = (fields || []).filter((f) => f.active !== false).sort((a, b) => (a.order || 0) - (b.order || 0));
  const setVal = (key, value) => setValues((cur) => ({ ...cur, [key]: value }));
  return (
    <div className="ea-form-grid" data-testid="ea-dynamic-form">
      {active.map((field) => {
        const val = values[field.key];
        const id = `ea-field-${field.key}`;
        return (
          <label key={field.key} className={field.type === "textarea" ? "ea-span-2" : ""}>
            {field.label}{field.required ? " *" : ""}
            {field.type === "textarea" && (
              <textarea id={id} value={val || ""} placeholder={field.placeholder || ""} disabled={busy}
                onChange={(e) => setVal(field.key, e.target.value)} data-testid={id} />
            )}
            {field.type === "select" && (
              <select id={id} value={val || ""} disabled={busy} onChange={(e) => setVal(field.key, e.target.value)} data-testid={id}>
                <option value="">Pilih…</option>
                {(field.options || []).map((opt) => <option key={opt} value={opt}>{opt}</option>)}
              </select>
            )}
            {field.type === "checkbox" && (
              <input id={id} type="checkbox" checked={!!val} disabled={busy} onChange={(e) => setVal(field.key, e.target.checked)} data-testid={id} />
            )}
            {field.type === "file" && (
              <span className="ea-file-field">
                <input id={id} type="file" disabled={busy} accept=".jpg,.jpeg,.png,.webp,.pdf"
                  onChange={(e) => onUpload(field.key, e.target.files?.[0])} data-testid={id} />
                {val?.name ? <span className="muted small">{val.name}</span> : null}
              </span>
            )}
            {["text", "number", "currency", "date", "datetime"].includes(field.type) && (
              <input
                id={id}
                type={field.type === "datetime" ? "datetime-local" : field.type === "date" ? "date" : field.type === "number" || field.type === "currency" ? "number" : "text"}
                value={val ?? ""}
                placeholder={field.placeholder || ""}
                disabled={busy}
                min={field.minimum ?? undefined}
                max={field.maximum ?? undefined}
                step={field.type === "currency" ? "0.01" : undefined}
                onChange={(e) => setVal(field.key, e.target.value)}
                data-testid={id}
              />
            )}
          </label>
        );
      })}
    </div>
  );
}

function RequestTable({ rows, loading, empty, employeeCol }) {
  return (
    <div className="table-wrap">
      <table className="ship-table" data-testid="ea-request-table">
        <thead>
          <tr>
            <th>No Pengajuan</th>
            {employeeCol ? <th>Karyawan</th> : null}
            <th>Jenis</th>
            <th>Tanggal</th>
            <th>Status</th>
            <th>Langkah saat ini</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {loading && <tr><td colSpan={employeeCol ? 7 : 6} className="muted">Memuat…</td></tr>}
          {!loading && !rows.length && <tr><td colSpan={employeeCol ? 7 : 6} className="muted">{empty}</td></tr>}
          {!loading && rows.map((row) => (
            <tr key={row.id} data-testid={`ea-row-${row.id}`}>
              <td><strong>{row.request_number}</strong></td>
              {employeeCol ? <td>{row.employee?.name || "-"}</td> : null}
              <td>{row.type_name}</td>
              <td>{formatDisplayDate(row.submitted_at)}</td>
              <td><span className={`ea-badge ea-badge-${(row.status || "").toLowerCase()}`}>{statusLabel(row.status)}</span></td>
              <td>{currentStepLabel(row)}</td>
              <td>
                <Link className="linkish" to={`/administrasi-karyawan/pengajuan/${row.id}`} data-testid={`ea-detail-${row.id}`}>Detail</Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function EmployeeAdmin({ mode }) {
  const meta = MODE_META[mode] || MODE_META.mine;
  const navigate = useNavigate();
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [types, setTypes] = useState([]);
  const [typeId, setTypeId] = useState("");
  const [values, setValues] = useState({});
  const [busy, setBusy] = useState(false);

  const selected = useMemo(() => types.find((t) => t.id === typeId), [types, typeId]);

  const loadList = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await api.employeeAdminRequests(meta.scope, q);
      setRows(res.requests || []);
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setLoading(false);
    }
  }, [meta.scope, q]);

  useEffect(() => {
    if (mode !== "create") loadList();
  }, [loadList, mode]);

  useEffect(() => {
    if (mode !== "create") return undefined;
    let cancelled = false;
    api.employeeAdminEligibleTypes().then((d) => {
      if (cancelled) return;
      const opts = eligibleTypeOptions(d.types || []);
      setTypes(opts);
      if (opts[0]) setTypeId(opts[0].id);
    }).catch((ex) => setErr(errDetail(ex)));
    return () => { cancelled = true; };
  }, [mode]);

  useEffect(() => {
    setValues({});
  }, [typeId]);

  const upload = async (key, file) => {
    if (!file) return;
    setBusy(true);
    setErr("");
    try {
      const res = await api.employeeAdminUpload(file, key);
      setValues((cur) => ({ ...cur, [key]: res.file }));
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!typeId) return;
    setBusy(true);
    setErr("");
    try {
      const res = await api.employeeAdminSubmitRequest({ type_id: typeId, values });
      navigate(`/administrasi-karyawan/pengajuan/${res.request.id}`);
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  if (mode === "create") {
    return (
      <div className="ea-page" data-testid={meta.testid}>
        <div className="page-heading">
          <div>
            <p className="eyebrow">{meta.eyebrow}</p>
            <h1>{meta.title}</h1>
            <p className="subhead">Pilih jenis pengajuan yang tersedia untuk Anda, lalu isi formulir digital.</p>
          </div>
        </div>
        {err && <div className="error" data-testid="ea-error"><AlertCircle size={14} /> {err}</div>}
        {!types.length && !err && <p className="muted" data-testid="ea-no-eligible">Tidak ada jenis pengajuan yang dapat Anda ajukan.</p>}
        {!!types.length && (
          <form className="settings-card" onSubmit={submit} data-testid="ea-create-form">
            <label>Jenis Pengajuan
              <select value={typeId} onChange={(e) => setTypeId(e.target.value)} data-testid="ea-type-select">
                {types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </label>
            {selected?.description ? <p className="muted small">{selected.description}</p> : null}
            <DynamicEmployeeForm
              fields={selected?.form_fields || []}
              values={values}
              setValues={setValues}
              onUpload={upload}
              busy={busy}
            />
            <button type="submit" className="primary" disabled={busy} data-testid="ea-submit">{busy ? "Mengirim…" : "Kirim Pengajuan"}</button>
          </form>
        )}
      </div>
    );
  }

  return (
    <div className="ea-page" data-testid={meta.testid}>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{meta.eyebrow}</p>
          <h1>{meta.title}</h1>
        </div>
        {mode === "mine" && (
          <Link to="/administrasi-karyawan/buat" className="primary" data-testid="ea-goto-create"><Plus size={14} /> Buat Pengajuan</Link>
        )}
      </div>
      {err && <div className="error"><AlertCircle size={14} /> {err}</div>}
      <div className="ea-toolbar">
        <label className="ea-search">
          <Search size={14} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cari nomor / jenis / nama" data-testid="ea-search" />
        </label>
      </div>
      <RequestTable
        rows={rows}
        loading={loading}
        empty="Belum ada pengajuan."
        employeeCol={mode !== "mine"}
      />
    </div>
  );
}
