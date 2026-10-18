import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate, useParams } from "react-router-dom";
import { Building2, PackagePlus, UserPlus, Users } from "lucide-react";
import MasterItemSearchSelect from "../components/MasterItemSearchSelect";
import ProductRequestCustomerSearch from "../components/ProductRequestCustomerSearch";
import { useAuth } from "../lib/auth";
import api from "../lib/api";
import { armProductRequestBeforeUnload, setProductRequestLeaveGuard } from "../lib/productRequestLeaveGuard";
import {
  PRODUCT_REQUEST_LIST_PATH,
  PRODUCT_REQUEST_NEW_PATH,
  canCreateProductRequest,
  canFeedbackProductRequest,
  canUpdateProductRequestStatus,
} from "../lib/navAccess";
import {
  CUSTOMER_MASTER,
  CUSTOMER_NEW,
  EMPTY_FORM,
  PRODUCT_MASTER,
  PRODUCT_REQUESTED,
  clearCustomerMode,
  clearProductMode,
  completenessCount,
  customerSourceLabel,
  formFromRequest,
  formatError,
  isPostSubmitStatus,
  productRequestFormsEqual,
  productSourceLabel,
  progressSteps,
  requiredComplete,
  selectMasterCustomer,
  selectMasterProduct,
  statusLabel,
  toPayload,
} from "../lib/productRequest";
import ProductRequestDetail from "../components/ProductRequestDetail";
import "./productRequest.css";

export default function ProductRequestForm() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user, hasPerm } = useAuth();
  const canCreate = canCreateProductRequest(hasPerm);
  const canFeedback = canFeedbackProductRequest(hasPerm);
  const canUpdateStatus = canUpdateProductRequestStatus(hasPerm);
  const [form, setForm] = useState(EMPTY_FORM);
  const [context, setContext] = useState(null);
  const [requestId, setRequestId] = useState(id || null);
  const [receipt, setReceipt] = useState(null);
  const [readonly, setReadonly] = useState(null);
  const [activities, setActivities] = useState([]);
  const [workflowBusy, setWorkflowBusy] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState({ type: "", text: "" });
  const [confirmLeave, setConfirmLeave] = useState(false);
  const baselineRef = useRef(EMPTY_FORM);
  const formRef = useRef(form);
  const allowLeaveRef = useRef(false);
  const pendingLeaveRef = useRef(null);
  const stayPathRef = useRef("/penjualan/permintaan-produk/baru");
  const stayStateRef = useRef(null);
  formRef.current = form;
  stayPathRef.current = id
    ? `/penjualan/permintaan-produk/${id}`
    : "/penjualan/permintaan-produk/baru";
  if (typeof window !== "undefined") stayStateRef.current = window.history.state;

  const adoptForm = (next) => {
    baselineRef.current = next;
    formRef.current = next;
    setForm(next);
  };

  useEffect(() => {
    let cancelled = false;
    api.productRequestContext()
      .then((data) => { if (!cancelled) setContext(data.context); })
      .catch(() => { if (!cancelled) setContext(null); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!id) {
      setRequestId(null);
      setReceipt(null);
      setReadonly(null);
      setActivities([]);
      adoptForm(EMPTY_FORM);
      return undefined;
    }
    let cancelled = false;
    setBusy("load");
    Promise.all([
      api.getProductRequest(id),
      api.listProductRequestActivities(id).catch(() => ({ activities: [] })),
    ])
      .then(([data, activityRes]) => {
        if (cancelled) return;
        const request = data.request;
        if (request?.receipt_only) {
          setReceipt(request);
          setReadonly(null);
          setActivities([]);
          return;
        }
        if (isPostSubmitStatus(request?.status)) {
          setReadonly(request);
          setActivities(activityRes.activities || []);
          setReceipt(null);
          return;
        }
        setRequestId(request.id);
        setActivities([]);
        adoptForm(formFromRequest(request));
      })
      .catch((err) => setMessage({ type: "error", text: formatError(err) }))
      .finally(() => { if (!cancelled) setBusy(""); });
    return () => { cancelled = true; };
  }, [id]);

  const editing = !receipt && !readonly;
  useEffect(() => {
    if (!editing) return undefined;

    const dirty = () => !productRequestFormsEqual(formRef.current, baselineRef.current);

    const destinationFrom = (node) => {
      if (!node?.closest) return null;
      const anchor = node.closest("a[href]");
      if (anchor) {
        if (anchor.target === "_blank" || anchor.hasAttribute("download")) return null;
        let url;
        try {
          url = new URL(anchor.getAttribute("href"), window.location.href);
        } catch {
          return null;
        }
        if (url.origin !== window.location.origin) return null;
        const dest = `${url.pathname}${url.search}${url.hash}`;
        const here = `${window.location.pathname}${window.location.search}${window.location.hash}`;
        return dest === here ? null : dest;
      }
      const tab = node.closest("[data-workspace-tab]");
      if (!tab) return null;
      const closing = Boolean(node.closest(".kb-workspace-tab-close"));
      const active = tab.getAttribute("data-active") === "1";
      if (closing) return active ? (tab.getAttribute("data-path") || "close-active") : null;
      const dest = tab.getAttribute("data-path") || "";
      if (!dest || dest === window.location.pathname) return null;
      return dest;
    };

    const onClick = (event) => {
      if (allowLeaveRef.current || !dirty()) return;
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const dest = destinationFrom(event.target);
      if (!dest) return;
      const anchor = event.target.closest?.("a[href]");
      const control = anchor || event.target.closest?.("button");
      event.preventDefault();
      event.stopPropagation();
      pendingLeaveRef.current = { kind: "click", el: control };
      setConfirmLeave(true);
    };

    document.addEventListener("click", onClick, true);
    return () => {
      document.removeEventListener("click", onClick, true);
    };
  }, [editing]);

  useEffect(() => {
    if (!editing) {
      setProductRequestLeaveGuard(null);
      return undefined;
    }
    setProductRequestLeaveGuard(() => {
      if (allowLeaveRef.current === "once") {
        allowLeaveRef.current = false;
        return false;
      }
      if (allowLeaveRef.current === true) return false;
      if (productRequestFormsEqual(formRef.current, baselineRef.current)) return false;
      window.history.pushState(stayStateRef.current, "", stayPathRef.current);
      pendingLeaveRef.current = { kind: "pop" };
      setConfirmLeave(true);
      return true;
    });
    return () => setProductRequestLeaveGuard(null);
  }, [editing]);

  const leaveDirty = editing && !productRequestFormsEqual(form, baselineRef.current);
  useEffect(() => {
    if (!leaveDirty) return undefined;
    const onBeforeUnload = (event) => armProductRequestBeforeUnload(event);
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [leaveDirty]);

  const cancelLeave = () => {
    pendingLeaveRef.current = null;
    setConfirmLeave(false);
  };

  const discardLeave = () => {
    const pending = pendingLeaveRef.current;
    pendingLeaveRef.current = null;
    setConfirmLeave(false);
    if (!pending) return;
    if (pending.kind === "pop") {
      allowLeaveRef.current = "once";
      window.history.back();
      return;
    }
    allowLeaveRef.current = true;
    pending.el?.click();
    allowLeaveRef.current = false;
  };

  const complete = useMemo(() => completenessCount(form), [form]);
  const steps = useMemo(() => progressSteps(form), [form]);
  const submitReady = requiredComplete(form);
  const creatorName = context?.created_by?.name || user?.name || user?.email || "—";
  const branchName = context?.branch?.name || "—";

  const setModeCustomer = (source) => setForm((prev) => clearCustomerMode(prev, source));
  const setModeProduct = (source) => setForm((prev) => clearProductMode(prev, source));

  const saveDraft = async () => {
    setBusy("draft");
    setMessage({ type: "", text: "" });
    try {
      const payload = toPayload(form);
      const data = requestId
        ? await api.updateProductRequest(requestId, payload)
        : await api.createProductRequest(payload);
      const saved = formFromRequest(data.request);
      setRequestId(data.request.id);
      adoptForm(saved);
      setMessage({ type: "ok", text: "Draft tersimpan." });
      if (!id && data.request.id) navigate(`/penjualan/permintaan-produk/${data.request.id}`, { replace: true });
    } catch (err) {
      setMessage({ type: "error", text: formatError(err) });
    } finally {
      setBusy("");
    }
  };

  const submit = async () => {
    if (!submitReady) return;
    setBusy("submit");
    setMessage({ type: "", text: "" });
    try {
      const payload = toPayload(form);
      let currentId = requestId;
      let submitPayload = payload;
      if (!currentId) {
        const created = await api.createProductRequest(payload);
        currentId = created.request.id;
        const createdForm = formFromRequest(created.request);
        setRequestId(currentId);
        adoptForm(createdForm);
        submitPayload = toPayload(createdForm);
      } else {
        const updated = await api.updateProductRequest(currentId, payload);
        const updatedForm = formFromRequest(updated.request);
        adoptForm(updatedForm);
        submitPayload = toPayload(updatedForm);
      }
      const data = await api.submitProductRequest(currentId, submitPayload);
      baselineRef.current = formRef.current;
      setReceipt(data.receipt);
      setReadonly(null);
      setMessage({ type: "ok", text: "Permintaan terkirim." });
    } catch (err) {
      setMessage({ type: "error", text: formatError(err) });
    } finally {
      setBusy("");
    }
  };

  if (receipt) {
    return (
      <div className="prq-page" data-testid="prq-receipt-page">
        <div className="prq-receipt">
          <h1>Permintaan berhasil dikirim ke Holding</h1>
          <dl className="prq-dl">
            <dt>Nomor Permintaan</dt>
            <dd data-testid="prq-receipt-number">{receipt.request_number}</dd>
            <dt>Waktu Dikirim</dt>
            <dd data-testid="prq-receipt-time">{receipt.submitted_at}</dd>
            <dt>Status</dt>
            <dd data-testid="prq-receipt-status">Terkirim</dd>
          </dl>
          {canCreate && (
            <button
              type="button"
              className="prq-btn prq-btn-primary"
              data-testid="prq-new-after-submit"
              onClick={() => {
                setReceipt(null);
                adoptForm(EMPTY_FORM);
                setRequestId(null);
                navigate(PRODUCT_REQUEST_NEW_PATH);
              }}
            >
              Buat Permintaan Baru
            </button>
          )}
        </div>
      </div>
    );
  }

  const refreshReadonly = async (requestIdValue) => {
    const [detailRes, activityRes] = await Promise.all([
      api.getProductRequest(requestIdValue),
      api.listProductRequestActivities(requestIdValue),
    ]);
    setReadonly(detailRes.request);
    setActivities(activityRes.activities || []);
  };

  const onSubmitFeedback = async (payload) => {
    if (!readonly?.id) return false;
    setWorkflowBusy("feedback");
    setMessage(null);
    try {
      await api.addProductRequestFeedback(readonly.id, payload);
      await refreshReadonly(readonly.id);
      return true;
    } catch (err) {
      setMessage({ type: "error", text: formatError(err) });
      return false;
    } finally {
      setWorkflowBusy("");
    }
  };

  const onSubmitStatus = async (payload) => {
    if (!readonly?.id) return false;
    setWorkflowBusy("status");
    setMessage(null);
    try {
      await api.updateProductRequestStatus(readonly.id, payload);
      await refreshReadonly(readonly.id);
      return true;
    } catch (err) {
      setMessage({ type: "error", text: formatError(err) });
      return false;
    } finally {
      setWorkflowBusy("");
    }
  };

  if (readonly) {
    return (
      <div className="prq-page prq-detail-page" data-testid="prq-readonly-page">
        <div className="prq-head">
          <div>
            <h1>Detail Permintaan Produk</h1>
            <p>
              {(readonly.status_label || statusLabel(readonly.status))}
              {readonly.submitted_at ? ` · ${readonly.submitted_at}` : ""}
            </p>
          </div>
          <button type="button" className="prq-btn prq-btn-ghost" onClick={() => navigate(PRODUCT_REQUEST_LIST_PATH)}>
            Kembali ke Daftar
          </button>
        </div>
        {message?.text && (
          <div className={"prq-msg" + (message.type === "error" ? " is-error" : "")}>{message.text}</div>
        )}
        <section className="prq-card prq-detail-card">
          <h2>{readonly.request_number}</h2>
          <ProductRequestDetail
            request={readonly}
            activities={activities}
            canFeedback={canFeedback}
            canUpdateStatus={canUpdateStatus}
            workflowBusy={workflowBusy}
            onSubmitFeedback={onSubmitFeedback}
            onSubmitStatus={onSubmitStatus}
          />
        </section>
      </div>
    );
  }

  return (
    <div className="prq-page prq-form-page" data-testid="prq-form-page">
      <div className="prq-head">
        <div>
          <h1>Permintaan Produk Customer</h1>
          <p>Catat satu permintaan produk dari customer lengkap dan akurat.</p>
        </div>
      </div>

      <div className="prq-progress" data-testid="prq-progress">
        {steps.map((step, index) => (
          <div key={step.key} className={"prq-progress-step" + (step.done ? " is-done" : "")} data-testid={`prq-progress-${step.key}`}>
            <strong data-testid={`prq-progress-circle-${step.key}`}>{index + 1}</strong>
            <span data-testid={`prq-progress-label-${step.key}`}>{step.label}</span>
          </div>
        ))}
      </div>

      <div className="prq-body">
        <div className="prq-main" data-testid="prq-main">
          <section className="prq-card" data-testid="prq-customer-card">
            <div className="prq-card-head">
              <div>
                <h2 data-testid="prq-customer-title">1. Data Customer</h2>
                <p className="muted">Pilih master customer atau input customer baru.</p>
              </div>
              {complete.checks.customerIdentity && <span className="prq-badge">Data customer lengkap</span>}
            </div>
            <div className="prq-modes">
              <button
                type="button"
                className={"prq-mode" + (form.customer_source === CUSTOMER_MASTER ? " is-active" : "")}
                data-testid="prq-customer-master"
                onClick={() => setModeCustomer(CUSTOMER_MASTER)}
              >
                <Users size={15} />
                <div>
                  <strong>Pilih dari Master CRM</strong>
                  <span>Cari customer yang sudah ada di database</span>
                </div>
              </button>
              <button
                type="button"
                className={"prq-mode" + (form.customer_source === CUSTOMER_NEW ? " is-active" : "")}
                data-testid="prq-customer-new"
                onClick={() => setModeCustomer(CUSTOMER_NEW)}
              >
                <UserPlus size={15} />
                <div>
                  <strong>Input Customer Baru</strong>
                  <span>Tambah customer baru ke database permintaan</span>
                </div>
              </button>
            </div>
            {form.customer_source === CUSTOMER_MASTER && (
              <>
                <ProductRequestCustomerSearch
                  value={form.customer_name}
                  selectedId={form.customer_id}
                  onSelectCustomer={(customer) => setForm((prev) => selectMasterCustomer(prev, customer))}
                  onClear={() => setForm((prev) => clearCustomerMode(prev, CUSTOMER_MASTER))}
                />
                {form.customer_id && (
                  <div className="prq-snapshot" data-testid="prq-customer-snapshot">
                    <div><span>Nama / Kode</span><strong>{form.customer_name} · {form.customer_id}</strong></div>
                    <div><span>PIC</span><strong>{form.pic || "—"}</strong></div>
                    <div><span>WhatsApp</span><strong>{form.whatsapp || "—"}</strong></div>
                    <div><span>Kota</span><strong>{form.city || "—"}</strong></div>
                  </div>
                )}
              </>
            )}
            {form.customer_source === CUSTOMER_NEW && (
              <div className="prq-grid is-customer">
                <label className="prq-field">
                  <span>Nama Customer *</span>
                  <input data-testid="prq-new-customer-name" value={form.customer_name} onChange={(e) => setForm((p) => ({ ...p, customer_name: e.target.value }))} />
                </label>
                <label className="prq-field">
                  <span>Nama PIC</span>
                  <input data-testid="prq-new-customer-pic" value={form.pic} onChange={(e) => setForm((p) => ({ ...p, pic: e.target.value }))} />
                </label>
                <label className="prq-field">
                  <span>WhatsApp</span>
                  <input data-testid="prq-new-customer-wa" value={form.whatsapp} onChange={(e) => setForm((p) => ({ ...p, whatsapp: e.target.value }))} />
                </label>
                <label className="prq-field">
                  <span>Kota</span>
                  <input data-testid="prq-new-customer-city" value={form.city} onChange={(e) => setForm((p) => ({ ...p, city: e.target.value }))} />
                </label>
              </div>
            )}
          </section>

          <section className="prq-card" data-testid="prq-product-card">
            <div className="prq-card-head">
              <div>
                <h2 data-testid="prq-product-title">2. Data Produk</h2>
                <p className="muted">Pilih master produk atau input requested product.</p>
              </div>
              {complete.checks.productIdentity && <span className="prq-badge">Data produk lengkap</span>}
            </div>
            <div className="prq-modes">
              <button
                type="button"
                className={"prq-mode" + (form.product_source === PRODUCT_MASTER ? " is-active" : "")}
                data-testid="prq-product-master"
                onClick={() => setModeProduct(PRODUCT_MASTER)}
              >
                <Building2 size={15} />
                <div>
                  <strong>Pilih Produk Master</strong>
                  <span>Cari produk yang sudah ada di database</span>
                </div>
              </button>
              <button
                type="button"
                className={"prq-mode" + (form.product_source === PRODUCT_REQUESTED ? " is-active" : "")}
                data-testid="prq-product-new"
                onClick={() => setModeProduct(PRODUCT_REQUESTED)}
              >
                <PackagePlus size={15} />
                <div>
                  <strong>Input Produk Baru</strong>
                  <span>Tambah produk baru (requested product)</span>
                </div>
              </button>
            </div>
            {form.product_source === PRODUCT_MASTER && (
              <MasterItemSearchSelect
                value={form.product_id ? { id: form.product_id, item_code: form.product_code, item_name: form.product_name } : null}
                onChange={(item) => setForm((prev) => (item ? selectMasterProduct(prev, item) : clearProductMode(prev, PRODUCT_MASTER)))}
                testId="prq-product-search"
              />
            )}
            {form.product_source === PRODUCT_REQUESTED && (
              <div className="prq-grid is-product">
                <label className="prq-field">
                  <span>Nama Produk *</span>
                  <input data-testid="prq-new-product-name" value={form.product_name} onChange={(e) => setForm((p) => ({ ...p, product_name: e.target.value }))} />
                </label>
                <label className="prq-field">
                  <span>Kategori</span>
                  <input data-testid="prq-new-product-category" value={form.category} onChange={(e) => setForm((p) => ({ ...p, category: e.target.value }))} />
                </label>
                <label className="prq-field">
                  <span>Ukuran</span>
                  <input data-testid="prq-new-product-size" value={form.size} onChange={(e) => setForm((p) => ({ ...p, size: e.target.value }))} />
                </label>
                <label className="prq-field">
                  <span data-testid="prq-material-label">Material</span>
                  <input data-testid="prq-new-product-material" value={form.material} onChange={(e) => setForm((p) => ({ ...p, material: e.target.value }))} />
                </label>
                <label className="prq-field is-wide">
                  <span data-testid="prq-model-label">Model / Spesifikasi</span>
                  <input data-testid="prq-new-product-model" value={form.model} onChange={(e) => setForm((p) => ({ ...p, model: e.target.value }))} />
                </label>
              </div>
            )}
          </section>

          <section className="prq-card" data-testid="prq-potential-card">
            <div className="prq-card-head">
              <div>
                <h2 data-testid="prq-potential-title">3. Informasi Potensi</h2>
                <p className="muted">Qty, frekuensi, harga, dan tanggal kebutuhan.</p>
              </div>
              {complete.checks.qty && complete.checks.frequency && complete.checks.price && complete.checks.neededDate && (
                <span className="prq-badge">Data potensi lengkap</span>
              )}
            </div>
            <div className="prq-grid is-potential">
              <label className="prq-field">
                <span>Jumlah (Qty) *</span>
                <input data-testid="prq-qty" type="number" min="1" step="1" value={form.potential_qty} onChange={(e) => setForm((p) => ({ ...p, potential_qty: e.target.value }))} />
              </label>
              <label className="prq-field">
                <span>Frekuensi *</span>
                <input data-testid="prq-frequency" value={form.frequency} onChange={(e) => setForm((p) => ({ ...p, frequency: e.target.value }))} />
              </label>
              <label className="prq-field">
                <span>Target Harga *</span>
                <input data-testid="prq-price" type="number" min="0" step="1" value={form.target_price} onChange={(e) => setForm((p) => ({ ...p, target_price: e.target.value }))} />
              </label>
              <label className="prq-field">
                <span>Tanggal Dibutuhkan *</span>
                <input data-testid="prq-needed-date" type="date" value={form.needed_date} onChange={(e) => setForm((p) => ({ ...p, needed_date: e.target.value }))} />
              </label>
              <label className="prq-field is-half">
                <span>Sumber Channel</span>
                <input data-testid="prq-channel" value={form.request_channel} onChange={(e) => setForm((p) => ({ ...p, request_channel: e.target.value }))} />
              </label>
              <label className="prq-field is-half">
                <span>Catatan</span>
                <input data-testid="prq-note" value={form.request_note} maxLength={2000} onChange={(e) => setForm((p) => ({ ...p, request_note: e.target.value }))} />
              </label>
            </div>
          </section>
        </div>

        <aside className="prq-side" data-testid="prq-summary">
          <section className="prq-card">
            <h2>Ringkasan Permintaan</h2>
            <div className="prq-side-block">
              <dl className="prq-dl">
                <dt>Customer</dt>
                <dd data-testid="prq-summary-customer-source">{customerSourceLabel(form.customer_source)}</dd>
                <dt>Nama</dt>
                <dd>{form.customer_name || "—"}</dd>
                {form.customer_source === CUSTOMER_MASTER && <><dt>Kode</dt><dd>{form.customer_id || "—"}</dd></>}
                <dt>PIC</dt>
                <dd>{form.pic || "—"}</dd>
                <dt>WhatsApp</dt>
                <dd>{form.whatsapp || "—"}</dd>
                <dt>Kota</dt>
                <dd>{form.city || "—"}</dd>
              </dl>
            </div>
            <div className="prq-side-block">
              <dl className="prq-dl">
                <dt>Produk</dt>
                <dd data-testid="prq-summary-product-source">{productSourceLabel(form.product_source)}</dd>
                <dt>Nama</dt>
                <dd>{form.product_name || "—"}</dd>
                {form.product_source === PRODUCT_MASTER && <><dt>Kode</dt><dd>{form.product_code || "—"}</dd></>}
                {form.product_source === PRODUCT_REQUESTED && (
                  <>
                    <dt>Kategori</dt><dd>{form.category || "—"}</dd>
                    <dt>Ukuran</dt><dd>{form.size || "—"}</dd>
                    <dt>Material</dt><dd>{form.material || "—"}</dd>
                    <dt>Model</dt><dd>{form.model || "—"}</dd>
                  </>
                )}
                <dt>Qty</dt><dd>{form.potential_qty || "—"}</dd>
                <dt>Frekuensi</dt><dd>{form.frequency || "—"}</dd>
                <dt>Harga</dt><dd>{form.target_price === "" ? "—" : form.target_price}</dd>
                <dt>Tanggal</dt><dd>{form.needed_date || "—"}</dd>
              </dl>
            </div>
            <div className="prq-side-block">
              <dl className="prq-dl">
                <dt>Dibuat oleh</dt>
                <dd data-testid="prq-summary-creator">{creatorName}</dd>
                <dt>Cabang</dt>
                <dd data-testid="prq-summary-branch">{branchName}</dd>
              </dl>
            </div>
            <div className="prq-lock">Detail hanya untuk Holding & Direktur</div>
          </section>
        </aside>
      </div>

      <div className="prq-actions" data-testid="prq-actions">
        <div className={"prq-valid" + (submitReady ? " is-ok" : "")} data-testid="prq-valid-count">
          {complete.done}/{complete.total} data wajib lengkap
        </div>
        <div className="prq-msg" data-testid="prq-message">
          {busy === "draft" && "Menyimpan draft…"}
          {busy === "submit" && "Mengirim permintaan…"}
          {!busy && message.text && <span className={message.type === "error" ? "is-error" : "is-ok"}>{message.text}</span>}
        </div>
        <div className="prq-actions-btns">
          <button
            type="button"
            className="prq-btn prq-btn-ghost"
            data-testid="prq-save-draft"
            disabled={!canCreate || Boolean(busy)}
            onClick={saveDraft}
          >
            Simpan Draft
          </button>
          <button
            type="button"
            className="prq-btn prq-btn-primary"
            data-testid="prq-submit"
            disabled={!canCreate || !submitReady || Boolean(busy)}
            onClick={submit}
          >
            Kirim Permintaan
          </button>
        </div>
      </div>
      {confirmLeave && createPortal(
        <div className="prq-unsaved" role="presentation" data-testid="prq-unsaved-dialog">
          <div className="prq-unsaved-card" role="dialog" aria-modal="true" aria-labelledby="prq-unsaved-title">
            <h2 id="prq-unsaved-title">Perubahan belum disimpan</h2>
            <p>Isian pada form ini belum disimpan. Tetap di halaman ini, atau buang perubahan untuk pindah.</p>
            <div className="prq-unsaved-actions">
              <button type="button" className="prq-btn prq-btn-ghost" data-testid="prq-unsaved-cancel" onClick={cancelLeave}>
                Batal
              </button>
              <button type="button" className="prq-btn prq-btn-primary" data-testid="prq-unsaved-discard" onClick={discardLeave}>
                Buang Perubahan
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
