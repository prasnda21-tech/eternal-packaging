import fs from "fs";
import path from "path";

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
