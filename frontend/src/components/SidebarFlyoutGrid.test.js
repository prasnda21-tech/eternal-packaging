import fs from "fs";
import path from "path";

describe("SidebarFlyoutGrid", () => {
  const grid = fs.readFileSync(path.join(__dirname, "SidebarFlyoutGrid.jsx"), "utf8");

  test("keeps the approved Penjualan tile contract", () => {
    expect(grid).toMatch(/className="sidebar-flyout is-sales-grid"/);
    expect(grid).toMatch(/<h2>\{title\}<\/h2>/);
    expect(grid).toMatch(/className="sales-menu-grid"/);
    expect(grid).toMatch(/className=\{\(\{ isActive \}\) => "sales-menu-tile" \+ \(isActive \? " is-active" : ""\)\}/);
    expect(grid).toMatch(/data-tone=\{item\.tone\}/);
    expect(grid).toMatch(/aria-label=\{item\.label\}/);
    expect(grid).toMatch(/sales-menu-tile-icon/);
    expect(grid).toMatch(/aria-hidden="true"/);
    expect(grid).toMatch(/size=\{28\}/);
    expect(grid).toMatch(/sales-menu-tile-label/);
    expect(grid).toMatch(/role="dialog"/);
  });

  test("does not render an empty flyout", () => {
    expect(grid).toMatch(/if \(!blocks\.length\) return null/);
    expect(grid).toMatch(/\(block\.items \|\| \[\]\)\.length > 0/);
  });
});
