import fs from "fs";
import path from "path";

describe("Kas & Bank frontend contracts", () => {
  test("App routes include four Kas & Bank pages", () => {
    const src = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
    expect(src).toMatch(/\/kas-bank\/daftar/);
    expect(src).toMatch(/\/kas-bank\/penerimaan/);
    expect(src).toMatch(/\/kas-bank\/pembayaran/);
    expect(src).toMatch(/\/kas-bank\/transfer/);
    expect(src).toMatch(/cash_bank\.view/);
    expect(src).toMatch(/DaftarKasBank/);
    expect(src).toMatch(/PenerimaanKasBank/);
    expect(src).toMatch(/PembayaranKasBank/);
    expect(src).toMatch(/TransferKasBank/);
  });

  test("AppShell has Kas & Bank flyout + workspace tabs", () => {
    const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
    expect(shell).toMatch(/sidebar-flyout-cash-bank/);
    expect(shell).toMatch(/top-nav-kas-bank/);
    expect(shell).toMatch(/cashBankOpen && cashBankItems/);
    expect(shell).toMatch(/CashBankWorkspaceTabs/);
    expect(shell).toMatch(/useCashBankWorkspaceTabs/);
    expect(shell).toMatch(/content-kas-bank-workspace/);
  });

  test("auth logout clears Kas & Bank workspace tabs", () => {
    const auth = fs.readFileSync(path.join(__dirname, "../lib/auth.jsx"), "utf8");
    expect(auth).toMatch(/clearCashBankWorkspaceTabs/);
  });

  test("Voucher page full workspace form/detail + status actions", () => {
    const src = fs.readFileSync(path.join(__dirname, "CashBankVoucherPage.jsx"), "utf8");
    expect(src).toMatch(/cash_bank\.post/);
    expect(src).toMatch(/cash_bank\.reverse/);
    expect(src).toMatch(/status !== "draft"/);
    expect(src).toMatch(/Posting/);
    expect(src).toMatch(/Reversal/);
    expect(src).toMatch(/Draft/);
    expect(src).toMatch(/Posted/);
    expect(src).toMatch(/Reversed/);
    expect(src).toMatch(/kb-status-draft/);
    expect(src).toMatch(/kb-status-posted/);
    expect(src).toMatch(/kb-status-reversed/);
    expect(src).toMatch(/kb-workspace-panel/);
    expect(src).toMatch(/KbSection|CashBankFormPrimitives/);
    expect(src).toMatch(/KbField/);
    expect(src).toMatch(/kb-workspace-form-body/);
    expect(src).toMatch(/emp-modal-foot/);
    expect(src).toMatch(/form-error/);
    expect(src).toMatch(/Simpan Draft/);
    expect(src).toMatch(/Informasi Transaksi/);
    expect(src).toMatch(/Kas\/Bank Tujuan/);
    expect(src).toMatch(/Kas\/Bank Sumber/);
    expect(src).toMatch(/Dari Kas\/Bank/);
    expect(src).toMatch(/Ke Kas\/Bank/);
    expect(src).toMatch(/kb-transfer-flow/);
    expect(src).toMatch(/JournalReadOnly|kas-bank-journal-readonly/);
    expect(src).toMatch(/Total Debit/);
    expect(src).toMatch(/Total Credit/);
    expect(src).toMatch(/statusFilter/);
    expect(src).toMatch(/kb-toolbar/);
    expect(src).toMatch(/formatRp/);
    expect(src).toMatch(/className="num/);
    expect(src).toMatch(/empty-state/);
    expect(src).toMatch(/Trash2/);
  });

  test("Daftar page full workspace list/form/detail", () => {
    const src = fs.readFileSync(path.join(__dirname, "DaftarKasBank.jsx"), "utf8");
    expect(src).toMatch(/cash_bank\.manage/);
    expect(src).toMatch(/createCashBankAccount/);
    expect(src).toMatch(/kas-bank-workspace/);
    expect(src).toMatch(/kb-workspace-panel/);
    expect(src).toMatch(/KbSection|kb-section/);
    expect(src).toMatch(/KbField|kb-field/);
    expect(src).toMatch(/kb-workspace-form-body/);
    expect(src).toMatch(/emp-modal-foot/);
    expect(src).toMatch(/kas-bank-form-account/);
    expect(src).toMatch(/Nama Kas\/Bank/);
    expect(src).toMatch(/Akun Perkiraan/);
    expect(src).toMatch(/Status/);
    expect(src).toMatch(/Keterangan/);
    expect(src).toMatch(/Lihat/);
    expect(src).toMatch(/empty-state/);
    expect(src).toMatch(/pill-ok/);
    expect(src).toMatch(/kb-detail-grid/);
    expect(src).toMatch(/kb-toolbar/);
    expect(src).toMatch(/activeFilter/);
  });

  test("Daftar COA picker is BANK-only with safe legacy current link", () => {
    const src = fs.readFileSync(path.join(__dirname, "DaftarKasBank.jsx"), "utf8");
    expect(src).toMatch(/account_type/);
    expect(src).toMatch(/BANK/);
    expect(src).toMatch(/isBank/);
    expect(src).toMatch(/isCurrent/);
    expect(src).toMatch(/disabled=\{\!\!editRow\}/);
    expect(src).not.toMatch(/a\.name\.includes\(/);
    expect(src).not.toMatch(/toLowerCase\(\)\.includes\(["']kas/);
  });

  test("api client exposes cash bank endpoints", () => {
    const src = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
    expect(src).toMatch(/cashBankAccounts/);
    expect(src).toMatch(/postCashBankReceipt/);
    expect(src).toMatch(/reverseCashBankPayment/);
    expect(src).toMatch(/postCashBankTransfer/);
  });

  test("workspace CSS covers tabs + full content", () => {
    const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
    expect(css).toMatch(/\.kas-bank-page/);
    expect(css).toMatch(/kb-transfer-flow/);
    expect(css).toMatch(/kb-status-draft/);
    expect(css).toMatch(/kb-workspace-tabs/);
    expect(css).toMatch(/content-kas-bank-workspace/);
    expect(css).toMatch(/kb-workspace-panel/);
    expect(css).toMatch(/\.kb-field/);
    expect(css).toMatch(/\.kb-section/);
    expect(css).toMatch(/repeat\(2,minmax\(0,1fr\)\)/);
    expect(css).toMatch(/kb-toolbar/);
    expect(css).toMatch(/kb-journal-totals/);
    expect(css).toMatch(/1\.5px/);
  });

  test("cashBank module tokens exist", () => {
    const css = fs.readFileSync(path.join(__dirname, "../ui-system.css"), "utf8");
    expect(css).toMatch(/data-module="cashBank"/);
  });
});
