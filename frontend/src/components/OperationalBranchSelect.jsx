import { useEffect, useState } from "react";
import api from "../lib/api";

/** Operational branches are independent of customer outlet and Accurate IDs. */
export default function OperationalBranchSelect({ value, onChange, disabled = false }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    api.operationalBranchOptions().then((result) => {
      if (alive) setData(result);
    }).catch((e) => { if (alive) setError(e.response?.data?.detail || "Gagal memuat cabang"); });
    return () => { alive = false; };
  }, []);
  useEffect(() => {
    if (!value && data?.primary_branch_id) onChange(data.primary_branch_id);
  }, [value, data, onChange]);
  return <label>Cabang Operasional
    <select aria-label="Cabang Operasional" value={value || ""} required disabled={disabled || !data}
      onChange={(e) => onChange(e.target.value)}>
      <option value="">Pilih cabang</option>
      {(data?.branches || []).map((b) => <option key={b.id} value={b.id}>{b.name || b.code}</option>)}
    </select>
    {error && <span role="alert">{error}</span>}
  </label>;
}
