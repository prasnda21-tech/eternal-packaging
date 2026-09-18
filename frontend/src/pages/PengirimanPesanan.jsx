import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  ExternalLink,
  FileText,
  Package,
  Plus,
  Printer,
  RefreshCw,
  Search,
  Trash2,
  Truck,
  X,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate } from "../lib/constants";
import SjDeliveryLocationPicker, { sjOverridePayload } from "../components/SjDeliveryLocationPicker";
import { deliveryDateInRange, paginateRows, SALES_DEFAULT_PAGE_SIZE } from "../lib/salesWorkspaceList";
import SalesPaginationBar from "../components/salesWorkspace/SalesPaginationBar";
import SalesWorkspaceDrawer from "../components/salesWorkspace/SalesWorkspaceDrawer";
import SalesRowMenu from "../components/salesWorkspace/SalesRowMenu";
import "./salesWorkspacePages.css";

function getTodayString() {
  const d = new Date();
  return d.toISOString().split("T")[0];
}

export default function PengirimanPesanan() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { user, hasPerm } = useAuth();

  const canCreate = hasPerm("sales.delivery.create") || hasPerm("sales_order.create") || hasPerm("po.edit") || user?.role === "admin";
  const canPrint = hasPerm("sales.delivery.print") || hasPerm("sales.delivery.view") || hasPerm("sales_order.view") || user?.role === "admin";
  const canDelete = hasPerm("sales.delivery.delete") || hasPerm("sales.delivery.create") || hasPerm("po.edit") || user?.role === "admin";

  // Navigation mode: 'list' | 'create' | 'detail'
  const queryPoId = searchParams.get("po_id") || "";
  const initialMode = (searchParams.get("action") === "create" || !!queryPoId) ? "create" : "list";
  const [viewMode, setViewMode] = useState(initialMode);
  
  // List state
  const [deliveries, setDeliveries] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listErr, setListErr] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [listPage, setListPage] = useState(1);
  const [listPageSize, setListPageSize] = useState(SALES_DEFAULT_PAGE_SIZE);
  const [createStep, setCreateStep] = useState(1);
  const [drawerTab, setDrawerTab] = useState("ringkasan");
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteErr, setDeleteErr] = useState("");

  // Selected PO for Create Delivery Form
  const [selectedPoId, setSelectedPoId] = useState(queryPoId);
  const [poSummary, setPoSummary] = useState(null);
  const [poSummaryLoading, setPoSummaryLoading] = useState(false);
  const [poSummaryErr, setPoSummaryErr] = useState("");

  // Create Form State
  const [formData, setFormData] = useState({
    delivery_date: getTodayString(),
    outlet: "",
    recipient: "",
    address: "",
    delivery_zone: "",
    sales: "",
    expedition: "Internal fleet",
    notes: "",
    line_qtys: {}, // { [line_idx]: number }
    line_notes: {}, // { [line_idx]: string }
  });
  const [formBusy, setFormBusy] = useState(false);
  const [formErr, setFormErr] = useState("");
  const [successMsg, setSuccessMsg] = useState("");
  const [sjLocationOverride, setSjLocationOverride] = useState(null);

  // Detail Modal / View State
  const [selectedDeliveryId, setSelectedDeliveryId] = useState(null);
  const [detailData, setDetailData] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailErr, setDetailErr] = useState("");

  // Available POs for selection dropdown when creating from scratch
  const [availablePOs, setAvailablePOs] = useState([]);
  const [loadingPOs, setLoadingPOs] = useState(false);

  // Load deliveries list
  const loadDeliveries = useCallback(async () => {
    setListLoading(true);
    setListErr("");
    try {
      const params = {};
      if (searchQuery.trim()) params.q = searchQuery.trim();
      if (statusFilter !== "all") params.status = statusFilter;
      const res = await api.listSalesDeliveries(params);
      setDeliveries(res.deliveries || []);
      setSelectedIds(new Set());
    } catch (e) {
      const det = e.response?.data?.detail;
      setListErr(typeof det === "object" ? det.message : (det || "Gagal memuat daftar Pengiriman Pesanan"));
    } finally {
      setListLoading(false);
    }
  }, [searchQuery, statusFilter]);

  // Load active POs with remaining quantity
  const loadActivePOs = useCallback(async () => {
    if (!canCreate) return;
    setLoadingPOs(true);
    try {
      const res = await api.listSalesOrders({ status: "aktif" });
      const activeList = (res.orders || []).filter(
        (o) => !o.cancelled && (!o.delivery_summary || o.delivery_summary.total_remaining > 0)
      );
      setAvailablePOs(activeList);
    } catch {
      // Best effort load POs
    } finally {
      setLoadingPOs(false);
    }
  }, [canCreate]);

  // Load PO summary when selectedPoId changes
  const loadPoSummary = useCallback(async (poId) => {
    if (!poId) {
      setPoSummary(null);
      setSjLocationOverride(null);
      return;
    }
    setPoSummaryLoading(true);
    setPoSummaryErr("");
    setFormErr("");
    try {
      const data = await api.getPoDeliverySummary(poId);
      setPoSummary(data);
      setSjLocationOverride(null);

      // Pre-fill header fields
      setFormData((prev) => {
        const initialQtys = {};
        const initialNotes = {};
        (data.lines || []).forEach((l) => {
          initialQtys[l.line_idx] = l.remaining_qty;
          initialNotes[l.line_idx] = "";
        });

        return {
          ...prev,
          delivery_date: prev.delivery_date || getTodayString(),
          outlet: data.outlet || "",
          recipient: data.recipient || "",
          address: data.address || "",
          delivery_zone: data.delivery_zone || "",
          sales: data.sales || "",
          expedition: data.expedition || "Internal fleet",
          notes: "",
          line_qtys: initialQtys,
          line_notes: initialNotes,
        };
      });
    } catch (e) {
      const det = e.response?.data?.detail;
      setPoSummaryErr(typeof det === "object" ? det.message : (det || "Gagal memuat rincian PO"));
    } finally {
      setPoSummaryLoading(false);
    }
  }, []);

  // Load detail of a delivery
  const loadDeliveryDetail = useCallback(async (delivId) => {
    if (!delivId) return;
    setDetailLoading(true);
    setDetailErr("");
    try {
      const data = await api.getSalesDelivery(delivId);
      setDetailData(data);
    } catch (e) {
      const det = e.response?.data?.detail;
      setDetailErr(typeof det === "object" ? det.message : (det || "Gagal memuat rincian Pengiriman Pesanan"));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (viewMode === "list") {
      loadDeliveries();
    }
  }, [viewMode, loadDeliveries]);

  useEffect(() => {
    if (viewMode === "create") {
      loadActivePOs();
      if (selectedPoId) {
        loadPoSummary(selectedPoId);
      }
    }
  }, [viewMode, selectedPoId, loadActivePOs, loadPoSummary]);

  useEffect(() => {
    const qPo = searchParams.get("po_id");
    const qAct = searchParams.get("action");
    if (qAct === "create") {
      setViewMode("create");
      if (qPo) {
        setSelectedPoId(qPo);
      }
    }
  }, [searchParams]);

  // Handle PO selection from dropdown
  const handleSelectPO = (poId) => {
    setSelectedPoId(poId);
    setSearchParams(poId ? { action: "create", po_id: poId } : { action: "create" });
  };

  // Change shipped qty for a specific line item
  const handleLineQtyChange = (lineIdx, rawVal) => {
    const val = parseInt(rawVal, 10);
    const num = isNaN(val) ? 0 : Math.max(0, val);
    setFormData((prev) => ({
      ...prev,
      line_qtys: {
        ...prev.line_qtys,
        [lineIdx]: num,
      },
    }));
  };

  // Change notes for a specific line item
  const handleLineNoteChange = (lineIdx, val) => {
    setFormData((prev) => ({
      ...prev,
      line_notes: {
        ...prev.line_notes,
        [lineIdx]: val,
      },
    }));
  };

  // Fill all remaining quantities
  const handleFillAllRemaining = () => {
    if (!poSummary?.lines) return;
    const qtys = {};
    poSummary.lines.forEach((l) => {
      qtys[l.line_idx] = l.remaining_qty;
    });
    setFormData((prev) => ({ ...prev, line_qtys: qtys }));
  };

  // Clear all line quantities
  const handleClearAllQtys = () => {
    if (!poSummary?.lines) return;
    const qtys = {};
    poSummary.lines.forEach((l) => {
      qtys[l.line_idx] = 0;
    });
    setFormData((prev) => ({ ...prev, line_qtys: qtys }));
  };

  // Total Qty to ship in current form
  const totalShippingQty = useMemo(() => {
    if (!poSummary?.lines) return 0;
    return poSummary.lines.reduce((acc, l) => acc + (formData.line_qtys[l.line_idx] || 0), 0);
  }, [poSummary, formData.line_qtys]);

  // Validate if any line exceeds remaining
  const overshipmentErrors = useMemo(() => {
    if (!poSummary?.lines) return [];
    const errs = [];
    poSummary.lines.forEach((l) => {
      const q = formData.line_qtys[l.line_idx] || 0;
      if (q > l.remaining_qty) {
        errs.push(`Item '${l.name}': kuantitas kirim (${q}) melebihi sisa PO (${l.remaining_qty} ${l.unit})`);
      }
    });
    return errs;
  }, [poSummary, formData.line_qtys]);

  // Submit delivery
  const handleSubmitDelivery = async (e) => {
    e.preventDefault();
    setFormErr("");
    setSuccessMsg("");

    if (!poSummary) {
      setFormErr("Silakan pilih Pesanan Penjualan (PO) terlebih dahulu.");
      return;
    }

    if (totalShippingQty <= 0) {
      setFormErr("Kuantitas pengiriman sekarang harus lebih besar dari 0.");
      return;
    }

    if (overshipmentErrors.length > 0) {
      setFormErr(overshipmentErrors.join("; "));
      return;
    }

    // Build payload lines
    const lineItemsPayload = [];
    poSummary.lines.forEach((l) => {
      const q = formData.line_qtys[l.line_idx] || 0;
      if (q > 0) {
        lineItemsPayload.push({
          line_idx: l.line_idx,
          line_id: l.line_id,
          qty: q,
          notes: formData.line_notes[l.line_idx] || "",
        });
      }
    });

    const payload = {
      po_id: poSummary.po_id,
      delivery_date: formData.delivery_date,
      outlet: formData.outlet,
      recipient: formData.recipient,
      address: formData.address,
      delivery_zone: formData.delivery_zone,
      sales: formData.sales,
      expedition: formData.expedition,
      notes: formData.notes,
      line_items: lineItemsPayload,
    };
    const locationOverride = sjOverridePayload(sjLocationOverride);
    if (locationOverride) payload.delivery_location = locationOverride;

    setFormBusy(true);
    try {
      const res = await api.createSalesDelivery(payload);
      setSuccessMsg(res.message || "Pengiriman Pesanan berhasil dibuat");
      
      // Auto open detail for the newly created delivery
      const newDelivId = res.delivery?.id;
      if (newDelivId) {
        setSelectedDeliveryId(newDelivId);
        loadDeliveryDetail(newDelivId);
      }

      // Refresh list
      loadDeliveries();
      setViewMode("list");
      setSearchParams({});
    } catch (err) {
      const det = err.response?.data?.detail;
      setFormErr(typeof det === "object" ? det.message : (det || "Gagal membuat Pengiriman Pesanan"));
    } finally {
      setFormBusy(false);
    }
  };

  // Open PDF directly
  const handlePrintPdf = (deliveryId) => {
    const url = api.salesDeliveryPdfUrl(deliveryId);
    window.open(url, "_blank");
  };

  const toggleSelect = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const toggleSelectAll = () => {
    if (selectedIds.size === deliveries.length && deliveries.length > 0) setSelectedIds(new Set());
    else setSelectedIds(new Set(deliveries.map((d) => d.id)));
  };
  const filteredDeliveries = useMemo(
    () => (deliveries || []).filter((d) => deliveryDateInRange(d, dateFrom, dateTo)),
    [deliveries, dateFrom, dateTo]
  );
  const pagedDeliveries = useMemo(
    () => paginateRows(filteredDeliveries, listPage, listPageSize),
    [filteredDeliveries, listPage, listPageSize]
  );
  const isInteractiveTarget = (target) =>
    Boolean(target?.closest("input, button, a, select, textarea, label, [role='menu'], [role='menuitem']"));

  useEffect(() => {
    setListPage(1);
  }, [searchQuery, statusFilter, dateFrom, dateTo]);
  const formatDeleteError = (det) => {
    if (typeof det === "object" && det) {
      const blockers = det.blockers || [];
      if (blockers.length) {
        return `${det.message || "Tidak dapat menghapus."} ${blockers.map((b) => `${b.number || b.id}: ${b.reason}`).join(" ")}`.trim();
      }
      return det.message || JSON.stringify(det);
    }
    return det || "Gagal menghapus.";
  };
  const handleDelete = async (id) => {
    setDeleteLoading(true);
    setDeleteErr("");
    try {
      await api.deleteSalesDelivery(id);
      setDeleteConfirm(null);
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      loadDeliveries();
    } catch (e) {
      setDeleteErr(formatDeleteError(e.response?.data?.detail));
    } finally {
      setDeleteLoading(false);
    }
  };

  const handleBulkDelete = async () => {
    setDeleteLoading(true);
    setDeleteErr("");
    try {
      const ids = deleteConfirm?.ids || Array.from(selectedIds);
      await api.bulkDeleteSalesDeliveries(ids);
      setDeleteConfirm(null);
      setSelectedIds(new Set());
      loadDeliveries();
    } catch (e) {
      setDeleteErr(formatDeleteError(e.response?.data?.detail));
    } finally {
      setDeleteLoading(false);
    }
  };

  return (
    <div className="page-container penjualan-page penjualan-workspace sales-compact-workspace">
      {/* HEADER SECTION */}
      <div className="page-heading sw-page-header">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {viewMode === "create" ? (
            <button
              type="button"
              className="ghost sm"
              onClick={() => {
                setViewMode("list");
                setSearchParams({});
              }}
              data-testid="btn-back-to-delivery-list"
              style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 12px", borderRadius: 6, border: "1px solid #cbd5e1", background: "#f8fafc", cursor: "pointer" }}
            >
              <ArrowLeft size={16} /> Kembali
            </button>
          ) : (
            <div style={{ width: 40, height: 40, borderRadius: 10, background: "#fef3c7", color: "#d97706", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Truck size={22} />
            </div>
          )}
          <div>
            <h1 style={{ margin: 0, fontSize: "1.5rem", fontWeight: 700, color: "#0f172a" }}>
              {viewMode === "create" ? "Buat Pengiriman Pesanan (Surat Jalan)" : "Pengiriman Pesanan"}
            </h1>
            <p style={{ margin: "2px 0 0 0", fontSize: "0.85rem", color: "#64748b" }}>
              {viewMode === "create"
                ? "Proses dan buat Surat Jalan pengiriman dari Pesanan Penjualan (PO asal)."
                : "Daftar seluruh dokumen Surat Jalan / Pengiriman Pesanan Penjualan."}
            </p>
          </div>
        </div>

        {viewMode === "list" && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {canDelete && selectedIds.size > 0 && (
              <button
                type="button"
                data-testid="btn-hapus-terpilih"
                onClick={() => {
                  setDeleteErr("");
                  setDeleteConfirm({
                    ids: Array.from(selectedIds),
                    numbers: deliveries.filter((d) => selectedIds.has(d.id)).map((d) => d.sj_number),
                  });
                }}
                style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 14px", borderRadius: 8, background: "#fee2e2", color: "#b91c1c", border: "1px solid #fecaca", cursor: "pointer", fontWeight: 600 }}
              >
                <Trash2 size={16} /> Hapus Terpilih ({selectedIds.size})
              </button>
            )}
            {canCreate && (
          <button
            type="button"
            className="primary"
            onClick={() => {
              setViewMode("create");
              setSelectedPoId("");
              setSearchParams({ action: "create" });
            }}
            data-testid="btn-new-delivery-order"
            style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 16px", borderRadius: 8, background: "#0284c7", color: "#fff", border: "none", cursor: "pointer", fontWeight: 600 }}
          >
            <Plus size={16} /> + Buat Pengiriman Pesanan
          </button>
            )}
          </div>
        )}
      </div>

      {/* SUCCESS BANNER */}
      {successMsg && (
        <div
          className="alert-box success"
          data-testid="delivery-success-banner"
          style={{ background: "#ecfdf5", border: "1px solid #a7f3d0", color: "#047857", padding: "12px 16px", borderRadius: 8, marginBottom: 16, display: "flex", alignItems: "center", justifyContent: "space-between" }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <CheckCircle2 size={18} />
            <span>{successMsg}</span>
          </div>
          <button type="button" onClick={() => setSuccessMsg("")} style={{ background: "none", border: "none", cursor: "pointer", color: "#047857" }}>
            <X size={16} />
          </button>
        </div>
      )}

      {/* VIEW: CREATE DELIVERY ORDER */}
      {viewMode === "create" && (
        <form onSubmit={handleSubmitDelivery} className="sw-form-shell">
          <div className="sw-stepper">
            <span className={`sw-step${createStep === 1 ? " is-active" : ""}`}>1. Data Pengiriman</span>
            <span className={`sw-step${createStep === 2 ? " is-active" : ""}`}>2. Alokasi Qty dan Review</span>
          </div>
          <div className="sw-form-body">
          {formErr && (
            <div
              className="alert-box error"
              data-testid="form-error-banner"
              style={{ background: "#fef2f2", border: "1px solid #fecaca", color: "#b91c1c", padding: "12px 16px", borderRadius: 8, marginBottom: 16, display: "flex", alignItems: "center", gap: 10 }}
            >
              <AlertCircle size={18} />
              <span>{formErr}</span>
            </div>
          )}

          {createStep === 1 && (
          <section className="panel" style={{ background: "#fff", padding: 12, marginBottom: 12 }}>
            <header style={{ borderBottom: "1px solid #f1f5f9", paddingBottom: 12, marginBottom: 16 }}>
              <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#1e293b" }}>1. Data Pesanan Penjualan (PO Asal)</h3>
              <p style={{ margin: "4px 0 0 0", fontSize: "0.85rem", color: "#64748b" }}>
                Data diisi otomatis dari Pesanan Penjualan dan dapat disesuaikan untuk pengiriman ini.
              </p>
            </header>

            {poSummary && (
              <div className="sw-summary-strip" data-testid="delivery-readonly-summary">
                <div><span>Nomor SJ</span><strong>{poSummary.suggested_sj_number || "-"}</strong></div>
                <div><span>Customer</span><strong>{poSummary.customer || "-"}</strong></div>
                <div><span>PO</span><strong>{poSummary.po_number || "-"}</strong></div>
                <div><span>Sales</span><strong>{formData.sales || poSummary.sales || "-"}</strong></div>
              </div>
            )}
            <div className="sw-form-grid">
              {/* Select PO */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Nomor PO <span style={{ color: "#ef4444" }}>*</span>
                </label>
                {queryPoId ? (
                  <input
                    type="text"
                    readOnly
                    value={poSummary?.po_number ? `${poSummary.po_number}` : queryPoId}
                    data-testid="display-po-number"
                    style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", background: "#f8fafc", color: "#0f172a", fontWeight: 600, boxSizing: "border-box" }}
                  />
                ) : (
                  <select
                    value={selectedPoId}
                    onChange={(e) => handleSelectPO(e.target.value)}
                    disabled={formBusy}
                    data-testid="select-po-for-delivery"
                    style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", background: "#fff" }}
                    required
                  >
                    <option value="">-- Pilih Pesanan Penjualan --</option>
                    {availablePOs.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.po_number} - {p.customer} {p.outlet ? `(${p.outlet})` : ""} [Sisa: {p.delivery_summary?.total_remaining ?? p.total_quantity} pcs]
                      </option>
                    ))}
                  </select>
                )}
                <p style={{ margin: "4px 0 0 0", fontSize: "0.75rem", color: "#64748b" }}>
                  Terkunci berdasarkan PO dan urutan pengiriman.
                </p>
              </div>

              {/* SJ Number Read-Only Indicator */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Nomor SJ (Backend-Authoritative)
                </label>
                <input
                  type="text"
                  readOnly
                  value={poSummary?.suggested_sj_number ? `${poSummary.suggested_sj_number} (Pengiriman #${poSummary.next_shipment_index})` : "(Pilih PO untuk melihat No SJ)"}
                  data-testid="display-suggested-sj"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #e2e8f0", borderRadius: 6, fontSize: "0.95rem", background: "#f1f5f9", color: "#0369a1", fontWeight: 600, boxSizing: "border-box" }}
                />
                <p style={{ margin: "4px 0 0 0", fontSize: "0.75rem", color: "#64748b" }}>
                  Terkunci berdasarkan PO dan urutan pengiriman.
                </p>
              </div>

              {/* Tanggal Pengiriman */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Tanggal Pengiriman <span style={{ color: "#ef4444" }}>*</span>
                </label>
                <input
                  type="date"
                  value={formData.delivery_date}
                  onChange={(e) => setFormData((p) => ({ ...p, delivery_date: e.target.value }))}
                  data-testid="input-delivery-date"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                  required
                />
              </div>

              {/* Customer Read-Only */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Customer
                </label>
                <input
                  type="text"
                  readOnly
                  value={poSummary?.customer || "-"}
                  data-testid="display-customer"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #e2e8f0", borderRadius: 6, fontSize: "0.95rem", background: "#f8fafc", color: "#334155", boxSizing: "border-box" }}
                />
              </div>

              {/* Outlet */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Outlet / Cabang
                </label>
                <input
                  type="text"
                  value={formData.outlet}
                  onChange={(e) => setFormData((p) => ({ ...p, outlet: e.target.value }))}
                  placeholder="Nama cabang / outlet"
                  data-testid="input-delivery-outlet"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              {/* Penerima PIC */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Penerima (PIC)
                </label>
                <input
                  type="text"
                  value={formData.recipient}
                  onChange={(e) => setFormData((p) => ({ ...p, recipient: e.target.value }))}
                  placeholder="Nama penerima di lokasi"
                  data-testid="input-delivery-recipient"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              {/* Sales */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Sales Representative
                </label>
                <input
                  type="text"
                  value={formData.sales}
                  onChange={(e) => setFormData((p) => ({ ...p, sales: e.target.value }))}
                  placeholder="Nama sales"
                  data-testid="input-delivery-sales"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              {/* Ekspedisi */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Ekspedisi / Armada
                </label>
                <input
                  type="text"
                  value={formData.expedition}
                  onChange={(e) => setFormData((p) => ({ ...p, expedition: e.target.value }))}
                  placeholder="Internal fleet / nama kurir"
                  data-testid="input-delivery-expedition"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              {/* Zona Kirim */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Zona Kirim
                </label>
                <input
                  type="text"
                  value={formData.delivery_zone}
                  onChange={(e) => setFormData((p) => ({ ...p, delivery_zone: e.target.value }))}
                  placeholder="mis. Jakarta Barat"
                  data-testid="input-delivery-zone"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>
            </div>

            {/* Alamat Lengkap */}
            <div style={{ marginTop: 16 }}>
              <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                Alamat Lengkap Pengiriman
              </label>
              <textarea
                value={formData.address}
                onChange={(e) => setFormData((p) => ({ ...p, address: e.target.value }))}
                placeholder="Alamat tujuan pengiriman..."
                rows={2}
                data-testid="input-delivery-address"
                style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
              />
            </div>

            {poSummary && (
              <SjDeliveryLocationPicker
                poLocation={poSummary.delivery_location || null}
                override={sjLocationOverride}
                onOverrideChange={setSjLocationOverride}
                disabled={formBusy}
              />
            )}

            {/* Catatan */}
            <div style={{ marginTop: 12 }}>
              <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                Catatan Pengiriman
              </label>
              <input
                type="text"
                value={formData.notes}
                onChange={(e) => setFormData((p) => ({ ...p, notes: e.target.value }))}
                placeholder="Instruksi pengiriman, kontak penerima, nomor resi dll."
                data-testid="input-delivery-notes"
                style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
              />
            </div>
          </section>
          )}

          {createStep === 2 && (
          <section className="panel" style={{ background: "#fff", padding: 12, marginBottom: 12 }}>
            <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #f1f5f9", paddingBottom: 12, marginBottom: 16, flexWrap: "wrap", gap: 8 }}>
              <div>
                <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#1e293b" }}>2. Alokasi Kuantitas Pengiriman</h3>
                <p style={{ margin: "2px 0 0 0", fontSize: "0.85rem", color: "#64748b" }}>
                  Tentukan jumlah qty yang dikirimkan pada Surat Jalan ini. Sisa kuantitas PO akan tetap tersedia untuk pengiriman berikutnya.
                </p>
              </div>
              {poSummary?.lines?.length > 0 && (
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    type="button"
                    onClick={handleFillAllRemaining}
                    data-testid="btn-fill-all-remaining"
                    style={{ padding: "6px 12px", background: "#e0f2fe", color: "#0369a1", border: "1px solid #bae6fd", borderRadius: 6, fontSize: "0.8rem", fontWeight: 600, cursor: "pointer" }}
                  >
                    Kirim Semua Sisa
                  </button>
                  <button
                    type="button"
                    onClick={handleClearAllQtys}
                    data-testid="btn-clear-all-qtys"
                    style={{ padding: "6px 12px", background: "#f8fafc", color: "#475569", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.8rem", fontWeight: 600, cursor: "pointer" }}
                  >
                    Reset 0
                  </button>
                </div>
              )}
            </header>

            {poSummaryLoading ? (
              <div style={{ padding: 32, textAlign: "center", color: "#64748b" }}>
                Memuat rincian PO...
              </div>
            ) : !poSummary ? (
              <div style={{ padding: 40, textAlign: "center", color: "#64748b" }}>
                <Package size={36} style={{ opacity: 0.4, marginBottom: 8 }} />
                <p style={{ margin: 0, fontWeight: 500 }}>Silakan pilih Nomor PO pada bagian 1 di atas.</p>
              </div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.9rem" }}>
                  <thead>
                    <tr style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
                      <th style={{ padding: "10px 8px", width: 40 }}>#</th>
                      <th style={{ padding: "10px 8px", minWidth: 220 }}>Barang / Jasa</th>
                      <th style={{ padding: "10px 8px", width: 100 }}>Proses</th>
                      <th style={{ padding: "10px 8px", width: 100, textAlign: "right" }}>Qty PO</th>
                      <th style={{ padding: "10px 8px", width: 110, textAlign: "right" }}>Terkirim</th>
                      <th style={{ padding: "10px 8px", width: 110, textAlign: "right" }}>Sisa PO</th>
                      <th style={{ padding: "10px 8px", width: 140 }}>Qty Kirim Sekarang <span style={{ color: "#ef4444" }}>*</span></th>
                      <th style={{ padding: "10px 8px", minWidth: 160 }}>Catatan Baris</th>
                    </tr>
                  </thead>
                  <tbody>
                    {poSummary.lines.map((line) => {
                      const qtyVal = formData.line_qtys[line.line_idx] ?? 0;
                      const isOver = qtyVal > line.remaining_qty;
                      const isZeroRemaining = line.remaining_qty <= 0;

                      return (
                        <tr key={line.line_idx} data-testid={`delivery-line-row-${line.line_idx}`} style={{ borderBottom: "1px solid #f1f5f9", background: isOver ? "#fef2f2" : "#fff" }}>
                          <td style={{ padding: "10px 8px", color: "#64748b", fontWeight: 600 }}>
                            {line.line_idx + 1}
                          </td>
                          <td style={{ padding: "10px 8px" }}>
                            <div style={{ fontWeight: 600, color: "#1e293b" }}>{line.name}</div>
                            {line.color && <div style={{ fontSize: "0.75rem", color: "#64748b" }}>Warna: {line.color}</div>}
                          </td>
                          <td style={{ padding: "10px 8px" }}>
                            <span style={{ fontSize: "0.75rem", padding: "2px 6px", borderRadius: 4, background: line.production_process === "non_print" ? "#fef3c7" : "#e0f2fe", color: line.production_process === "non_print" ? "#92400e" : "#0369a1", fontWeight: 600 }}>
                              {line.production_process === "non_print" ? "Non-Cetak" : "Cetak"}
                            </span>
                          </td>
                          <td style={{ padding: "10px 8px", textAlign: "right", color: "#475569" }}>
                            {line.ordered_qty.toLocaleString("id-ID")} {line.unit}
                          </td>
                          <td style={{ padding: "10px 8px", textAlign: "right", color: "#64748b" }}>
                            {line.shipped_qty.toLocaleString("id-ID")} {line.unit}
                          </td>
                          <td style={{ padding: "10px 8px", textAlign: "right", fontWeight: 600, color: line.remaining_qty > 0 ? "#0284c7" : "#94a3b8" }}>
                            {line.remaining_qty.toLocaleString("id-ID")} {line.unit}
                          </td>
                          <td style={{ padding: "10px 8px" }}>
                            <input
                              type="number"
                              min="0"
                              max={line.remaining_qty}
                              disabled={isZeroRemaining || formBusy}
                              value={qtyVal}
                              onChange={(e) => handleLineQtyChange(line.line_idx, e.target.value)}
                              data-testid={`input-ship-qty-${line.line_idx}`}
                              style={{
                                width: "100%",
                                padding: "6px 8px",
                                border: "1px solid",
                                borderColor: isOver ? "#ef4444" : "#cbd5e1",
                                borderRadius: 6,
                                boxSizing: "border-box",
                                fontWeight: 600,
                                textAlign: "right",
                                background: isZeroRemaining ? "#f1f5f9" : "#fff",
                              }}
                            />
                            {isOver && (
                              <span style={{ display: "block", fontSize: "0.7rem", color: "#ef4444", marginTop: 2 }}>
                                Max: {line.remaining_qty}
                              </span>
                            )}
                          </td>
                          <td style={{ padding: "10px 8px" }}>
                            <input
                              type="text"
                              placeholder="Catatan item..."
                              disabled={isZeroRemaining || formBusy}
                              value={formData.line_notes[line.line_idx] || ""}
                              onChange={(e) => handleLineNoteChange(line.line_idx, e.target.value)}
                              data-testid={`input-ship-note-${line.line_idx}`}
                              style={{ width: "100%", padding: "6px 8px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box", fontSize: "0.85rem" }}
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {/* Total Summary Footer */}
            {poSummary && (
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 16, padding: "12px 16px", background: "#f8fafc", borderRadius: 8, flexWrap: "wrap", gap: 12 }}>
                <div style={{ fontSize: "0.85rem", color: "#475569" }}>
                  Total Sisa PO: <strong>{poSummary.total_remaining.toLocaleString("id-ID")} pcs</strong>
                </div>
                <div style={{ fontSize: "1rem", color: totalShippingQty > 0 ? "#0369a1" : "#475569", fontWeight: 700 }}>
                  Total Kuantitas Surat Jalan Ini: <span data-testid="total-delivery-qty">{totalShippingQty.toLocaleString("id-ID")}</span> pcs
                </div>
              </div>
            )}
          </section>
          )}
          </div>

          {/* Form Actions */}
          <div className="sw-form-foot">
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setViewMode("list");
                setSearchParams({});
              }}
              disabled={formBusy}
              data-testid="btn-cancel-delivery"
            >
              Batal
            </button>
            {createStep > 1 && (
              <button type="button" className="ghost" onClick={() => setCreateStep(1)} disabled={formBusy}>
                Kembali
              </button>
            )}
            {createStep === 1 ? (
              <button type="button" className="primary" onClick={() => setCreateStep(2)}>
                Berikutnya
              </button>
            ) : (
            <button
              type="submit"
              className="primary"
              disabled={formBusy || !poSummary || totalShippingQty <= 0 || overshipmentErrors.length > 0}
              data-testid="btn-submit-delivery-order"
            >
              {formBusy ? "Memproses..." : "Buat & Terbitkan Surat Jalan"}
            </button>
            )}
          </div>
        </form>
      )}

      {/* VIEW: LIST DELIVERIES */}
      {viewMode === "list" && (
        <section className="sw-list-panel">
          {listErr && (
            <div className="alert-box error" data-testid="list-error-banner" style={{ background: "#fef2f2", border: "1px solid #fecaca", color: "#b91c1c", padding: "8px 12px", display: "flex", alignItems: "center", gap: 10 }}>
              <AlertCircle size={16} />
              <span>{listErr}</span>
            </div>
          )}
          <div className="sw-toolbar">
            <div className="sw-search">
              <Search size={16} className="sw-search-icon" />
              <input
                type="text"
                placeholder="Cari No SJ / No PO / Customer / Outlet / Sales..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                data-testid="search-deliveries-input"
              />
            </div>
            <div className="sw-chip-group">
              {[
                { id: "all", label: "Semua Status" },
                { id: "SEBAGIAN", label: "Sebagian" },
                { id: "SELESAI", label: "Selesai" },
              ].map((tab) => (
                <button key={tab.id} type="button" className={`sw-chip${statusFilter === tab.id ? " is-active" : ""}`} onClick={() => setStatusFilter(tab.id)} data-testid={`filter-status-${tab.id}`}>
                  {tab.label}
                </button>
              ))}
            </div>
            <input type="date" aria-label="Dari tanggal" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} style={{ height: 40 }} />
            <input type="date" aria-label="Sampai tanggal" value={dateTo} onChange={(e) => setDateTo(e.target.value)} style={{ height: 40 }} />
          </div>
          <div className="sw-table-scroll sw-desktop-table">
            <table className="sw-table">
              <thead>
                <tr>
                  {canDelete && (
                    <th style={{ width: 40 }}>
                      <input type="checkbox" data-testid="select-all-deliveries" checked={deliveries.length > 0 && selectedIds.size === deliveries.length} onChange={toggleSelectAll} aria-label="Pilih seluruh hasil, bukan hanya halaman ini" title="Pilih seluruh hasil" />
                    </th>
                  )}
                  <th>Surat Jalan</th>
                  <th>PO / Customer</th>
                  <th className="num">Total Qty</th>
                  <th>Status</th>
                  <th className="sw-hide-tablet">Dibuat Oleh</th>
                  <th className="sw-actions">Aksi</th>
                </tr>
              </thead>
              <tbody>
                {listLoading ? (
                  <tr><td colSpan={canDelete ? 7 : 6}>Memuat daftar Pengiriman Pesanan...</td></tr>
                ) : filteredDeliveries.length === 0 ? (
                  <tr>
                    <td colSpan={canDelete ? 7 : 6} data-testid="empty-deliveries">
                      <div className="sw-empty">
                        <Truck size={32} style={{ opacity: 0.4 }} />
                        <p style={{ margin: 0, fontWeight: 500 }}>Belum ada data Pengiriman Pesanan yang sesuai.</p>
                      </div>
                    </td>
                  </tr>
                ) : (
                  pagedDeliveries.rows.map((d) => (
                    <tr
                      key={d.id}
                      data-testid={`delivery-row-${d.id}`}
                      className={`is-clickable${selectedDeliveryId === d.id ? " is-active" : ""}`}
                      onClick={(e) => {
                        if (isInteractiveTarget(e.target)) return;
                        setDrawerTab("ringkasan");
                        setSelectedDeliveryId(d.id);
                        loadDeliveryDetail(d.id);
                      }}
                    >
                      {canDelete && (
                        <td>
                          <input type="checkbox" data-testid={`delivery-checkbox-${d.id}`} checked={selectedIds.has(d.id)} onChange={() => toggleSelect(d.id)} aria-label={`Pilih ${d.sj_number}`} />
                        </td>
                      )}
                      <td>
                        <button type="button" className="sw-primary" data-testid={`sj-number-${d.sj_number}`} onClick={() => { setDrawerTab("ringkasan"); setSelectedDeliveryId(d.id); loadDeliveryDetail(d.id); }} style={{ background: "none", border: 0, padding: 0, color: "#0369a1", fontWeight: 700, cursor: "pointer" }}>
                          {d.sj_number}
                        </button>
                        <div className="sw-secondary">{formatDisplayDate(d.delivery_date) || "-"}</div>
                      </td>
                      <td>
                        <Link to={`/po/${d.po_id}`} style={{ color: "#0284c7", textDecoration: "none" }} data-testid={`po-link-${d.po_number}`}>{d.po_number}</Link>
                        <div className="sw-secondary">{d.customer || "-"}{d.outlet ? ` · ${d.outlet}` : ""}</div>
                      </td>
                      <td className="num">{Number(d.total_qty || 0).toLocaleString("id-ID")}</td>
                      <td>
                        <span className="pj-status-badge" data-testid={`delivery-status-${d.id}`} style={{ display: "inline-block", padding: "2px 8px", background: String(d.status).toUpperCase() === "SELESAI" ? "#dcfce7" : "#e0f2fe", color: String(d.status).toUpperCase() === "SELESAI" ? "#166534" : "#075985", borderRadius: 12, fontSize: "0.75rem", fontWeight: 700 }}>
                          {String(d.status).toUpperCase() === "SELESAI" ? "SELESAI" : "SEBAGIAN"}
                        </span>
                      </td>
                      <td className="sw-hide-tablet">{d.created_by_name || d.created_by || "-"}</td>
                      <td className="sw-actions" onClick={(e) => e.stopPropagation()}>
                        <div style={{ display: "inline-flex", gap: 6, justifyContent: "flex-end" }}>
                          {canPrint && (
                            <button type="button" className="sw-icon-btn" onClick={() => handlePrintPdf(d.id)} data-testid={`btn-print-delivery-${d.id}`} title="Cetak PDF Surat Jalan" aria-label="Cetak PDF Surat Jalan">
                              <Printer size={14} />
                            </button>
                          )}
                          <SalesRowMenu
                            testId={`delivery-row-menu-${d.id}`}
                            items={[
                              {
                                id: "view",
                                label: "Lihat",
                                testId: `btn-view-delivery-${d.id}`,
                                onClick: () => {
                                  setDrawerTab("ringkasan");
                                  setSelectedDeliveryId(d.id);
                                  loadDeliveryDetail(d.id);
                                },
                              },
                              canDelete && {
                                id: "delete",
                                label: "Hapus",
                                danger: true,
                                testId: `btn-delete-delivery-${d.id}`,
                                onClick: () => {
                                  setDeleteErr("");
                                  setDeleteConfirm({ id: d.id, number: d.sj_number, numbers: [d.sj_number] });
                                },
                              },
                            ]}
                          />
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <div className="sw-mobile-cards">
            {pagedDeliveries.rows.map((d) => (
              <button key={d.id} type="button" className="sw-card" onClick={() => { setSelectedDeliveryId(d.id); loadDeliveryDetail(d.id); }}>
                <strong>{d.sj_number}</strong>
                <div className="sw-secondary">{d.po_number} · {d.customer}</div>
              </button>
            ))}
          </div>
          <SalesPaginationBar
            page={pagedDeliveries.page}
            pageSize={pagedDeliveries.pageSize}
            pageCount={pagedDeliveries.pageCount}
            total={pagedDeliveries.total}
            start={pagedDeliveries.start}
            end={pagedDeliveries.end}
            onPageChange={setListPage}
            onPageSizeChange={(size) => { setListPageSize(size); setListPage(1); }}
          />
        </section>
      )}

      <SalesWorkspaceDrawer
        open={Boolean(selectedDeliveryId)}
        title={detailData?.delivery?.sj_number ? `Detail Surat Jalan: ${detailData.delivery.sj_number}` : "Detail Surat Jalan"}
        subtitle={detailData?.delivery ? `Pengiriman #${detailData.delivery.shipment_index} dari PO ${detailData.delivery.po_number}` : ""}
        testId="delivery-detail-modal"
        tabs={[
          { id: "ringkasan", label: "Ringkasan" },
          { id: "barang", label: "Barang Dikirim" },
          { id: "loading", label: "Loading" },
          { id: "bukti", label: "Bukti Pengiriman" },
          { id: "riwayat", label: "Riwayat" },
        ]}
        activeTab={drawerTab}
        onTabChange={setDrawerTab}
        onClose={() => { setSelectedDeliveryId(null); setDetailData(null); }}
        footer={(
          <>
            {canPrint && detailData?.delivery?.id && (
              <button type="button" className="primary" onClick={() => handlePrintPdf(detailData.delivery.id)} data-testid="btn-print-modal-pdf">
                <Printer size={16} /> Cetak PDF
              </button>
            )}
            <button type="button" className="ghost" onClick={() => { setSelectedDeliveryId(null); setDetailData(null); }} data-testid="btn-close-modal-bottom">Tutup</button>
          </>
        )}
      >
        {detailLoading && <p>Memuat rincian...</p>}
        {detailErr && <p>{detailErr}</p>}
        {detailData && drawerTab === "ringkasan" && (
          <>
            {detailData.po_summary && (
              <div data-testid="delivery-po-progress-summary" className="sw-summary-strip">
                <div><span>TOTAL QTY PO</span><strong data-testid="detail-total-po-qty">{Number(detailData.po_summary.total_ordered || 0).toLocaleString("id-ID")} pcs</strong></div>
                <div><span>SUDAH DIBUAT SURAT JALAN</span><strong data-testid="detail-cumulative-sj-qty">{Number(detailData.po_summary.total_shipped || 0).toLocaleString("id-ID")} pcs</strong></div>
                <div><span>SISA</span><strong data-testid="detail-remaining-po-qty">{Number(detailData.po_summary.total_remaining || 0).toLocaleString("id-ID")} pcs</strong></div>
                <div>
                  <span>STATUS</span>
                  <strong data-testid="detail-delivery-status">{(detailData.po_summary.total_remaining === 0 || detailData.po_summary.delivery_status === "SELESAI") ? "SELESAI" : "SEBAGIAN"}</strong>
                </div>
              </div>
            )}
            <p>Customer: <strong>{detailData.delivery?.customer || "-"}</strong></p>
            <p>Outlet: <strong>{detailData.delivery?.outlet || "-"}</strong></p>
            <p>Alamat: <strong>{detailData.delivery?.address || "-"}</strong></p>
          </>
        )}
        {detailData && drawerTab === "barang" && (
          <table className="sw-table">
            <thead>
              <tr><th>#</th><th>Barang / Jasa</th><th className="num">Qty Kirim</th></tr>
            </thead>
            <tbody>
              {(detailData.delivery?.line_items || []).map((li, idx) => (
                <tr key={idx}>
                  <td>{idx + 1}</td>
                  <td>{li.name}</td>
                  <td className="num">{Number(li.qty || li.quantity || 0).toLocaleString("id-ID")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {detailData && drawerTab === "loading" && (
          <p>{detailData.delivery?.loading_notes || detailData.delivery?.armada || "Tidak ada data loading terpisah pada dokumen ini."}</p>
        )}
        {detailData && drawerTab === "bukti" && (
          <p>{detailData.delivery?.notes || "Bukti pengiriman dapat dilihat dari PDF Surat Jalan bila tersedia."}</p>
        )}
        {detailData && drawerTab === "riwayat" && (
          <div>
            <h4>Riwayat Pengiriman untuk PO {detailData.delivery?.po_number}:</h4>
            {(detailData.all_po_deliveries || []).map((shipment) => (
              <div key={shipment.id}>Surat Jalan <strong>{shipment.sj_number}</strong> (Kirim #{shipment.shipment_index}) - {formatDisplayDate(shipment.delivery_date)}</div>
            ))}
          </div>
        )}
        <button type="button" className="sr-only" data-testid="btn-close-delivery-modal" onClick={() => { setSelectedDeliveryId(null); setDetailData(null); }}>Tutup</button>
      </SalesWorkspaceDrawer>

      {deleteConfirm && (
        <div className="modal-overlay" data-testid="delete-confirm-modal" style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 80 }}>
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 460 }}>
            <h3 style={{ margin: "0 0 8px" }}>Hapus Surat Jalan?</h3>
            <p style={{ margin: "0 0 12px", color: "#64748b", fontSize: "0.9rem" }}>Dokumen berikut akan dihapus:</p>
            <ul data-testid="delete-confirm-numbers" style={{ margin: "0 0 16px", paddingLeft: 18, fontWeight: 700, color: "#0369a1" }}>
              {(deleteConfirm.numbers || [deleteConfirm.number]).filter(Boolean).map((n) => <li key={n}>{n}</li>)}
            </ul>
            {deleteErr && <div data-testid="delete-error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "8px 12px", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>{deleteErr}</div>}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" onClick={() => { setDeleteConfirm(null); setDeleteErr(""); }} disabled={deleteLoading} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff" }}>Batal</button>
              <button
                type="button"
                data-testid="btn-confirm-delete"
                onClick={() => (deleteConfirm.ids ? handleBulkDelete() : handleDelete(deleteConfirm.id))}
                disabled={deleteLoading}
                style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#dc2626", color: "#fff", fontWeight: 600 }}
              >
                {deleteLoading ? "Menghapus..." : "Hapus"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
