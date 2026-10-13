import { useEffect, useState } from "react";
import api from "../lib/api";
import {
  FORM_ACCESS_DEFS,
  applyFormToggle,
  payloadForRoleSave,
  roleHasForm,
} from "../lib/formAccess";
import { isDirectorFullAccessRole } from "../lib/packagePermissions";

export default function FormAccessMatrix({ notify, canEdit }) {
  const [roles, setRoles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = async () => {
    setErr("");
    try {
      const d = await api.adminListRoles();
      setRoles(d.roles || []);
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat role");
    }
  };
  useEffect(() => { load(); }, []);

  const toggle = async (roleDoc, formId, enabled) => {
    if (!canEdit || busy || roleDoc.role === "admin" || isDirectorFullAccessRole(roleDoc.role)) return;
    setBusy(true);
    setErr("");
    try {
      const next = applyFormToggle(roleDoc.permissions || [], formId, enabled);
      // Form matrix save omits confirm_package_keys — never activates legacy package grants.
      await api.adminUpdateRolePermissions(roleDoc.role, payloadForRoleSave(next));
      notify?.(`Form ${FORM_ACCESS_DEFS.find((f) => f.id === formId)?.label || formId} untuk ${roleDoc.label || roleDoc.role} disimpan`);
      await load();
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal menyimpan akses form");
    } finally {
      setBusy(false);
    }
  };

  const visibleRoles = roles.filter((r) => r.status !== "inactive");

  return (
    <div className="settings-card" data-testid="form-access-matrix" style={{ marginBottom: 16 }}>
      <h3 style={{ marginTop: 0 }}>Akses Form per Role</h3>
      <p className="muted small">
        Satu role boleh punya banyak form, dan satu form boleh diberikan ke banyak role.
        Pengaturan ini memakai permission RBAC yang sudah ada — bukan sistem otorisasi kedua.
        Admin mengatur sendiri; tidak ada form yang otomatis aktif hanya karena nama role.
      </p>
      {err && <div className="error" data-testid="form-access-error">{typeof err === "object" ? err.message : err}</div>}
      <div className="table-wrap form-access-wrap">
        <table className="ship-table form-access-table" data-testid="form-access-table">
          <thead>
            <tr>
              <th>Role</th>
              {FORM_ACCESS_DEFS.map((f) => (
                <th key={f.id} data-testid={`form-access-col-${f.id}`}>{f.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRoles.map((r) => {
              const director = isDirectorFullAccessRole(r.role);
              const locked = r.role === "admin" || director || !canEdit;
              return (
                <tr key={r.role} data-testid={`form-access-row-${r.role}`}>
                  <td>
                    <strong>{r.label || r.role}</strong>
                    <div className="muted small mono">{r.role}</div>
                    {director && <div className="muted small">Akses penuh sistem</div>}
                  </td>
                  {FORM_ACCESS_DEFS.map((f) => {
                    const on = r.role === "admin" || director || roleHasForm(r.permissions, f.id);
                    return (
                      <td key={f.id}>
                        <input
                          type="checkbox"
                          checked={on}
                          disabled={locked || busy}
                          onChange={(e) => toggle(r, f.id, e.target.checked)}
                          data-testid={`form-access-${r.role}-${f.id}`}
                        />
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!canEdit && (
        <p className="muted small" style={{ marginTop: 8 }}>
          Ubah centang membutuhkan permission <code>permissions.manage</code>.
        </p>
      )}
    </div>
  );
}
