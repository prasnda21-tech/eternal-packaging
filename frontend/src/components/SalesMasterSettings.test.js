const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "SalesMasterSettings.jsx"), "utf8");
const crmSrc = fs.readFileSync(path.join(__dirname, "CrmDailyReportOptionsSettings.jsx"), "utf8");

describe("SalesMasterSettings + CrmDailyReportOptionsSettings", () => {
  test("master sales uses controlled inputs and portaled drawer", () => {
    expect(src).toMatch(/data-testid="sales-master-tab"/);
    expect(src).toMatch(/data-testid="sales-master-name"/);
    expect(src).toMatch(/salesMasterCreate|salesMasterUpdate/);
    expect(src).toMatch(/onChange=\{\(e\) => patchDraft\("sales_name"/);
    expect(src).toMatch(/data-testid="sales-master-drawer"/);
    expect(src).toMatch(/createPortal/);
    expect(src).toMatch(/sales-master-drawer-head/);
    expect(src).toMatch(/sales-master-drawer-foot/);
    expect(src).toMatch(/data-testid="sales-master-search"/);
    expect(src).toMatch(/pill-ok|pill-cancel/);
    expect(src).toMatch(/sales-master-field-error/);
    expect(src).toMatch(/sm-col-resize|sm-filter-/);
    expect(src).toMatch(/sales-master-reset-layout/);
    expect(src).toMatch(/writeSalesMasterColPrefs|readSalesMasterColPrefs/);
    expect(src).toMatch(/sales-master-rename-warning/);
    expect(src).toMatch(/sales-master-rename-ack/);
    expect(src).toMatch(/sales_user_mapping/);
    expect(src).toMatch(/Alias Accurate lama tetap terhubung/);
    expect(src).toMatch(/atribusi laporan[\s\S]*historis|atribusi historis|tidak mengubah mapping \/ atribusi/);
    expect(src).toMatch(/Batalkan dengan menutup drawer tanpa Simpan/i);
    expect(src).toMatch(/renamePending && !renameAck/);
    expect(src).toMatch(/sales-master-pwa-hint/);
    expect(src).toMatch(/Persetujuan alias saja tidak memberi akses PWA/);
    expect(src).toMatch(/sales-master-pwa-status/);
    expect(src).toMatch(/data-testid="sales-master-cancel"/);
  });

  test("crm options keep __other__ and controlled labels", () => {
    expect(crmSrc).toMatch(/__other__/);
    expect(crmSrc).toMatch(/no_order_reasons/);
    expect(crmSrc).toMatch(/complaint_categories/);
    expect(crmSrc).toMatch(/issue_categories/);
    expect(crmSrc).toMatch(/updateCrmDailyReportOptionsSettings/);
    expect(crmSrc).toMatch(/data-testid=\{`crm-opt-label-\$\{cat\.key\}-\$\{i\}`\}/);
  });
});
