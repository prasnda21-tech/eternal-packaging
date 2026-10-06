import fs from "fs";
import path from "path";

describe("SidebarFlyoutGrid", () => {
  const grid = fs.readFileSync(path.join(__dirname, "SidebarFlyoutGrid.jsx"), "utf8");

  test("renders a compact list instead of the tile grid", () => {
    expect(grid).toMatch(/className="sidebar-flyout is-menu-list"/);
    expect(grid).toMatch(/<h2>\{title\}<\/h2>/);
    expect(grid).toMatch(/className="sidebar-menu-list"/);
    expect(grid).toMatch(/className=\{\(\{ isActive \}\) => "sidebar-menu-link" \+ \(isActive \? " is-active" : ""\)\}/);
    expect(grid).toMatch(/aria-label=\{item\.label\}/);
    expect(grid).toMatch(/sidebar-menu-link-icon/);
    expect(grid).toMatch(/aria-hidden="true"/);
    expect(grid).toMatch(/size=\{18\}/);
    expect(grid).toMatch(/sidebar-menu-link-label/);
    expect(grid).toMatch(/role="dialog"/);
    expect(grid).toMatch(/sidebar-menu-section/);
    expect(grid).toMatch(/is-unlabeled/);
    expect(grid).not.toMatch(/sales-menu-tile/);
    expect(grid).not.toMatch(/is-sales-grid/);
  });

  test("does not render an empty flyout", () => {
    expect(grid).toMatch(/if \(!blocks\.length\) return null/);
    expect(grid).toMatch(/\(block\.items \|\| \[\]\)\.length > 0/);
  });
});
