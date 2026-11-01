import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Eye, ExternalLink, Search, X } from "lucide-react";
import { Link } from "react-router-dom";
import { formatUrgentIdDate, formatUrgentIdDateTime } from "../../lib/urgentPoUi";

export const URGENT_REQUEST_STATUS_LABEL = {
  pending: "Menunggu",
  approving: "Sedang Diproses",
  approved: "Disetujui",
  rejected: "Ditolak",
  cancelled: "Dibatalkan",
};

export const URGENT_REQUEST_FILTERS = [
  { key: "active", label: "Aktif (Menunggu & Diproses)" },
  { key: "all", label: "Semua" },
  { key: "approved", label: "Disetujui" },
  { key: "rejected", label: "Ditolak" },
  { key: "cancelled", label: "Dibatalkan" },
];

const PAGE_SIZES = [5, 10, 20, 50];

export function urgentStatusBadgeClass(status) {
  const key = String(status || "").toLowerCase();
  if (key === "pending") return "pd-badge is-urgent-pending";
  if (key === "approving") return "pd-badge is-urgent-approving";
  if (key === "approved") return "pd-badge is-urgent-approved";
  if (key === "rejected") return "pd-badge is-urgent-rejected";
  if (key === "cancelled") return "pd-badge is-urgent-cancelled";
  return "pd-badge";
}

export function urgentStatusLabel(status) {
  const key = String(status || "").toLowerCase();
  return URGENT_REQUEST_STATUS_LABEL[key] || key || "—";
}

function historyLabel(action) {
  const key = String(action || "").toLowerCase();
  if (key === "submitted") return "Pengajuan dibuat";
  if (key === "approved") return "Disetujui";
  if (key === "rejected") return "Ditolak";
  if (key === "cancelled") return "Dibatalkan";
  if (key === "approving" || key === "claim") return "Sedang diproses";
  return key || "Perubahan status";
}

function QuietPanel({ status, onRetry, note }) {
  if (status === "loading") {
    return (
      <div className="pd-urgent-quiet is-loading" data-testid="produksi-urgent-loading">
        <div className="pd-urgent-skeleton" />
        <div className="pd-urgent-skeleton" />
        <div className="pd-urgent-skeleton" />
      </div>
    );
  }
  if (status === "forbidden") {
    return (
      <div className="pd-urgent-quiet is-forbidden" data-testid="produksi-urgent-forbidden">
        <strong>Akses Ditolak</strong>
        <p>{note || "Anda tidak memiliki izin untuk melihat daftar pengajuan urgent."}</p>
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className="pd-urgent-quiet is-error" data-testid="produksi-urgent-error">
        <strong>Gagal memuat data</strong>
        <p>{note || "Periksa koneksi lalu coba lagi."}</p>
        {onRetry ? (
          <button type="button" className="kb-btn" data-testid="produksi-urgent-retry" onClick={onRetry}>
            Coba Lagi
          </button>
        ) : null}
      </div>
    );
  }
  return (
    <div className="pd-urgent-quiet is-empty" data-testid="produksi-urgent-empty">
      <strong>Tidak ada pengajuan urgent</strong>
      <p>{note || "Belum ada pengajuan urgent dengan filter yang dipilih."}</p>
    </div>
  );
}

function DetailDrawer({ row, canOpenPo, onClose }) {
  const titleId = useId();
  if (!row) return null;
  const status = String(row.status || "").toLowerCase();
  const history = Array.isArray(row.history) ? row.history : [];
  return (
    <div className="kb-overlay" data-testid="produksi-urgent-drawer-overlay" onMouseDown={onClose}>
      <aside
        className="kb-drawer pd-urgent-drawer"
        data-testid="produksi-urgent-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="kb-drawer-head">
          <div>
            <h2 id={titleId}>Detail Pengajuan Urgent</h2>
            <span className={urgentStatusBadgeClass(status)} data-testid="produksi-urgent-drawer-status">
              {urgentStatusLabel(status)}
            </span>
          </div>
          <button type="button" className="kb-drawer-close" aria-label="Tutup" onClick={onClose}>×</button>
        </header>
        <div className="kb-drawer-body">
          <section className="pd-urgent-detail-block">
            <h3>Informasi Pengajuan</h3>
            <dl className="pd-urgent-detail-list">
              <div><dt>Nomor PO</dt><dd data-testid="produksi-urgent-drawer-po">{row.po_number || "—"}</dd></div>
              <div><dt>Customer</dt><dd>{row.customer_name || "—"}</dd></div>
              <div><dt>Cabang</dt><dd>{row.branch_name || "—"}</dd></div>
              <div><dt>Tanggal Pengajuan</dt><dd>{formatUrgentIdDateTime(row.requested_at) || "—"}</dd></div>
              <div><dt>Pemohon</dt><dd>{row.requested_by_name || "—"}</dd></div>
              <div className="is-block">
                <dt>Alasan Urgent</dt>
                <dd className="pd-urgent-reason-box" data-testid="produksi-urgent-drawer-reason">{row.reason || "—"}</dd>
              </div>
              <div><dt>Target Tanggal</dt><dd>{formatUrgentIdDate(row.target_completion_date) || row.target_completion_date || "—"}</dd></div>
              <div><dt>Status</dt><dd><span className={urgentStatusBadgeClass(status)}>{urgentStatusLabel(status)}</span></dd></div>
              <div><dt>Tanggal Keputusan</dt><dd>{formatUrgentIdDateTime(row.decided_at) || "—"}</dd></div>
              {row.decision_notes ? (
                <div className="is-block"><dt>Catatan Keputusan</dt><dd>{row.decision_notes}</dd></div>
              ) : null}
            </dl>
          </section>
          <section className="pd-urgent-detail-block">
            <h3>Riwayat Pengajuan</h3>
            {history.length ? (
              <ol className="pd-urgent-timeline" data-testid="produksi-urgent-timeline">
                {history.map((entry, index) => {
                  const last = index === history.length - 1;
                  return (
                    <li key={`${entry.action}-${entry.at}-${index}`} className={last ? "is-current" : ""}>
                      <span className="pd-urgent-timeline-dot" aria-hidden="true" />
                      <div>
                        <strong>{historyLabel(entry.action)}</strong>
                        <p>{formatUrgentIdDateTime(entry.at) || "—"}{entry.by_name ? ` · ${entry.by_name}` : ""}</p>
                        {entry.note ? <p className="pd-urgent-timeline-note">{entry.note}</p> : null}
                      </div>
                    </li>
                  );
                })}
                {status === "pending" || status === "approving" ? (
                  <li className="is-pending">
                    <span className="pd-urgent-timeline-dot is-open" aria-hidden="true" />
                    <div>
                      <strong>Menunggu keputusan</strong>
                      <p>Sedang dalam antrian review.</p>
                    </div>
                  </li>
                ) : null}
              </ol>
            ) : (
              <p className="muted small">Belum ada riwayat.</p>
            )}
          </section>
        </div>
        <footer className="pd-urgent-drawer-foot">
          {canOpenPo && row.order_id ? (
            <Link
              className="kb-btn is-primary pd-urgent-open-po"
              to={`/po/${encodeURIComponent(row.order_id)}`}
              data-testid="produksi-urgent-open-po"
            >
              Buka Detail PO <ExternalLink size={14} aria-hidden="true" />
            </Link>
          ) : (
            <p className="pd-urgent-po-locked" data-testid="produksi-urgent-po-locked">Akses detail PO terbatas</p>
          )}
        </footer>
      </aside>
    </div>
  );
}

export default function UrgentRequestsWidget({ model, fetchList, editing = false }) {
  const [statusFilter, setStatusFilter] = useState("active");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [body, setBody] = useState(null);
  const [loadState, setLoadState] = useState("loading");
  const [selected, setSelected] = useState(null);
  const requestRef = useRef(0);

  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, debouncedQuery, pageSize]);

  const gated = model?.status === "forbidden";

  useEffect(() => {
    if (gated) {
      setLoadState("forbidden");
      setBody(null);
      return undefined;
    }
    if (typeof fetchList !== "function") {
      setLoadState("error");
      return undefined;
    }
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    setLoadState("loading");
    let live = true;
    fetchList({ status: statusFilter, q: debouncedQuery, page, page_size: pageSize })
      .then((payload) => {
        if (!live || requestId !== requestRef.current) return;
        setBody(payload || null);
        const total = Number(payload?.total || 0);
        setLoadState(total > 0 ? "ready" : "empty");
      })
      .catch((error) => {
        if (!live || requestId !== requestRef.current) return;
        setBody(null);
        setLoadState(error?.response?.status === 403 ? "forbidden" : "error");
      });
    return () => { live = false; };
  }, [gated, fetchList, statusFilter, debouncedQuery, page, pageSize]);

  const items = body?.items || [];
  const total = Number(body?.total || 0);
  const canOpenPo = Boolean(body?.can_open_po);
  const pageCount = Math.max(1, Math.ceil(total / pageSize) || 1);
  const from = total ? (page - 1) * pageSize + 1 : 0;
  const to = total ? Math.min(page * pageSize, total) : 0;

  const pageButtons = useMemo(() => {
    const maxButtons = 5;
    const start = Math.max(1, Math.min(page - 2, pageCount - maxButtons + 1));
    const end = Math.min(pageCount, start + maxButtons - 1);
    const list = [];
    for (let n = Math.max(1, start); n <= end; n += 1) list.push(n);
    return list;
  }, [page, pageCount]);

  function retry() {
    setLoadState((current) => (current === "error" ? "loading" : current));
    setPage((value) => value);
    requestRef.current += 1;
    setLoadState("loading");
    if (typeof fetchList !== "function") return;
    fetchList({ status: statusFilter, q: debouncedQuery, page, page_size: pageSize })
      .then((payload) => {
        setBody(payload || null);
        setLoadState(Number(payload?.total || 0) > 0 ? "ready" : "empty");
      })
      .catch((error) => {
        setBody(null);
        setLoadState(error?.response?.status === 403 ? "forbidden" : "error");
      });
  }

  return (
    <div className="pd-urgent-requests" data-testid="produksi-urgent-requests">
      <div className="pd-urgent-toolbar" data-drill-ignore="true">
        <div className={`pd-urgent-search${query ? " has-value" : ""}`}>
          <Search size={14} className="pd-urgent-search-icon" aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Cari nomor PO atau nama customer..."
            data-testid="produksi-urgent-search"
            aria-label="Cari pengajuan urgent"
            disabled={editing || gated}
          />
          {query ? (
            <button
              type="button"
              className="pd-urgent-search-clear"
              data-testid="produksi-urgent-search-clear"
              aria-label="Bersihkan pencarian"
              disabled={editing || gated}
              onClick={() => setQuery("")}
            >
              <X size={14} aria-hidden="true" />
            </button>
          ) : null}
        </div>
        <div className="pd-switch pd-urgent-filters" role="group" aria-label="Filter status pengajuan urgent">
          {URGENT_REQUEST_FILTERS.map((item) => (
            <button
              type="button"
              key={item.key}
              className={`kb-btn${statusFilter === item.key ? " is-on" : ""}`}
              data-testid={`produksi-urgent-filter-${item.key}`}
              disabled={editing || gated}
              onClick={() => setStatusFilter(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {loadState === "ready" ? (
        <>
          <div className="pd-table-wrap pd-urgent-table-wrap">
            <table className="pd-table pd-urgent-table" data-testid="produksi-urgent-table">
              <thead>
                <tr>
                  <th>Tgl Pengajuan <span aria-hidden="true">↓</span></th>
                  <th>PO</th>
                  <th>Customer</th>
                  <th>Cabang</th>
                  <th>Pemohon</th>
                  <th>Alasan Urgent</th>
                  <th>Target Tanggal</th>
                  <th>Status</th>
                  <th>Tgl Keputusan</th>
                  <th>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.id} data-testid="produksi-urgent-row">
                    <td>{formatUrgentIdDate(row.requested_at) || "—"}</td>
                    <td className="pd-po">{row.po_number || "—"}</td>
                    <td>{row.customer_name || "—"}</td>
                    <td>{row.branch_name || "—"}</td>
                    <td>{row.requested_by_name || "—"}</td>
                    <td className="pd-urgent-reason">{row.reason || "—"}</td>
                    <td>{formatUrgentIdDate(row.target_completion_date) || row.target_completion_date || "—"}</td>
                    <td>
                      <span className={urgentStatusBadgeClass(row.status)} data-testid="produksi-urgent-status">
                        {urgentStatusLabel(row.status)}
                      </span>
                    </td>
                    <td>{formatUrgentIdDate(row.decided_at) || "—"}</td>
                    <td>
                      <button
                        type="button"
                        className="kb-btn pd-urgent-view"
                        data-testid="produksi-urgent-view"
                        disabled={editing}
                        onClick={() => setSelected(row)}
                      >
                        <Eye size={14} aria-hidden="true" /> Lihat
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pd-urgent-pager" data-testid="produksi-urgent-pager">
            <p>Menampilkan {from}-{to} dari {total} data</p>
            <label>
              Baris per halaman
              <select
                value={pageSize}
                onChange={(event) => setPageSize(Number(event.target.value) || 10)}
                data-testid="produksi-urgent-page-size"
                disabled={editing}
              >
                {PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
              </select>
            </label>
            <div className="pd-urgent-pages">
              <button type="button" className="kb-btn" disabled={editing || page <= 1} onClick={() => setPage(1)} aria-label="Halaman pertama">«</button>
              <button type="button" className="kb-btn" disabled={editing || page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} aria-label="Sebelumnya">‹</button>
              {pageButtons.map((n) => (
                <button
                  type="button"
                  key={n}
                  className={`kb-btn${n === page ? " is-on" : ""}`}
                  disabled={editing}
                  onClick={() => setPage(n)}
                >
                  {n}
                </button>
              ))}
              <button type="button" className="kb-btn" disabled={editing || page >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))} aria-label="Berikutnya">›</button>
              <button type="button" className="kb-btn" disabled={editing || page >= pageCount} onClick={() => setPage(pageCount)} aria-label="Halaman terakhir">»</button>
            </div>
          </div>
        </>
      ) : (
        <QuietPanel status={loadState} onRetry={loadState === "error" ? retry : undefined} />
      )}

      {selected ? (
        <DetailDrawer row={selected} canOpenPo={canOpenPo} onClose={() => setSelected(null)} />
      ) : null}
    </div>
  );
}
