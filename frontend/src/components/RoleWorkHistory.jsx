import { formatDisplayDateTime } from "../lib/constants";
import { fmtQty } from "./qtyUi";

export default function RoleWorkHistory({ entries, unit = "pcs", mode = "qty", testid = "role-work-history" }) {
  const rows = Array.isArray(entries) ? entries : [];
  if (!rows.length) {
    return <p className="muted small" data-testid={`${testid}-empty`}>Belum ada pekerjaan terkonfirmasi.</p>;
  }
  return (
    <table className="work-history-table" data-testid={testid}>
      <thead>
        <tr>
          <th>{mode === "evidence" ? "Aksi / Qty" : "Qty"}</th>
          <th>Waktu</th>
          <th>Nama</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((e, i) => (
          <tr key={e.id || i} className="is-locked" data-testid={`${testid}-row-${i}`}>
            <td className="mono">
              {e.label && (e.qty == null || mode === "evidence")
                ? `${e.label}${e.qty != null ? ` · ${fmtQty(e.qty)} ${unit}` : ""}`
                : `${fmtQty(e.qty)} ${unit}`}
            </td>
            <td className="mono small">{e.created_at ? formatDisplayDateTime(e.created_at) : "—"}</td>
            <td>{e.created_by_name || e.created_by || "—"}</td>
            <td className="muted small">Terkonfirmasi</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
