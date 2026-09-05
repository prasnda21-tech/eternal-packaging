import fs from "fs";
import path from "path";
import { cashBankMenuItems, cashBankNavVisible, NAV_KEYS, NAV_MENU_FALLBACK } from "./navAccess";

const has = (...keys) => (k) => keys.includes(k);

describe("cashBank nav", () => {
  test("fallback menus include Kas & Bank children", () => {
    const kids = NAV_MENU_FALLBACK.filter((m) => m.parent === NAV_KEYS.cashBank || m.key === NAV_KEYS.cashBank);
    expect(kids.map((m) => m.key)).toEqual(expect.arrayContaining([
      NAV_KEYS.cashBank,
      NAV_KEYS.cashBankAccounts,
      NAV_KEYS.cashBankReceipts,
      NAV_KEYS.cashBankPayments,
      NAV_KEYS.cashBankTransfers,
    ]));
  });

  test("no page perm → hidden", () => {
    const v = cashBankNavVisible(has());
    expect(v.parent).toBe(false);
    expect(cashBankMenuItems(has()).length).toBe(0);
  });

  test("cash_bank.view derives all four menus", () => {
    const v = cashBankNavVisible(has("cash_bank.view"));
    expect(v.parent).toBe(true);
    expect(v.accounts).toBe(true);
    expect(v.receipts).toBe(true);
    expect(v.payments).toBe(true);
    expect(v.transfers).toBe(true);
    expect(cashBankMenuItems(has("cash_bank.view")).map((i) => i.to)).toEqual([
      "/kas-bank/daftar",
      "/kas-bank/penerimaan",
      "/kas-bank/pembayaran",
      "/kas-bank/transfer",
    ]);
  });
});
