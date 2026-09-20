import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import PiutangUsaha, {
  IntegrityMarker,
  integrityWarningLabel,
  wibCalendarDate,
  wibMonthStart,
} from "./PiutangUsaha";

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("Piutang Usaha frontend contracts", () => {
  const src = fs.readFileSync(path.join(__dirname, "PiutangUsaha.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");

  test("route, RBAC, and api client are wired", () => {
    expect(app).toMatch(/\/buku-besar\/piutang-usaha/);
    expect(app).toMatch(/PiutangUsaha/);
    expect(app).toMatch(/accounts_receivable\.view/);
    expect(app).toMatch(/NAV_KEYS\.ledgerAccountsReceivable/);
    expect(api).toMatch(/accountsReceivableSummary/);
    expect(api).toMatch(/accountsReceivableAging/);
    expect(api).toMatch(/accountsReceivableCustomers/);
    expect(api).toMatch(/accountsReceivableStatement/);
    expect(api).toMatch(/\/ledger\/accounts-receivable\/summary/);
  });

  test("compact workspace tabs and aging reference label", () => {
    expect(src).toMatch(/piutang-usaha-page/);
    expect(src).toMatch(/piutang-tab-\$\{t\.id\}/);
    expect(src).toMatch(/id: "ringkasan"/);
    expect(src).toMatch(/id: "aging"/);
    expect(src).toMatch(/id: "customers"/);
    expect(src).toMatch(/id: "statement"/);
    expect(src).toMatch(/Tanggal Acuan Aging/);
    expect(src).not.toMatch(/As Of Accounting/);
    expect(src).toMatch(/piutang-aging-reference-date/);
    expect(src).toMatch(/kb-ar-table-scroll/);
  });

  test("ringkasan cards, aging table, statement, and disclaimers", () => {
    expect(src).toMatch(/Accounting AR/);
    expect(src).toMatch(/Operational Outstanding/);
    expect(src).toMatch(/Pending Receipt/);
    expect(src).toMatch(/Selisih non-Faktur \/ jurnal lain/);
    expect(src).toMatch(/piutang-aging-table/);
    expect(src).toMatch(/piutang-customers-table/);
    expect(src).toMatch(/piutang-statement-table/);
    expect(src).toMatch(/piutang-pending-receipts/);
    expect(src).toMatch(/Piutang Accounting dihitung dari jurnal yang sudah diposting/);
    expect(src).toMatch(/Statement mengikuti tanggal jurnal/);
    expect(src).toMatch(/openCustomerStatement/);
    expect(src).toMatch(/Rekonsiliasi GL menggunakan seluruh Piutang Accounting saat ini/);
    expect(src).toMatch(/piutang-integrity-panel/);
    expect(src).toMatch(/piutang-aging-integrity-/);
    expect(src).toMatch(/wibCalendarDate/);
    expect(src).not.toMatch(/toISOString\(\)\.slice\(0, 10\)/);
  });

  test("GET-only API usage and no write helpers", () => {
    expect(src).toMatch(/api\.accountsReceivableSummary/);
    expect(src).toMatch(/api\.accountsReceivableAging/);
    expect(src).toMatch(/api\.accountsReceivableCustomers/);
    expect(src).toMatch(/api\.accountsReceivableStatement/);
    expect(src).not.toMatch(/api\.\w+\(.*post/i);
    expect(api).not.toMatch(/accountsReceivable.*(post|patch|delete)/i);
  });

  test("nav includes Piutang Usaha under Buku Besar", () => {
    expect(nav).toMatch(/ledgerAccountsReceivable/);
    expect(nav).toMatch(/nav\.ledger\.accounts_receivable/);
    expect(nav).toMatch(/\/buku-besar\/piutang-usaha/);
    expect(nav).toMatch(/accounts_receivable\.view/);
    expect(shell).toMatch(/NAV_KEYS\.ledgerAccountsReceivable/);
    expect(css).toMatch(/kb-ar-tabs/);
    expect(css).toMatch(/kb-ar-table-scroll/);
  });
});

describe("Piutang Usaha integrity visibility and WIB date", () => {
  test("WIB calendar date ignores UTC midnight and host timezone", () => {
    expect(wibCalendarDate("2026-09-19T18:30:00Z")).toBe("2026-09-20");
    expect(wibMonthStart("2026-09-19T18:30:00Z")).toBe("2026-09-01");
    expect(wibCalendarDate(new Date("2026-09-20T00:00:00+07:00"))).toBe("2026-09-20");
  });

  test("warning labels are human readable", () => {
    expect(integrityWarningLabel("RECEIPT_AR_ACCOUNT_MISMATCH")).toBe("Akun AR penerimaan tidak sesuai faktur");
    expect(integrityWarningLabel("NEGATIVE_ACCOUNTING_OUTSTANDING")).toBe("Saldo Accounting AR negatif");
    expect(integrityWarningLabel("missing_sales_invoice_document")).toBe("Jurnal faktur tidak memiliki dokumen Faktur Penjualan");
  });

  test("Aging integrity marker renders warning text only when codes exist", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
      root.render(
        <div>
          <IntegrityMarker warnings={["RECEIPT_AR_ACCOUNT_MISMATCH"]} testid="warn-row" />
          <IntegrityMarker warnings={[]} testid="clean-row" />
        </div>
      );
    });
    expect(container.querySelector('[data-testid="warn-row"]').textContent).toContain("Perhatian");
    expect(container.querySelector('[data-testid="warn-row"]').getAttribute("title")).toContain("Akun AR penerimaan tidak sesuai faktur");
    expect(container.querySelector('[data-testid="clean-row"]').textContent).toBe("—");
    act(() => root.unmount());
    container.remove();
  });

  test("summary anomaly panel renders when integrity_anomalies exist", () => {
    const api = require("../lib/api");
    api.default.accountsReceivableSummary = jest.fn().mockResolvedValue({
      total_accounting_ar: 10,
      total_operational_outstanding: 10,
      total_pending_receipts: 0,
      not_due: 10,
      d1_30: 0,
      d31_60: 0,
      d61_90: 0,
      over_90: 0,
      account_breakdown: [],
      reconciliation_note: "Rekonsiliasi GL menggunakan seluruh Piutang Accounting saat ini dan tidak dipersempit oleh filter pelanggan, sales, atau aging.",
      integrity_anomalies: [{
        code: "RECEIPT_AR_ACCOUNT_MISMATCH",
        label: "Akun AR penerimaan tidak sesuai faktur",
        invoice_number: "INV-1",
      }],
    });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
      root.render(<PiutangUsaha />);
    });
    return Promise.resolve().then(() => act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    })).then(() => {
      expect(container.querySelector('[data-testid="piutang-integrity-panel"]').textContent).toContain("1 anomali integritas accounting terdeteksi");
      expect(container.querySelector('[data-testid="piutang-integrity-list"]').textContent).toContain("Akun AR penerimaan tidak sesuai faktur");
      expect(container.querySelector('[data-testid="piutang-recon-scope"]').textContent).toContain("tidak dipersempit");
      act(() => root.unmount());
      container.remove();
    });
  });
});
