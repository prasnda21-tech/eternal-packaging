import { Fragment, useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Package,
  Info,
  Plus,
  Search,
  Truck,
  X,
} from "lucide-react";
import { useAuth } from "../lib/auth";
import api from "../lib/api";
import { NAV_KEYS } from "../lib/navAccess";
import CustomerSearchSelect from "../components/CustomerSearchSelect";
import MasterItemSearchSelect from "../components/MasterItemSearchSelect";
import SalesInvoiceDateField from "../components/SalesInvoiceDateField";
import {
  formatOrderRupiah,
  lineSellingSubtotal,
} from "../lib/salesOrderLinePrice";
import {
  STATUS_LABEL,
  PRA_PO_STATUS_FILTERS,
  emptyPraPoLine,
  emptyPraPoForm,
  praPoDocToForm,
  buildPraPoPayload,
  validatePraPoDraft,
  validatePraPoSubmit,
  praPoLineQtyLabel,
} from "../lib/praPoForm";
import "./pesananPenjualan.css";
import "./praPo.css";

export {
  STATUS_LABEL,
  PRA_PO_STATUS_FILTERS,
  emptyPraPoLine,
  emptyPraPoForm,
  praPoDocToForm,
  buildPraPoPayload,
  validatePraPoDraft,
  validatePraPoSubmit,
  praPoLineQtyLabel,
};

function statusClass(status) {
  const st = String(status || "");
  if (st === "verified") return "is-verified";
  if (st === "returned") return "is-returned";
  if (st === "submitted" || st === "resubmitted") return "is-queue";
  if (st === "draft") return "is-draft";
  return "";
}

function PraField({ label, required, error, children, className = "" }) {
  return (
    <label className={`so-create-field${error ? " is-error" : ""}${className ? ` ${className}` : ""}`}>
      <span>
        {label}
        {required ? <span className="so-req"> *</span> : null}
      </span>
      {children}
    </label>
  );
}

export default function PraPoPage() {
  const { hasPerm, user } = useAuth();
  const navigate = useNavigate();
  const { id: routeId } = useParams();
  const [searchParams] = useSearchParams();
  const createIntent = searchParams.get("create") === "1";
  const canCreate = hasPerm("pra_po.create");
  const canVerify = hasPerm("pra_po.verify");
  const canReturn = hasPerm("pra_po.return");
  const canViewAll = hasPerm("pra_po.view_all") || canVerify || canReturn;

  const [viewMode, setViewMode] = useState(() => (routeId || createIntent ? "form" : "list"));
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");
  const [successMsg, setSuccessMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [listLoading, setListLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [form, setForm] = useState(emptyPraPoForm);
  const [createTab, setCreateTab] = useState("items");
  const [returnReason, setReturnReason] = useState("");
  const [masterCustomers, setMasterCustomers] = useState([]);
  const [searchInput, setSearchInput] = useState("");
  const [searchQ, setSearchQ] = useState("");
  const [statusFilter, setStatusFilter] = useState(canViewAll ? "queue" : "all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [listTotal, setListTotal] = useState(0);
  const [statusCounts, setStatusCounts] = useState({
    all: 0, draft: 0, queue: 0, returned: 0, verified: 0,
  });
  const [fieldError, setFieldError] = useState("");

  const uid = String(user?.id || user?.user_id || "");

  const reload = useCallback(async () => {
    setListLoading(true);
    setError("");
    try {
      const params = {
        page,
        page_size: pageSize,
      };
      if (!canViewAll) params.view = "own";
      if (statusFilter && statusFilter !== "all") params.status = statusFilter;
      if (searchQ.trim()) params.q = searchQ.trim();
      const data = await api.listPraPo(params);
      setItems(data.items || []);
      setListTotal(Number(data.total) || 0);
      setStatusCounts({
        all: Number(data.counts?.all) || 0,
        draft: Number(data.counts?.draft) || 0,
        queue: Number(data.counts?.queue) || 0,
        returned: Number(data.counts?.returned) || 0,
        verified: Number(data.counts?.verified) || 0,
      });
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "Gagal memuat Pra PO");
    } finally {
      setListLoading(false);
    }
  }, [canViewAll, page, pageSize, statusFilter, searchQ]);

  const loadOptions = useCallback(async () => {
    try {
      const data = await api.salesOrderOptions();
      setMasterCustomers(Array.isArray(data?.master_customers) ? data.master_customers : []);
    } catch {
      setMasterCustomers([]);
    }
  }, []);

  useEffect(() => {
    if (viewMode !== "list") return undefined;
    reload();
    return undefined;
  }, [reload, viewMode]);

  useEffect(() => {
    loadOptions();
  }, [loadOptions]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      const next = String(searchInput || "").trim();
      setSearchQ((prev) => {
        if (prev === next) return prev;
        setPage(1);
        return next;
      });
    }, 300);
    return () => window.clearTimeout(handle);
  }, [searchInput]);

  useEffect(() => {
    if (!routeId) {
      if (createIntent && canCreate) {
        setViewMode("form");
        setSelected(null);
        return undefined;
      }
      setViewMode("list");
      setSelected(null);
      return undefined;
    }
    let cancelled = false;
    setViewMode("form");
    setBusy(true);
    api.getPraPo(routeId)
      .then((d) => {
        if (cancelled) return;
        setSelected(d.pra_po);
        setForm(praPoDocToForm(d.pra_po));
        setReturnReason("");
        setFieldError("");
        setSuccessMsg("");
      })
      .catch((e) => {
        if (!cancelled) setError(e?.response?.data?.detail || e?.message || "Gagal membuka Pra PO");
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => { cancelled = true; };
  }, [routeId, createIntent, canCreate]);

  const isOwner = selected && String(selected.created_by_user_id || "") === uid;
  const canEditForm = Boolean(
    canCreate
    && (
      !selected
      || (isOwner && (selected.status === "draft" || selected.status === "returned"))
    ),
  );
  const canSubmitForm = Boolean(
    selected
    && isOwner
    && (selected.status === "draft" || selected.status === "returned"),
  );
  const showAdminActions = Boolean(
    selected
    && (selected.status === "submitted" || selected.status === "resubmitted"),
  );

  const pageCount = Math.max(1, Math.ceil(listTotal / pageSize) || 1);
  const listRangeStart = listTotal === 0 ? 0 : (page - 1) * pageSize + 1;
  const listRangeEnd = Math.min(listTotal, page * pageSize);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const newCustomer = Boolean(form.customer?.trim()) && !form.customer_id;

  const openCreate = () => {
    setSelected(null);
    setForm(emptyPraPoForm());
    setCreateTab("items");
    setFieldError("");
    setSuccessMsg("");
    setError("");
    setViewMode("form");
    navigate("/penjualan/pra-po?create=1", { replace: true });
  };

  const leaveForm = () => {
    setViewMode("list");
    setSelected(null);
    setForm(emptyPraPoForm());
    setFieldError("");
    setSuccessMsg("");
    navigate("/penjualan/pra-po", { replace: true });
    reload();
  };

  const openRow = async (row) => {
    navigate(`/penjualan/pra-po/${row.id}`);
  };

  const handleCustomerSelect = (customer) => {
    if (!customer) return;
    setForm((prev) => ({
      ...prev,
      customer: customer.name || "",
      customer_id: customer.id || null,
      customer_phone: customer.phone || prev.customer_phone || "",
      address: customer.address || prev.address || "",
    }));
  };

  const handleCustomerTextChange = (text) => {
    setForm((prev) => ({
      ...prev,
      customer: text,
      customer_id: null,
    }));
  };

  const handleCustomerClear = () => {
    setForm((prev) => ({
      ...prev,
      customer: "",
      customer_id: null,
      customer_phone: "",
      address: "",
    }));
  };

  const handleSelectMasterItem = (index, master) => {
    setForm((prev) => {
      const lines = [...prev.line_items];
      const current = lines[index];
      if (!current) return prev;
      if (!master) {
        lines[index] = {
          ...current,
          name: "",
          item_code: "",
          accurate_item_id: null,
          master_item_id: null,
          item_type: "",
        };
      } else {
        const masterProcess = master.production_process === "non_print"
          ? "non_print"
          : master.production_process === "print"
            ? "print"
            : null;
        lines[index] = {
          ...current,
          name: master.item_name || master.name || current.name,
          item_code: master.item_code || current.item_code,
          unit: master.unit || current.unit || "pcs",
          accurate_item_id: master.accurate_item_id || null,
          master_item_id: master.id || master.master_item_id || null,
          item_type: String(master.item_type || "").trim().toUpperCase() || "",
          ...(masterProcess ? { production_process: masterProcess } : {}),
        };
      }
      return { ...prev, line_items: lines };
    });
  };

  const handleLineChange = (index, field, value) => {
    setForm((prev) => {
      const lines = [...prev.line_items];
      if (!lines[index]) return prev;
      lines[index] = { ...lines[index], [field]: value };
      return { ...prev, line_items: lines };
    });
  };

  const addLineItem = () => {
    setForm((prev) => ({ ...prev, line_items: [...prev.line_items, emptyPraPoLine()] }));
  };

  const removeLineItem = (index) => {
    setForm((prev) => {
      if (prev.line_items.length <= 1) return prev;
      return { ...prev, line_items: prev.line_items.filter((_, i) => i !== index) };
    });
  };

  const saveDraft = async () => {
    const err = validatePraPoDraft(form);
    if (err) {
      setFieldError(err);
      return;
    }
    setBusy(true);
    setFieldError("");
    setError("");
    try {
      const payload = buildPraPoPayload(form);
      let doc;
      if (selected?.id && canEditForm) {
        const d = await api.updatePraPo(selected.id, payload);
        doc = d.pra_po;
      } else {
        const d = await api.createPraPo(payload);
        doc = d.pra_po;
      }
      setSelected(doc);
      setForm(praPoDocToForm(doc));
      setSuccessMsg("Draft Pra PO tersimpan.");
      await reload();
      navigate(`/penjualan/pra-po/${doc.id}`, { replace: true });
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "Gagal menyimpan draft");
    } finally {
      setBusy(false);
    }
  };

  const submitPraPo = async () => {
    const err = validatePraPoSubmit(form);
    if (err) {
      setFieldError(err);
      if (/HP|Alamat/i.test(err)) setCreateTab("ship");
      else if (/Master|Harga|Quantity|Satuan|Baris/i.test(err)) setCreateTab("items");
      return;
    }
    setBusy(true);
    setFieldError("");
    setError("");
    try {
      const payload = buildPraPoPayload(form);
      let pra = selected;
      if (pra?.id && canEditForm) {
        const saved = await api.updatePraPo(pra.id, payload);
        pra = saved.pra_po;
      } else if (!pra?.id) {
        const created = await api.createPraPo(payload);
        pra = created.pra_po;
        navigate(`/penjualan/pra-po/${pra.id}`, { replace: true });
      }
      const d = await api.submitPraPo(pra.id);
      setSelected(d.pra_po);
      setForm(praPoDocToForm(d.pra_po));
      setSuccessMsg(d.pra_po.status === "resubmitted" ? "Pra PO diajukan ulang." : "Pra PO diajukan.");
      await reload();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "Gagal mengajukan");
    } finally {
      setBusy(false);
    }
  };

  const onReturn = async () => {
    if (!selected?.id) return;
    if (!String(returnReason || "").trim()) {
      setFieldError("Alasan pengembalian wajib diisi.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const d = await api.returnPraPo(selected.id, { reason: returnReason });
      setSelected(d.pra_po);
      setForm(praPoDocToForm(d.pra_po));
      setReturnReason("");
      setSuccessMsg("Pra PO dikembalikan ke CRM.");
      await reload();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "Gagal mengembalikan");
    } finally {
      setBusy(false);
    }
  };

  const onVerify = async () => {
    if (!selected?.id) return;
    setBusy(true);
    setError("");
    try {
      const d = await api.verifyPraPo(selected.id);
      setSelected(d.pra_po);
      setForm(praPoDocToForm(d.pra_po));
      setSuccessMsg("Pra PO disetujui. Pesanan Penjualan dibuat.");
      await reload();
      if (d.order?.id) {
        navigate(`/po/${d.order.id}`);
      }
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "Gagal menyetujui");
    } finally {
      setBusy(false);
    }
  };

  if (viewMode === "form") {
    const title = selected?.number
      ? (canEditForm ? "Edit Pra PO" : "Detail Pra PO")
      : "Buat Pra PO";
    return (
      <div
        className="sales-order-page penjualan-page penjualan-workspace so-create-workspace ux90-so-create pra-po-page"
        data-testid="pra-po-page"
        data-mode="form"
      >
        <form
          className="so-create-form"
          noValidate
          data-create-tab={createTab}
          onSubmit={(e) => {
            e.preventDefault();
            if (canEditForm) saveDraft();
          }}
          data-testid="pra-po-create-form"
        >
          <header className="so-create-header" data-testid="pra-po-create-header">
            <button type="button" className="so-create-back" onClick={leaveForm} data-testid="pra-po-back-list">
              <ArrowLeft size={16} /> Kembali
            </button>
            <div className="so-create-heading">
              <div>
                <h1 data-testid="pra-po-form-title">{title}</h1>
                <p>
                  {selected?.number ? (
                    <>
                      Nomor <strong data-testid="pra-po-number">{selected.number}</strong>
                      {" · "}
                      <span className={`pra-po-status-badge ${statusClass(selected.status)}`} data-testid="pra-po-status-badge">
                        {STATUS_LABEL[selected.status] || selected.status}
                      </span>
                    </>
                  ) : (
                    "Draft Pra PO CRM — diverifikasi Admin sebelum menjadi Pesanan Penjualan."
                  )}
                </p>
              </div>
            </div>
          </header>

          {(error || fieldError) ? (
            <div className="so-create-banner is-error" data-testid="pra-po-error" role="alert">
              <AlertCircle size={16} />
              <span>{fieldError || (typeof error === "string" ? error : JSON.stringify(error))}</span>
            </div>
          ) : null}
          {successMsg ? (
            <div className="so-create-banner is-success" data-testid="pra-po-success">
              <CheckCircle2 size={16} />
              <span>{successMsg}</span>
            </div>
          ) : null}

          {selected?.return_reason ? (
            <div className="so-create-banner is-error" data-testid="pra-po-return-reason" role="status">
              <AlertCircle size={16} />
              <span>Alasan pengembalian: {selected.return_reason}</span>
            </div>
          ) : null}

          <div className="so-create-stage" data-scroll-owner="form" data-testid="pra-po-create-scroll">
            <div className="ux90-so-maincol">
              <section className="so-create-main">
                <div className="ux90-txn-header ux90-so-txn-header so-sum-row" data-testid="pra-po-main-fields">
                  <div className="so-sum-chip is-customer is-editing ux90-txn-field">
                    <PraField label="Pelanggan" required error={/Customer|Pelanggan/i.test(fieldError)}>
                      <div className="so-sum-editor">
                        {canEditForm ? (
                          <CustomerSearchSelect
                            customers={masterCustomers}
                            value={form.customer}
                            selectedId={form.customer_id}
                            onTextChange={handleCustomerTextChange}
                            onSelectCustomer={handleCustomerSelect}
                            onClear={handleCustomerClear}
                            disabled={busy}
                            required
                            testId="pra-po-customer"
                          />
                        ) : (
                          <input className="so-line-input" value={form.customer} readOnly data-testid="pra-po-customer" />
                        )}
                      </div>
                    </PraField>
                  </div>
                  <div className="so-sum-chip is-editing so-sum-chip-date ux90-txn-field">
                    <PraField label="Tanggal">
                      <div className="so-sum-editor">
                        {canEditForm ? (
                          <SalesInvoiceDateField
                            valueIso={form.order_date}
                            onChange={(iso) => setForm((p) => ({ ...p, order_date: iso }))}
                            testId="pra-po-order-date"
                            calendarLabel="Kalender tanggal Pra PO"
                          />
                        ) : (
                          <input className="so-line-input" value={form.order_date || ""} readOnly />
                        )}
                      </div>
                    </PraField>
                  </div>
                </div>

                {form.customer?.trim() ? (
                  <div className="so-create-note">
                    {form.customer_id ? (
                      <span data-testid="pra-po-badge-customer-registered">✓ Pelanggan Terdaftar di Master</span>
                    ) : (
                      <span data-testid="pra-po-badge-customer-new">+ Pelanggan Baru — isi HP dan alamat di tab Pengiriman</span>
                    )}
                  </div>
                ) : null}

                <div className="ux90-so-doc-body">
                  <nav className="ux90-so-vtabs" role="tablist" aria-label="Bagian Pra PO">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={createTab === "items"}
                      className={createTab === "items" ? "is-active" : ""}
                      data-testid="pra-po-tab-items"
                      onClick={() => setCreateTab("items")}
                    >
                      <Package size={18} aria-hidden="true" />
                      <span>Rincian</span>
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={createTab === "info"}
                      className={createTab === "info" ? "is-active" : ""}
                      data-testid="pra-po-tab-info"
                      onClick={() => setCreateTab("info")}
                    >
                      <Info size={18} aria-hidden="true" />
                      <span>Info</span>
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={createTab === "ship"}
                      className={createTab === "ship" ? "is-active" : ""}
                      data-testid="pra-po-tab-ship"
                      onClick={() => setCreateTab("ship")}
                    >
                      <Truck size={18} aria-hidden="true" />
                      <span>Pengiriman</span>
                      {newCustomer && (!String(form.customer_phone || "").trim() || !String(form.address || "").trim()) ? (
                        <span className="ux90-req" title="HP/alamat wajib untuk pelanggan baru">*</span>
                      ) : null}
                    </button>
                  </nav>

                  <div className="ux90-so-pane">
                    <div
                      className="ux90-so-pane-panel ux90-so-pane-items so-create-lines so-items-only"
                      data-pane="items"
                      hidden={createTab !== "items"}
                    >
                      <div className="so-create-line-toolbar">
                        <div className="so-create-line-heading">
                          <h2>Rincian Barang <span className="so-req">*</span></h2>
                          <span data-testid="pra-po-line-count">{form.line_items.length} item</span>
                        </div>
                        {canEditForm ? (
                          <div className="so-create-line-actions">
                            <button type="button" className="so-create-add" onClick={addLineItem} data-testid="pra-po-add-line">
                              <Plus size={14} /> Tambah barang
                            </button>
                          </div>
                        ) : null}
                      </div>
                      <div className="so-create-lines-scroll" data-testid="pra-po-lines-table">
                        <table className="so-create-lines-table">
                          <thead>
                            <tr>
                              <th className="so-col-no">No</th>
                              <th className="so-col-name">Produk <span className="so-req">*</span></th>
                              <th className="so-col-warna">Varian / Warna</th>
                              <th className="so-col-proses">Proses</th>
                              <th className="so-col-qty">Qty <span className="so-req">*</span></th>
                              <th className="so-col-unit">Satuan</th>
                              <th className="so-col-price">Harga Jual <span className="so-req">*</span></th>
                              <th className="so-col-sub">Subtotal</th>
                              <th className="so-col-action">Aksi</th>
                            </tr>
                          </thead>
                          <tbody>
                            {form.line_items.map((line, index) => {
                              const subtotal = lineSellingSubtotal(line.quantity, line.unit_price);
                              return (
                                <Fragment key={line.rowKey || line.line_id || index}>
                                  <tr className="so-line-main" data-testid={`pra-po-line-row-${index}`}>
                                    <td className="so-col-no">{index + 1}</td>
                                    <td className="so-col-name">
                                      <div className="so-line-product-body">
                                        {canEditForm ? (
                                          <MasterItemSearchSelect
                                            testId={`pra-po-line-search-${index}`}
                                            value={
                                              line.accurate_item_id || line.master_item_id
                                                ? {
                                                  accurate_item_id: line.accurate_item_id,
                                                  id: line.master_item_id,
                                                  master_item_id: line.master_item_id,
                                                  item_name: line.name,
                                                  item_code: line.item_code,
                                                  unit: line.unit,
                                                  item_type: line.item_type || "",
                                                }
                                                : null
                                            }
                                            onChange={(master) => handleSelectMasterItem(index, master)}
                                            placeholder="Cari nama / kode barang…"
                                            fetchItems={(params) => api.salesCatalogProducts(params)}
                                          />
                                        ) : (
                                          <div data-testid={`pra-po-line-name-${index}`}>
                                            <strong>{line.name}</strong>
                                            {line.item_code ? <div className="so-line-code">{line.item_code}</div> : null}
                                          </div>
                                        )}
                                        {line.item_code && canEditForm ? (
                                          <div className="so-line-code" data-testid={`pra-po-line-code-${index}`}>{line.item_code}</div>
                                        ) : null}
                                        {/* IDs intentionally never rendered for CRM */}
                                      </div>
                                    </td>
                                    <td className="so-col-warna">
                                      <input
                                        type="text"
                                        className="so-line-input"
                                        value={line.color || ""}
                                        onChange={(e) => handleLineChange(index, "color", e.target.value)}
                                        placeholder="Warna"
                                        disabled={!canEditForm}
                                        data-testid={`pra-po-line-color-${index}`}
                                      />
                                    </td>
                                    <td className="so-col-proses">
                                      <select
                                        className="so-line-input"
                                        value={line.production_process || "print"}
                                        onChange={(e) => handleLineChange(index, "production_process", e.target.value)}
                                        disabled={!canEditForm}
                                        data-testid={`pra-po-line-process-${index}`}
                                        aria-label="Proses"
                                      >
                                        <option value="print">Cetak</option>
                                        <option value="non_print">Tidak Cetak</option>
                                      </select>
                                    </td>
                                    <td className="so-col-qty">
                                      <input
                                        type="number"
                                        min="1"
                                        className="so-line-input"
                                        value={line.quantity}
                                        onChange={(e) => handleLineChange(index, "quantity", e.target.value)}
                                        disabled={!canEditForm}
                                        data-testid={`pra-po-line-qty-${index}`}
                                        required
                                      />
                                    </td>
                                    <td className="so-col-unit">
                                      <input
                                        type="text"
                                        className="so-line-input"
                                        value={line.unit || "pcs"}
                                        onChange={(e) => handleLineChange(index, "unit", e.target.value)}
                                        disabled={!canEditForm}
                                        data-testid={`pra-po-line-unit-${index}`}
                                      />
                                    </td>
                                    <td className="so-col-price">
                                      <input
                                        type="text"
                                        inputMode="decimal"
                                        className="so-line-input so-price-input"
                                        value={line.unit_price ?? ""}
                                        onChange={(e) => handleLineChange(index, "unit_price", e.target.value)}
                                        placeholder="Harga"
                                        disabled={!canEditForm}
                                        data-testid={`pra-po-line-price-${index}`}
                                      />
                                    </td>
                                    <td className="so-col-sub" data-testid={`pra-po-line-subtotal-${index}`}>
                                      {formatOrderRupiah(subtotal)}
                                    </td>
                                    <td className="so-col-action">
                                      {canEditForm ? (
                                        <button
                                          type="button"
                                          onClick={() => removeLineItem(index)}
                                          disabled={form.line_items.length <= 1}
                                          data-testid={`pra-po-remove-line-${index}`}
                                          aria-label="Hapus baris"
                                        >
                                          Hapus
                                        </button>
                                      ) : null}
                                    </td>
                                  </tr>
                                </Fragment>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    <div
                      className="ux90-so-pane-panel ux90-so-pane-info so-create-extra so-info-only"
                      data-pane="info"
                      hidden={createTab !== "info"}
                      data-testid="pra-po-info-pane"
                    >
                      <PraField label="Catatan Pesanan">
                        <textarea
                          className="so-line-input so-create-address"
                          rows={3}
                          value={form.notes || ""}
                          onChange={(e) => setForm((p) => ({ ...p, notes: e.target.value }))}
                          disabled={!canEditForm}
                          data-testid="pra-po-notes"
                        />
                      </PraField>
                      <PraField label="Outlet / Cabang">
                        <input
                          className="so-line-input"
                          value={form.outlet || ""}
                          onChange={(e) => setForm((p) => ({ ...p, outlet: e.target.value }))}
                          disabled={!canEditForm}
                          data-testid="pra-po-outlet"
                        />
                      </PraField>
                      <PraField label="Tenaga Penjual (Sales)">
                        <input
                          className="so-line-input"
                          value={form.sales || ""}
                          onChange={(e) => setForm((p) => ({ ...p, sales: e.target.value }))}
                          disabled={!canEditForm}
                          data-testid="pra-po-sales"
                        />
                      </PraField>
                      <PraField label="Termin Pembayaran">
                        <input
                          className="so-line-input"
                          value={form.payment_terms || ""}
                          onChange={(e) => setForm((p) => ({ ...p, payment_terms: e.target.value }))}
                          disabled={!canEditForm}
                          data-testid="pra-po-payment-terms"
                        />
                      </PraField>
                    </div>

                    <div
                      className="ux90-so-pane-panel ux90-so-pane-ship so-create-extra"
                      data-pane="ship"
                      hidden={createTab !== "ship"}
                      data-testid="pra-po-ship-pane"
                    >
                      <PraField
                        label="No. HP / WhatsApp"
                        required={newCustomer}
                        error={/HP|WhatsApp/i.test(fieldError)}
                      >
                        <input
                          className="so-line-input"
                          value={form.customer_phone || ""}
                          onChange={(e) => setForm((p) => ({ ...p, customer_phone: e.target.value }))}
                          disabled={!canEditForm}
                          data-testid="pra-po-customer-phone"
                        />
                      </PraField>
                      <PraField
                        label="Alamat Lengkap Pengiriman"
                        required={newCustomer}
                        error={/Alamat/i.test(fieldError)}
                        className="so-create-field-address"
                      >
                        <textarea
                          className="so-line-input so-create-address"
                          rows={3}
                          value={form.address || ""}
                          onChange={(e) => setForm((p) => ({ ...p, address: e.target.value }))}
                          disabled={!canEditForm}
                          data-testid="pra-po-address"
                        />
                      </PraField>
                      <PraField label="Penerima (PIC)">
                        <input
                          className="so-line-input"
                          value={form.recipient || ""}
                          onChange={(e) => setForm((p) => ({ ...p, recipient: e.target.value }))}
                          disabled={!canEditForm}
                          data-testid="pra-po-recipient"
                        />
                      </PraField>
                      <PraField label="Zona Kirim">
                        <input
                          className="so-line-input"
                          value={form.delivery_zone || ""}
                          onChange={(e) => setForm((p) => ({ ...p, delivery_zone: e.target.value }))}
                          disabled={!canEditForm}
                          data-testid="pra-po-delivery-zone"
                        />
                      </PraField>
                      <PraField label="Ekspedisi / Pengiriman">
                        <input
                          className="so-line-input"
                          value={form.expedition || ""}
                          onChange={(e) => setForm((p) => ({ ...p, expedition: e.target.value }))}
                          disabled={!canEditForm}
                          data-testid="pra-po-expedition"
                        />
                      </PraField>
                      <PraField label="Tanggal Pengiriman">
                        {canEditForm ? (
                          <SalesInvoiceDateField
                            valueIso={form.deadline || ""}
                            onChange={(iso) => setForm((p) => ({ ...p, deadline: iso, estimated_delivery: iso }))}
                            testId="pra-po-deadline"
                            calendarLabel="Kalender pengiriman"
                          />
                        ) : (
                          <input className="so-line-input" value={form.deadline || ""} readOnly />
                        )}
                      </PraField>
                    </div>
                  </div>
                </div>
              </section>
            </div>
          </div>

          <div className="so-create-actionbar" data-testid="pra-po-actionbar">
            <button type="button" className="so-create-back" onClick={leaveForm} data-testid="pra-po-cancel">
              Kembali
            </button>
            <div className="pra-po-actionbar-right">
              {canEditForm ? (
                <button type="button" className="so-create-auto" onClick={saveDraft} disabled={busy} data-testid="pra-po-create-submit">
                  Simpan Draft
                </button>
              ) : null}
              {canSubmitForm || (canCreate && !selected) ? (
                <button
                  type="button"
                  className="so-create-save"
                  onClick={submitPraPo}
                  disabled={busy}
                  data-testid="pra-po-submit"
                >
                  {selected?.status === "returned" ? "Ajukan Ulang" : "Ajukan Pra PO"}
                </button>
              ) : null}
              {showAdminActions && canVerify ? (
                <button type="button" className="so-create-save" onClick={onVerify} disabled={busy} data-testid="pra-po-verify">
                  Setujui → Pesanan Penjualan
                </button>
              ) : null}
              {showAdminActions && canReturn ? (
                <>
                  <input
                    className="so-line-input pra-po-return-input"
                    placeholder="Alasan pengembalian (wajib)"
                    value={returnReason}
                    onChange={(e) => setReturnReason(e.target.value)}
                    data-testid="pra-po-return-input"
                  />
                  <button type="button" className="so-create-auto" onClick={onReturn} disabled={busy} data-testid="pra-po-return">
                    Kembalikan
                  </button>
                </>
              ) : null}
              {selected?.sales_order_id ? (
                <button
                  type="button"
                  className="so-create-auto"
                  onClick={() => navigate(`/po/${selected.sales_order_id}`)}
                  data-testid="pra-po-open-so"
                >
                  Buka {selected.sales_order_po_number || "Pesanan"}
                </button>
              ) : null}
            </div>
          </div>
        </form>
        <span hidden>{NAV_KEYS.praPo}</span>
      </div>
    );
  }

  return (
    <div
      className="sales-order-page penjualan-page penjualan-workspace so-list-workspace pra-po-page"
      data-testid="pra-po-page"
      data-mode="list"
    >
      <div className="so-list" data-testid="pra-po-list">
        <header className="so-list-header">
          <div className="so-list-heading">
            <h1>Pra PO</h1>
            <p>
              CRM mengajukan Pra PO. Admin memverifikasi sebelum menjadi Pesanan Penjualan.
              {" "}
              <span data-testid="pra-po-list-count">
                {listTotal === 0
                  ? "0 dokumen"
                  : `${listRangeStart}–${listRangeEnd} dari ${listTotal.toLocaleString("id-ID")} dokumen`}
              </span>
            </p>
          </div>
          <div className="so-list-header-actions">
            <button type="button" className="so-list-refresh" onClick={reload} disabled={listLoading} data-testid="pra-po-refresh">
              Muat ulang
            </button>
            {canCreate ? (
              <button type="button" className="so-list-create" onClick={openCreate} data-testid="pra-po-btn-create">
                <Plus size={14} /> Buat Pra PO
              </button>
            ) : null}
          </div>
        </header>

        <div className="so-list-tabs" role="tablist" aria-label="Filter status Pra PO">
          {PRA_PO_STATUS_FILTERS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={statusFilter === tab.id}
              className={statusFilter === tab.id ? "is-active" : ""}
              data-testid={`pra-po-filter-${tab.id}`}
              onClick={() => {
                setStatusFilter(tab.id);
                setPage(1);
              }}
            >
              {tab.label}
              <span className="so-tab-count">{(statusCounts[tab.id] || 0).toLocaleString("id-ID")}</span>
            </button>
          ))}
        </div>

        <div className="so-list-toolbar">
          <div className="so-list-search">
            <Search size={16} />
            <input
              type="text"
              placeholder="Cari nomor Pra PO atau pelanggan…"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              data-testid="pra-po-search"
            />
            {searchInput ? (
              <button
                type="button"
                onClick={() => {
                  setSearchInput("");
                  setSearchQ("");
                  setPage(1);
                }}
                aria-label="Hapus pencarian"
                data-testid="pra-po-search-clear"
              >
                <X size={14} />
              </button>
            ) : null}
          </div>
        </div>

        {error ? (
          <div className="so-list-error" data-testid="pra-po-error" role="alert">
            <AlertCircle size={16} />
            <span>{typeof error === "string" ? error : JSON.stringify(error)}</span>
          </div>
        ) : null}

        <div className="so-list-table-wrap">
          <table className="so-list-table" data-testid="pra-po-table">
            <thead>
              <tr>
                <th>Nomor</th>
                <th>Customer</th>
                <th>Status</th>
                <th>Barang / Qty</th>
                <th>SO Terkait</th>
                <th>Diperbarui</th>
                <th>Aksi</th>
              </tr>
            </thead>
            <tbody>
              {listLoading ? (
                <tr><td colSpan={7} className="muted">Memuat…</td></tr>
              ) : items.length === 0 ? (
                <tr><td colSpan={7} className="muted" data-testid="pra-po-empty">Belum ada Pra PO.</td></tr>
              ) : items.map((row) => (
                <tr key={row.id} data-testid={`pra-po-row-${row.id}`}>
                  <td>{row.number}</td>
                  <td>{row.customer}</td>
                  <td>
                    <span className={`pra-po-status-badge ${statusClass(row.status)}`}>
                      {STATUS_LABEL[row.status] || row.status}
                    </span>
                  </td>
                  <td>{praPoLineQtyLabel(row)}</td>
                  <td>{row.sales_order_po_number || "—"}</td>
                  <td>{row.updated_at ? String(row.updated_at).slice(0, 16).replace("T", " ") : "—"}</td>
                  <td>
                    <button type="button" className="ghost sm" onClick={() => openRow(row)} data-testid={`pra-po-open-${row.id}`}>
                      {canViewAll && (row.status === "submitted" || row.status === "resubmitted") ? "Verifikasi" : "Buka"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <footer className="so-list-pager" data-testid="pra-po-pagination">
          <span data-testid="pra-po-page-range">
            {listTotal === 0
              ? "Tidak ada data"
              : `Menampilkan ${listRangeStart}–${listRangeEnd} dari ${listTotal.toLocaleString("id-ID")}`}
          </span>
          <label>
            Baris per halaman
            <select
              data-testid="pra-po-page-size"
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value) || 50);
                setPage(1);
              }}
            >
              {[25, 50, 100].map((size) => (
                <option key={size} value={size}>{size}</option>
              ))}
            </select>
          </label>
          <div className="so-list-pages">
            <button
              type="button"
              data-testid="pra-po-page-prev"
              disabled={page <= 1 || listLoading}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
            >
              Sebelumnya
            </button>
            <span data-testid="pra-po-page-indicator">
              Halaman {page} / {pageCount}
            </span>
            <button
              type="button"
              data-testid="pra-po-page-next"
              disabled={page >= pageCount || listLoading || listTotal === 0}
              onClick={() => setPage((value) => value + 1)}
            >
              Berikutnya
            </button>
          </div>
        </footer>
      </div>
      {canViewAll ? <span hidden data-testid="pra-po-queue" /> : null}
      <span hidden>{NAV_KEYS.praPo}</span>
    </div>
  );
}
