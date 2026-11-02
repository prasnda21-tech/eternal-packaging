import {
  COMPLAINT_CATEGORY_OPTIONS,
  CRM_DAILY_DRAFT_PREFIX,
  NO_ORDER_REASON_LAINNYA,
  NO_ORDER_REASON_PRESETS,
  PO_CUSTOMER_MASTER_ACTIVE,
  PO_CUSTOMER_MASTER_INACTIVE,
  PO_CUSTOMER_MASTER_MISSING,
  PO_CUSTOMER_MASTER_NONE,
  SALES_MANUAL_CHOICE,
  SALES_SOURCE_EXISTING,
  SALES_SOURCE_MANUAL,
  STATUS_ORDER_OPTIONS,
  assessCrmPoCustomerLink,
  buildCrmDailyDraftSnapshot,
  canAccessCrmDailyReport,
  canCreateCrmDailyReport,
  canUseManualSales,
  canViewAllCrmDailyReports,
  clearCrmDailyDraft,
  clearCrmDailyDraftsForUser,
  complaintLabel,
  crmDailyDraftStorageKey,
  emptyCrmDailyReportForm,
  formFromCrmDailyReport,
  issueLabel,
  noOrderReasonChoice,
  readCrmDailyDraft,
  sanitizeCrmDailyDraft,
  toCrmDailyReportPayload,
  truncateText,
  validateCrmDailyReportForm,
  writeCrmDailyDraft,
} from "./crmDailyReport";

const has = (...keys) => (k) => keys.includes(k);

describe("crmDailyReport helpers", () => {
  test("permission helpers", () => {
    expect(canCreateCrmDailyReport(has("crm.daily_report.create"))).toBe(true);
    expect(canViewAllCrmDailyReports(has("crm.daily_report.view_all"))).toBe(true);
    expect(canAccessCrmDailyReport(has("crm.daily_report.view_own"))).toBe(true);
    expect(canAccessCrmDailyReport(has("po.view"))).toBe(false);
  });

  test("excel-aligned option lists", () => {
    expect(STATUS_ORDER_OPTIONS).toEqual([
      "ORDER",
      "TIDAK ORDER",
      "PENGAJUAN PO",
      "PENGAJUAN DITOLAK",
      "FOLLOW UP",
    ]);
    expect(NO_ORDER_REASON_PRESETS).toHaveLength(6);
    expect(COMPLAINT_CATEGORY_OPTIONS).toContain("CUP CACAT");
  });

  test("new requires whatsapp; outlet optional", () => {
    const form = {
      ...emptyCrmDailyReportForm("2026-04-16"),
      status_customer: "New",
      whatsapp: "",
      outlet_name: "",
      sales_user_id: "u1",
      sales_label: "Ayub",
      sales_source: SALES_SOURCE_EXISTING,
      status_order: "ORDER",
    };
    expect(validateCrmDailyReportForm(form).whatsapp).toBeTruthy();
    form.whatsapp = "0812";
    expect(validateCrmDailyReportForm(form).whatsapp).toBeFalsy();
  });

  test("sales existing required; manual only for TIDAK ORDER without PO", () => {
    const base = {
      ...emptyCrmDailyReportForm("2026-04-16"),
      whatsapp: "0812",
      status_order: "ORDER",
    };
    expect(validateCrmDailyReportForm(base).sales_label).toBeTruthy();
    expect(canUseManualSales(base)).toBe(false);

    const manualOrder = {
      ...base,
      sales_choice: SALES_MANUAL_CHOICE,
      sales_source: SALES_SOURCE_MANUAL,
      sales_label: "Freelance",
    };
    expect(validateCrmDailyReportForm(manualOrder).sales_label).toMatch(/Input Manual/);

    const manualOk = {
      ...base,
      status_order: "TIDAK ORDER",
      no_order_reason_choice: "Harga terlalu mahal",
      no_order_reason: "Harga terlalu mahal",
      sales_choice: SALES_MANUAL_CHOICE,
      sales_source: SALES_SOURCE_MANUAL,
      sales_label: "Freelance",
      sales_user_id: "",
    };
    expect(canUseManualSales(manualOk)).toBe(true);
    expect(validateCrmDailyReportForm(manualOk).sales_label).toBeFalsy();
    expect(validateCrmDailyReportForm({ ...manualOk, sales_label: "" }).sales_label).toMatch(/Nama Sales Manual/);

    const manualWithPo = { ...manualOk, order_id: "ord-1" };
    expect(canUseManualSales(manualWithPo)).toBe(false);
    expect(validateCrmDailyReportForm(manualWithPo).sales_label).toBeTruthy();
  });

  test("payload clears inactive conditional fields and sets sales_source", () => {
    const payload = toCrmDailyReportPayload({
      ...emptyCrmDailyReportForm("2026-04-16"),
      status_customer: "New",
      whatsapp: "0812",
      outlet_name: "Toko",
      sales_label: "Ayub",
      sales_user_id: "u-ayub",
      sales_source: SALES_SOURCE_EXISTING,
      status_order: "ORDER",
      no_order_reason: "should clear",
      no_order_reason_choice: "Harga terlalu mahal",
      has_complaint: false,
      complaint_category: "CUP CACAT",
      complaint_description: "x",
      has_issue: false,
      issue_description: "y",
      notes: "n",
    }, "sub-1");
    expect(payload.no_order_reason).toBe("");
    expect(payload.complaint_category).toBe("");
    expect(payload.sales_source).toBe(SALES_SOURCE_EXISTING);
    expect(payload.sales_user_id).toBe("u-ayub");
    expect(payload.client_submit_id).toBe("sub-1");

    const manualPayload = toCrmDailyReportPayload({
      ...emptyCrmDailyReportForm("2026-04-16"),
      whatsapp: "0812",
      status_order: "TIDAK ORDER",
      no_order_reason_choice: NO_ORDER_REASON_LAINNYA,
      no_order_reason: "Budget",
      sales_choice: SALES_MANUAL_CHOICE,
      sales_source: SALES_SOURCE_MANUAL,
      sales_label: "Manual Name",
      sales_user_id: "should-clear",
    });
    expect(manualPayload.sales_source).toBe(SALES_SOURCE_MANUAL);
    expect(manualPayload.sales_user_id).toBeNull();
    expect(manualPayload.sales_label).toBe("Manual Name");
  });

  test("legacy sales form opens without forcing remapping", () => {
    const form = formFromCrmDailyReport({
      report_date: "2026-04-16",
      status_customer: "New",
      whatsapp: "0812",
      status_order: "ORDER",
      sales_label: "Old Free Text",
      sales_user_id: null,
    });
    expect(form.sales_source).toBe("legacy");
    expect(validateCrmDailyReportForm(form).sales_label).toBeFalsy();
    expect(noOrderReasonChoice("Teks bebas lama")).toBe(NO_ORDER_REASON_LAINNYA);
  });

  test("list labels truncate without category badges", () => {
    expect(complaintLabel({ has_complaint: false })).toBe("Tidak");
    expect(complaintLabel({ has_complaint: true, complaint_category: "CUP CACAT", complaint_description: "panjang sekali deskripsi" })).toBe("CUP CACAT");
    expect(issueLabel({ has_issue: true, issue_description: "Harga kompetitor terlalu murah sekali dan panjang" })).toMatch(/…$/);
    expect(truncateText("", 10)).toBe("—");
  });

  test("draft storage keys are user/mode/record scoped", () => {
    const a = { id: "user-a" };
    const b = { id: "user-b" };
    expect(crmDailyDraftStorageKey(a, "create")).toBe(`${CRM_DAILY_DRAFT_PREFIX}user-a:create:new`);
    expect(crmDailyDraftStorageKey(a, "edit", "r9")).toBe(`${CRM_DAILY_DRAFT_PREFIX}user-a:edit:r9`);
    expect(crmDailyDraftStorageKey(a, "create")).not.toBe(crmDailyDraftStorageKey(b, "create"));
    expect(crmDailyDraftStorageKey(a, "edit", "r1")).not.toBe(crmDailyDraftStorageKey(a, "edit", "r2"));
    expect(crmDailyDraftStorageKey(null, "create")).toBe("");
  });

  test("draft sanitize rejects bad version and strips invalid manual sales", () => {
    expect(sanitizeCrmDailyDraft({ v: 99, form: {} })).toBeNull();
    const snap = buildCrmDailyDraftSnapshot({
      ...emptyCrmDailyReportForm("2026-04-16"),
      status_order: "ORDER",
      sales_source: SALES_SOURCE_MANUAL,
      sales_choice: SALES_MANUAL_CHOICE,
      sales_label: "Manual X",
      whatsapp: "0812",
    }, { salesQ: "Manual X" });
    expect(snap.form.sales_source).toBe("");
    expect(snap.form.sales_label).toBe("");
  });

  test("draft read/write/clear is user-isolated and safe on storage errors", () => {
    const store = window.sessionStorage;
    store.clear();
    const userA = { id: "ua" };
    const userB = { id: "ub" };
    const keyA = crmDailyDraftStorageKey(userA, "create");
    const keyB = crmDailyDraftStorageKey(userB, "create");
    writeCrmDailyDraft(keyA, buildCrmDailyDraftSnapshot({
      ...emptyCrmDailyReportForm("2026-04-16"),
      whatsapp: "08121111",
      notes: "draft-a",
    }, {}));
    writeCrmDailyDraft(keyB, buildCrmDailyDraftSnapshot({
      ...emptyCrmDailyReportForm("2026-04-16"),
      whatsapp: "08122222",
      notes: "draft-b",
    }, {}));
    expect(readCrmDailyDraft(keyA).form.notes).toBe("draft-a");
    expect(readCrmDailyDraft(keyB).form.notes).toBe("draft-b");
    clearCrmDailyDraftsForUser(userA);
    expect(readCrmDailyDraft(keyA)).toBeNull();
    expect(readCrmDailyDraft(keyB).form.notes).toBe("draft-b");
    clearCrmDailyDraft(keyB);
    expect(readCrmDailyDraft(keyB)).toBeNull();
    expect(writeCrmDailyDraft("", buildCrmDailyDraftSnapshot(emptyCrmDailyReportForm(), {}))).toBe(false);
  });

  test("assessCrmPoCustomerLink binds active master on PO-first and rejects mismatch", () => {
    const activeOrder = {
      id: "ord-a",
      po_number: "PO/A",
      customer_id: "c-a",
      customer: "KOMMU SPACE CIREBON",
      customer_master_status: PO_CUSTOMER_MASTER_ACTIVE,
      customer_master: { id: "c-a", name: "KOMMU SPACE CIREBON", phone: "0812", city: "Cirebon" },
    };
    const bind = assessCrmPoCustomerLink({
      order: activeOrder,
      selectedCustomerId: "",
      statusCustomer: "Repeat",
    });
    expect(bind.action).toBe("allow");
    expect(bind.bindCustomer.id).toBe("c-a");
    expect(bind.bindCustomer.name).toBe("KOMMU SPACE CIREBON");

    const mismatch = assessCrmPoCustomerLink({
      order: activeOrder,
      selectedCustomerId: "c-other",
      statusCustomer: "Repeat",
    });
    expect(mismatch.action).toBe("reject");
    expect(mismatch.reason).toMatch(/tidak sesuai/i);

    const inactive = assessCrmPoCustomerLink({
      order: {
        ...activeOrder,
        customer_master_status: PO_CUSTOMER_MASTER_INACTIVE,
        customer_master: { id: "c-a", name: "X", is_active: false },
      },
      selectedCustomerId: "",
      statusCustomer: "Repeat",
    });
    expect(inactive.action).toBe("reject");
    expect(inactive.reason).toMatch(/nonaktif/i);

    const missing = assessCrmPoCustomerLink({
      order: {
        id: "ord-m",
        customer_id: "gone",
        customer_master_status: PO_CUSTOMER_MASTER_MISSING,
        customer_master: null,
      },
      selectedCustomerId: "",
      statusCustomer: "Repeat",
    });
    expect(missing.action).toBe("reject");

    const legacy = assessCrmPoCustomerLink({
      order: {
        id: "ord-l",
        customer_id: "",
        customer: "KOMMU SPACE CIREBON",
        customer_master_status: PO_CUSTOMER_MASTER_NONE,
      },
      selectedCustomerId: "c-a",
      statusCustomer: "Repeat",
    });
    expect(legacy.action).toBe("confirm_legacy");
    expect(legacy.requireLegacyConfirm).toBe(true);

    const legacyConfirmed = assessCrmPoCustomerLink({
      order: {
        id: "ord-l",
        customer_id: "",
        customer_master_status: PO_CUSTOMER_MASTER_NONE,
      },
      selectedCustomerId: "c-a",
      statusCustomer: "Repeat",
      legacyConfirmed: true,
    });
    expect(legacyConfirmed.action).toBe("allow");

    // Same name prefix must not auto-merge distinct masters.
    const kudus = {
      id: "ord-k",
      customer_id: "c-kudus",
      customer: "KOMMU SPACE KUDUS",
      customer_master_status: PO_CUSTOMER_MASTER_ACTIVE,
      customer_master: { id: "c-kudus", name: "KOMMU SPACE KUDUS", phone: "0813" },
    };
    expect(assessCrmPoCustomerLink({
      order: kudus,
      selectedCustomerId: "c-uns",
      statusCustomer: "Repeat",
    }).action).toBe("reject");
  });

  test("legacy PO confirm required on validate + payload flag", () => {
    const form = {
      ...emptyCrmDailyReportForm("2026-04-16"),
      status_customer: "Repeat",
      customer_id: "c-a",
      order_id: "ord-l",
      po_number: "PO/LEGACY",
      sales_user_id: "u1",
      sales_label: "Ayub",
      sales_source: SALES_SOURCE_EXISTING,
      legacy_po_needs_confirm: true,
      legacy_po_customer_confirmed: false,
    };
    expect(validateCrmDailyReportForm(form).order_id).toMatch(/legacy/i);
    form.legacy_po_customer_confirmed = true;
    expect(validateCrmDailyReportForm(form).order_id).toBeFalsy();
    const payload = toCrmDailyReportPayload(form);
    expect(payload.legacy_po_customer_confirmed).toBe(true);
  });
});
