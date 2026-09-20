import fs from "fs";
import path from "path";
import { ACCOUNT_TYPE_CODES, accountTypeLabel } from "./AkunPerkiraan";

describe("Akun Perkiraan page", () => {
  const src = fs.readFileSync(path.join(__dirname, "AkunPerkiraan.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

  test("Tipe Akun column and create/edit select present", () => {
    expect(src).toMatch(/Akun Perkiraan/);
    expect(src).toMatch(/Kode Perkiraan/);
    expect(src).toMatch(/Nama Akun/);
    expect(src).toMatch(/Tipe Akun/);
    expect(src).toMatch(/akun-form-account-type/);
    expect(src).toMatch(/account_type_locked/);
    expect(src).toMatch(/Tipe akun terkunci karena akun sudah digunakan\./);
    expect(src).toMatch(/ACCOUNT_TYPE_CODES/);
    expect(src).toMatch(/account_type_label/);
    expect(src).toMatch(/ledgerAccountTypes/);
    expect(src).toMatch(/Edit Akun|openEdit/);
    expect(src).toMatch(/Tambah Akun/);
    expect(src).toMatch(/Hapus/);
    expect(src).not.toMatch(/[Ss]aldo/);
    expect(src).not.toMatch(/[Dd]ebit/);
    expect(src).not.toMatch(/[Kk]redit/);
  });

  test("unmapped fallback label", () => {
    expect(accountTypeLabel(null)).toBe("Belum ditentukan");
    expect(accountTypeLabel("")).toBe("Belum ditentukan");
    expect(accountTypeLabel("BANK")).toBe("BANK");
    expect(ACCOUNT_TYPE_CODES).toContain("BANK");
    expect(ACCOUNT_TYPE_CODES).toContain("OASS");
    expect(ACCOUNT_TYPE_CODES).toHaveLength(16);
  });

  test("API client exposes update and account types", () => {
    expect(api).toMatch(/ledgerAccountTypes/);
    expect(api).toMatch(/updateLedgerAccount/);
    expect(api).toMatch(/createLedgerAccount/);
  });

  test("route and sidebar Buku Besar are wired", () => {
    expect(app).toMatch(/\/buku-besar\/akun-perkiraan/);
    expect(shell).toMatch(/top-nav-buku-besar/);
    expect(shell).toMatch(/sidebar-flyout-ledger/);
    expect(shell).toMatch(/Buku Besar/);
  });

  test("manage gates add/delete; live search present", () => {
    expect(src).toMatch(/ledger\.accounts\.manage/);
    expect(src).toMatch(/useDebouncedLiveSearch/);
    expect(src).toMatch(/akun-perkiraan-delete-modal/);
  });
});
