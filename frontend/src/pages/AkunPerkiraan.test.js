import fs from "fs";
import path from "path";

describe("Akun Perkiraan page", () => {
  const src = fs.readFileSync(path.join(__dirname, "AkunPerkiraan.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");

  test("page title and columns are code+name only", () => {
    expect(src).toMatch(/Akun Perkiraan/);
    expect(src).toMatch(/Kode Perkiraan/);
    expect(src).toMatch(/Nama Akun/);
    expect(src).toMatch(/Tambah Akun/);
    expect(src).toMatch(/Hapus/);
    expect(src).not.toMatch(/[Ss]aldo/);
    expect(src).not.toMatch(/[Dd]ebit/);
    expect(src).not.toMatch(/[Kk]redit/);
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
