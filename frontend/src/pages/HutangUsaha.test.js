import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import HutangUsaha, {
  IntegrityMarker,
  integrityWarningLabel,
  wibCalendarDate,
  wibMonthStart,
} from "./HutangUsaha";

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("Hutang Usaha frontend contracts", () => {
  const src = fs.readFileSync(path.join(__dirname, "HutangUsaha.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");

  test("route, RBAC, and api client are wired", () => {
    expect(app).toMatch(/\/buku-besar\/hutang-usaha/);
    expect(app).toMatch(/HutangUsaha/);
    expect(app).toMatch(/accounts_payable\.view/);
    expect(app).toMatch(/NAV_KEYS\.ledgerAccountsPayable/);
    expect(api).toMatch(/accountsPayableSummary/);
    expect(api).toMatch(/accountsPayableAging/);
    expect(api).toMatch(/accountsPayableSuppliers/);
    expect(api).toMatch(/accountsPayableStatement/);
    expect(api).toMatch(/\/ledger\/accounts-payable\/summary/);
  });

  test("compact workspace tabs and aging reference label", () => {
    expect(src).toMatch(/hutang-usaha-page/);
    expect(src).toMatch(/hutang-tab-\$\{t\.id\}/);
    expect(src).toMatch(/id: "ringkasan"/);
    expect(src).toMatch(/id: "aging"/);
    expect(src).toMatch(/id: "suppliers"/);
    expect(src).toMatch(/id: "statement"/);
    expect(src).toMatch(/Tanggal Acuan Aging/);
    expect(src).not.toMatch(/As Of Accounting/);
    expect(src).toMatch(/hutang-aging-reference-date/);
    expect(src).toMatch(/kb-ar-table-scroll/);
  });

  test("ringkasan cards, aging table, statement, and disclaimers", () => {
    expect(src).toMatch(/Accounting AP/);
    expect(src).toMatch(/Operational Outstanding/);
    expect(src).toMatch(/Pending Payment/);
    expect(src).toMatch(/Selisih non-Faktur \/ jurnal lain/);
    expect(src).toMatch(/hutang-aging-table/);
    expect(src).toMatch(/hutang-suppliers-table/);
    expect(src).toMatch(/hutang-statement-table/);
    expect(src).toMatch(/hutang-pending-payments/);
    expect(src).toMatch(/Hutang Accounting dihitung dari jurnal yang sudah diposting/);
    expect(src).toMatch(/Statement mengikuti tanggal jurnal/);
    expect(src).toMatch(/openSupplierStatement/);
    expect(src).toMatch(/Rekonsiliasi GL menggunakan seluruh Hutang Accounting saat ini/);
    expect(src).toMatch(/hutang-integrity-panel/);
    expect(src).toMatch(/hutang-aging-integrity-/);
    expect(src).toMatch(/wibCalendarDate/);
    expect(src).not.toMatch(/toISOString\(\)\.slice\(0, 10\)/);
  });

  test("GET-only API usage and no write helpers", () => {
    expect(src).toMatch(/api\.accountsPayableSummary/);
    expect(src).toMatch(/api\.accountsPayableAging/);
    expect(src).toMatch(/api\.accountsPayableSuppliers/);
    expect(src).toMatch(/api\.accountsPayableStatement/);
    expect(src).not.toMatch(/api\.\w+\(.*post/i);
    expect(api).not.toMatch(/accountsPayable.*(post|patch|delete)/i);
  });

  test("nav includes Hutang Usaha under Buku Besar", () => {
    expect(nav).toMatch(/ledgerAccountsPayable/);
    expect(nav).toMatch(/nav\.ledger\.accounts_payable/);
    expect(nav).toMatch(/\/buku-besar\/hutang-usaha/);
    expect(nav).toMatch(/accounts_payable\.view/);
    expect(shell).toMatch(/NAV_KEYS\.ledgerAccountsPayable/);
  });
});

describe("Hutang Usaha integrity visibility and WIB date", () => {
  test("WIB calendar date ignores UTC midnight and host timezone", () => {
    expect(wibCalendarDate("2026-09-19T18:30:00Z")).toBe("2026-09-20");
    expect(wibMonthStart("2026-09-19T18:30:00Z")).toBe("2026-09-01");
    expect(wibCalendarDate(new Date("2026-09-20T00:00:00+07:00"))).toBe("2026-09-20");
  });

  test("warning labels are human readable", () => {
    expect(integrityWarningLabel("PAYMENT_AP_ACCOUNT_MISMATCH")).toBe("Akun AP pembayaran tidak sesuai faktur");
    expect(integrityWarningLabel("NEGATIVE_ACCOUNTING_AP")).toBe("Saldo Accounting AP negatif");
    expect(integrityWarningLabel("missing_purchase_invoice_document")).toBe("Jurnal faktur tidak memiliki dokumen Faktur Pembelian");
  });

  test("Aging integrity marker renders warning text only when codes exist", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
      root.render(
        <div>
          <IntegrityMarker warnings={["PAYMENT_AP_ACCOUNT_MISMATCH"]} testid="warn-row" />
          <IntegrityMarker warnings={[]} testid="clean-row" />
        </div>
      );
    });
    expect(container.querySelector('[data-testid="warn-row"]').textContent).toContain("Perhatian");
    expect(container.querySelector('[data-testid="warn-row"]').getAttribute("title")).toContain("Akun AP pembayaran tidak sesuai faktur");
    expect(container.querySelector('[data-testid="clean-row"]').textContent).toBe("—");
    act(() => root.unmount());
    container.remove();
  });

  test("summary anomaly panel renders when integrity_anomalies exist", () => {
    const api = require("../lib/api");
    api.default.accountsPayableSummary = jest.fn().mockResolvedValue({
      total_accounting_ap: 10,
      total_operational_outstanding: 10,
      total_pending_payments: 0,
      not_due: 10,
      d1_30: 0,
      d31_60: 0,
      d61_90: 0,
      over_90: 0,
      account_breakdown: [],
      reconciliation_note: "Rekonsiliasi GL menggunakan seluruh Hutang Accounting saat ini dan tidak dipersempit oleh filter supplier atau aging.",
      integrity_anomalies: [{
        code: "PAYMENT_AP_ACCOUNT_MISMATCH",
        label: "Akun AP pembayaran tidak sesuai faktur",
        invoice_number: "FPB-1",
      }],
    });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
      root.render(<HutangUsaha />);
    });
    return Promise.resolve().then(() => act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    })).then(() => {
      expect(container.querySelector('[data-testid="hutang-integrity-panel"]').textContent).toContain("1 anomali integritas accounting terdeteksi");
      expect(container.querySelector('[data-testid="hutang-integrity-list"]').textContent).toContain("Akun AP pembayaran tidak sesuai faktur");
      expect(container.querySelector('[data-testid="hutang-recon-scope"]').textContent).toContain("tidak dipersempit");
      act(() => root.unmount());
      container.remove();
    });
  });
});
