import { useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  COMPLAINT_CATEGORY_OPTIONS,
  CRM_DAILY_DRAFT_DEBOUNCE_MS,
  CRM_DAILY_REPORT_LIST_PATH,
  CRM_OTHER_OPTION_ID,
  NO_ORDER_REASON_LAINNYA,
  NO_ORDER_REASON_PRESETS,
  SALES_MANUAL_CHOICE,
  SALES_SOURCE_EXISTING,
  SALES_SOURCE_LEGACY,
  SALES_SOURCE_MANUAL,
  STATUS_ORDER_OPTIONS,
  buildCrmDailyDraftSnapshot,
  canCreateCrmDailyReport,
  canEditOwnCrmDailyReport,
  canUseManualSales,
  clearCrmDailyDraft,
  crmDailyDraftStorageKey,
  emptyCrmDailyReportForm,
  formFromCrmDailyReport,
  readCrmDailyDraft,
  statusOrderBadgeClass,
  toCrmDailyReportPayload,
  validateCrmDailyReportForm,
  writeCrmDailyDraft,
} from "../lib/crmDailyReport";
import "./crmDailyReport.css";
import PoIdentityBadges from "../components/PoIdentityBadges";

function errText(e) {
  const d = e?.response?.data?.detail;
  if (typeof d === "string") return d;
  return d?.message || e?.message || "Gagal menyimpan";
}

function matchSalesMasterId(catalog, { userId = "", label = "" } = {}) {
  const rows = Array.isArray(catalog) ? catalog : [];
  const uid = String(userId || "").trim();
  const name = String(label || "").trim();
  if (uid) {
    const byUid = rows.find((row) => String(row?.user_id || "").trim() === uid);
    if (byUid?.id) return String(byUid.id);
  }
  if (name) {
    const byName = rows.find((row) => String(row?.sales_name || "").trim() === name);
    if (byName?.id) return String(byName.id);
  }
  return "";
}

function Field({ label, required, error, wide, children }) {
  return (
    <div className={`cdr-field${wide ? " is-wide" : ""}${error ? " is-error" : ""}`}>
      {label ? <label>{label}{required ? <span className="req"> *</span> : null}</label> : null}
      {children}
      {error ? <p className="cdr-field-error">{error}</p> : null}
    </div>
  );
}

export default function CrmDailyReportForm() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const isNew = !id || id === "baru";
  const forceEdit = params.get("edit") === "1";
  const { hasPerm, user } = useAuth();
  const navigate = useNavigate();
  const listId = useId();
  const poListId = useId();
  const salesListId = useId();

  const [meta, setMeta] = useState(null);
  const [form, setForm] = useState(() => emptyCrmDailyReportForm());
  const [mode, setMode] = useState(isNew ? "edit" : "view");
  const [errors, setErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");
  const [customerQ, setCustomerQ] = useState("");
  const [customerHits, setCustomerHits] = useState([]);
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [poQ, setPoQ] = useState("");
  const [poHits, setPoHits] = useState([]);
  const [selectedOrder, setSelectedOrder] = useState(null);
  const [salesOptions, setSalesOptions] = useState([]);
  const [salesQ, setSalesQ] = useState("");
  const [salesOpen, setSalesOpen] = useState(false);
  const [waWarning, setWaWarning] = useState("");
  const [pendingEditDraft, setPendingEditDraft] = useState(null);
  const [draftBanner, setDraftBanner] = useState("");
  const [hasLocalDraft, setHasLocalDraft] = useState(false);
  const [draftPersistReady, setDraftPersistReady] = useState(false);
  const submitLock = useRef(false);
  const clientSubmitId = useRef(`cdr-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
  const manualSalesRef = useRef(null);
  const draftKey = crmDailyDraftStorageKey(user, isNew ? "create" : "edit", isNew ? "new" : id);

  const applyDraftSnapshot = (snap) => {
    if (!snap?.form) return;
    setForm(snap.form);
    setSelectedCustomer(snap.ui?.selectedCustomer || null);
    setSelectedOrder(snap.ui?.selectedOrder || null);
    setCustomerQ(snap.ui?.customerQ || "");
    setPoQ(snap.ui?.poQ || snap.form.po_number || "");
    setSalesQ(snap.ui?.salesQ || snap.form.sales_label || "");
    setErrors({});
  };

  const patch = (key, value) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => {
      if (!prev[key]) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  useEffect(() => {
    api.crmDailyReportMeta()
      .then((r) => {
        setMeta(r);
        if (isNew) {
          setForm((prev) => (prev.report_date ? prev : { ...prev, report_date: r.today_wib || prev.report_date }));
        }
      })
      .catch(() => {});
  }, [isNew]);

  // CREATE: restore session draft immediately (no auto-POST).
  useEffect(() => {
    if (!isNew || !draftKey) {
      if (isNew) setDraftPersistReady(false);
      return;
    }
    const snap = readCrmDailyDraft(draftKey);
    if (snap) {
      applyDraftSnapshot(snap);
      setDraftBanner("Draft lokal dipulihkan. Belum dikirim ke server.");
      setHasLocalDraft(true);
    } else {
      setHasLocalDraft(false);
    }
    setDraftPersistReady(true);
  }, [draftKey, isNew]);

  useEffect(() => {
    if (isNew) return undefined;
    let cancelled = false;
    setDraftPersistReady(false);
    setPendingEditDraft(null);
    api.getCrmDailyReport(id)
      .then((r) => {
        if (cancelled) return;
        const report = r.report || {};
        const next = formFromCrmDailyReport(report);
        setForm(next);
        if (report.customer_id) {
          setSelectedCustomer({ id: report.customer_id, name: report.outlet_name, phone: report.whatsapp });
        } else {
          setSelectedCustomer(null);
        }
        if (report.order_id || report.po_number) {
          setSelectedOrder({
            id: report.order_id,
            po_number: report.po_number,
            customer: report.outlet_name,
          });
          setPoQ(report.po_number || "");
        } else {
          setSelectedOrder(null);
          setPoQ("");
        }
        setSalesQ(report.sales_label || "");
        const editable = Boolean(report.can_edit) && canEditOwnCrmDailyReport(hasPerm);
        const nextMode = forceEdit && editable ? "edit" : "view";
        setMode(nextMode);
        // Server wins by default; offer draft only after explicit choice.
        const snap = draftKey ? readCrmDailyDraft(draftKey) : null;
        if (snap && nextMode === "edit") {
          setPendingEditDraft(snap);
          setHasLocalDraft(true);
          setDraftBanner("");
          setDraftPersistReady(false);
        } else {
          setPendingEditDraft(null);
          setHasLocalDraft(Boolean(snap));
          setDraftPersistReady(nextMode === "edit");
        }
      })
      .catch((e) => setToast(errText(e)));
    return () => { cancelled = true; };
    // hasPerm is intentionally omitted — auth recreates the fn each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftKey, forceEdit, id, isNew]);

  // Debounced session draft persist (never auto-submits).
  useEffect(() => {
    if (!draftKey || !draftPersistReady || mode !== "edit") return undefined;
    const handle = window.setTimeout(() => {
      const ok = writeCrmDailyDraft(draftKey, buildCrmDailyDraftSnapshot(form, {
        customerQ,
        poQ,
        salesQ,
        selectedCustomer,
        selectedOrder,
      }));
      if (ok) setHasLocalDraft(true);
    }, CRM_DAILY_DRAFT_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [customerQ, draftKey, draftPersistReady, form, mode, poQ, salesQ, selectedCustomer, selectedOrder]);

  useEffect(() => {
    if (form.status_customer !== "Repeat" || mode !== "edit") return undefined;
    const handle = window.setTimeout(() => {
      api.crmDailyReportCustomers({ q: customerQ })
        .then((r) => setCustomerHits(r.customers || []))
        .catch(() => setCustomerHits([]));
    }, 150);
    return () => window.clearTimeout(handle);
  }, [customerQ, form.status_customer, mode]);

  useEffect(() => {
    if (mode !== "edit") return undefined;
    const handle = window.setTimeout(() => {
      const params = { q: poQ };
      if (form.status_customer === "Repeat" && form.customer_id) {
        params.customer_id = form.customer_id;
      }
      api.crmDailyReportOrders(params)
        .then((r) => setPoHits(r.orders || []))
        .catch(() => setPoHits([]));
    }, 150);
    return () => window.clearTimeout(handle);
  }, [poQ, form.customer_id, form.status_customer, mode]);

  // Operational sales identity from Master Sales catalog (meta.sales_catalog).
  // Does not call /crm-daily-reports/sales (mapping→users) and does not touch CRM outlet ownership.
  useEffect(() => {
    if (mode !== "edit") return undefined;
    const catalog = Array.isArray(meta?.sales_catalog) ? meta.sales_catalog : [];
    const needle = String(salesQ || "").trim().toLowerCase();
    const rows = catalog
      .filter((row) => row && String(row.id || "").trim() && String(row.sales_name || "").trim())
      .filter((row) => {
        if (!needle) return true;
        const name = String(row.sales_name || "").toLowerCase();
        const uid = String(row.user_id || "").toLowerCase();
        return name.includes(needle) || uid.includes(needle);
      })
      .map((row) => ({
        sales_master_id: String(row.id),
        user_id: String(row.user_id || "").trim(),
        label: String(row.sales_name || "").trim(),
        sales_name: String(row.sales_name || "").trim(),
        name: String(row.sales_name || "").trim(),
      }));
    setSalesOptions(rows);
    return undefined;
  }, [salesQ, mode, meta]);

  const reporterName = meta?.reporter?.name || user?.name || "—";

  const applyOrderSelection = (order) => {
    if (!order) {
      setSelectedOrder(null);
      setForm((prev) => ({
        ...prev,
        order_id: "",
        po_number: "",
        sales_locked_from_po: false,
        sales_note: "",
      }));
      setPoQ("");
      return;
    }
    setSelectedOrder(order);
    setPoQ(order.po_number || "");
    const verified = Boolean(order.sales_verified && (order.sales_user_id || order.sales_label_resolved));
    const catalog = meta?.sales_catalog || [];
    setForm((prev) => {
      const next = {
        ...prev,
        order_id: order.id || "",
        po_number: order.po_number || "",
      };
      if (verified) {
        next.sales_user_id = order.sales_user_id || "";
        next.sales_label = order.sales_label_resolved || order.sales || "";
        next.sales_source = SALES_SOURCE_EXISTING;
        next.sales_choice = order.sales_user_id || "";
        next.sales_locked_from_po = true;
        next.sales_note = "Sales mengikuti PO yang dipilih";
        next.sales_master_id = matchSalesMasterId(catalog, {
          userId: next.sales_user_id,
          label: next.sales_label,
        });
        setSalesQ(next.sales_label);
      } else {
        const rawLabel = String(order.sales_label_resolved || order.sales || "").trim();
        if (rawLabel) {
          // Unmapped PO: keep Accurate label; do not silently substitute another Sales.
          next.sales_user_id = "";
          next.sales_label = rawLabel;
          next.sales_source = SALES_SOURCE_LEGACY;
          next.sales_choice = "";
          next.sales_locked_from_po = true;
          next.sales_note = "Sales PO belum terpetakan; label Accurate dipertahankan";
          next.sales_master_id = matchSalesMasterId(catalog, { label: rawLabel });
          setSalesQ(rawLabel);
        } else {
          next.sales_locked_from_po = false;
          next.sales_note = "PO tidak memiliki Sales terverifikasi; pilih Sales dari daftar";
          next.sales_master_id = "";
          if (prev.sales_source === SALES_SOURCE_MANUAL || prev.sales_choice === SALES_MANUAL_CHOICE) {
            next.sales_source = "";
            next.sales_choice = "";
            next.sales_user_id = "";
            next.sales_label = "";
            setSalesQ("");
          }
        }
      }
      return next;
    });
    setErrors((prev) => {
      const n = { ...prev };
      delete n.sales_label;
      return n;
    });
  };

  const applySalesSelection = (row) => {
    if (!row) return;
    const catalog = meta?.sales_catalog || [];
    if (row === SALES_MANUAL_CHOICE || row.user_id === SALES_MANUAL_CHOICE) {
      setForm((prev) => ({
        ...prev,
        sales_choice: SALES_MANUAL_CHOICE,
        sales_source: SALES_SOURCE_MANUAL,
        sales_user_id: "",
        sales_label: "",
        sales_master_id: "",
        sales_locked_from_po: false,
        sales_note: "",
      }));
      setSalesQ("");
      setSalesOpen(false);
      setErrors((prev) => {
        if (!prev.sales_label) return prev;
        const n = { ...prev };
        delete n.sales_label;
        return n;
      });
      window.setTimeout(() => {
        manualSalesRef.current?.focus?.();
      }, 0);
      return;
    }
    // Master Sales catalog row (manual-allowed): label snapshot, no verified user id.
    if (row.sales_master_id && !row.user_id) {
      setForm((prev) => ({
        ...prev,
        sales_choice: SALES_MANUAL_CHOICE,
        sales_source: SALES_SOURCE_MANUAL,
        sales_user_id: "",
        sales_label: row.label || row.sales_name || "",
        sales_master_id: row.sales_master_id,
        sales_locked_from_po: false,
        sales_note: "",
      }));
      setSalesQ(row.label || row.sales_name || "");
      setSalesOpen(false);
      return;
    }
    const label = row.label || row.sales_name || "";
    const userId = row.user_id || "";
    setForm((prev) => ({
      ...prev,
      sales_choice: userId,
      sales_user_id: userId,
      sales_label: label,
      sales_source: SALES_SOURCE_EXISTING,
      sales_master_id: row.sales_master_id || matchSalesMasterId(catalog, { userId, label }),
      sales_locked_from_po: false,
      sales_note: "",
    }));
    setSalesQ(label);
    setSalesOpen(false);
    setErrors((prev) => {
      if (!prev.sales_label) return prev;
      const n = { ...prev };
      delete n.sales_label;
      return n;
    });
  };

  const returnToSalesList = () => {
    setForm((prev) => ({
      ...prev,
      sales_choice: "",
      sales_source: "",
      sales_user_id: "",
      sales_label: "",
      sales_master_id: "",
      sales_locked_from_po: false,
      sales_note: "",
    }));
    setSalesQ("");
    setSalesOpen(true);
    setErrors((prev) => {
      if (!prev.sales_label) return prev;
      const n = { ...prev };
      delete n.sales_label;
      return n;
    });
  };

  const acceptEditDraft = () => {
    if (!pendingEditDraft) return;
    applyDraftSnapshot(pendingEditDraft);
    setPendingEditDraft(null);
    setHasLocalDraft(true);
    setDraftBanner("Draft edit dipulihkan. Belum dikirim ke server.");
    setDraftPersistReady(true);
  };

  const keepServerEdit = () => {
    if (draftKey) clearCrmDailyDraft(draftKey);
    setPendingEditDraft(null);
    setHasLocalDraft(false);
    setDraftBanner("Menampilkan data terbaru dari server.");
    setDraftPersistReady(mode === "edit");
  };

  const discardDraftConfirmed = () => {
    if (!window.confirm("Buang draft lokal formulir ini? Data yang belum disimpan akan hilang.")) return;
    if (draftKey) clearCrmDailyDraft(draftKey);
    setPendingEditDraft(null);
    setHasLocalDraft(false);
    setDraftBanner("");
    if (isNew) {
      setForm(emptyCrmDailyReportForm(meta?.today_wib || ""));
      setSelectedCustomer(null);
      setSelectedOrder(null);
      setCustomerQ("");
      setPoQ("");
      setSalesQ("");
      setErrors({});
      setDraftPersistReady(true);
    } else {
      setDraftPersistReady(mode === "edit");
    }
  };

  const onSubmit = async (e) => {
    e.preventDefault();
    if (submitLock.current || saving) return;
    if (pendingEditDraft) {
      setToast("Pilih lanjutkan draft atau data terbaru sebelum menyimpan");
      return;
    }
    const nextErrors = validateCrmDailyReportForm(form);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) {
      setToast("Lengkapi field wajib terlebih dahulu");
      return;
    }
    if (isNew && !canCreateCrmDailyReport(hasPerm)) {
      setToast("Tidak berwenang membuat laporan");
      return;
    }
    if (!isNew && !canEditOwnCrmDailyReport(hasPerm)) {
      setToast("Tidak berwenang mengedit laporan");
      return;
    }
    submitLock.current = true;
    setSaving(true);
    setToast("");
    try {
      const payload = toCrmDailyReportPayload(form, isNew ? clientSubmitId.current : undefined);
      const res = isNew
        ? await api.createCrmDailyReport(payload)
        : await api.updateCrmDailyReport(id, payload);
      if (res?.report?.wa_match_warning) setWaWarning(res.report.wa_match_warning);
      if (draftKey) clearCrmDailyDraft(draftKey);
      setHasLocalDraft(false);
      setDraftBanner("");
      setToast("Laporan tersimpan");
      navigate(`${CRM_DAILY_REPORT_LIST_PATH}/${res.report.id}`);
    } catch (err) {
      setToast(errText(err));
      submitLock.current = false;
      // Keep session draft on failed submit.
      if (draftKey && draftPersistReady) {
        const ok = writeCrmDailyDraft(draftKey, buildCrmDailyDraftSnapshot(form, {
          customerQ, poQ, salesQ, selectedCustomer, selectedOrder,
        }));
        if (ok) setHasLocalDraft(true);
      }
    } finally {
      setSaving(false);
    }
  };

  if (isNew && !canCreateCrmDailyReport(hasPerm)) {
    return <div className="cdr-page" data-testid="crm-daily-report-form-denied"><p>Akses ditolak.</p></div>;
  }

  const editing = mode === "edit";
  const showNoOrder = form.status_order === "TIDAK ORDER";
  const showNoOrderLainnya = showNoOrder && form.no_order_reason_choice === NO_ORDER_REASON_LAINNYA;
  const showComplaint = form.has_complaint;
  const showIssue = form.has_issue;
  const isNewCustomer = form.status_customer === "New";
  const allowManualSales = canUseManualSales(form);
  const showManualSalesInput = allowManualSales && (
    form.sales_choice === SALES_MANUAL_CHOICE || form.sales_source === SALES_SOURCE_MANUAL
  );
  const salesLocked = Boolean(form.sales_locked_from_po);

  if (!editing && !isNew) {
    return (
      <div className="cdr-page" data-testid="crm-daily-report-detail">
        <div className="cdr-head">
          <div>
            <h1>Detail Laporan Harian CRM</h1>
            <p>{form.report_date} · {reporterName}</p>
          </div>
          <div className="cdr-head-actions">
            <Link className="ghost" to={CRM_DAILY_REPORT_LIST_PATH}>Kembali</Link>
            {canEditOwnCrmDailyReport(hasPerm) && meta?.today_wib === form.report_date && (
              <button
                type="button"
                className="primary"
                onClick={() => {
                  setMode("edit");
                  const snap = draftKey ? readCrmDailyDraft(draftKey) : null;
                  if (snap) {
                    setPendingEditDraft(snap);
                    setHasLocalDraft(true);
                    setDraftPersistReady(false);
                  } else {
                    setHasLocalDraft(false);
                    setDraftPersistReady(true);
                  }
                }}
                data-testid="cdr-detail-edit"
              >
                Edit
              </button>
            )}
          </div>
        </div>
        <dl className="cdr-detail-panel">
          <div><dt>Tanggal</dt><dd>{form.report_date}</dd></div>
          <div><dt>CRM</dt><dd>{reporterName}</dd></div>
          <div><dt>Status Customer</dt><dd>{form.status_customer}</dd></div>
          <div><dt>Status Order</dt><dd>{form.status_order}</dd></div>
          <div><dt>Customer/Outlet</dt><dd>{form.outlet_name || "—"}</dd></div>
          <div><dt>No WA</dt><dd>{form.whatsapp || "—"}</dd></div>
          <div><dt>No PO</dt><dd>{form.po_number || "—"}</dd></div>
          <div><dt>Sales / Siapa yang Menjual</dt><dd>
            {form.sales_label || "—"}
            {form.sales_source === SALES_SOURCE_LEGACY ? " (teks lama, belum terverifikasi)" : ""}
            {form.sales_source === SALES_SOURCE_MANUAL ? " (input manual)" : ""}
          </dd></div>
          <div className="full"><dt>Keterangan Tidak Order</dt><dd>{form.no_order_reason || "—"}</dd></div>
          <div><dt>Komplain</dt><dd>{form.has_complaint ? "Ya" : "Tidak"}</dd></div>
          <div><dt>Jenis Komplain</dt><dd>{form.has_complaint ? (form.complaint_category || "—") : "—"}</dd></div>
          <div className="full"><dt>Deskripsi Komplain</dt><dd>{form.has_complaint ? (form.complaint_description || "—") : "—"}</dd></div>
          <div className="full"><dt>Kendala</dt><dd>{form.has_issue ? form.issue_description : "Tidak"}</dd></div>
          <div className="full"><dt>Catatan</dt><dd>{form.notes || "—"}</dd></div>
        </dl>
        {toast && <p data-testid="cdr-toast">{toast}</p>}
      </div>
    );
  }

  return (
    <form className="cdr-page cdr-form-page" onSubmit={onSubmit} data-testid="crm-daily-report-form">
      <div className="cdr-head">
        <div>
          <h1>{isNew ? "Form Laporan Harian CRM" : "Edit Laporan Harian CRM"}</h1>
          <p>Input hasil aktivitas customer per hari.</p>
        </div>
        <div className="cdr-head-actions">
          <Link className="ghost" to={CRM_DAILY_REPORT_LIST_PATH}>Kembali</Link>
          {(hasLocalDraft || pendingEditDraft) ? (
            <button type="button" className="ghost" onClick={discardDraftConfirmed} data-testid="cdr-discard-draft">
              Buang Draft
            </button>
          ) : null}
          <button type="submit" className="primary" disabled={saving || Boolean(pendingEditDraft)} data-testid="cdr-save">
            {saving ? "Menyimpan…" : "Simpan Laporan"}
          </button>
        </div>
      </div>

      {pendingEditDraft ? (
        <div className="cdr-draft-banner" role="status" data-testid="cdr-edit-draft-choice">
          <p>Draft edit lokal ditemukan. Data server ditampilkan sebagai default.</p>
          <div className="cdr-draft-banner-actions">
            <button type="button" className="primary" onClick={acceptEditDraft} data-testid="cdr-draft-continue">
              Lanjutkan draft sebelumnya
            </button>
            <button type="button" className="ghost" onClick={keepServerEdit} data-testid="cdr-draft-use-server">
              Gunakan data terbaru
            </button>
          </div>
        </div>
      ) : null}
      {draftBanner && !pendingEditDraft ? (
        <p className="cdr-draft-note" data-testid="cdr-draft-banner">{draftBanner}</p>
      ) : null}

      <div className="cdr-form-body">
        <div className="cdr-grid">
          <Field label="Tanggal Laporan" required error={errors.report_date}>
            <input
              type="date"
              value={form.report_date}
              onChange={(e) => patch("report_date", e.target.value)}
              data-testid="cdr-report-date"
              disabled={!isNew}
            />
          </Field>
          <Field label="CRM Pelapor" required>
            <input value={reporterName} disabled readOnly data-testid="cdr-reporter" />
          </Field>

          <Field label="Status Customer" required error={errors.status_customer}>
            <div className="cdr-toggle" role="group" aria-label="Status Customer">
              <button type="button" className={form.status_customer === "New" ? "is-on" : ""} data-testid="cdr-status-new" onClick={() => {
                patch("status_customer", "New");
                setSelectedCustomer(null);
                patch("customer_id", "");
              }}>New</button>
              <button type="button" className={form.status_customer === "Repeat" ? "is-on" : ""} data-testid="cdr-status-repeat" onClick={() => {
                patch("status_customer", "Repeat");
                patch("outlet_name", "");
                patch("whatsapp", "");
              }}>Repeat</button>
            </div>
          </Field>
          <Field label="Status Order" required error={errors.status_order}>
            <select
              value={form.status_order}
              onChange={(e) => {
                const next = e.target.value;
                setForm((prev) => {
                  const clearedManual = next !== "TIDAK ORDER" && (
                    prev.sales_source === SALES_SOURCE_MANUAL || prev.sales_choice === SALES_MANUAL_CHOICE
                  );
                  return {
                    ...prev,
                    status_order: next,
                    no_order_reason: next === "TIDAK ORDER" ? prev.no_order_reason : "",
                    no_order_reason_choice: next === "TIDAK ORDER" ? prev.no_order_reason_choice : "",
                    no_order_reason_id: next === "TIDAK ORDER" ? prev.no_order_reason_id : "",
                    ...(clearedManual && !prev.order_id ? {
                      sales_choice: "",
                      sales_source: "",
                      sales_user_id: "",
                      sales_label: "",
                      sales_master_id: "",
                      sales_note: "Pilih Sales dari daftar Eternal",
                    } : {}),
                  };
                });
                setErrors((prev) => {
                  if (!prev.status_order && !prev.no_order_reason && !prev.sales_label) return prev;
                  const n = { ...prev };
                  delete n.status_order;
                  delete n.no_order_reason;
                  delete n.sales_label;
                  return n;
                });
              }}
              data-testid="cdr-status-order"
              aria-label="Status Order"
            >
              {STATUS_ORDER_OPTIONS.map((opt) => (
                <option key={opt} value={opt}>{opt}</option>
              ))}
            </select>
          </Field>

          {isNewCustomer ? (
            <>
              <Field label="Nama Outlet" error={errors.outlet_name}>
                <input
                  value={form.outlet_name}
                  onChange={(e) => patch("outlet_name", e.target.value)}
                  placeholder="Opsional jika belum ada nama"
                  data-testid="cdr-outlet-name"
                />
              </Field>
              <Field label="No WhatsApp" required error={errors.whatsapp}>
                <input
                  value={form.whatsapp}
                  onChange={(e) => patch("whatsapp", e.target.value)}
                  placeholder="08…"
                  data-testid="cdr-whatsapp"
                />
              </Field>
              <div className="cdr-field is-wide">
                <p className="cdr-hint">Jika New, Nomor WhatsApp wajib. Nama Outlet opsional.</p>
              </div>
            </>
          ) : (
            <Field label="Customer / Outlet (Master)" required wide error={errors.customer_id}>
              <input
                value={customerQ}
                onChange={(e) => setCustomerQ(e.target.value)}
                placeholder="Cari nama / WA customer existing…"
                data-testid="cdr-customer-search"
                aria-controls={listId}
              />
              {selectedCustomer && (
                <p data-testid="cdr-customer-selected">
                  Terpilih: {selectedCustomer.name} · {selectedCustomer.phone || "—"}
                </p>
              )}
              <ul className="cdr-customer-results" id={listId} data-testid="cdr-customer-results">
                {customerHits.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedCustomer(c);
                        patch("customer_id", c.id);
                        patch("outlet_name", c.name || "");
                        patch("whatsapp", c.phone || "");
                        setCustomerQ(c.name || "");
                      }}
                    >
                      {c.name} · {c.phone || "—"}{c.city ? ` · ${c.city}` : ""}
                    </button>
                  </li>
                ))}
              </ul>
            </Field>
          )}

          <Field label="Nomor PO (opsional)" wide>
            <input
              value={poQ}
              onChange={(e) => {
                setPoQ(e.target.value);
                if (selectedOrder) {
                  applyOrderSelection(null);
                }
              }}
              placeholder="Cari nomor PO atau nama customer…"
              data-testid="cdr-po-search"
              aria-controls={poListId}
              autoComplete="off"
            />
            {selectedOrder && (
              <p className="cdr-hint" data-testid="cdr-po-selected">
                Terpilih: {selectedOrder.po_number || "—"} · {selectedOrder.customer || "—"}{" "}
                <PoIdentityBadges order={selectedOrder} testIdPrefix="cdr-po-selected" />
                {" "}
                <button type="button" className="ghost" data-testid="cdr-po-clear" onClick={() => applyOrderSelection(null)}>
                  Hapus PO
                </button>
              </p>
            )}
            <ul className="cdr-customer-results" id={poListId} data-testid="cdr-po-results">
              {poHits.map((order) => (
                <li key={order.id}>
                  <button type="button" onClick={() => applyOrderSelection(order)}>
                    {order.po_number || "—"} · {order.customer || "—"}{" "}
                    <PoIdentityBadges order={order} testIdPrefix={`cdr-po-hit-${order.po_number || order.id}`} />
                    {order.sales_verified
                      ? ` · Sales: ${order.sales_label_resolved || order.sales}`
                      : " · Sales belum terverifikasi"}
                  </button>
                </li>
              ))}
              {!poHits.length && poQ.trim() ? (
                <li className="cdr-empty-hit">Tidak ada PO yang cocok</li>
              ) : null}
            </ul>
          </Field>

          <Field label="Sales / Siapa yang Menjual" required wide error={errors.sales_label}>
            {form.sales_source === SALES_SOURCE_LEGACY && form.sales_label ? (
              <p className="cdr-hint" data-testid="cdr-sales-legacy">
                Nilai lama (belum terverifikasi): {form.sales_label}. Pilih ulang dari daftar bila memungkinkan.
              </p>
            ) : null}
            {form.sales_note ? (
              <p className="cdr-hint" data-testid="cdr-sales-note">{form.sales_note}</p>
            ) : null}

            {showManualSalesInput ? (
              <div className="cdr-sales-manual-box" data-testid="cdr-sales-manual-box">
                <p className="cdr-hint">Mode input manual aktif. Sales tidak terverifikasi di master Eternal.</p>
                <label className="cdr-inline-label" htmlFor="cdr-sales-manual-name">
                  Nama Sales Manual <span className="req">*</span>
                </label>
                <input
                  id="cdr-sales-manual-name"
                  ref={manualSalesRef}
                  value={form.sales_label}
                  onChange={(e) => {
                    const value = e.target.value;
                    setForm((prev) => ({
                      ...prev,
                      sales_label: value,
                      sales_source: SALES_SOURCE_MANUAL,
                      sales_choice: SALES_MANUAL_CHOICE,
                      sales_user_id: "",
                      sales_master_id: matchSalesMasterId(meta?.sales_catalog || [], { label: value }),
                    }));
                    setErrors((prev) => {
                      if (!prev.sales_label) return prev;
                      const n = { ...prev };
                      delete n.sales_label;
                      return n;
                    });
                  }}
                  placeholder="Ketik nama Sales…"
                  maxLength={80}
                  data-testid="cdr-sales-manual-name"
                />
                <button
                  type="button"
                  className="ghost cdr-sales-back"
                  data-testid="cdr-sales-back-to-list"
                  onClick={returnToSalesList}
                >
                  Kembali ke daftar Sales
                </button>
              </div>
            ) : (
              <>
                <input
                  value={salesQ}
                  onChange={(e) => {
                    if (salesLocked) return;
                    setSalesQ(e.target.value);
                    setSalesOpen(true);
                  }}
                  onFocus={() => { if (!salesLocked) setSalesOpen(true); }}
                  placeholder={salesLocked ? "Sales mengikuti PO" : "Cari Sales Eternal…"}
                  data-testid="cdr-sales"
                  aria-controls={salesListId}
                  autoComplete="off"
                  disabled={salesLocked}
                  readOnly={salesLocked}
                />
                {salesOpen && !salesLocked ? (
                  <ul className="cdr-customer-results cdr-sales-results" id={salesListId} data-testid="cdr-sales-results">
                    {salesOptions.map((row) => (
                      <li key={row.sales_master_id || row.user_id || row.label}>
                        <button type="button" onClick={() => applySalesSelection(row)}>
                          {row.label || row.sales_name}
                          {row.user_id ? "" : " · tanpa akun Eternal"}
                        </button>
                      </li>
                    ))}
                    {!salesOptions.length ? (
                      <li className="cdr-empty-hit" data-testid="cdr-sales-empty">
                        {(meta?.sales_catalog || []).length
                          ? "Tidak ada Sales yang cocok"
                          : "Tidak ada Sales aktif di Master Sales untuk cabang Anda"}
                      </li>
                    ) : null}
                    {allowManualSales ? (
                      <li className="cdr-sales-manual-sep" aria-hidden="true" />
                    ) : null}
                    {allowManualSales ? (
                      <li>
                        <button
                          type="button"
                          className="cdr-sales-manual-option"
                          data-testid="cdr-sales-manual-option"
                          onClick={() => applySalesSelection(SALES_MANUAL_CHOICE)}
                        >
                          + Input Manual / Sales Lainnya
                        </button>
                      </li>
                    ) : null}
                  </ul>
                ) : null}
              </>
            )}
          </Field>

          <Field label="Komplain?" required>
            <div className="cdr-toggle">
              <button
                type="button"
                className={`is-danger ${form.has_complaint ? "is-on" : ""}`}
                data-testid="cdr-complaint-yes"
                onClick={() => {
                  setForm((prev) => ({
                    ...prev,
                    has_complaint: true,
                    legacy_complaint: false,
                  }));
                }}
              >Ya</button>
              <button
                type="button"
                className={!form.has_complaint ? "is-on" : ""}
                data-testid="cdr-complaint-no"
                onClick={() => {
                  setForm((prev) => ({
                    ...prev,
                    has_complaint: false,
                    complaint_category: "",
                    complaint_category_id: "",
                    complaint_description: "",
                    legacy_complaint: false,
                  }));
                }}
              >Tidak</button>
            </div>
          </Field>
          <Field label="Kendala?" required>
            <div className="cdr-toggle">
              <button type="button" className={`is-danger ${form.has_issue ? "is-on" : ""}`} data-testid="cdr-issue-yes" onClick={() => patch("has_issue", true)}>Ya</button>
              <button type="button" className={!form.has_issue ? "is-on" : ""} data-testid="cdr-issue-no" onClick={() => {
                setForm((prev) => ({
                  ...prev,
                  has_issue: false,
                  issue_category_id: "",
                  issue_description: "",
                }));
              }}>Tidak</button>
            </div>
          </Field>

          {showNoOrder && (
            <Field label="Keterangan Tidak Order" required wide error={errors.no_order_reason}>
              <select
                value={form.no_order_reason_choice || ""}
                onChange={(e) => {
                  const choice = e.target.value;
                  const dyn = (meta?.no_order_reasons || []).find((o) => o.label === choice || o.id === choice);
                  const isOther = choice === NO_ORDER_REASON_LAINNYA
                    || dyn?.is_other
                    || dyn?.id === (meta?.other_option_id || CRM_OTHER_OPTION_ID);
                  setForm((prev) => ({
                    ...prev,
                    no_order_reason_choice: isOther ? NO_ORDER_REASON_LAINNYA : (dyn?.label || choice),
                    no_order_reason_id: isOther
                      ? (meta?.other_option_id || CRM_OTHER_OPTION_ID)
                      : (dyn?.id || ""),
                    no_order_reason: isOther
                      ? (NO_ORDER_REASON_PRESETS.includes(prev.no_order_reason) ? "" : prev.no_order_reason)
                      : (dyn?.label || choice),
                  }));
                  setErrors((prev) => {
                    if (!prev.no_order_reason) return prev;
                    const n = { ...prev };
                    delete n.no_order_reason;
                    return n;
                  });
                }}
                data-testid="cdr-no-order-reason"
                aria-label="Keterangan Tidak Order"
              >
                <option value="">Pilih keterangan…</option>
                {NO_ORDER_REASON_PRESETS.map((opt) => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
                {(meta?.no_order_reasons || [])
                  .filter((o) => !o.is_other && !NO_ORDER_REASON_PRESETS.includes(o.label))
                  .map((o) => (
                    <option key={o.id} value={o.label}>{o.label}</option>
                  ))}
                <option value={NO_ORDER_REASON_LAINNYA}>{NO_ORDER_REASON_LAINNYA}</option>
              </select>
              {showNoOrderLainnya ? (
                <textarea
                  value={form.no_order_reason}
                  onChange={(e) => patch("no_order_reason", e.target.value)}
                  maxLength={500}
                  placeholder="Isi keterangan Lainnya…"
                  data-testid="cdr-no-order-reason-lainnya"
                  style={{ marginTop: 8 }}
                />
              ) : null}
            </Field>
          )}
          {showComplaint && (
            <>
              <Field label="Jenis Komplain" required wide error={errors.complaint_category}>
                <select
                  value={form.complaint_category || ""}
                  onChange={(e) => {
                    const value = e.target.value;
                    const dyn = (meta?.complaint_categories || []).find((o) => o.label === value || o.id === value);
                    setForm((prev) => ({
                      ...prev,
                      complaint_category: dyn?.label || value,
                      complaint_category_id: dyn?.id || "",
                      legacy_complaint: false,
                    }));
                    setErrors((prev) => {
                      if (!prev.complaint_category) return prev;
                      const n = { ...prev };
                      delete n.complaint_category;
                      return n;
                    });
                  }}
                  data-testid="cdr-complaint-category"
                  aria-label="Jenis Komplain"
                >
                  <option value="">Pilih jenis komplain…</option>
                  {COMPLAINT_CATEGORY_OPTIONS.map((opt) => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                  {(meta?.complaint_categories || [])
                    .filter((o) => !o.is_other && !COMPLAINT_CATEGORY_OPTIONS.includes(o.label))
                    .map((o) => (
                      <option key={o.id} value={o.label}>{o.label}</option>
                    ))}
                </select>
              </Field>
              <Field label="Deskripsi Komplain" required wide error={errors.complaint_description}>
                <textarea
                  value={form.complaint_description}
                  onChange={(e) => patch("complaint_description", e.target.value)}
                  maxLength={500}
                  data-testid="cdr-complaint-desc"
                />
              </Field>
            </>
          )}
          {showIssue && (
            <Field label="Deskripsi Kendala" required wide error={errors.issue_description}>
              <textarea
                value={form.issue_description}
                onChange={(e) => patch("issue_description", e.target.value)}
                maxLength={500}
                data-testid="cdr-issue-desc"
              />
            </Field>
          )}
          <Field label="Catatan Tambahan" wide>
            <textarea
              value={form.notes}
              onChange={(e) => patch("notes", e.target.value)}
              maxLength={500}
              data-testid="cdr-notes"
            />
          </Field>
        </div>

        {(waWarning || toast) && (
          <p className="cdr-hint" data-testid="cdr-toast">{waWarning || toast}</p>
        )}

        <div className="cdr-ringkas" data-testid="cdr-summary-bar">
          <span>CRM: {reporterName}</span>
          <span>Customer: {isNewCustomer ? (form.outlet_name || form.whatsapp || "—") : (selectedCustomer?.name || form.outlet_name || "—")}</span>
          <span className={`cdr-badge ${statusOrderBadgeClass(form.status_order)}`}>{form.status_order}</span>
          <span className={`cdr-badge ${form.status_customer === "New" ? "is-new" : "is-repeat"}`}>{form.status_customer}</span>
          <span className={`cdr-badge ${form.has_complaint ? "is-ya" : ""}`}>
            Komplain: {form.has_complaint ? (form.complaint_category || "Ya") : "Tidak"}
          </span>
          <span className={`cdr-badge ${form.has_issue ? "is-ya" : ""}`}>Kendala: {form.has_issue ? "Ya" : "Tidak"}</span>
        </div>
        <p className="cdr-disclaimer">
          Laporan ini hanya untuk pelaporan aktivitas harian CRM, tidak mempengaruhi data PO, stok, atau HPP di sistem.
        </p>
      </div>

      <div className="cdr-form-footer">
        <button type="submit" className="primary" disabled={saving} data-testid="cdr-save-footer">
          {saving ? "Menyimpan…" : "Simpan Laporan"}
        </button>
      </div>
    </form>
  );
}
