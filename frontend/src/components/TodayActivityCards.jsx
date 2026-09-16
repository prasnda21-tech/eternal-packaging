import { Link } from "react-router-dom";
import { fmtQty } from "./qtyUi";

function TodayTable({ title, empty, rows, columns, allHref, allLabel, total, testid }) {
  return (
    <section className="today-card" data-testid={testid}>
      <header className="today-card-head">
        <h3>{title}</h3>
        {typeof total === "number" && <span className="muted small">{total}</span>}
      </header>
      {rows.length === 0 ? (
        <p className="today-empty" data-testid={`${testid}-empty`}>{empty}</p>
      ) : (
        <div className="today-table-wrap">
          <table className="today-table">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c.key} className={c.num ? "num" : ""}>{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.row_id || r.id || r.po_number} data-testid={`${testid}-row-${r.po_number}`}>
                  {columns.map((c) => (
                    <td key={c.key} className={c.num ? "num" : ""}>
                      {c.render ? c.render(r) : (r[c.key] ?? "—")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {allHref && total > rows.length && (
        <div className="today-card-foot">
          <Link to={allHref} className="ghost sm" data-testid={`${testid}-see-all`}>
            {allLabel || "Lihat semua"}
          </Link>
        </div>
      )}
    </section>
  );
}

const workCols = [
  {
    key: "po_number",
    label: "No PO",
    render: (r) => <Link to={`/po/${r.id}`} data-testid={`today-work-po-${r.po_number}`}>{r.po_number}</Link>,
  },
  {
    key: "customer",
    label: "Customer / Outlet",
    render: (r) => (
      <span>
        <strong>{r.customer || "—"}</strong>
        {r.outlet ? <span className="muted small"> · {r.outlet}</span> : null}
      </span>
    ),
  },
  { key: "item", label: "Barang" },
  { key: "stage", label: "Tahapan" },
  { key: "operator", label: "Operator" },
  {
    key: "qty",
    label: "Qty",
    num: true,
    render: (r) => (r.qty == null ? "—" : fmtQty(r.qty)),
  },
];

const shipCols = [
  {
    key: "po_number",
    label: "No PO",
    render: (r) => <Link to={`/po/${r.id}`} data-testid={`today-ship-po-${r.po_number}`}>{r.po_number}</Link>,
  },
  {
    key: "customer",
    label: "Customer / Outlet",
    render: (r) => (
      <span>
        <strong>{r.customer || "—"}</strong>
        {r.outlet ? <span className="muted small"> · {r.outlet}</span> : null}
      </span>
    ),
  },
  { key: "surat_jalan", label: "Surat Jalan", render: (r) => r.surat_jalan || "—" },
  {
    key: "item",
    label: "Item",
    render: (r) => (
      <span>
        {r.item || "—"}
        {r.line_count ? <span className="muted small"> · {r.line_count} line</span> : null}
      </span>
    ),
  },
  {
    key: "qty",
    label: "Qty",
    num: true,
    render: (r) => (r.qty == null ? "—" : fmtQty(r.qty)),
  },
  {
    key: "status",
    label: "Status",
    render: (r) => (
      <span className={r.partial && r.status === "Terkirim" ? "" : ""} data-testid={`today-ship-status-${r.po_number}`}>
        {r.status || "—"}
      </span>
    ),
  },
];

export default function TodayActivityCards({ data }) {
  const work = data?.work_today || { preview: [], total: 0 };
  const ship = data?.shipments_today || { preview: [], total: 0 };
  return (
    <div className="today-cards" data-testid="today-activity-cards">
      <TodayTable
        title="Pengerjaan Hari Ini"
        empty="Tidak ada pengerjaan hari ini"
        rows={work.preview || []}
        columns={workCols}
        total={work.total || 0}
        allHref="/produksi"
        allLabel="Lihat semua"
        testid="today-work"
      />
      <TodayTable
        title="Pengiriman Terjadwal Hari Ini"
        empty="Tidak ada pengiriman hari ini"
        rows={ship.preview || []}
        columns={shipCols}
        total={ship.total || 0}
        testid="today-ship"
      />
    </div>
  );
}
