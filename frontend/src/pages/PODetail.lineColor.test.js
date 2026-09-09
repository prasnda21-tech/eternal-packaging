import fs from "fs";
import path from "path";
import { salesOrderLineColor, salesOrderLineColorsByIndex } from "../lib/salesOrderLineColor";

describe("PO Detail line color display", () => {
  const detail = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
  const route = fs.readFileSync(path.join(__dirname, "../components/ProductionRoutePanel.jsx"), "utf8");
  const penjualan = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");

  test("line item Warna uses salesOrderLineColor inside the per-line map", () => {
    expect(detail).toMatch(/\(order\.line_items \|\| \[\]\)\.map\(\(li, i\) => \{/);
    expect(detail).toMatch(/import \{ salesOrderLineColor \} from "\.\.\/lib\/salesOrderLineColor"/);
    expect(detail).toMatch(/data-testid=\{`line-item-color-\$\{i\}`\}/);
    expect(detail).toMatch(/Warna: \{salesOrderLineColor\(li\)/);
    expect(detail).not.toMatch(/Warna: \{order\.color\}/);
    expect(detail).not.toMatch(/line_items\[0\]\.color/);
  });

  test("production route panel shows each line color independently", () => {
    expect(route).toMatch(/lis\.map\(\(li, idx\) => \{/);
    expect(route).toMatch(/\{li\.color && <div className="muted small">\{li\.color\}<\/div>\}/);
    expect(route).not.toMatch(/order\.color/);
  });

  test("Informasi PO shows order-level Keterangan from order.notes", () => {
    expect(detail).toMatch(/data-testid="detail-keterangan"/);
    expect(detail).toMatch(/\{order\.notes\}/);
    expect(detail).toMatch(/Keterangan/);
  });

  test("Pesanan Penjualan binds Warna input to exact line.color", () => {
    expect(penjualan).toMatch(/value=\{line\.color \|\| ""\}/);
    expect(penjualan).toMatch(/handleLineChange\(originalIndex, "color"/);
    expect(penjualan).toMatch(/data-testid=\{`line-color-\$\{originalIndex\}`\}/);
  });

  test("CASE 1–3: same-name RED/BLUE stay independent on Detail mapping", () => {
    const order = {
      line_items: [
        { name: "Preview Cup", color: "RED" },
        { name: "Preview Cup", color: "BLUE" },
      ],
    };
    const rendered = (order.line_items || []).map((li) => `Warna: ${salesOrderLineColor(li) || "-"}`);
    expect(rendered[0]).toBe("Warna: RED");
    expect(rendered[1]).toBe("Warna: BLUE");
  });

  test("CASE 4–5: multi-color and missing color do not cross-assign", () => {
    const mapped = salesOrderLineColorsByIndex([
      { name: "Preview Cup", color: "RED / WHITE / BLACK" },
      { name: "Preview Cup", color: null },
    ]);
    expect(mapped[0].color).toBe("RED / WHITE / BLACK");
    expect(mapped[1].color).toBe("");
  });

  test("null persisted color renders Warna: -", () => {
    const rendered = ([{ name: "Cup", color: null }]).map(
      (li) => `Warna: ${salesOrderLineColor(li) || "-"}`
    );
    expect(rendered[0]).toBe("Warna: -");
  });

  test("structured Accurate Warna strings render as-is", () => {
    expect(salesOrderLineColor({ color: "PUTIH, HIJAU, PINK" })).toBe("PUTIH, HIJAU, PINK");
    expect(salesOrderLineColor({ color: "BENING" })).toBe("BENING");
  });

  test("CASE 6: re-fetch payload replaces displayed color", () => {
    let order = { line_items: [{ name: "Preview Cup", color: "RED" }] };
    expect(salesOrderLineColor(order.line_items[0])).toBe("RED");
    order = { line_items: [{ name: "Preview Cup", color: "BLUE" }] };
    expect(salesOrderLineColor(order.line_items[0])).toBe("BLUE");
  });
});
