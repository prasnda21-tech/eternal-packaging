import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Eye, Pencil } from "lucide-react";
import { useAuth } from "../lib/auth";
import api from "../lib/api";
import {
  PRODUCT_REQUEST_NEW_PATH,
  canCreateProductRequest,
  canFeedbackProductRequest,
  canReadOwnProductRequestDraft,
  canUpdateProductRequestStatus,
} from "../lib/navAccess";
import {
  STATUS_OPTIONS,
  customerSourceLabel,
  formatError,
  productSourceLabel,
  statusBadgeClass,
  statusLabel,
} from "../lib/productRequest";
import ProductRequestDetail from "../components/ProductRequestDetail";
import "./productRequest.css";

const FILTER_ALL = "all";
const FILTER_OWN_DRAFTS = "own_drafts";

export default function ProductRequestList() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const canCreate = canCreateProductRequest(hasPerm);
  const canSeeOwnDrafts = canReadOwnProductRequestDraft(hasPerm);
  const canFeedback = canFeedbackProductRequest(hasPerm);
  const canUpdateStatus = canUpdateProductRequestStatus(hasPerm);
  const [viewFilter, setViewFilter] = useState(FILTER_ALL);
  const [rows, setRows] = useState([]);
  const [drafts, setDrafts] = useState([]);
  const [detail, setDetail] = useState(null);
  const [activities, setActivities] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [workflowBusy, setWorkflowBusy] = useState("");

  const reloadLists = async () => {
    const jobs = [
      api.listProductRequests({ view: "submitted" }).then((data) => {
        setRows(data.requests || []);
      }),
    ];
    if (canSeeOwnDrafts) {
      jobs.push(
        api.listProductRequests({ view: "own_drafts" }).then((data) => {
          setDrafts(data.requests || []);
        }),
      );
    } else {
      setDrafts([]);
    }
    await Promise.all(jobs);
  };

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    reloadLists()
      .catch((err) => { if (!cancelled) setError(formatError(err)); })
      .finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, [canSeeOwnDrafts]);

  const openDetail = async (rowOrId) => {
    const id = typeof rowOrId === "string" ? rowOrId : rowOrId?.id;
    if (!id) return;
    setError("");
    try {
      const [detailRes, activityRes] = await Promise.all([
        api.getProductRequest(id),
        api.listProductRequestActivities(id),
      ]);
      if (detailRes.request?.receipt_only) return;
      if (detailRes.request?.status === "draft") return;
      setDetail(detailRes.request);
      setActivities(activityRes.activities || []);
      if (viewFilter === FILTER_OWN_DRAFTS) setViewFilter(FILTER_ALL);
    } catch (err) {
      setError(formatError(err));
    }
  };

  useEffect(() => {
    const openId = searchParams.get("open");
    if (!openId) return undefined;
    let cancelled = false;
    openDetail(openId).finally(() => {
      if (cancelled) return;
      const next = new URLSearchParams(searchParams);
      next.delete("open");
      setSearchParams(next, { replace: true });
    });
    return () => { cancelled = true; };
  }, [searchParams]);

  const closeDetail = () => {
    setDetail(null);
    setActivities([]);
    setWorkflowBusy("");
  };

  const refreshDetail = async (requestId) => {
    const [detailRes, activityRes] = await Promise.all([
      api.getProductRequest(requestId),
      api.listProductRequestActivities(requestId),
    ]);
    setDetail(detailRes.request);
    setActivities(activityRes.activities || []);
    await reloadLists();
  };

  const onSubmitFeedback = async (payload) => {
    if (!detail?.id) return false;
    setWorkflowBusy("feedback");
    setError("");
    try {
      await api.addProductRequestFeedback(detail.id, payload);
      await refreshDetail(detail.id);
      return true;
    } catch (err) {
      setError(formatError(err));
      return false;
    } finally {
      setWorkflowBusy("");
    }
  };

  const onSubmitStatus = async (payload) => {
    if (!detail?.id) return false;
    setWorkflowBusy("status");
    setError("");
    try {
      await api.updateProductRequestStatus(detail.id, payload);
      await refreshDetail(detail.id);
      return true;
    } catch (err) {
      setError(formatError(err));
      return false;
    } finally {
      setWorkflowBusy("");
    }
  };

  const statusCounts = useMemo(() => {
    const counts = { [FILTER_ALL]: rows.length };
    for (const opt of STATUS_OPTIONS) counts[opt.value] = 0;
    for (const row of rows) {
      if (counts[row.status] == null) counts[row.status] = 0;
      counts[row.status] += 1;
    }
    return counts;
  }, [rows]);

  const showDrafts = canSeeOwnDrafts && viewFilter === FILTER_OWN_DRAFTS;
  const showSubmitted = !showDrafts;
  const filteredRows = viewFilter === FILTER_ALL || viewFilter === FILTER_OWN_DRAFTS
    ? rows
    : rows.filter((row) => row.status === viewFilter);

  const filterPills = [
    { id: FILTER_ALL, label: "Semua", count: statusCounts[FILTER_ALL], testId: "prq-filter-all", countTestId: "prq-count-all" },
    ...STATUS_OPTIONS.map((opt) => ({
      id: opt.value,
      label: opt.label,
      count: statusCounts[opt.value] || 0,
      testId: `prq-filter-${opt.value}`,
      countTestId: `prq-filter-count-${opt.value}`,
    })),
  ];
  if (canSeeOwnDrafts) {
    filterPills.push({
      id: FILTER_OWN_DRAFTS,
      label: "Draft Saya",
      count: drafts.length,
      testId: "prq-tab-own-drafts",
      countTestId: "prq-count-draft",
    });
  }

  const headDescription = showDrafts
    ? "Draft milik Anda."
    : viewFilter === FILTER_ALL
      ? "Daftar permintaan yang sudah dikirim."
      : `Filter status: ${statusLabel(viewFilter)}.`;

  return (
    <div className="prq-page prq-list-page" data-testid="prq-list-page">
      <div className="prq-head prq-list-head" data-testid="prq-list-head">
        <div>
          <h1>Permintaan Produk</h1>
          <p>{headDescription}</p>
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
      {error && <div className="prq-msg is-error" data-testid="prq-list-error">{error}</div>}
      <div className="prq-list-chrome">
        <div className="prq-list-tabs" role="tablist" aria-label="Filter status permintaan" data-testid="prq-list-tabs">
          {filterPills.map((pill) => {
            const selected = viewFilter === pill.id;
            return (
              <button
                key={pill.id}
                type="button"
                role="tab"
                aria-selected={selected}
                className={"prq-tab" + (selected ? " is-active" : "")}
                data-testid={pill.testId}
                onClick={() => setViewFilter(pill.id)}
              >
                {pill.label}
                <span className="prq-count" data-testid={pill.countTestId}>{pill.count}</span>
              </button>
            );
          })}
        </div>
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
              <th>Feedback</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {!busy && filteredRows.length === 0 && (
              <tr><td colSpan={11}>Belum ada permintaan terkirim.</td></tr>
            )}
            {filteredRows.map((row) => (
              <tr key={row.id} data-testid={`prq-row-${row.id}`} onClick={() => openDetail(row)}>
                <td className="prq-doc">{row.request_number}</td>
                <td>{(row.submitted_at || row.created_at || "").slice(0, 10)}</td>
                <td className="prq-identity">
                  <strong className="prq-customer">{row.customer_name || "—"}</strong>
                  <span className="prq-doc">{row.request_number}</span>
                </td>
                <td>{customerSourceLabel(row.customer_source)}</td>
                <td>{row.product_name}</td>
                <td>{productSourceLabel(row.product_source)}</td>
                <td className="prq-qty">{row.potential_qty}</td>
                <td>{row.created_by_name}</td>
                <td>{row.branch_name}</td>
                <td>
                  <span className={`prq-status ${statusBadgeClass(row.status)}`}>
                    {row.status_label || statusLabel(row.status)}
                  </span>
                </td>
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
                <td className="prq-doc">{row.request_number}</td>
                <td>{(row.updated_at || row.created_at || "").slice(0, 10)}</td>
                <td className="prq-identity">
                  <strong className="prq-customer">{row.customer_name || "—"}</strong>
                  <span className="prq-doc">{row.request_number}</span>
                </td>
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
            <button type="button" className="prq-drawer-scrim" aria-label="Tutup" onClick={closeDetail} />
            <aside className="prq-drawer" data-testid="prq-detail-drawer">
              <h2>{detail.request_number}</h2>
              <p className="muted">
                {(detail.status_label || statusLabel(detail.status))}
                {detail.submitted_at ? ` · ${detail.submitted_at}` : ""}
              </p>
              <ProductRequestDetail
                request={detail}
                testId="prq-drawer-detail"
                activities={activities}
                canFeedback={canFeedback}
                canUpdateStatus={canUpdateStatus}
                workflowBusy={workflowBusy}
                onSubmitFeedback={onSubmitFeedback}
                onSubmitStatus={onSubmitStatus}
              />
            </aside>
          </>
        )}
      </div>
    </div>
  );
}
