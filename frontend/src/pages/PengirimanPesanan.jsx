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
      return;
    }
    setPoSummaryLoading(true);
    setPoSummaryErr("");
    setFormErr("");
    try {
      const data = await api.getPoDeliverySummary(poId);
      setPoSummary(data);

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
    <div className="page-container penjualan-page penjualan-workspace" style={{ padding: 24, maxWidth: 1200, margin: "0 auto" }}>
      {/* HEADER SECTION */}
      <div className="page-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20, flexWrap: "wrap", gap: 12 }}>
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
        <form onSubmit={handleSubmitDelivery}>
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

          {/* Section 1: PO Selection & Information */}
          <section className="panel" style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, padding: 20, marginBottom: 20, boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
            <header style={{ borderBottom: "1px solid #f1f5f9", paddingBottom: 12, marginBottom: 16 }}>
              <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#1e293b" }}>1. Data Pesanan Penjualan (PO Asal)</h3>
              <p style={{ margin: "4px 0 0 0", fontSize: "0.85rem", color: "#64748b" }}>
                Data diisi otomatis dari Pesanan Penjualan dan dapat disesuaikan untuk pengiriman ini.
              </p>
            </header>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 16 }}>
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

          {/* Section 2: Line Items & Quantity Allocation */}
          <section className="panel" style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, padding: 20, marginBottom: 20, boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
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

          {/* Form Actions */}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 12, marginTop: 24 }}>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setViewMode("list");
                setSearchParams({});
              }}
              disabled={formBusy}
              data-testid="btn-cancel-delivery"
              style={{ padding: "10px 20px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer", fontWeight: 600 }}
            >
              Batal
            </button>
            <button
              type="submit"
              className="primary"
              disabled={formBusy || !poSummary || totalShippingQty <= 0 || overshipmentErrors.length > 0}
              data-testid="btn-submit-delivery-order"
              style={{
                padding: "10px 24px",
                borderRadius: 8,
                background: (!poSummary || totalShippingQty <= 0 || overshipmentErrors.length > 0 || formBusy) ? "#94a3b8" : "#0284c7",
                color: "#fff",
                border: "none",
                cursor: (!poSummary || totalShippingQty <= 0 || overshipmentErrors.length > 0 || formBusy) ? "not-allowed" : "pointer",
                fontWeight: 600,
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              {formBusy ? "Memproses..." : "Buat & Terbitkan Surat Jalan"}
            </button>
          </div>
        </form>
      )}

      {/* VIEW: LIST DELIVERIES */}
      {viewMode === "list" && (
        <section className="panel" style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, padding: 20, boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
          {listErr && (
            <div
              className="alert-box error"
              data-testid="list-error-banner"
              style={{ background: "#fef2f2", border: "1px solid #fecaca", color: "#b91c1c", padding: "10px 14px", borderRadius: 8, marginBottom: 16, display: "flex", alignItems: "center", gap: 10 }}
            >
              <AlertCircle size={16} />
              <span>{listErr}</span>
            </div>
          )}

          {/* Filter Bar */}
          <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <div style={{ position: "relative", minWidth: 280, flex: "1 1 300px" }}>
              <Search size={16} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "#94a3b8" }} />
              <input
                type="text"
                placeholder="Cari No SJ / No PO / Customer / Outlet / Sales..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                data-testid="search-deliveries-input"
                style={{ width: "100%", padding: "8px 12px 8px 34px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.9rem", boxSizing: "border-box" }}
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery("")}
                  style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: "#94a3b8" }}
                >
                  <X size={14} />
                </button>
              )}
            </div>

            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {[
                { id: "all", label: "Semua Status" },
                { id: "SEBAGIAN", label: "Sebagian" },
                { id: "SELESAI", label: "Selesai" },
              ].map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setStatusFilter(tab.id)}
                  data-testid={`filter-status-${tab.id}`}
                  style={{
                    padding: "6px 14px",
                    borderRadius: 6,
                    border: "1px solid",
                    borderColor: statusFilter === tab.id ? "#0284c7" : "#e2e8f0",
                    background: statusFilter === tab.id ? "#e0f2fe" : "#f8fafc",
                    color: statusFilter === tab.id ? "#0369a1" : "#475569",
                    fontWeight: statusFilter === tab.id ? 600 : 500,
                    cursor: "pointer",
                    fontSize: "0.85rem",
                  }}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

          {/* Deliveries Table */}
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.9rem" }}>
              <thead>
                <tr style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
                  {canDelete && (
                    <th style={{ padding: "10px 8px", width: 36 }}>
                      <input type="checkbox" data-testid="select-all-deliveries" checked={deliveries.length > 0 && selectedIds.size === deliveries.length} onChange={toggleSelectAll} aria-label="Pilih semua baris tampilan" />
                    </th>
                  )}
                  <th style={{ padding: "10px 12px" }}>No SJ</th>
                  <th style={{ padding: "10px 12px" }}>No PO</th>
                  <th style={{ padding: "10px 12px" }}>Tanggal Kirim</th>
                  <th style={{ padding: "10px 12px" }}>Customer</th>
                  <th style={{ padding: "10px 12px" }}>Outlet</th>
                  <th style={{ padding: "10px 12px", textAlign: "right" }}>Total Qty</th>
                  <th style={{ padding: "10px 12px" }}>Status</th>
                  <th style={{ padding: "10px 12px" }}>Dibuat Oleh</th>
                  <th style={{ padding: "10px 12px", textAlign: "center" }}>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {listLoading ? (
                  <tr>
                    <td colSpan={canDelete ? 10 : 9} style={{ padding: 32, textAlign: "center", color: "#64748b" }}>
                      Memuat daftar Pengiriman Pesanan...
                    </td>
                  </tr>
                ) : deliveries.length === 0 ? (
                  <tr>
                    <td colSpan={canDelete ? 10 : 9} style={{ padding: 40, textAlign: "center", color: "#64748b" }} data-testid="empty-deliveries">
                      <Truck size={32} style={{ opacity: 0.4, marginBottom: 8 }} />
                      <p style={{ margin: 0, fontWeight: 500 }}>Belum ada data Pengiriman Pesanan yang sesuai.</p>
                      {canCreate && (
                        <button
                          type="button"
                          onClick={() => {
                            setViewMode("create");
                            setSelectedPoId("");
                          }}
                          style={{ marginTop: 12, padding: "6px 14px", background: "#0284c7", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontWeight: 600, fontSize: "0.85rem" }}
                        >
                          + Buat Pengiriman Pertama
                        </button>
                      )}
                    </td>
                  </tr>
                ) : (
                  deliveries.map((d) => (
                    <tr key={d.id} data-testid={`delivery-row-${d.id}`} style={{ borderBottom: "1px solid #f1f5f9" }}>
                      {canDelete && (
                        <td style={{ padding: "10px 8px" }}>
                          <input type="checkbox" data-testid={`delivery-checkbox-${d.id}`} checked={selectedIds.has(d.id)} onChange={() => toggleSelect(d.id)} aria-label={`Pilih ${d.sj_number}`} />
                        </td>
                      )}
                      <td style={{ padding: "10px 12px", fontWeight: 700, color: "#0369a1" }}>
                        <span data-testid={`sj-number-${d.sj_number}`}>{d.sj_number}</span>
                      </td>
                      <td style={{ padding: "10px 12px", fontWeight: 500 }}>
                        <Link to={`/po/${d.po_id}`} style={{ color: "#0284c7", textDecoration: "none" }} data-testid={`po-link-${d.po_number}`}>
                          {d.po_number}
                        </Link>
                      </td>
                      <td style={{ padding: "10px 12px", color: "#475569" }}>
                        {formatDisplayDate(d.delivery_date) || "-"}
                      </td>
                      <td style={{ padding: "10px 12px", fontWeight: 500, color: "#1e293b" }}>
                        {d.customer || "-"}
                      </td>
                      <td style={{ padding: "10px 12px", color: "#475569" }}>
                        {d.outlet || "-"}
                      </td>
                      <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 600, color: "#0f172a" }}>
                        {Number(d.total_qty || 0).toLocaleString("id-ID")} pcs
                      </td>
                      <td style={{ padding: "10px 12px" }}>
                        <span
                          className="pj-status-badge"
                          data-testid={`delivery-status-${d.id}`}
                          style={{
                            display: "inline-block",
                            padding: "2px 8px",
                            background: String(d.status).toUpperCase() === "SELESAI" ? "#dcfce7" : "#e0f2fe",
                            color: String(d.status).toUpperCase() === "SELESAI" ? "#166534" : "#075985",
                            borderRadius: 12,
                            fontSize: "0.75rem",
                            fontWeight: 700,
                            border: String(d.status).toUpperCase() === "SELESAI" ? "1.5px solid #86efac" : "1.5px solid #7dd3fc",
                          }}
                        >
                          {String(d.status).toUpperCase() === "SELESAI" ? "SELESAI" : "SEBAGIAN"}
                        </span>
                      </td>
                      <td style={{ padding: "10px 12px", color: "#64748b", fontSize: "0.85rem" }}>
                        {d.created_by_name || d.created_by || "-"}
                      </td>
                      <td style={{ padding: "10px 12px", textAlign: "center" }}>
                        <div style={{ display: "flex", justifyContent: "center", gap: 6 }}>
                          <button
                            type="button"
                            onClick={() => {
                              setSelectedDeliveryId(d.id);
                              loadDeliveryDetail(d.id);
                            }}
                            data-testid={`btn-view-delivery-${d.id}`}
                            style={{ padding: "4px 10px", background: "#f1f5f9", color: "#334155", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.8rem", fontWeight: 600, cursor: "pointer" }}
                          >
                            Lihat
                          </button>
                          {canPrint && (
                            <button
                              type="button"
                              onClick={() => handlePrintPdf(d.id)}
                              data-testid={`btn-print-delivery-${d.id}`}
                              title="Cetak PDF Surat Jalan"
                              style={{ padding: "4px 8px", background: "#f8fafc", color: "#0284c7", border: "1px solid #cbd5e1", borderRadius: 6, cursor: "pointer", display: "flex", alignItems: "center", gap: 4, fontSize: "0.8rem", fontWeight: 600 }}
                            >
                              <Printer size={14} /> PDF
                            </button>
                          )}
                          {canDelete && (
                            <button
                              type="button"
                              data-testid={`btn-delete-delivery-${d.id}`}
                              onClick={() => {
                                setDeleteErr("");
                                setDeleteConfirm({
                                  id: d.id,
                                  number: d.sj_number,
                                  numbers: [d.sj_number],
                                });
                              }}
                              style={{
                                padding: "4px 10px",
                                fontSize: "0.8rem",
                                borderRadius: 6,
                                border: "1px solid #fecaca",
                                background: "#fef2f2",
                                color: "#b91c1c",
                                cursor: "pointer",
                                fontWeight: 600,
                              }}
                            >
                              Hapus
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* DETAIL MODAL */}
      {selectedDeliveryId && detailData && (
        <div
          className="modal-overlay"
          data-testid="delivery-detail-modal"
          style={{ position: "fixed", top: 0, left: 0, right: 0, bottom: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, padding: 16 }}
        >
          <div
            className="modal-content"
            style={{ background: "#fff", borderRadius: 12, width: "100%", maxWidth: 840, maxHeight: "90vh", overflowY: "auto", padding: 24, boxShadow: "0 10px 25px rgba(0,0,0,0.15)" }}
          >
            {/* Modal Header */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #e2e8f0", paddingBottom: 12, marginBottom: 16 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{ width: 36, height: 36, borderRadius: 8, background: "#e0f2fe", color: "#0369a1", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Truck size={20} />
                </div>
                <div>
                  <h2 style={{ margin: 0, fontSize: "1.25rem", color: "#0f172a" }}>
                    Detail Surat Jalan: <span style={{ color: "#0284c7" }}>{detailData.delivery?.sj_number}</span>
                  </h2>
                  <span style={{ fontSize: "0.8rem", color: "#64748b" }}>
                    Pengiriman #{detailData.delivery?.shipment_index} dari PO <strong>{detailData.delivery?.po_number}</strong>
                  </span>
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  setSelectedDeliveryId(null);
                  setDetailData(null);
                }}
                data-testid="btn-close-delivery-modal"
                style={{ background: "none", border: "none", cursor: "pointer", color: "#64748b" }}
              >
                <X size={20} />
              </button>
            </div>

            {/* PO Progress Summary Box */}
            {detailData.po_summary && (
              <div
                style={{
                  background: "#f0f9ff",
                  border: "1px solid #bae6fd",
                  borderRadius: 8,
                  padding: "14px 16px",
                  marginBottom: 16,
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
                  gap: 12,
                }}
                data-testid="delivery-po-progress-summary"
              >
                <div>
                  <span style={{ color: "#0369a1", fontSize: "0.75rem", fontWeight: 600, display: "block", textTransform: "uppercase" }}>
                    TOTAL QTY PO
                  </span>
                  <strong style={{ fontSize: "1.1rem", color: "#0c4a6e" }} data-testid="detail-total-po-qty">
                    {Number(detailData.po_summary.total_ordered || 0).toLocaleString("id-ID")} pcs
                  </strong>
                </div>
                <div>
                  <span style={{ color: "#0369a1", fontSize: "0.75rem", fontWeight: 600, display: "block", textTransform: "uppercase" }}>
                    SUDAH DIBUAT SURAT JALAN
                  </span>
                  <strong style={{ fontSize: "1.1rem", color: "#0284c7" }} data-testid="detail-cumulative-sj-qty">
                    {Number(detailData.po_summary.total_shipped || 0).toLocaleString("id-ID")} pcs
                  </strong>
                </div>
                <div>
                  <span style={{ color: "#0369a1", fontSize: "0.75rem", fontWeight: 600, display: "block", textTransform: "uppercase" }}>
                    SISA
                  </span>
                  <strong style={{ fontSize: "1.1rem", color: Number(detailData.po_summary.total_remaining || 0) === 0 ? "#16a34a" : "#ea580c" }} data-testid="detail-remaining-po-qty">
                    {Number(detailData.po_summary.total_remaining || 0).toLocaleString("id-ID")} pcs
                  </strong>
                </div>
                <div>
                  <span style={{ color: "#0369a1", fontSize: "0.75rem", fontWeight: 600, display: "block", textTransform: "uppercase" }}>
                    STATUS
                  </span>
                  <span
                    data-testid="detail-delivery-status"
                    style={{
                      display: "inline-block",
                      marginTop: 2,
                      padding: "3px 10px",
                      background: (detailData.po_summary.total_remaining === 0 || detailData.po_summary.delivery_status === "SELESAI") ? "#dcfce7" : "#e0f2fe",
                      color: (detailData.po_summary.total_remaining === 0 || detailData.po_summary.delivery_status === "SELESAI") ? "#166534" : "#075985",
                      borderRadius: 12,
                      fontSize: "0.8rem",
                      fontWeight: 700,
                    }}
                  >
                    {(detailData.po_summary.total_remaining === 0 || detailData.po_summary.delivery_status === "SELESAI") ? "SELESAI" : "SEBAGIAN"}
                  </span>
                </div>
              </div>
            )}

            {/* Delivery Meta Grid */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12, background: "#f8fafc", padding: 16, borderRadius: 8, marginBottom: 16, fontSize: "0.85rem" }}>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Nomor PO:</span>
                <Link to={`/po/${detailData.delivery?.po_id}`} style={{ color: "#0284c7", fontWeight: 600, textDecoration: "none" }}>
                  {detailData.delivery?.po_number}
                </Link>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Customer:</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.customer || "-"}</strong>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Outlet:</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.outlet || "-"}</strong>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Tanggal Kirim:</span>
                <strong style={{ color: "#1e293b" }}>{formatDisplayDate(detailData.delivery?.delivery_date) || "-"}</strong>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Penerima (PIC):</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.recipient || "-"}</strong>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Sales:</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.sales || "-"}</strong>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Ekspedisi / Zona:</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.expedition || "-"} ({detailData.delivery?.delivery_zone || "-"})</strong>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Dibuat Oleh:</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.created_by_name || detailData.delivery?.created_by || "-"}</strong>
              </div>
              <div style={{ gridColumn: "1 / -1" }}>
                <span style={{ color: "#64748b", display: "block" }}>Alamat Lengkap:</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.address || "-"}</strong>
              </div>
              {detailData.delivery?.notes && (
                <div style={{ gridColumn: "1 / -1" }}>
                  <span style={{ color: "#64748b", display: "block" }}>Catatan:</span>
                  <strong style={{ color: "#1e293b" }}>{detailData.delivery?.notes}</strong>
                </div>
              )}
            </div>

            {/* Line Items on this SJ */}
            <div style={{ marginBottom: 20 }}>
              <h4 style={{ margin: "0 0 8px 0", fontSize: "0.95rem", color: "#1e293b" }}>Daftar Barang Dikirim pada Surat Jalan Ini:</h4>
              <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.85rem" }}>
                <thead>
                  <tr style={{ background: "#f1f5f9", borderBottom: "1px solid #cbd5e1", color: "#475569" }}>
                    <th style={{ padding: "8px" }}>#</th>
                    <th style={{ padding: "8px" }}>Barang / Jasa</th>
                    <th style={{ padding: "8px" }}>Proses</th>
                    <th style={{ padding: "8px", textAlign: "right" }}>Qty Kirim</th>
                    <th style={{ padding: "8px" }}>Satuan</th>
                    <th style={{ padding: "8px" }}>Catatan</th>
                  </tr>
                </thead>
                <tbody>
                  {(detailData.delivery?.line_items || []).map((li, idx) => (
                    <tr key={idx} style={{ borderBottom: "1px solid #f1f5f9" }}>
                      <td style={{ padding: "8px", color: "#64748b" }}>{idx + 1}</td>
                      <td style={{ padding: "8px", fontWeight: 600, color: "#1e293b" }}>
                        {li.name}
                        {li.color && <div style={{ fontSize: "0.75rem", color: "#64748b", fontWeight: 400 }}>{li.color}</div>}
                      </td>
                      <td style={{ padding: "8px" }}>
                        <span style={{ fontSize: "0.75rem", padding: "2px 6px", borderRadius: 4, background: li.production_process === "non_print" ? "#fef3c7" : "#e0f2fe", color: li.production_process === "non_print" ? "#92400e" : "#0369a1" }}>
                          {li.production_process === "non_print" ? "Non-Cetak" : "Cetak"}
                        </span>
                      </td>
                      <td style={{ padding: "8px", textAlign: "right", fontWeight: 700, color: "#0f172a" }}>
                        {Number(li.qty || li.quantity || 0).toLocaleString("id-ID")}
                      </td>
                      <td style={{ padding: "8px", color: "#475569" }}>{li.unit || "pcs"}</td>
                      <td style={{ padding: "8px", color: "#64748b" }}>{li.notes || "-"}</td>
                    </tr>
                  ))}
                  <tr style={{ background: "#f8fafc", fontWeight: 700, borderTop: "2px solid #cbd5e1" }}>
                    <td colSpan={3} style={{ padding: "8px", textAlign: "right" }}>Total Kuantitas:</td>
                    <td style={{ padding: "8px", textAlign: "right", color: "#0284c7" }}>
                      {Number(detailData.delivery?.total_qty || 0).toLocaleString("id-ID")}
                    </td>
                    <td colSpan={2} style={{ padding: "8px" }}>pcs</td>
                  </tr>
                </tbody>
              </table>
            </div>

            {/* Shipment History for this PO */}
            {detailData.all_po_deliveries?.length > 1 && (
              <div style={{ marginBottom: 20, padding: 14, background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 8 }}>
                <h4 style={{ margin: "0 0 8px 0", fontSize: "0.9rem", color: "#166534", display: "flex", alignItems: "center", gap: 6 }}>
                  <Clock size={16} /> Riwayat Pengiriman untuk PO {detailData.delivery?.po_number}:
                </h4>
                <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: "0.85rem" }}>
                  {detailData.all_po_deliveries.map((shipment) => (
                    <div
                      key={shipment.id}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        padding: "6px 10px",
                        background: shipment.id === detailData.delivery?.id ? "#dcfce7" : "#fff",
                        border: "1px solid",
                        borderColor: shipment.id === detailData.delivery?.id ? "#86efac" : "#e2e8f0",
                        borderRadius: 6,
                        fontWeight: shipment.id === detailData.delivery?.id ? 600 : 400,
                      }}
                    >
                      <span>
                        Surat Jalan <strong>{shipment.sj_number}</strong> (Kirim #{shipment.shipment_index}) - {formatDisplayDate(shipment.delivery_date)}
                      </span>
                      <span style={{ fontWeight: 700, color: "#15803d" }}>
                        {Number(shipment.total_qty || 0).toLocaleString("id-ID")} pcs
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Modal Actions */}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, borderTop: "1px solid #e2e8f0", paddingTop: 14 }}>
              {canPrint && (
                <button
                  type="button"
                  onClick={() => handlePrintPdf(detailData.delivery?.id)}
                  data-testid="btn-print-modal-pdf"
                  style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 16px", background: "#0284c7", color: "#fff", border: "none", borderRadius: 6, fontWeight: 600, cursor: "pointer" }}
                >
                  <Printer size={16} /> Cetak PDF Surat Jalan
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  setSelectedDeliveryId(null);
                  setDetailData(null);
                }}
                data-testid="btn-close-modal-bottom"
                style={{ padding: "8px 16px", background: "#f1f5f9", color: "#334155", border: "1px solid #cbd5e1", borderRadius: 6, fontWeight: 600, cursor: "pointer" }}
              >
                Tutup
              </button>
            </div>
          </div>
        </div>
      )}

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
