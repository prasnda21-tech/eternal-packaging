import {
  COMPLAINT_CATEGORY_OPTIONS,
  CRM_DAILY_DRAFT_PREFIX,
  NO_ORDER_REASON_LAINNYA,
  NO_ORDER_REASON_PRESETS,
  SALES_MANUAL_CHOICE,
  SALES_SOURCE_EXISTING,
  SALES_SOURCE_MANUAL,
  STATUS_ORDER_OPTIONS,
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

    // Active Master without Eternal account is verified (not manual).
    const masterNoUser = {
      ...base,
      sales_master_id: "sm-offline",
      sales_label: "Sales Offline",
      sales_user_id: "",
      sales_source: SALES_SOURCE_EXISTING,
      sales_choice: "sm-offline",
    };
    expect(validateCrmDailyReportForm(masterNoUser).sales_label).toBeFalsy();
    expect(validateCrmDailyReportForm({
      ...manualOk,
      sales_master_id: "sm-offline",
    }).sales_label).toMatch(/Master Sales/);
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
      sales_master_id: "should-clear",
    });
    expect(manualPayload.sales_source).toBe(SALES_SOURCE_MANUAL);
    expect(manualPayload.sales_user_id).toBeNull();
    expect(manualPayload.sales_master_id).toBeNull();
    expect(manualPayload.sales_label).toBe("Manual Name");

    const masterNoUserPayload = toCrmDailyReportPayload({
      ...emptyCrmDailyReportForm("2026-04-16"),
      whatsapp: "0812",
      status_order: "ORDER",
      sales_master_id: "sm-offline",
      sales_label: "Sales Offline",
      sales_user_id: "",
      sales_source: SALES_SOURCE_EXISTING,
      sales_choice: "sm-offline",
    });
    expect(masterNoUserPayload.sales_source).toBe(SALES_SOURCE_EXISTING);
    expect(masterNoUserPayload.sales_master_id).toBe("sm-offline");
    expect(masterNoUserPayload.sales_user_id).toBeNull();
  });

  test("master without account opens as existing, not legacy/manual", () => {
    const form = formFromCrmDailyReport({
      report_date: "2026-04-16",
      status_customer: "New",
      whatsapp: "0812",
      status_order: "ORDER",
      sales_label: "Sales Offline",
      sales_user_id: null,
      sales_master_id: "sm-offline",
      sales_source: "existing",
    });
    expect(form.sales_source).toBe(SALES_SOURCE_EXISTING);
    expect(form.sales_choice).toBe("sm-offline");
    expect(form.sales_master_id).toBe("sm-offline");
    expect(validateCrmDailyReportForm(form).sales_label).toBeFalsy();
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
});
