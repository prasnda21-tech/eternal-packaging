import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
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
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";
import MasterItemSearchSelect from "../components/MasterItemSearchSelect";
import {
  REPEAT_NO_PREVIOUS_MESSAGE,
  clearRepeatDerivedFormFields,
  mapRepeatSourceToFormPatch,
} from "../lib/salesOrderRepeat";
import {
  capturePoOpenFromList,
  poNavStateFromList,
} from "../lib/poListReturn";

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

export function filterPoLineItems(lineItems, searchQuery) {
  const q = (searchQuery || "").trim().toLowerCase();
  const list = (lineItems || []).map((line, originalIndex) => ({
    line,
    originalIndex,
  }));
  if (!q) return list;
  return list.filter(({ line }) => {
    const name = String(line?.name || "").toLowerCase();
    const code = String(line?.item_code || "").toLowerCase();
    return name.includes(q) || code.includes(q);
  });
}

export default function PesananPenjualan() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const { user, hasPerm } = useAuth();

  const canCreate = hasPerm("sales_order.create") || hasPerm("po.edit") || user?.role === "admin";
  const canDelete = hasPerm("po.delete") || user?.role === "admin";
  const [viewMode, setViewMode] = useState("list"); // 'list' | 'create'
  
  // List state
  const [orders, setOrders] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listErr, setListErr] = useState("");
  const [searchQuery, setSearchQuery] = useState(() => searchParams.get("q") || "");
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get("status") || "all");
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
    customer_id: null,
    customer_phone: "",
    customer_address: "",
    customer_pic: "",
    customer_email: "",
    customer_city: "",
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
  const [lineSearchQuery, setLineSearchQuery] = useState("");

  // Repeat Order (create-form prefill only)
  const [repeatSourceIndicator, setRepeatSourceIndicator] = useState("");
  const [repeatDesignFiles, setRepeatDesignFiles] = useState([]);
  const [repeatBusy, setRepeatBusy] = useState(false);
  const [repeatMsg, setRepeatMsg] = useState("");
  const [repeatConfirm, setRepeatConfirm] = useState(null);
  const repeatAbortRef = useRef(null);
  const repeatRequestCustomerIdRef = useRef(null);
  const selectedCustomerIdRef = useRef(null);

  useEffect(() => {
    selectedCustomerIdRef.current = formData.customer_id || null;
  }, [formData.customer_id]);

  const clearRepeatState = useCallback(() => {
    setRepeatSourceIndicator("");
    setRepeatDesignFiles([]);
    setRepeatMsg("");
    setRepeatConfirm(null);
    repeatAbortRef.current?.abort();
    repeatRequestCustomerIdRef.current = null;
  }, []);

  const filteredLineItems = useMemo(
    () => filterPoLineItems(formData.line_items, lineSearchQuery),
    [formData.line_items, lineSearchQuery]
  );

  const loadOrdersAbortRef = useRef(null);

  const loadOptions = useCallback(async () => {
    setOptionsLoading(true);
    try {
      const data = await api.salesOrderOptions();
      setOptions(data || {});
      if (data?.suggested_po_number) {
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
  }, []);

  const loadOrders = useCallback(async () => {
    loadOrdersAbortRef.current?.abort();
    const ac = new AbortController();
    loadOrdersAbortRef.current = ac;
    setListLoading(true);
    setListErr("");
    try {
      const params = {};
      if (searchQuery.trim()) params.q = searchQuery.trim();
      if (statusFilter !== "all") params.status = statusFilter;
      const res = await api.listSalesOrders(params, { signal: ac.signal });
      if (ac.signal.aborted) return;
      setOrders(res.orders || []);
      setSelectedIds(new Set());
    } catch (e) {
      if (e?.code === "ERR_CANCELED" || e?.name === "CanceledError" || ac.signal.aborted) {
        return;
      }
      const det = e.response?.data?.detail;
      setListErr(typeof det === "object" ? det.message : (det || "Gagal memuat daftar Pesanan Penjualan"));
    } finally {
      if (!ac.signal.aborted) {
        setListLoading(false);
      }
    }
  }, [searchQuery, statusFilter]);

  // Sync filter/search into URL so browser Back restores list presentation state.
  useEffect(() => {
    if (viewMode !== "list") return;
    const next = new URLSearchParams();
    if (searchQuery.trim()) next.set("q", searchQuery.trim());
    if (statusFilter && statusFilter !== "all") next.set("status", statusFilter);
    const nextStr = next.toString();
    const curStr = searchParams.toString();
    if (nextStr === curStr) return;
    setSearchParams(next, { replace: true });
  }, [searchQuery, statusFilter, viewMode, searchParams, setSearchParams]);

  const poListNavState = (extra = {}) => poNavStateFromList({
    pathname: location.pathname,
    search: location.search,
    extra,
  });

  const onOpenPo = (orderId) => {
    capturePoOpenFromList({
      user,
      orderId,
      pathname: location.pathname,
      search: location.search,
      scrollY: typeof window !== "undefined" ? window.scrollY : undefined,
    });
  };
  useEffect(() => {
    if (viewMode === "create") {
      loadOptions();
    }
  }, [viewMode, loadOptions]);

  useEffect(() => {
    if (viewMode === "list") {
      loadOrders();
    }
    return () => {
      loadOrdersAbortRef.current?.abort();
    };
  }, [viewMode, loadOrders]);

  // When customer changes, handle autofill suggestions for outlet/address/sales/payment_terms
  const handleCustomerChange = (newCustomer) => {
    const hadRepeat = Boolean(repeatSourceIndicator || repeatDesignFiles.length || repeatConfirm);
    clearRepeatState();
    const outlets = options.outlets_by_customer?.[newCustomer] || [];
    let patch = { customer: newCustomer };

    const norm = (s) => (s || "").trim().toLowerCase().replace(/\s+/g, " ");
    const masterMatch = options.master_customers?.find(
      (c) => norm(c.name) === norm(newCustomer)
    );

    if (masterMatch) {
      patch.customer_id = masterMatch.id;
      patch.customer_phone = masterMatch.phone || "";
      if (masterMatch.address) {
        patch.address = masterMatch.address;
        patch.customer_address = masterMatch.address;
      }
      if (masterMatch.pic) {
        patch.recipient = masterMatch.pic;
        patch.customer_pic = masterMatch.pic;
      }
      if (masterMatch.email) patch.customer_email = masterMatch.email;
      if (masterMatch.city) patch.customer_city = masterMatch.city;
    } else {
      patch.customer_id = null;
    }

    if (outlets.length === 1) {
      const o = outlets[0];
      patch = {
        ...patch,
        outlet: o.outlet || "",
        address: o.address || patch.address || "",
        recipient: o.recipient || patch.recipient || "",
        delivery_zone: o.delivery_zone || "",
        sales: o.sales || "",
        payment_terms: o.payment_terms || "",
      };
    }

    setFormData((prev) => {
      // After a Repeat Order was applied, never leave previous customer's copied details.
      const base = hadRepeat
        ? clearRepeatDerivedFormFields(prev, { keepCustomer: true })
        : prev;
      return {
        ...base,
        ...patch,
        po_number: prev.po_number,
        order_date: prev.order_date,
        created_by_user: prev.created_by_user,
        estimated_delivery: hadRepeat ? prev.estimated_delivery : (patch.estimated_delivery ?? prev.estimated_delivery),
        deadline: hadRepeat ? prev.deadline : (patch.deadline ?? prev.deadline),
        expedition: patch.expedition || (hadRepeat ? "Internal fleet" : prev.expedition) || "Internal fleet",
      };
    });
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

  const handleSelectMasterItem = (index, master) => {
    setFormData((prev) => {
      const lines = [...prev.line_items];
      if (!master) {
        lines[index] = {
          ...lines[index],
          name: "",
          item_code: "",
          accurate_item_id: null,
          master_item_id: null,
        };
      } else {
        lines[index] = {
          ...lines[index],
          name: master.item_name || lines[index].name,
          item_code: master.item_code || lines[index].item_code,
          unit: master.unit || lines[index].unit || "pcs",
          accurate_item_id: master.accurate_item_id || null,
          master_item_id: master.id || master.master_item_id || null,
        };
      }
      return { ...prev, line_items: lines };
    });
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
    clearRepeatState();
    setFormData({
      po_number: options.suggested_po_number || "",
      order_date: getTodayString(),
      customer: "",
      customer_id: null,
      customer_phone: "",
      customer_address: "",
      customer_pic: "",
      customer_email: "",
      customer_city: "",
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
    setLineSearchQuery("");
  };

  const handleRepeatOrderClick = async () => {
    const customerId = formData.customer_id;
    if (!customerId) {
      setRepeatMsg("Pilih customer terdaftar terlebih dahulu.");
      return;
    }
    setRepeatMsg("");
    setRepeatBusy(true);
    repeatAbortRef.current?.abort();
    const ac = new AbortController();
    repeatAbortRef.current = ac;
    repeatRequestCustomerIdRef.current = customerId;
    try {
      const data = await api.salesOrderRepeatSource(customerId, { signal: ac.signal });
      if (ac.signal.aborted) return;
      if (repeatRequestCustomerIdRef.current !== customerId) return;
      if (selectedCustomerIdRef.current !== customerId) return;
      if (!data?.found || !data?.source) {
        setRepeatMsg(REPEAT_NO_PREVIOUS_MESSAGE);
        setRepeatConfirm(null);
        return;
      }
      if (String(data.source.customer_id || "").trim() !== String(customerId).trim()) {
        setRepeatMsg(REPEAT_NO_PREVIOUS_MESSAGE);
        return;
      }
      setRepeatConfirm({
        customerId,
        source: data.source,
      });
    } catch (e) {
      if (e?.code === "ERR_CANCELED" || e?.name === "CanceledError" || ac.signal.aborted) {
        return;
      }
      const det = e.response?.data?.detail;
      setRepeatMsg(typeof det === "object" ? det.message : (det || "Gagal memuat pesanan sebelumnya."));
    } finally {
      if (!ac.signal.aborted) setRepeatBusy(false);
    }
  };

  const applyRepeatOrder = () => {
    if (!repeatConfirm?.source) return;
    const customerId = formData.customer_id;
    if (!customerId || repeatConfirm.customerId !== customerId) {
      setRepeatConfirm(null);
      setRepeatMsg(REPEAT_NO_PREVIOUS_MESSAGE);
      return;
    }
    const { formPatch, designFiles, sourcePoNumber } = mapRepeatSourceToFormPatch(
      repeatConfirm.source,
      { customer_id: customerId, customer: formData.customer }
    );
    if (!formPatch) {
      setRepeatConfirm(null);
      setRepeatMsg(REPEAT_NO_PREVIOUS_MESSAGE);
      return;
    }
    setFormData((prev) => ({
      ...prev,
      ...formPatch,
      // Keep new-order identity fields
      po_number: prev.po_number,
      created_by_user: prev.created_by_user,
      customer: prev.customer,
      customer_id: prev.customer_id,
    }));
    setRepeatDesignFiles(designFiles);
    setRepeatSourceIndicator(sourcePoNumber || "");
    setRepeatConfirm(null);
    setRepeatMsg("");
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
    const norm = (s) => (s || "").trim().toLowerCase().replace(/\s+/g, " ");
    const isMaster = options.master_customers?.some((c) => norm(c.name) === norm(formData.customer));
    if (!isMaster && !formData.customer_id) {
      if (!formData.customer_phone?.trim()) {
        setFormErr("Pelanggan baru terdeteksi: No. HP / WhatsApp pelanggan wajib diisi.");
        return;
      }
      if (!formData.address?.trim()) {
        setFormErr("Pelanggan baru terdeteksi: Alamat lengkap pengiriman wajib diisi.");
        return;
      }
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
        design_files: (repeatDesignFiles || [])
          .filter((f) => f?.data_url)
          .map((f) => ({
            name: f.name || "design",
            data_url: f.data_url,
            content_type: f.content_type || undefined,
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
    <div className="sales-order-page penjualan-page penjualan-workspace" data-testid="sales-order-page" style={{ padding: "20px 24px", maxWidth: 1200, margin: "0 auto" }}>
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

          <FormProgress
            percentage={Math.round(([formData.po_number, formData.po_date, formData.customer_name, formData.line_items.some(l => l.product_name && Number(l.quantity) > 0)].filter(Boolean).length / 4) * 100)}
            completed={[formData.po_number, formData.po_date, formData.customer_name, formData.line_items.some(l => l.product_name && Number(l.quantity) > 0)].filter(Boolean).length}
            total={4}
            className="mb-4"
          />

          {/* Card 1: Informasi Header PO */}
          <FormSection
            id="so-header"
            title="Informasi Pesanan (Header)"
            description="Informasi utama pesanan penjualan, pelanggan, dan ketentuan pengiriman."
            status={computeStatus([
              { value: formData.po_number, isRequired: true },
              { value: formData.po_date, isRequired: true },
              { value: formData.customer_name, isRequired: true },
            ])}
          >

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
                {formData.customer?.trim() && (
                  <div style={{ marginTop: 4, fontSize: "0.78rem" }}>
                    {options.master_customers?.some(
                      (c) => (c.name || "").trim().toLowerCase() === formData.customer.trim().toLowerCase()
                    ) || formData.customer_id ? (
                      <span data-testid="badge-customer-registered" style={{ color: "#059669", fontWeight: 500, display: "inline-flex", alignItems: "center", gap: 4 }}>
                        ✓ Pelanggan Terdaftar di Master
                      </span>
                    ) : (
                      <span data-testid="badge-customer-new" style={{ color: "#0284c7", fontWeight: 500, display: "inline-flex", alignItems: "center", gap: 4 }}>
                        + Pelanggan Baru (otomatis masuk ke Data Pelanggan saat PO disimpan)
                      </span>
                    )}
                  </div>
                )}
                <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
                  <button
                    type="button"
                    data-testid="btn-repeat-order"
                    onClick={handleRepeatOrderClick}
                    disabled={!formData.customer_id || repeatBusy || formBusy}
                    title={formData.customer_id ? "Salin detail dari PO valid terakhir customer ini" : "Pilih customer terdaftar terlebih dahulu"}
                    style={{
                      padding: "6px 12px",
                      borderRadius: 6,
                      border: "1px solid #cbd5e1",
                      background: formData.customer_id ? "#f8fafc" : "#f1f5f9",
                      color: formData.customer_id ? "#0f172a" : "#94a3b8",
                      fontWeight: 600,
                      fontSize: "0.82rem",
                      cursor: formData.customer_id && !repeatBusy ? "pointer" : "not-allowed",
                    }}
                  >
                    {repeatBusy ? "Mencari…" : "Repeat Order"}
                  </button>
                  {repeatSourceIndicator ? (
                    <span data-testid="repeat-source-indicator" style={{ fontSize: "0.78rem", color: "#0369a1", fontWeight: 500 }}>
                      Repeat dari PO: {repeatSourceIndicator}
                    </span>
                  ) : null}
                </div>
                {repeatMsg ? (
                  <div data-testid="repeat-empty-message" style={{ marginTop: 6, fontSize: "0.8rem", color: "#b45309", background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 6, padding: "6px 10px" }}>
                    {repeatMsg}
                  </div>
                ) : null}
                {repeatDesignFiles.length > 0 ? (
                  <div data-testid="repeat-design-files" style={{ marginTop: 6, fontSize: "0.78rem", color: "#475569" }}>
                    Design file yang akan disalin: {repeatDesignFiles.map((f) => f.name).join(", ")}
                  </div>
                ) : null}
              </div>

              {/* No. HP / WhatsApp Pelanggan */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  No. HP / WhatsApp Pelanggan
                </label>
                <input
                  type="text"
                  value={formData.customer_phone || ""}
                  onChange={(e) => setFormData((p) => ({ ...p, customer_phone: e.target.value }))}
                  placeholder="mis. 0812-3456-7890"
                  data-testid="input-customer-phone"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
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
          </FormSection>

          {/* Card 2: Line Items */}
          <FormSection
            id="so-lines"
            title="Daftar Line Item (Barang / Jasa)"
            description="Masing-masing baris item memiliki identitas independen (tidak akan dimerge otomatis)."
            status={formData.line_items.some(l => l.product_name && Number(l.quantity) > 0) ? "complete" : "incomplete"}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
              <div style={{ position: "relative", minWidth: 260, maxWidth: 380, flex: "1 1 260px" }}>
                <Search size={16} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "#94a3b8", pointerEvents: "none" }} />
                <input
                  type="text"
                  data-testid="line-items-search-input"
                  value={lineSearchQuery}
                  onChange={(e) => setLineSearchQuery(e.target.value)}
                  placeholder="Cari barang / jasa..."
                  style={{
                    width: "100%",
                    padding: "8px 32px 8px 34px",
                    borderRadius: 6,
                    border: "1px solid #cbd5e1",
                    fontSize: "0.875rem",
                    boxSizing: "border-box",
                  }}
                />
                {lineSearchQuery && (
                  <button
                    type="button"
                    data-testid="btn-clear-line-search"
                    onClick={() => setLineSearchQuery("")}
                    style={{
                      position: "absolute",
                      right: 8,
                      top: "50%",
                      transform: "translateY(-50%)",
                      background: "transparent",
                      border: "none",
                      cursor: "pointer",
                      color: "#94a3b8",
                      padding: 2,
                      display: "flex",
                      alignItems: "center",
                    }}
                    title="Bersihkan pencarian"
                  >
                    <X size={14} />
                  </button>
                )}
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
            </div>

            <div
              data-testid="line-items-table-container"
              style={{
                overflowX: "auto",
                overflowY: "auto",
                minHeight: "380px",
                maxHeight: "560px",
                border: "1px solid #e2e8f0",
                borderRadius: 8,
              }}
            >
              <table style={{ width: "100%", borderCollapse: "separate", borderSpacing: 0, textAlign: "left", fontSize: "0.9rem" }}>
                <thead style={{ position: "sticky", top: 0, zIndex: 2, background: "#f8fafc" }}>
                  <tr style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
                    <th style={{ padding: "10px 8px", width: 40, background: "#f8fafc", borderBottom: "2px solid #e2e8f0" }}>#</th>
                    <th style={{ padding: "10px 8px", minWidth: 220, background: "#f8fafc", borderBottom: "2px solid #e2e8f0" }}>Barang / Jasa <span style={{ color: "#ef4444" }}>*</span></th>
                    <th style={{ padding: "10px 8px", width: 120, background: "#f8fafc", borderBottom: "2px solid #e2e8f0" }}>Kode</th>
                    <th style={{ padding: "10px 8px", width: 130, background: "#f8fafc", borderBottom: "2px solid #e2e8f0" }}>Proses</th>
                    <th style={{ padding: "10px 8px", width: 100, background: "#f8fafc", borderBottom: "2px solid #e2e8f0" }}>Qty <span style={{ color: "#ef4444" }}>*</span></th>
                    <th style={{ padding: "10px 8px", width: 80, background: "#f8fafc", borderBottom: "2px solid #e2e8f0" }}>Satuan</th>
                    <th style={{ padding: "10px 8px", width: 140, background: "#f8fafc", borderBottom: "2px solid #e2e8f0" }}>Warna</th>
                    <th style={{ padding: "10px 8px", minWidth: 160, background: "#f8fafc", borderBottom: "2px solid #e2e8f0" }}>Catatan Item</th>
                    <th style={{ padding: "10px 8px", width: 48, textAlign: "center", background: "#f8fafc", borderBottom: "2px solid #e2e8f0" }}>Aksi</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredLineItems.length === 0 ? (
                    <tr>
                      <td
                        colSpan={9}
                        data-testid="line-items-empty-state"
                        style={{
                          padding: "48px 16px",
                          textAlign: "center",
                          color: "#64748b",
                          fontSize: "0.95rem",
                        }}
                      >
                        Tidak ada barang/jasa yang cocok.
                      </td>
                    </tr>
                  ) : (
                    filteredLineItems.map(({ line, originalIndex }) => (
                      <tr key={originalIndex} data-testid={`line-item-row-${originalIndex}`} style={{ borderBottom: "1px solid #f1f5f9" }}>
                        <td style={{ padding: "10px 8px", color: "#64748b", fontWeight: 600, borderBottom: "1px solid #f1f5f9" }}>{originalIndex + 1}</td>
                        <td style={{ padding: "10px 8px", borderBottom: "1px solid #f1f5f9" }}>
                          <MasterItemSearchSelect
                            testId={`line-item-search-${originalIndex}`}
                            value={
                              line.accurate_item_id || line.master_item_id
                                ? {
                                    accurate_item_id: line.accurate_item_id,
                                    id: line.master_item_id,
                                    master_item_id: line.master_item_id,
                                    item_name: line.name,
                                    item_code: line.item_code,
                                    unit: line.unit,
                                  }
                                : null
                            }
                            onChange={(master) => handleSelectMasterItem(originalIndex, master)}
                            placeholder="Cari nama / kode barang…"
                          />
                          {!line.accurate_item_id && !line.master_item_id && (
                            <input
                              type="text"
                              value={line.name}
                              onChange={(e) => handleLineChange(originalIndex, "name", e.target.value)}
                              placeholder="Atau ketik nama bebas (tanpa master)"
                              data-testid={`line-name-${originalIndex}`}
                              style={{ width: "100%", marginTop: 6, padding: "6px 10px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box" }}
                            />
                          )}
                        </td>
                        <td style={{ padding: "10px 8px", borderBottom: "1px solid #f1f5f9" }}>
                          <input
                            type="text"
                            value={line.item_code || ""}
                            onChange={(e) => handleLineChange(originalIndex, "item_code", e.target.value)}
                            placeholder="Kode"
                            data-testid={`line-code-${originalIndex}`}
                            style={{ width: "100%", padding: "6px 10px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box" }}
                          />
                        </td>
                        <td style={{ padding: "10px 8px", borderBottom: "1px solid #f1f5f9" }}>
                          <select
                            value={line.production_process}
                            onChange={(e) => handleLineChange(originalIndex, "production_process", e.target.value)}
                            data-testid={`line-process-${originalIndex}`}
                            style={{ width: "100%", padding: "6px 8px", border: "1px solid #cbd5e1", borderRadius: 6, background: "#fff" }}
                          >
                            <option value="print">Cetak</option>
                            <option value="non_print">Tidak Cetak</option>
                          </select>
                        </td>
                        <td style={{ padding: "10px 8px", borderBottom: "1px solid #f1f5f9" }}>
                          <input
                            type="number"
                            min="1"
                            value={line.quantity}
                            onChange={(e) => handleLineChange(originalIndex, "quantity", e.target.value)}
                            data-testid={`line-qty-${originalIndex}`}
                            style={{ width: "100%", padding: "6px 10px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box" }}
                            required
                          />
                        </td>
                        <td style={{ padding: "10px 8px", borderBottom: "1px solid #f1f5f9" }}>
                          <input
                            type="text"
                            value={line.unit || "pcs"}
                            onChange={(e) => handleLineChange(originalIndex, "unit", e.target.value)}
                            data-testid={`line-unit-${originalIndex}`}
                            style={{ width: "100%", padding: "6px 8px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box" }}
                          />
                        </td>
                        <td style={{ padding: "10px 8px", borderBottom: "1px solid #f1f5f9" }}>
                          <input
                            type="text"
                            value={line.color || ""}
                            onChange={(e) => handleLineChange(originalIndex, "color", e.target.value)}
                            placeholder="mis. Full color"
                            data-testid={`line-color-${originalIndex}`}
                            style={{ width: "100%", padding: "6px 10px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box" }}
                          />
                        </td>
                        <td style={{ padding: "10px 8px", borderBottom: "1px solid #f1f5f9" }}>
                          <input
                            type="text"
                            value={line.notes || ""}
                            onChange={(e) => handleLineChange(originalIndex, "notes", e.target.value)}
                            placeholder="Catatan..."
                            data-testid={`line-notes-${originalIndex}`}
                            style={{ width: "100%", padding: "6px 10px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box" }}
                          />
                        </td>
                        <td style={{ padding: "10px 8px", textAlign: "center", borderBottom: "1px solid #f1f5f9" }}>
                          <button
                            type="button"
                            onClick={() => removeLineItem(originalIndex)}
                            disabled={formData.line_items.length <= 1}
                            data-testid={`btn-remove-line-${originalIndex}`}
                            style={{ background: "transparent", border: "none", color: formData.line_items.length <= 1 ? "#cbd5e1" : "#ef4444", cursor: formData.line_items.length <= 1 ? "not-allowed" : "pointer", padding: 4 }}
                            title="Hapus baris"
                          >
                            <Trash2 size={16} />
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {/* Total Summary Footer */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 16, padding: "12px 16px", background: "#f8fafc", borderRadius: 8 }}>
              <span style={{ fontSize: "0.9rem", color: "#475569" }}>
                Total Item: <strong>{formData.line_items.length}</strong>
                {lineSearchQuery.trim() && (
                  <span style={{ marginLeft: 8, color: "#64748b" }}>
                    (Menampilkan {filteredLineItems.length} dari {formData.line_items.length})
                  </span>
                )}
              </span>
              <span style={{ fontSize: "1rem", color: "#0f172a" }}>
                Total Quantity: <strong>{totalQty.toLocaleString("id-ID")} pcs</strong>
              </span>
            </div>
          </FormSection>

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
                          <Link
                            to={`/po/${o.id}`}
                            state={poListNavState({ po_number: o.po_number, customer: o.customer })}
                            onClick={() => onOpenPo(o.id)}
                            style={{ color: "#0284c7", textDecoration: "none" }}
                            data-testid={`po-link-${o.po_number}`}
                          >
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
                            <span className="pj-status-badge" style={{ display: "inline-block", padding: "2px 8px", background: "#fee2e2", color: "#991b1b", borderRadius: 12, fontSize: "0.75rem", fontWeight: 600, border: "1.5px solid #fca5a5" }}>
                              Cancelled
                            </span>
                          ) : isHold ? (
                            <span className="pj-status-badge" style={{ display: "inline-block", padding: "2px 8px", background: "#fef3c7", color: "#92400e", borderRadius: 12, fontSize: "0.75rem", fontWeight: 600, border: "1.5px solid #fcd34d" }}>
                              HOLD
                            </span>
                          ) : (
                            <span className="pj-status-badge" style={{ display: "inline-block", padding: "2px 8px", background: o.current_stage >= 14 ? "#dcfce7" : "#e0f2fe", color: o.current_stage >= 14 ? "#166534" : "#075985", borderRadius: 12, fontSize: "0.75rem", fontWeight: 600, border: o.current_stage >= 14 ? "1.5px solid #86efac" : "1.5px solid #7dd3fc" }}>
                              {stageLabel}
                            </span>
                          )}
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "center" }}>
                          <div style={{ display: "flex", justifyContent: "center", gap: 6 }}>
                            <Link
                              to={`/po/${o.id}`}
                              state={poListNavState({ po_number: o.po_number, customer: o.customer })}
                              onClick={() => onOpenPo(o.id)}
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

      {repeatConfirm && (
        <div className="modal-overlay" data-testid="repeat-confirm-modal" style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 80 }}>
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 460, margin: 16 }}>
            <h3 style={{ margin: "0 0 8px" }}>Salin detail dari PO terakhir ini?</h3>
            <dl style={{ margin: "0 0 16px", fontSize: "0.9rem" }}>
              <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
                <dt style={{ color: "#64748b", minWidth: 110 }}>No PO</dt>
                <dd data-testid="repeat-confirm-po" style={{ margin: 0, fontWeight: 700, color: "#0284c7" }}>{repeatConfirm.source?.source_po_number || "—"}</dd>
              </div>
              <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
                <dt style={{ color: "#64748b", minWidth: 110 }}>Customer</dt>
                <dd data-testid="repeat-confirm-customer" style={{ margin: 0 }}>{formData.customer || repeatConfirm.source?.customer || "—"}</dd>
              </div>
              <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
                <dt style={{ color: "#64748b", minWidth: 110 }}>Tanggal</dt>
                <dd data-testid="repeat-confirm-date" style={{ margin: 0 }}>
                  {formatDisplayDate(repeatConfirm.source?.source_order_date || repeatConfirm.source?.source_created_at) || "—"}
                </dd>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <dt style={{ color: "#64748b", minWidth: 110 }}>Jumlah baris</dt>
                <dd data-testid="repeat-confirm-lines" style={{ margin: 0 }}>{repeatConfirm.source?.line_count ?? (repeatConfirm.source?.line_items || []).length}</dd>
              </div>
            </dl>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, flexWrap: "wrap" }}>
              <button
                type="button"
                data-testid="btn-repeat-cancel"
                onClick={() => setRepeatConfirm(null)}
                style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff" }}
              >
                Batal
              </button>
              <button
                type="button"
                data-testid="btn-repeat-confirm"
                onClick={applyRepeatOrder}
                style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#0284c7", color: "#fff", fontWeight: 600 }}
              >
                Gunakan Repeat Order
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
