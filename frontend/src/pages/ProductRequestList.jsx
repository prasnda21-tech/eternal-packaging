import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Eye, Pencil } from "lucide-react";
import { useAuth } from "../lib/auth";
import api from "../lib/api";
import {
  PRODUCT_REQUEST_NEW_PATH,
  canCreateProductRequest,
  canReadAllProductRequests,
  canReadOwnProductRequestDraft,
} from "../lib/navAccess";
import { customerSourceLabel, formatError, productSourceLabel } from "../lib/productRequest";
import ProductRequestDetail from "../components/ProductRequestDetail";
import "./productRequest.css";

export default function ProductRequestList() {
  const navigate = useNavigate();
  const { hasPerm } = useAuth();
  const canCreate = canCreateProductRequest(hasPerm);
  const canReadAll = canReadAllProductRequests(hasPerm);
  const canSeeOwnDrafts = canReadOwnProductRequestDraft(hasPerm);
  const [tab, setTab] = useState(canReadAll ? "submitted" : "own_drafts");
  const [rows, setRows] = useState([]);
  const [drafts, setDrafts] = useState([]);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!canReadAll && !canSeeOwnDrafts) return undefined;
    let cancelled = false;
    setBusy(true);
    const jobs = [];
    if (canReadAll) {
      jobs.push(
        api.listProductRequests({ view: "submitted" }).then((data) => {
          if (!cancelled) setRows(data.requests || []);
        }),
      );
    }
    if (canSeeOwnDrafts) {
      jobs.push(
        api.listProductRequests({ view: "own_drafts" }).then((data) => {
          if (!cancelled) setDrafts(data.requests || []);
        }),
      );
    }
    Promise.all(jobs)
      .catch((err) => { if (!cancelled) setError(formatError(err)); })
      .finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, [canReadAll, canSeeOwnDrafts]);

  if (!canReadAll && !canSeeOwnDrafts) {
    return <Navigate to="/" replace />;
  }

  const openDetail = async (row) => {
    setError("");
    try {
      const data = await api.getProductRequest(row.id);
      if (data.request?.receipt_only) return;
      setDetail(data.request);
    } catch (err) {
      setError(formatError(err));
    }
  };

  const showSubmitted = canReadAll && tab === "submitted";
  const showDrafts = canSeeOwnDrafts && tab === "own_drafts";

  return (
    <div className="prq-page prq-list-page" data-testid="prq-list-page">
      <div className="prq-head prq-list-head" data-testid="prq-list-head">
        <div>
          <h1>Permintaan Produk</h1>
          <p>{showDrafts ? "Draft milik Anda." : "Daftar permintaan yang sudah dikirim."}</p>
        </div>
        {canCreate && (
          <button
            type="button"
            className="prq-btn prq-btn-primary"
            data-testid="prq-create-from-list"
            onClick={() => navigate(PRODUCT_REQUEST_NEW_PATH)}
          >
            Buat Permintaan
          </button>
        )}
      </div>
      <div className="prq-list-body">
      {error && <div className="prq-msg is-error">{error}</div>}
      <div className="prq-list-tabs" role="tablist" data-testid="prq-list-tabs">
        {canReadAll && (
          <button
            type="button"
            role="tab"
            aria-selected={tab === "submitted"}
            className={"prq-tab" + (tab === "submitted" ? " is-active" : "")}
            data-testid="prq-tab-submitted"
            onClick={() => setTab("submitted")}
          >
            Terkirim
            <span className="prq-count prq-count-submitted" data-testid="prq-count-submitted">{rows.length}</span>
          </button>
        )}
        {canSeeOwnDrafts && (
          <button
            type="button"
            role="tab"
            aria-selected={tab === "own_drafts"}
            className={"prq-tab" + (tab === "own_drafts" ? " is-active" : "")}
            data-testid="prq-tab-own-drafts"
            onClick={() => setTab("own_drafts")}
          >
            Draft Saya
            <span className="prq-count prq-count-draft" data-testid="prq-count-draft">{drafts.length}</span>
          </button>
        )}
      </div>
      {showSubmitted && (
      <div className="prq-list-wrap">
        <table className="prq-table" data-testid="prq-list-table">
          <colgroup>
            <col className="prq-col-number" />
            <col className="prq-col-date" />
            <col className="prq-col-customer" />
            <col className="prq-col-csource" />
            <col className="prq-col-product" />
            <col className="prq-col-psource" />
            <col className="prq-col-qty" />
            <col className="prq-col-crm" />
            <col className="prq-col-branch" />
            <col className="prq-col-status" />
            <col className="prq-col-action" />
          </colgroup>
          <thead>
            <tr>
              <th>Nomor</th>
              <th>Tanggal</th>
              <th>Customer</th>
              <th>Sumber Customer</th>
              <th>Produk</th>
              <th>Sumber Produk</th>
              <th>Qty Potensi</th>
              <th>CRM</th>
              <th>Cabang</th>
              <th>Status</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {!busy && rows.length === 0 && (
              <tr><td colSpan={11}>Belum ada permintaan terkirim.</td></tr>
            )}
            {rows.map((row) => (
              <tr key={row.id} data-testid={`prq-row-${row.id}`} onClick={() => openDetail(row)}>
                <td>{row.request_number}</td>
                <td>{(row.submitted_at || row.created_at || "").slice(0, 10)}</td>
                <td>{row.customer_name}</td>
                <td>{customerSourceLabel(row.customer_source)}</td>
                <td>{row.product_name}</td>
                <td>{productSourceLabel(row.product_source)}</td>
                <td className="prq-qty">{row.potential_qty}</td>
                <td>{row.created_by_name}</td>
                <td>{row.branch_name}</td>
                <td><span className="prq-status prq-status-submitted">Terkirim</span></td>
                <td>
                  <button
                    type="button"
                    className="prq-btn prq-btn-view"
                    data-testid={`prq-view-${row.id}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      openDetail(row);
                    }}
                  >
                    <Eye size={12} /> Lihat Detail
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}
      {showDrafts && (
      <div className="prq-list-wrap">
        <table className="prq-table prq-draft-table" data-testid="prq-draft-table">
          <colgroup>
            <col className="prq-col-number" />
            <col className="prq-col-date" />
            <col className="prq-col-customer" />
            <col className="prq-col-product" />
            <col className="prq-col-qty" />
            <col className="prq-col-progress" />
            <col className="prq-col-status" />
            <col className="prq-col-action" />
          </colgroup>
          <thead>
            <tr>
              <th>Nomor</th>
              <th>Tanggal Update</th>
              <th>Customer</th>
              <th>Produk</th>
              <th>Qty Potensi</th>
              <th>Kelengkapan</th>
              <th>Status</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {!busy && drafts.length === 0 && (
              <tr><td colSpan={8}>Belum ada draft.</td></tr>
            )}
            {drafts.map((row) => (
              <tr key={row.id} data-testid={`prq-draft-row-${row.id}`}>
                <td>{row.request_number}</td>
                <td>{(row.updated_at || row.created_at || "").slice(0, 10)}</td>
                <td>{row.customer_name}</td>
                <td>{row.product_name}</td>
                <td className="prq-qty">{row.potential_qty ?? ""}</td>
                <td data-testid={`prq-draft-complete-${row.id}`}>
                  {row.completeness ? `${row.completeness.done}/${row.completeness.total}` : "—"}
                </td>
                <td><span className="prq-status prq-status-draft">Draft</span></td>
                <td>
                  <button
                    type="button"
                    className="prq-btn prq-btn-continue"
                    data-testid={`prq-continue-${row.id}`}
                    onClick={() => navigate(`/penjualan/permintaan-produk/${row.id}`)}
                  >
                    <Pencil size={12} /> Lanjutkan
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}
      </div>
      <div className="prq-drawer-root" hidden={!detail} data-testid="prq-detail-drawer-root">
        {detail && (
          <>
            <button type="button" className="prq-drawer-scrim" aria-label="Tutup" onClick={() => setDetail(null)} />
            <aside className="prq-drawer" data-testid="prq-detail-drawer">
              <h2>{detail.request_number}</h2>
              <p className="muted">Snapshot saat dikirim · {detail.submitted_at}</p>
              <ProductRequestDetail request={detail} testId="prq-drawer-detail" />
            </aside>
          </>
        )}
      </div>
    </div>
  );
}
