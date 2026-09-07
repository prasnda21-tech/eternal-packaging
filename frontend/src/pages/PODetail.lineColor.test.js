import fs from "fs";
import path from "path";

describe("PO Detail line color display", () => {
  const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
  const route = fs.readFileSync(path.join(__dirname, "../components/ProductionRoutePanel.jsx"), "utf8");

  test("line item Warna uses persisted li.color inside the per-line map", () => {
    expect(detail).toMatch(/\(order\.line_items \|\| \[\]\)\.map\(\(li, i\) => \{/);
    expect(detail).toMatch(/Warna: \{li\.color\}/);
    expect(detail).not.toMatch(/Warna: \{order\.color\}/);
    expect(detail).not.toMatch(/line_items\[0\]\.color/);
  });

  test("production route panel shows each line color independently", () => {
    expect(route).toMatch(/lis\.map\(\(li, idx\) => \{/);
    expect(route).toMatch(/\{li\.color && <div className="muted small">\{li\.color\}<\/div>\}/);
    expect(route).not.toMatch(/order\.color/);
  });
});
