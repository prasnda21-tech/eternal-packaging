import { useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  CRM_DAILY_REPORT_LIST_PATH,
  canCreateCrmDailyReport,
  canEditOwnCrmDailyReport,
  emptyCrmDailyReportForm,
  toCrmDailyReportPayload,
  validateCrmDailyReportForm,
} from "../lib/crmDailyReport";
import "./crmDailyReport.css";

function errText(e) {
  const d = e?.response?.data?.detail;
  if (typeof d === "string") return d;
  return d?.message || e?.message || "Gagal menyimpan";
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

  const [meta, setMeta] = useState(null);
  const [form, setForm] = useState(() => emptyCrmDailyReportForm());
  const [mode, setMode] = useState(isNew ? "edit" : "view");
  const [errors, setErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");
  const [customerQ, setCustomerQ] = useState("");
  const [customerHits, setCustomerHits] = useState([]);
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [waWarning, setWaWarning] = useState("");
  const submitLock = useRef(false);
  const clientSubmitId = useRef(`cdr-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);

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
          setForm((prev) => ({ ...prev, report_date: r.today_wib || prev.report_date }));
        }
      })
      .catch(() => {});
  }, [isNew]);

  useEffect(() => {
    if (isNew) return undefined;
    let cancelled = false;
    api.getCrmDailyReport(id)
      .then((r) => {
        if (cancelled) return;
        const report = r.report || {};
        setForm({
          report_date: report.report_date || "",
          status_customer: report.status_customer || "New",
          customer_id: report.customer_id || "",
          outlet_name: report.outlet_name || "",
          whatsapp: report.whatsapp || "",
          order_id: report.order_id || "",
          po_number: report.po_number || "",
          sales_master_id: report.sales_master_id || "",
          sales_label: report.sales_label || "",
          sales_user_id: report.sales_user_id || "",
          status_order: report.status_order || "ORDER",
          no_order_reason_id: report.no_order_reason_id || "",
          no_order_reason: report.no_order_reason || "",
          has_complaint: Boolean(report.has_complaint),
          complaint_category_id: report.complaint_category_id || "",
          complaint_description: report.complaint_description || "",
          has_issue: Boolean(report.has_issue),
          issue_category_id: report.issue_category_id || "",
          issue_description: report.issue_description || "",
          notes: report.notes || "",
        });
        if (report.customer_id) {
          setSelectedCustomer({ id: report.customer_id, name: report.outlet_name, phone: report.whatsapp });
        }
        const editable = Boolean(report.can_edit) && canEditOwnCrmDailyReport(hasPerm);
        setMode(forceEdit && editable ? "edit" : "view");
      })
      .catch((e) => setToast(errText(e)));
    return () => { cancelled = true; };
  }, [forceEdit, hasPerm, id, isNew]);

  useEffect(() => {
    if (form.status_customer !== "Repeat" || mode !== "edit") return undefined;
    const handle = window.setTimeout(() => {
      api.crmDailyReportCustomers({ q: customerQ })
        .then((r) => setCustomerHits(r.customers || []))
        .catch(() => setCustomerHits([]));
    }, 150);
    return () => window.clearTimeout(handle);
  }, [customerQ, form.status_customer, mode]);

  const reporterName = meta?.reporter?.name || user?.name || "—";

  const onSubmit = async (e) => {
    e.preventDefault();
    if (submitLock.current || saving) return;
    const nextErrors = validateCrmDailyReportForm(form, meta || {});
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
      setToast("Laporan tersimpan");
      navigate(`${CRM_DAILY_REPORT_LIST_PATH}/${res.report.id}`);
    } catch (err) {
      setToast(errText(err));
      submitLock.current = false;
    } finally {
      setSaving(false);
    }
  };

  if (isNew && !canCreateCrmDailyReport(hasPerm)) {
    return <div className="cdr-page" data-testid="crm-daily-report-form-denied"><p>Akses ditolak.</p></div>;
  }

  const editing = mode === "edit";
  const showNoOrder = form.status_order === "TIDAK ORDER";
  const showComplaint = form.has_complaint;
  const showIssue = form.has_issue;
  const isNewCustomer = form.status_customer === "New";

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
              <button type="button" className="primary" onClick={() => setMode("edit")} data-testid="cdr-detail-edit">Edit</button>
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
          <div><dt>Sales</dt><dd>{form.sales_label || "—"}</dd></div>
          <div className="full"><dt>Keterangan Tidak Order</dt><dd>{form.no_order_reason || "—"}</dd></div>
          <div className="full"><dt>Komplain</dt><dd>{form.has_complaint ? form.complaint_description : "Tidak"}</dd></div>
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
          <button type="submit" className="primary" disabled={saving} data-testid="cdr-save">
            {saving ? "Menyimpan…" : "Simpan Laporan"}
          </button>
        </div>
      </div>

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
            <div className="cdr-toggle" role="group" aria-label="Status Order">
              <button type="button" className={form.status_order === "ORDER" ? "is-on" : ""} data-testid="cdr-order-yes" onClick={() => {
                patch("status_order", "ORDER");
                patch("no_order_reason", "");
                patch("no_order_reason_id", "");
              }}>ORDER</button>
              <button type="button" className={`is-danger ${form.status_order === "TIDAK ORDER" ? "is-on" : ""}`} data-testid="cdr-order-no" onClick={() => patch("status_order", "TIDAK ORDER")}>TIDAK ORDER</button>
            </div>
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

          <Field label="No PO (opsional)">
            <input
              value={form.po_number}
              onChange={(e) => patch("po_number", e.target.value)}
              placeholder="PO/…"
              data-testid="cdr-po-number"
            />
          </Field>
          <Field label="Sales" required error={errors.sales_label}>
            {(meta?.sales_catalog || []).length > 0 ? (
              <select
                value={form.sales_master_id || ""}
                onChange={(e) => {
                  const sid = e.target.value;
                  const hit = (meta.sales_catalog || []).find((s) => s.id === sid);
                  setForm((prev) => ({
                    ...prev,
                    sales_master_id: sid,
                    sales_label: hit?.sales_name || "",
                    sales_user_id: hit?.user_id || "",
                  }));
                  setErrors((prev) => {
                    if (!prev.sales_label) return prev;
                    const next = { ...prev };
                    delete next.sales_label;
                    return next;
                  });
                }}
                data-testid="cdr-sales"
              >
                <option value="">— pilih Sales —</option>
                {(meta.sales_catalog || []).map((s) => (
                  <option key={s.id} value={s.id}>{s.sales_name}</option>
                ))}
              </select>
            ) : (
              <>
                <input
                  value={form.sales_label}
                  onChange={(e) => patch("sales_label", e.target.value)}
                  data-testid="cdr-sales"
                  placeholder={isNew ? "Master Sales belum diisi — hubungi pengelola" : form.sales_label}
                  disabled={isNew}
                />
                {isNew ? <p className="cdr-hint">Master Sales kosong. Isi di Pengaturan → Master Sales.</p> : null}
              </>
            )}
          </Field>

          <Field label="Komplain?" required>
            <div className="cdr-toggle">
              <button type="button" className={`is-danger ${form.has_complaint ? "is-on" : ""}`} data-testid="cdr-complaint-yes" onClick={() => patch("has_complaint", true)}>Ya</button>
              <button type="button" className={!form.has_complaint ? "is-on" : ""} data-testid="cdr-complaint-no" onClick={() => {
                patch("has_complaint", false);
                patch("complaint_description", "");
                patch("complaint_category_id", "");
              }}>Tidak</button>
            </div>
          </Field>
          <Field label="Kendala?" required>
            <div className="cdr-toggle">
              <button type="button" className={`is-danger ${form.has_issue ? "is-on" : ""}`} data-testid="cdr-issue-yes" onClick={() => patch("has_issue", true)}>Ya</button>
              <button type="button" className={!form.has_issue ? "is-on" : ""} data-testid="cdr-issue-no" onClick={() => {
                patch("has_issue", false);
                patch("issue_description", "");
                patch("issue_category_id", "");
              }}>Tidak</button>
            </div>
          </Field>

          {showNoOrder && (
            <>
              <Field label="Alasan Tidak Order" required wide error={errors.no_order_reason}>
                <select
                  value={form.no_order_reason_id || ""}
                  onChange={(e) => {
                    const oid = e.target.value;
                    const hit = (meta?.no_order_reasons || []).find((o) => o.id === oid);
                    patch("no_order_reason_id", oid);
                    if (oid && oid !== (meta?.other_option_id || "__other__")) {
                      patch("no_order_reason", hit?.label || "");
                    } else if (oid === (meta?.other_option_id || "__other__")) {
                      patch("no_order_reason", "");
                    }
                  }}
                  data-testid="cdr-no-order-reason-id"
                >
                  <option value="">— pilih alasan —</option>
                  {(meta?.no_order_reasons || []).map((o) => (
                    <option key={o.id} value={o.id}>{o.label}</option>
                  ))}
                </select>
              </Field>
              {(form.no_order_reason_id === (meta?.other_option_id || "__other__") || (!form.no_order_reason_id && form.no_order_reason)) && (
                <Field label="Penjelasan Tidak Order" required wide error={errors.no_order_reason}>
                  <textarea
                    value={form.no_order_reason}
                    onChange={(e) => patch("no_order_reason", e.target.value)}
                    maxLength={500}
                    data-testid="cdr-no-order-reason"
                  />
                </Field>
              )}
            </>
          )}
          {showComplaint && (
            <>
              <Field label="Kategori Komplain" required wide error={errors.complaint_description}>
                <select
                  value={form.complaint_category_id || ""}
                  onChange={(e) => {
                    const oid = e.target.value;
                    const hit = (meta?.complaint_categories || []).find((o) => o.id === oid);
                    patch("complaint_category_id", oid);
                    if (oid && oid !== (meta?.other_option_id || "__other__")) {
                      patch("complaint_description", hit?.label || "");
                    } else {
                      patch("complaint_description", "");
                    }
                  }}
                  data-testid="cdr-complaint-category"
                >
                  <option value="">— pilih kategori —</option>
                  {(meta?.complaint_categories || []).map((o) => (
                    <option key={o.id} value={o.id}>{o.label}</option>
                  ))}
                </select>
              </Field>
              {(form.complaint_category_id === (meta?.other_option_id || "__other__") || (!form.complaint_category_id && form.complaint_description)) && (
                <Field label="Deskripsi Komplain" required wide error={errors.complaint_description}>
                  <textarea
                    value={form.complaint_description}
                    onChange={(e) => patch("complaint_description", e.target.value)}
                    maxLength={500}
                    data-testid="cdr-complaint-desc"
                  />
                </Field>
              )}
            </>
          )}
          {showIssue && (
            <>
              <Field label="Kategori Kendala" required wide error={errors.issue_description}>
                <select
                  value={form.issue_category_id || ""}
                  onChange={(e) => {
                    const oid = e.target.value;
                    const hit = (meta?.issue_categories || []).find((o) => o.id === oid);
                    patch("issue_category_id", oid);
                    if (oid && oid !== (meta?.other_option_id || "__other__")) {
                      patch("issue_description", hit?.label || "");
                    } else {
                      patch("issue_description", "");
                    }
                  }}
                  data-testid="cdr-issue-category"
                >
                  <option value="">— pilih kategori —</option>
                  {(meta?.issue_categories || []).map((o) => (
                    <option key={o.id} value={o.id}>{o.label}</option>
                  ))}
                </select>
              </Field>
              {(form.issue_category_id === (meta?.other_option_id || "__other__") || (!form.issue_category_id && form.issue_description)) && (
                <Field label="Deskripsi Kendala" required wide error={errors.issue_description}>
                  <textarea
                    value={form.issue_description}
                    onChange={(e) => patch("issue_description", e.target.value)}
                    maxLength={500}
                    data-testid="cdr-issue-desc"
                  />
                </Field>
              )}
            </>
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
          <span className={`cdr-badge ${form.status_order === "ORDER" ? "is-order" : "is-no-order"}`}>{form.status_order}</span>
          <span className={`cdr-badge ${form.status_customer === "New" ? "is-new" : "is-repeat"}`}>{form.status_customer}</span>
          <span className={`cdr-badge ${form.has_complaint ? "is-ya" : ""}`}>Komplain: {form.has_complaint ? "Ya" : "Tidak"}</span>
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
