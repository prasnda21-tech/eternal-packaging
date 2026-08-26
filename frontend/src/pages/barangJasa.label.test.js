import fs from "fs";
import path from "path";
import { NAV_KEYS, NAV_MENU_FALLBACK } from "../lib/navAccess";

describe("Barang/Jasa display label", () => {
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const page = fs.readFileSync(path.join(__dirname, "MasterBarangJasa.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");

  test("sidebar and page title use Barang/Jasa not Master Barang/Jasa", () => {
    expect(NAV_MENU_FALLBACK.find((m) => m.key === NAV_KEYS.masterItems).label).toBe("Barang/Jasa");
    expect(NAV_MENU_FALLBACK.some((m) => m.label === "Master Barang/Jasa")).toBe(false);
    expect(shell).toMatch(/> Barang\/Jasa/);
    expect(shell).not.toMatch(/Master Barang\/Jasa/);
    expect(page).toMatch(/<h1 data-testid="master-items-title">Barang\/Jasa<\/h1>/);
    expect(page).not.toMatch(/Master Barang\/Jasa/);
  });

  test("route and nav permission key stay unchanged", () => {
    expect(NAV_KEYS.masterItems).toBe("nav.inventory.master_items");
    expect(app).toMatch(/path="\/persediaan\/master-barang-jasa"/);
    expect(shell).toMatch(/to="\/persediaan\/master-barang-jasa"/);
    expect(nav).toMatch(/masterItems:\s*"nav\.inventory\.master_items"/);
  });
});
