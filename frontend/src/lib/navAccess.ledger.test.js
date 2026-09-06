import { ledgerMenuItems, ledgerNavVisible, NAV_KEYS, NAV_MENU_FALLBACK } from "./navAccess";

const has = (...keys) => (k) => keys.includes(k);

describe("Buku Besar nav", () => {
  test("sidebar catalog includes parent, Akun Perkiraan, Jurnal Umum, Saldo Awal", () => {
    const labels = NAV_MENU_FALLBACK
      .filter((m) => m.parent === NAV_KEYS.ledger || m.key === NAV_KEYS.ledger)
      .map((m) => m.label);
    expect(labels).toEqual(["Buku Besar", "Akun Perkiraan", "Jurnal Umum", "Saldo Awal"]);
  });

  test("hidden without grants", () => {
    const v = ledgerNavVisible(has());
    expect(v.parent).toBe(false);
    expect(ledgerMenuItems(has()).length).toBe(0);
  });

  test("view permission shows Akun Perkiraan", () => {
    const v = ledgerNavVisible(has("ledger.accounts.view"));
    expect(v.parent).toBe(true);
    expect(v.accounts).toBe(true);
    expect(v.generalJournal).toBe(false);
    expect(v.openingBalance).toBe(false);
    expect(ledgerMenuItems(has("ledger.accounts.view")).map((i) => i.to)).toEqual([
      "/buku-besar/akun-perkiraan",
    ]);
  });

  test("general_journal.view shows Jurnal Umum under Buku Besar", () => {
    const v = ledgerNavVisible(has("general_journal.view"));
    expect(v.parent).toBe(true);
    expect(v.generalJournal).toBe(true);
    expect(ledgerMenuItems(has("general_journal.view")).map((i) => i.to)).toEqual([
      "/buku-besar/jurnal-umum",
    ]);
  });

  test("opening_balance.view shows Saldo Awal under Buku Besar", () => {
    const v = ledgerNavVisible(has("opening_balance.view"));
    expect(v.parent).toBe(true);
    expect(v.openingBalance).toBe(true);
    expect(ledgerMenuItems(has("opening_balance.view")).map((i) => i.to)).toEqual([
      "/buku-besar/saldo-awal",
    ]);
  });

  test("nav.ledger.accounts alone is enough", () => {
    const v = ledgerNavVisible(has(NAV_KEYS.ledgerAccounts));
    expect(v.parent).toBe(true);
    expect(v.accounts).toBe(true);
  });
});
