import {
  canAccessCrmDailyReport,
  canCreateCrmDailyReport,
  canViewAllCrmDailyReports,
  complaintLabel,
  emptyCrmDailyReportForm,
  issueLabel,
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

  test("new requires whatsapp; outlet optional", () => {
    const form = {
      ...emptyCrmDailyReportForm("2026-04-16"),
      status_customer: "New",
      whatsapp: "",
      outlet_name: "",
      sales_label: "AYUB",
      status_order: "ORDER",
    };
    expect(validateCrmDailyReportForm(form).whatsapp).toBeTruthy();
    form.whatsapp = "0812";
    expect(validateCrmDailyReportForm(form).whatsapp).toBeFalsy();
  });

  test("repeat requires customer; conditional fields", () => {
    const form = {
      ...emptyCrmDailyReportForm("2026-04-16"),
      status_customer: "Repeat",
      customer_id: "",
      sales_label: "AYUB",
      status_order: "TIDAK ORDER",
      no_order_reason: "",
      has_complaint: true,
      complaint_description: "",
      has_issue: true,
      issue_description: "",
    };
    const errors = validateCrmDailyReportForm(form);
    expect(errors.customer_id).toBeTruthy();
    expect(errors.no_order_reason).toBeTruthy();
    expect(errors.complaint_description).toBeTruthy();
    expect(errors.issue_description).toBeTruthy();
  });

  test("payload clears inactive conditional fields", () => {
    const payload = toCrmDailyReportPayload({
      ...emptyCrmDailyReportForm("2026-04-16"),
      status_customer: "New",
      whatsapp: "0812",
      outlet_name: "Toko",
      sales_label: "AYUB",
      status_order: "ORDER",
      no_order_reason: "should clear",
      has_complaint: false,
      complaint_description: "x",
      has_issue: false,
      issue_description: "y",
      notes: "n",
    }, "sub-1");
    expect(payload.no_order_reason).toBe("");
    expect(payload.complaint_description).toBe("");
    expect(payload.issue_description).toBe("");
    expect(payload.client_submit_id).toBe("sub-1");
  });

  test("list labels truncate without category badges", () => {
    expect(complaintLabel({ has_complaint: false })).toBe("Tidak");
    expect(issueLabel({ has_issue: true, issue_description: "Harga kompetitor terlalu murah sekali dan panjang" })).toMatch(/…$/);
    expect(truncateText("", 10)).toBe("—");
  });
});
