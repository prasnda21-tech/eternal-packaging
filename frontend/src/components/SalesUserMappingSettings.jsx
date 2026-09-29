import { useEffect, useState } from "react";
import api from "../lib/api";

const SALES_RESPONSIBLE_ROLES = new Set(["sales_eternal", "holding_team", "direktur_utama"]);

function mappingTargetVisible(user, selectedId) {
  if (!user?.id || !SALES_RESPONSIBLE_ROLES.has(user.role)) {
    return user?.id && user.id === selectedId;
  }
  if (user.status === "inactive" && user.id !== selectedId) return false;
  return true;
}

export default function SalesUserMappingSettings({ notify, canEdit }) {
  const [entries, setEntries] = useState([]);
  const [users, setUsers] = useState([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.salesUserMapping().then((r) => setEntries(r.settings?.entries || [])).catch((e) => setErr(e.response?.data?.detail || e.message));
    api.adminListUsers().then((r) => setUsers(r.users || r || [])).catch(() => {});
  }, []);

  const setRow = (i, patch) => {
    setEntries((rows) => rows.map((row, idx) => (idx === i ? { ...row, ...patch } : row)));
  };
  const addRow = () => setEntries((rows) => [...rows, { label: "", user_id: "" }]);
  const removeRow = (i) => setEntries((rows) => rows.filter((_, idx) => idx !== i));

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const res = await api.updateSalesUserMapping({ entries });
      setEntries(res.settings?.entries || []);
      notify?.("Mapping Sales Accurate disimpan");
    } catch (e) {
      setErr(e.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="sales-user-mapping-tab" style={{ display: "grid", gap: 12, maxWidth: 820 }}>
      <p className="muted small">
        Mapping eksplisit label Accurate <code>orders.sales</code> → <code>users.id</code>.
        Target baru: akun aktif sales, holding, atau direktur. Ini tidak mengubah role atau hak akses.
        Tanpa baris yang sama persis, <code>sales_user_id</code> dikosongkan. Tidak ada fuzzy match.
      </p>
      {err && <p style={{ color: "#8f1f1f" }}>{err}</p>}
      <table className="ship-table">
        <thead>
          <tr>
            <th>Label Accurate</th>
            <th>users.id</th>
            {canEdit && <th />}
          </tr>
        </thead>
        <tbody>
          {entries.map((row, i) => (
            <tr key={`${row.label}-${i}`}>
              <td>
                <input
                  value={row.label || ""}
                  onChange={(e) => setRow(i, { label: e.target.value })}
                  disabled={!canEdit}
                  data-testid={`sales-map-label-${i}`}
                />
              </td>
              <td>
                <select
                  value={row.user_id || ""}
                  onChange={(e) => setRow(i, { user_id: e.target.value })}
                  disabled={!canEdit}
                  data-testid={`sales-map-user-${i}`}
                >
                  <option value="">— pilih user —</option>
                  {(Array.isArray(users) ? users : []).filter((u) => mappingTargetVisible(u, row.user_id)).map((u) => (
                    <option key={u.id} value={u.id}>{u.name} · {u.email}{u.employee_id ? ` · ${u.employee_id}` : ""}</option>
                  ))}
                </select>
              </td>
              {canEdit && (
                <td>
                  <button type="button" className="ghost sm" onClick={() => removeRow(i)}>Hapus</button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {canEdit && (
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="ghost sm" onClick={addRow} data-testid="sales-map-add">Tambah baris</button>
          <button type="button" className="primary sm" onClick={save} disabled={busy} data-testid="sales-map-save">
            {busy ? "Menyimpan…" : "Simpan mapping"}
          </button>
        </div>
      )}
    </div>
  );
}
