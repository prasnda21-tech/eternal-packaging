import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  FileText,
  Package,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate } from "../lib/constants";

function getTodayString() {
  const d = new Date();
  return d.toISOString().split("T")[0];
}

function getThreeDaysLaterString() {
  const d = new Date();
  d.setDate(d.getDate() + 3);
  return d.toISOString().split("T")[0];
}

const INITIAL_LINE_ITEM = {
  item_code: "",
  name: "",
  quantity: 1,
  unit: "pcs",
  color: "",
  production_process: "print",
  notes: "",
  accurate_item_id: null,
  master_item_id: null,
};

export default function PesananPenjualan() {
  const navigate = useNavigate();
  const { user, hasPerm } = useAuth();

  const canCreate = hasPerm("sales_order.create") || hasPerm("po.edit") || user?.role === "admin";
  const canDelete = hasPerm("po.delete") || user?.role === "admin";
  const [viewMode, setViewMode] = useState("list"); // 'list' | 'create'
  
  // List state
  const [orders, setOrders] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listErr, setListErr] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteErr, setDeleteErr] = useState("");

  // Options & Auto-fill masters
  const [options, setOptions] = useState({
    customers: [],
    outlets_by_customer: {},
    sales_options: [],
    master_items: [],
    suggested_po_number: "",
  });
  const [optionsLoading, setOptionsLoading] = useState(false);

  // Form state
  const [formData, setFormData] = useState({
    po_number: "",
    order_date: getTodayString(),
    customer: "",
    outlet: "",
    recipient: "",
    address: "",
    delivery_zone: "",
    payment_terms: "",
    sales: "",
    created_by_user: user?.name || user?.email || "",
    expedition: "Internal fleet",
    estimated_delivery: getThreeDaysLaterString(),
    deadline: getThreeDaysLaterString(),
    notes: "",
    line_items: [{ ...INITIAL_LINE_ITEM }],
  });

  const [formBusy, setFormBusy] = useState(false);
  const [formErr, setFormErr] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  const loadOptions = useCallback(async () => {
    setOptionsLoading(true);
    try {
      const data = await api.salesOrderOptions();
      setOptions(data || {});
      if (data?.suggested_po_number && !formData.po_number) {
        setFormData((prev) => ({
          ...prev,
          po_number: prev.po_number || data.suggested_po_number,
        }));
      }
    } catch {
      // Best effort load options
    } finally {
      setOptionsLoading(false);
    }
  }, [formData.po_number]);

  const loadOrders = useCallback(async () => {
    setListLoading(true);
    setListErr("");
    try {
      const params = {};
      if (searchQuery.trim()) params.q = searchQuery.trim();
      if (statusFilter !== "all") params.status = statusFilter;
      const res = await api.listSalesOrders(params);
      setOrders(res.orders || []);
      setSelectedIds(new Set());
    } catch (e) {
      const det = e.response?.data?.detail;
      setListErr(typeof det === "object" ? det.message : (det || "Gagal memuat daftar Pesanan Penjualan"));
    } finally {
      setListLoading(false);
    }
  }, [searchQuery, statusFilter]);

  useEffect(() => {
    loadOptions();
  }, [loadOptions]);

  useEffect(() => {
    if (viewMode === "list") {
      loadOrders();
    }
  }, [viewMode, loadOrders]);

  // When customer changes, handle autofill suggestions for outlet/address/sales/payment_terms
  const handleCustomerChange = (newCustomer) => {
    const outlets = options.outlets_by_customer?.[newCustomer] || [];
    let patch = { customer: newCustomer };
    if (outlets.length === 1) {
      const o = outlets[0];
      patch = {
        ...patch,
        outlet: o.outlet || "",
        address: o.address || formData.address,
        recipient: o.recipient || formData.recipient,
        delivery_zone: o.delivery_zone || formData.delivery_zone,
        sales: o.sales || formData.sales,
        payment_terms: o.payment_terms || formData.payment_terms,
      };
    }
    setFormData((prev) => ({ ...prev, ...patch }));
  };

  // When outlet changes, autofill matching address/recipient if available
  const handleOutletChange = (newOutlet) => {
    const outlets = options.outlets_by_customer?.[formData.customer] || [];
    const match = outlets.find((o) => o.outlet === newOutlet);
    if (match) {
      setFormData((prev) => ({
        ...prev,
        outlet: newOutlet,
        address: match.address || prev.address,
        recipient: match.recipient || prev.recipient,
        delivery_zone: match.delivery_zone || prev.delivery_zone,
        sales: match.sales || prev.sales,
        payment_terms: match.payment_terms || prev.payment_terms,
      }));
    } else {
      setFormData((prev) => ({ ...prev, outlet: newOutlet }));
    }
  };

  // Line item handlers
  const handleLineChange = (index, field, value) => {
    setFormData((prev) => {
      const lines = [...prev.line_items];
      lines[index] = { ...lines[index], [field]: value };
      return { ...prev, line_items: lines };
    });
  };

  const handleSelectMasterItem = (index, masterId) => {
    const master = options.master_items?.find((m) => String(m.id) === String(masterId) || String(m.accurate_item_id) === String(masterId));
    if (master) {
      setFormData((prev) => {
        const lines = [...prev.line_items];
        lines[index] = {
          ...lines[index],
          name: master.item_name || lines[index].name,
          item_code: master.item_code || lines[index].item_code,
          unit: master.unit || lines[index].unit || "pcs",
          accurate_item_id: master.accurate_item_id || null,
          master_item_id: master.id || null,
        };
        return { ...prev, line_items: lines };
      });
    }
  };

  const addLineItem = () => {
    setFormData((prev) => ({
      ...prev,
      line_items: [...prev.line_items, { ...INITIAL_LINE_ITEM }],
    }));
  };

  const removeLineItem = (index) => {
    if (formData.line_items.length <= 1) return;
    setFormData((prev) => ({
      ...prev,
      line_items: prev.line_items.filter((_, i) => i !== index),
    }));
  };

  const generatePoNumber = async () => {
    try {
      const data = await api.salesOrderOptions();
      if (data?.suggested_po_number) {
        setFormData((prev) => ({ ...prev, po_number: data.suggested_po_number }));
      }
    } catch {
      // Ignore
    }
  };

  const totalQty = useMemo(() => {
    return formData.line_items.reduce((sum, li) => sum + (Number(li.quantity) || 0), 0);
  }, [formData.line_items]);

  const toggleSelect = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const toggleSelectAll = () => {
    if (selectedIds.size === orders.length && orders.length > 0) setSelectedIds(new Set());
    else setSelectedIds(new Set(orders.map((o) => o.id)));
  };
  const formatDeleteError = (det) => {
    if (typeof det === "object" && det) {
      const blockers = det.blockers || [];
      if (blockers.length) {
        return `${det.message || "Tidak dapat menghapus."} ${blockers.map((b) => `${b.po_number || b.number || b.id}: ${b.reason}`).join(" ")}`.trim();
      }
      return det.message || JSON.stringify(det);
    }
    return det || "Gagal menghapus.";
  };
  const handleBulkDelete = async () => {
    setDeleteLoading(true);
    setDeleteErr("");
    try {
      await api.bulkDelete(Array.from(selectedIds), "Hapus dari daftar Pesanan Penjualan", "HAPUS");
      setDeleteConfirm(null);
      setSelectedIds(new Set());
      loadOrders();
    } catch (e) {
      setDeleteErr(formatDeleteError(e.response?.data?.detail));
    } finally {
      setDeleteLoading(false);
    }
  };

  const resetForm = () => {
    setFormData({
      po_number: options.suggested_po_number || "",
      order_date: getTodayString(),
      customer: "",
      outlet: "",
      recipient: "",
      address: "",
      delivery_zone: "",
      payment_terms: "",
      sales: "",
      created_by_user: user?.name || user?.email || "",
      expedition: "Internal fleet",
      estimated_delivery: getThreeDaysLaterString(),
      deadline: getThreeDaysLaterString(),
      notes: "",
      line_items: [{ ...INITIAL_LINE_ITEM }],
    });
    setFormErr("");
    setSuccessMsg("");
  };

  const handleFormSubmit = async (e) => {
    e?.preventDefault?.();
    setFormErr("");
    setSuccessMsg("");

    // Frontend validation
    if (!formData.customer.trim()) {
      setFormErr("Customer wajib diisi.");
      return;
    }
    if (!formData.line_items?.length) {
      setFormErr("Minimal harus ada 1 line item.");
      return;
    }
    for (let i = 0; i < formData.line_items.length; i++) {
      const li = formData.line_items[i];
      if (!li.name.trim()) {
        setFormErr(`Baris #${i + 1}: Nama barang/jasa wajib diisi.`);
        return;
      }
      const q = Number(li.quantity);
      if (!Number.isFinite(q) || q <= 0) {
        setFormErr(`Baris #${i + 1}: Quantity harus angka lebih besar dari 0.`);
        return;
      }
    }

    setFormBusy(true);
    try {
      const payload = {
        ...formData,
        po_number: formData.po_number.trim() || undefined,
        customer: formData.customer.trim(),
        line_items: formData.line_items.map((li) => ({
          ...li,
          name: li.name.trim(),
          quantity: Number(li.quantity),
          unit: (li.unit || "pcs").trim(),
          production_process: li.production_process === "non_print" ? "non_print" : "print",
        })),
      };

      const res = await api.createSalesOrder(payload);
      setSuccessMsg(res.message || "Pesanan Penjualan berhasil dibuat!");
      const createdId = res.order?.id;
      setTimeout(() => {
        if (createdId) {
          navigate(`/po/${createdId}`);
        } else {
          setViewMode("list");
          loadOrders();
        }
      }, 700);
    } catch (err) {
      const det = err.response?.data?.detail;
      if (typeof det === "object" && det?.message) {
        setFormErr(det.message + (det.existing_po_number ? ` (${det.existing_po_number})` : ""));
      } else {
        setFormErr(det || "Gagal menyimpan Pesanan Penjualan");
      }
    } finally {
      setFormBusy(false);
    }
  };

  const outletsForSelectedCustomer = useMemo(() => {
    return options.outlets_by_customer?.[formData.customer] || [];
  }, [options.outlets_by_customer, formData.customer]);

  return (
    <div className="sales-order-page" data-testid="sales-order-page" style={{ padding: "20px 24px", maxWidth: 1200, margin: "0 auto" }}>
      {/* Top Header */}
      <div className="page-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
        <div>
          <p className="eyebrow" style={{ color: "#64748b", fontSize: "0.85rem", fontWeight: 600, textTransform: "uppercase", marginBottom: 4 }}>
            Penjualan · Pesanan Penjualan
          </p>
          <h1 data-testid="sales-order-title" style={{ fontSize: "1.75rem", fontWeight: 700, margin: 0, color: "#0f172a" }}>
            {viewMode === "create" ? "Buat Pesanan Penjualan" : "Pesanan Penjualan"}
          </h1>
          <p className="subhead" style={{ color: "#64748b", margin: "4px 0 0 0", fontSize: "0.95rem" }}>
            {viewMode === "create"
              ? "Struktur data, validasi, dan alur workflow sama persis dengan PO Masuk / Import PO"
              : "Daftar PO Penjualan dan pembuatan Sales Order baru"}
          </p>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {viewMode === "list" ? (
            <>
              {canDelete && selectedIds.size > 0 && (
                <button
                  type="button"
                  data-testid="btn-hapus-terpilih"
                  onClick={() => {
                    setDeleteErr("");
                    setDeleteConfirm({
                      ids: Array.from(selectedIds),
                      numbers: orders.filter((o) => selectedIds.has(o.id)).map((o) => o.po_number),
                    });
                  }}
                  style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 14px", borderRadius: 8, background: "#fee2e2", color: "#b91c1c", border: "1px solid #fecaca", cursor: "pointer", fontWeight: 600 }}
                >
                  <Trash2 size={16} /> Hapus Terpilih ({selectedIds.size})
                </button>
              )}
              <button
                className="icon-button"
                onClick={loadOrders}
                disabled={listLoading}
                data-testid="sales-order-refresh-button"
                aria-label="Muat ulang"
                style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "8px 12px", cursor: "pointer", display: "flex", alignItems: "center" }}
              >
                <RefreshCw size={16} className={listLoading ? "spin" : ""} />
              </button>
              {canCreate && (
                <button
                  className="primary"
                  onClick={() => {
                    resetForm();
                    setViewMode("create");
                  }}
                  data-testid="create-sales-order-btn"
                  style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 16px", borderRadius: 8, fontWeight: 600, background: "#0284c7", color: "#fff", border: "none", cursor: "pointer" }}
                >
                  <Plus size={16} /> Buat Pesanan Penjualan
                </button>
              )}
            </>
          ) : (
            <button
              className="ghost"
              onClick={() => setViewMode("list")}
              data-testid="back-to-list-btn"
              style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer" }}
            >
              <ArrowLeft size={16} /> Kembali ke Daftar
            </button>
          )}
        </div>
      </div>

      {/* VIEW: CREATE FORM */}
      {viewMode === "create" && (
        <form onSubmit={handleFormSubmit} data-testid="sales-order-form">
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

          {successMsg && (
            <div
              className="alert-box success"
              data-testid="form-success-banner"
              style={{ background: "#f0fdf4", border: "1px solid #bbf7d0", color: "#15803d", padding: "12px 16px", borderRadius: 8, marginBottom: 16, display: "flex", alignItems: "center", gap: 10 }}
            >
              <CheckCircle2 size={18} />
              <span>{successMsg}</span>
            </div>
          )}

          {/* Card 1: Informasi Header PO */}
          <section className="panel" style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, padding: 20, marginBottom: 20, boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
            <header style={{ borderBottom: "1px solid #f1f5f9", paddingBottom: 12, marginBottom: 16 }}>
              <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#1e293b" }}>Informasi Pesanan (Header)</h3>
            </header>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16 }}>
              {/* No PO */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Nomor PO <span style={{ color: "#ef4444" }}>*</span>
                </label>
                <div style={{ display: "flex", gap: 6 }}>
                  <input
                    type="text"
                    value={formData.po_number}
                    onChange={(e) => setFormData((p) => ({ ...p, po_number: e.target.value }))}
                    placeholder="mis. PO-2026-001"
                    data-testid="input-po-number"
                    style={{ flex: 1, padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem" }}
                  />
                  <button
                    type="button"
                    className="ghost sm"
                    onClick={generatePoNumber}
                    title="Buat Nomor Otomatis"
                    data-testid="btn-generate-po"
                    style={{ padding: "8px 10px", border: "1px solid #cbd5e1", borderRadius: 6, background: "#f8fafc", cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}
                  >
                    <Sparkles size={14} /> Auto
                  </button>
                </div>
              </div>

              {/* Tanggal PO */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Tanggal PO <span style={{ color: "#ef4444" }}>*</span>
                </label>
                <input
                  type="date"
                  value={formData.order_date}
                  onChange={(e) => setFormData((p) => ({ ...p, order_date: e.target.value }))}
                  data-testid="input-order-date"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              {/* Customer */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Customer <span style={{ color: "#ef4444" }}>*</span>
                </label>
                <input
                  type="text"
                  list="customer-suggestions"
                  value={formData.customer}
                  onChange={(e) => handleCustomerChange(e.target.value)}
                  placeholder="Ketik atau pilih nama customer"
                  data-testid="input-customer"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                  required
                />
                <datalist id="customer-suggestions">
                  {options.customers?.map((c) => (
                    <option key={c} value={c} />
                  ))}
                </datalist>
              </div>

              {/* Outlet / Cabang */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Outlet / Cabang
                </label>
                <input
                  type="text"
                  list="outlet-suggestions"
                  value={formData.outlet}
                  onChange={(e) => handleOutletChange(e.target.value)}
                  placeholder="mis. Cabang Tebet / Pusat"
                  data-testid="input-outlet"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
                <datalist id="outlet-suggestions">
                  {outletsForSelectedCustomer.map((o) => (
                    <option key={o.outlet} value={o.outlet} />
                  ))}
                </datalist>
              </div>

              {/* Sales */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Tenaga Penjual (Sales)
                </label>
                <input
                  type="text"
                  list="sales-suggestions"
                  value={formData.sales}
                  onChange={(e) => setFormData((p) => ({ ...p, sales: e.target.value }))}
                  placeholder="mis. Vira"
                  data-testid="input-sales"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
                <datalist id="sales-suggestions">
                  {options.sales_options?.map((s) => (
                    <option key={s} value={s} />
                  ))}
                </datalist>
              </div>

              {/* Pembuat PO */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  User / Pembuat PO
                </label>
                <input
                  type="text"
                  value={formData.created_by_user}
                  onChange={(e) => setFormData((p) => ({ ...p, created_by_user: e.target.value }))}
                  placeholder="Nama user pembuat"
                  data-testid="input-created-by-user"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              {/* Penerima */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Penerima (PIC)
                </label>
                <input
                  type="text"
                  value={formData.recipient}
                  onChange={(e) => setFormData((p) => ({ ...p, recipient: e.target.value }))}
                  placeholder="mis. Ibu Rina"
                  data-testid="input-recipient"
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
                  placeholder="mis. Jakarta Selatan"
                  data-testid="input-delivery-zone"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              {/* Estimasi Kirim */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Estimasi Kirim
                </label>
                <input
                  type="date"
                  value={formData.estimated_delivery}
                  onChange={(e) => setFormData((p) => ({ ...p, estimated_delivery: e.target.value }))}
                  data-testid="input-estimated-delivery"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              {/* Deadline */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Deadline Produksi
                </label>
                <input
                  type="date"
                  value={formData.deadline}
                  onChange={(e) => setFormData((p) => ({ ...p, deadline: e.target.value }))}
                  data-testid="input-deadline"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              {/* Termin Bayar */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Termin Pembayaran
                </label>
                <input
                  type="text"
                  value={formData.payment_terms}
                  onChange={(e) => setFormData((p) => ({ ...p, payment_terms: e.target.value }))}
                  placeholder="mis. Cash, Net 14, Net 30, COD"
                  data-testid="input-payment-terms"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              {/* Ekspedisi */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Ekspedisi / Pengiriman
                </label>
                <input
                  type="text"
                  value={formData.expedition}
                  onChange={(e) => setFormData((p) => ({ ...p, expedition: e.target.value }))}
                  placeholder="mis. Internal fleet"
                  data-testid="input-expedition"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>
            </div>

            {/* Alamat Kirim */}
            <div style={{ marginTop: 16 }}>
              <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                Alamat Lengkap Pengiriman
              </label>
              <textarea
                value={formData.address}
                onChange={(e) => setFormData((p) => ({ ...p, address: e.target.value }))}
                placeholder="Alamat jalan, gedung, nomor, patokan"
                rows={2}
                data-testid="input-address"
                style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
              />
            </div>

            {/* Catatan PO */}
            <div style={{ marginTop: 12 }}>
              <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                Catatan Pesanan
              </label>
              <input
                type="text"
                value={formData.notes}
                onChange={(e) => setFormData((p) => ({ ...p, notes: e.target.value }))}
                placeholder="Catatan khusus pengerjaan, packing, atau instruksi"
                data-testid="input-notes"
                style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
              />
            </div>
          </section>

          {/* Card 2: Line Items */}
          <section className="panel" style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, padding: 20, marginBottom: 20, boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
            <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #f1f5f9", paddingBottom: 12, marginBottom: 16 }}>
              <div>
                <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#1e293b" }}>Daftar Line Item (Barang / Jasa)</h3>
                <p style={{ margin: "2px 0 0 0", fontSize: "0.85rem", color: "#64748b" }}>
                  Masing-masing baris item memiliki identitas independen (tidak akan dimerge otomatis).
                </p>
              </div>
              <button
                type="button"
                className="ghost sm"
                onClick={addLineItem}
                data-testid="btn-add-line-item"
                style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 12px", borderRadius: 6, border: "1px solid #cbd5e1", background: "#f8fafc", cursor: "pointer", fontWeight: 600 }}
              >
                <Plus size={14} /> Tambah Baris
              </button>
            </header>

            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.9rem" }}>
                <thead>
                  <tr style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
                    <th style={{ padding: "10px 8px", width: 40 }}>#</th>
                    <th style={{ padding: "10px 8px", minWidth: 220 }}>Barang / Jasa <span style={{ color: "#ef4444" }}>*</span></th>
                    <th style={{ padding: "10px 8px", width: 120 }}>Kode</th>
                    <th style={{ padding: "10px 8px", width: 130 }}>Proses</th>
                    <th style={{ padding: "10px 8px", width: 100 }}>Qty <span style={{ color: "#ef4444" }}>*</span></th>
                    <th style={{ padding: "10px 8px", width: 80 }}>Satuan</th>
                    <th style={{ padding: "10px 8px", width: 140 }}>Warna</th>
                    <th style={{ padding: "10px 8px", minWidth: 160 }}>Catatan Item</th>
                    <th style={{ padding: "10px 8px", width: 48, textAlign: "center" }}>Aksi</th>
                  </tr>
                </thead>
                <tbody>
                  {formData.line_items.map((line, idx) => (
                    <tr key={idx} data-testid={`line-item-row-${idx}`} style={{ borderBottom: "1px solid #f1f5f9" }}>
                      <td style={{ padding: "10px 8px", color: "#64748b", fontWeight: 600 }}>{idx + 1}</td>
                      <td style={{ padding: "10px 8px" }}>
                        <input
                          type="text"
                          list={`master-item-list-${idx}`}
                          value={line.name}
                          onChange={(e) => handleLineChange(idx, "name", e.target.value)}
                          placeholder="Nama barang..."
                          data-testid={`line-name-${idx}`}
                          style={{ width: "100%", padding: "6px 10px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box" }}
                          required
                        />
                        <datalist id={`master-item-list-${idx}`}>
                          {options.master_items?.map((m) => (
                            <option key={m.id || m.accurate_item_id} value={m.item_name}>
                              {m.item_code ? `[${m.item_code}] ` : ""}{m.item_name}
                            </option>
                          ))}
                        </datalist>
                      </td>
                      <td style={{ padding: "10px 8px" }}>
                        <input
                          type="text"
                          value={line.item_code || ""}
                          onChange={(e) => handleLineChange(idx, "item_code", e.target.value)}
                          placeholder="Kode"
                          data-testid={`line-code-${idx}`}
                          style={{ width: "100%", padding: "6px 10px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box" }}
                        />
                      </td>
                      <td style={{ padding: "10px 8px" }}>
                        <select
                          value={line.production_process}
                          onChange={(e) => handleLineChange(idx, "production_process", e.target.value)}
                          data-testid={`line-process-${idx}`}
                          style={{ width: "100%", padding: "6px 8px", border: "1px solid #cbd5e1", borderRadius: 6, background: "#fff" }}
                        >
                          <option value="print">Cetak</option>
                          <option value="non_print">Tidak Cetak</option>
                        </select>
                      </td>
                      <td style={{ padding: "10px 8px" }}>
                        <input
                          type="number"
                          min="1"
                          value={line.quantity}
                          onChange={(e) => handleLineChange(idx, "quantity", e.target.value)}
                          data-testid={`line-qty-${idx}`}
                          style={{ width: "100%", padding: "6px 10px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box" }}
                          required
                        />
                      </td>
                      <td style={{ padding: "10px 8px" }}>
                        <input
                          type="text"
                          value={line.unit || "pcs"}
                          onChange={(e) => handleLineChange(idx, "unit", e.target.value)}
                          data-testid={`line-unit-${idx}`}
                          style={{ width: "100%", padding: "6px 8px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box" }}
                        />
                      </td>
                      <td style={{ padding: "10px 8px" }}>
                        <input
                          type="text"
                          value={line.color || ""}
                          onChange={(e) => handleLineChange(idx, "color", e.target.value)}
                          placeholder="mis. Full color"
                          data-testid={`line-color-${idx}`}
                          style={{ width: "100%", padding: "6px 10px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box" }}
                        />
                      </td>
                      <td style={{ padding: "10px 8px" }}>
                        <input
                          type="text"
                          value={line.notes || ""}
                          onChange={(e) => handleLineChange(idx, "notes", e.target.value)}
                          placeholder="Catatan..."
                          data-testid={`line-notes-${idx}`}
                          style={{ width: "100%", padding: "6px 10px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box" }}
                        />
                      </td>
                      <td style={{ padding: "10px 8px", textAlign: "center" }}>
                        <button
                          type="button"
                          onClick={() => removeLineItem(idx)}
                          disabled={formData.line_items.length <= 1}
                          data-testid={`btn-remove-line-${idx}`}
                          style={{ background: "transparent", border: "none", color: formData.line_items.length <= 1 ? "#cbd5e1" : "#ef4444", cursor: formData.line_items.length <= 1 ? "not-allowed" : "pointer", padding: 4 }}
                          title="Hapus baris"
                        >
                          <Trash2 size={16} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Total Summary Footer */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 16, padding: "12px 16px", background: "#f8fafc", borderRadius: 8 }}>
              <span style={{ fontSize: "0.9rem", color: "#475569" }}>
                Total Item: <strong>{formData.line_items.length}</strong>
              </span>
              <span style={{ fontSize: "1rem", color: "#0f172a" }}>
                Total Quantity: <strong>{totalQty.toLocaleString("id-ID")} pcs</strong>
              </span>
            </div>
          </section>

          {/* Form Actions */}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 12, marginTop: 24 }}>
            <button
              type="button"
              className="ghost"
              onClick={() => setViewMode("list")}
              disabled={formBusy}
              data-testid="btn-cancel-form"
              style={{ padding: "10px 20px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer", fontWeight: 600 }}
            >
              Batal
            </button>
            <button
              type="submit"
              className="primary"
              disabled={formBusy}
              data-testid="btn-submit-sales-order"
              style={{ padding: "10px 24px", borderRadius: 8, background: "#0284c7", color: "#fff", border: "none", cursor: formBusy ? "not-allowed" : "pointer", fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}
            >
              {formBusy ? "Menyimpan..." : "Simpan Pesanan Penjualan"}
            </button>
          </div>
        </form>
      )}

      {/* VIEW: LIST ORDERS */}
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
                placeholder="Cari No PO / Customer / Outlet / Sales..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                data-testid="search-sales-orders-input"
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
                { id: "all", label: "Semua" },
                { id: "aktif", label: "Aktif" },
                { id: "selesai", label: "Selesai" },
                { id: "hold", label: "HOLD" },
                { id: "cancelled", label: "Batal" },
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

          {/* Orders Table */}
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.9rem" }}>
              <thead>
                <tr style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
                  {canDelete && (
                    <th style={{ padding: "10px 8px", width: 36 }}>
                      <input type="checkbox" data-testid="select-all-orders" checked={orders.length > 0 && selectedIds.size === orders.length} onChange={toggleSelectAll} aria-label="Pilih semua baris tampilan" />
                    </th>
                  )}
                  <th style={{ padding: "10px 12px" }}>No PO</th>
                  <th style={{ padding: "10px 12px" }}>Tanggal PO</th>
                  <th style={{ padding: "10px 12px" }}>Customer</th>
                  <th style={{ padding: "10px 12px" }}>Outlet</th>
                  <th style={{ padding: "10px 12px", textAlign: "right" }}>Total Qty</th>
                  <th style={{ padding: "10px 12px", textAlign: "right" }}>Sisa Kirim</th>
                  <th style={{ padding: "10px 12px" }}>Status Kirim</th>
                  <th style={{ padding: "10px 12px" }}>Sales</th>
                  <th style={{ padding: "10px 12px" }}>Status / Stage</th>
                  <th style={{ padding: "10px 12px", textAlign: "center" }}>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {listLoading ? (
                  <tr>
                    <td colSpan={canDelete ? 11 : 10} style={{ padding: 32, textAlign: "center", color: "#64748b" }}>
                      Memuat daftar Pesanan Penjualan...
                    </td>
                  </tr>
                ) : orders.length === 0 ? (
                  <tr>
                    <td colSpan={canDelete ? 11 : 10} style={{ padding: 40, textAlign: "center", color: "#64748b" }} data-testid="empty-sales-orders">
                      <FileText size={32} style={{ opacity: 0.4, marginBottom: 8 }} />
                      <p style={{ margin: 0, fontWeight: 500 }}>Belum ada Pesanan Penjualan yang sesuai filter.</p>
                      {canCreate && (
                        <button
                          type="button"
                          onClick={() => {
                            resetForm();
                            setViewMode("create");
                          }}
                          style={{ marginTop: 12, padding: "6px 14px", background: "#0284c7", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontWeight: 600, fontSize: "0.85rem" }}
                        >
                          + Buat PO Baru
                        </button>
                      )}
                    </td>
                  </tr>
                ) : (
                  orders.map((o) => {
                    const isCancelled = !!o.cancelled;
                    const isHold = !!o.hold;
                    const stageLabel = o.status_label || `Tahap ${o.current_stage}`;
                    const delivSummary = o.delivery_summary;
                    const remainQty = delivSummary ? delivSummary.total_remaining : Number(o.total_quantity || 0);
                    const delivStatus = delivSummary?.status || (isCancelled ? "DIBATALKAN" : "BELUM DIKIRIM");
                    const canProcessDelivery = !isCancelled && remainQty > 0;

                    return (
                      <tr key={o.id} data-testid={`order-row-${o.id}`} style={{ borderBottom: "1px solid #f1f5f9" }}>
                        {canDelete && (
                          <td style={{ padding: "10px 8px" }}>
                            <input type="checkbox" data-testid={`order-checkbox-${o.id}`} checked={selectedIds.has(o.id)} onChange={() => toggleSelect(o.id)} aria-label={`Pilih ${o.po_number}`} />
                          </td>
                        )}
                        <td style={{ padding: "10px 12px", fontWeight: 600 }}>
                          <Link to={`/po/${o.id}`} style={{ color: "#0284c7", textDecoration: "none" }} data-testid={`po-link-${o.po_number}`}>
                            {o.po_number}
                          </Link>
                        </td>
                        <td style={{ padding: "10px 12px", color: "#475569" }}>
                          {formatDisplayDate(o.order_date) || "-"}
                        </td>
                        <td style={{ padding: "10px 12px", fontWeight: 500, color: "#1e293b" }}>
                          {o.customer || "-"}
                        </td>
                        <td style={{ padding: "10px 12px", color: "#475569" }}>
                          {o.outlet || "-"}
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 600, color: "#0f172a" }}>
                          {Number(o.total_quantity || 0).toLocaleString("id-ID")} pcs
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 600, color: remainQty > 0 ? "#0284c7" : "#16a34a" }}>
                          {remainQty.toLocaleString("id-ID")} pcs
                        </td>
                        <td style={{ padding: "10px 12px" }}>
                          <span
                            style={{
                              display: "inline-block",
                              padding: "2px 8px",
                              borderRadius: 12,
                              fontSize: "0.75rem",
                              fontWeight: 600,
                              background: (delivStatus === "SELESAI" || delivStatus === "SELESAI PENGIRIMAN") ? "#dcfce7" : (delivStatus === "SEBAGIAN" || delivStatus === "SEBAGIAN DIKIRIM") ? "#e0f2fe" : "#f1f5f9",
                              color: (delivStatus === "SELESAI" || delivStatus === "SELESAI PENGIRIMAN") ? "#166534" : (delivStatus === "SEBAGIAN" || delivStatus === "SEBAGIAN DIKIRIM") ? "#0369a1" : "#475569",
                            }}
                          >
                            {delivStatus}
                          </span>
                        </td>
                        <td style={{ padding: "10px 12px", color: "#475569" }}>
                          {o.sales || o.sales_user || "-"}
                        </td>
                        <td style={{ padding: "10px 12px" }}>
                          {isCancelled ? (
                            <span style={{ display: "inline-block", padding: "2px 8px", background: "#fee2e2", color: "#991b1b", borderRadius: 12, fontSize: "0.75rem", fontWeight: 600 }}>
                              Cancelled
                            </span>
                          ) : isHold ? (
                            <span style={{ display: "inline-block", padding: "2px 8px", background: "#fef3c7", color: "#92400e", borderRadius: 12, fontSize: "0.75rem", fontWeight: 600 }}>
                              HOLD
                            </span>
                          ) : (
                            <span style={{ display: "inline-block", padding: "2px 8px", background: o.current_stage >= 14 ? "#dcfce7" : "#e0f2fe", color: o.current_stage >= 14 ? "#166534" : "#075985", borderRadius: 12, fontSize: "0.75rem", fontWeight: 600 }}>
                              {stageLabel}
                            </span>
                          )}
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "center" }}>
                          <div style={{ display: "flex", justifyContent: "center", gap: 6 }}>
                            <Link
                              to={`/po/${o.id}`}
                              data-testid={`btn-view-po-${o.id}`}
                              style={{ display: "inline-block", padding: "4px 8px", background: "#f1f5f9", color: "#334155", borderRadius: 6, textDecoration: "none", fontSize: "0.8rem", fontWeight: 600 }}
                            >
                              Lihat PO
                            </Link>
                            {canProcessDelivery && (
                              <Link
                                to={`/penjualan/pengiriman-pesanan?po_id=${o.id}&action=create`}
                                data-testid={`btn-process-delivery-${o.id}`}
                                title="Proses ke Pengiriman Pesanan"
                                style={{ display: "inline-block", padding: "4px 8px", background: "#e0f2fe", color: "#0369a1", borderRadius: 6, textDecoration: "none", fontSize: "0.8rem", fontWeight: 600, whiteSpace: "nowrap" }}
                              >
                                + Kirim
                              </Link>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {deleteConfirm && (
        <div className="modal-overlay" data-testid="delete-confirm-modal" style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 80 }}>
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 460 }}>
            <h3 style={{ margin: "0 0 8px" }}>Hapus Pesanan Penjualan?</h3>
            <p style={{ margin: "0 0 12px", color: "#64748b", fontSize: "0.9rem" }}>Dokumen berikut akan dihapus:</p>
            <ul data-testid="delete-confirm-numbers" style={{ margin: "0 0 16px", paddingLeft: 18, fontWeight: 700, color: "#0284c7" }}>
              {(deleteConfirm.numbers || []).map((n) => <li key={n}>{n}</li>)}
            </ul>
            {deleteErr && <div data-testid="delete-error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "8px 12px", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>{deleteErr}</div>}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" onClick={() => { setDeleteConfirm(null); setDeleteErr(""); }} disabled={deleteLoading} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff" }}>Batal</button>
              <button type="button" data-testid="btn-confirm-delete" onClick={handleBulkDelete} disabled={deleteLoading} style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#dc2626", color: "#fff", fontWeight: 600 }}>
                {deleteLoading ? "Menghapus..." : "Hapus"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
