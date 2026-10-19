import {
  COMPLAINT_CATEGORY_OPTIONS,
  NO_ORDER_REASON_LAINNYA,
  NO_ORDER_REASON_PRESETS,
  SALES_MANUAL_CHOICE,
  SALES_SOURCE_EXISTING,
  SALES_SOURCE_MANUAL,
  STATUS_ORDER_OPTIONS,
  canAccessCrmDailyReport,
  canCreateCrmDailyReport,
  canUseManualSales,
  canViewAllCrmDailyReports,
  complaintLabel,
  emptyCrmDailyReportForm,
  formFromCrmDailyReport,
  issueLabel,
  noOrderReasonChoice,
  toCrmDailyReportPayload,
  truncateText,
  validateCrmDailyReportForm,
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
});
