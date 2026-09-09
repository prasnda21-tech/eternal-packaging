import { salesOrderLineColor, salesOrderLineColorsByIndex } from "./salesOrderLineColor";

describe("salesOrderLineColor", () => {
  test("CASE 1: single-color line renders exact color", () => {
    expect(salesOrderLineColor({ name: "Preview Cup", color: "RED" })).toBe("RED");
  });

  test("CASE 2 + 3: same-name lines keep independent colors", () => {
    const lines = [
      { name: "Preview Cup", color: "RED", accurate_line_id: "a" },
      { name: "Preview Cup", color: "BLUE", accurate_line_id: "b" },
    ];
    const mapped = salesOrderLineColorsByIndex(lines);
    expect(mapped[0].color).toBe("RED");
    expect(mapped[1].color).toBe("BLUE");
    expect(mapped[0].name).toBe(mapped[1].name);
    expect(mapped[0].line_id).not.toBe(mapped[1].line_id);
  });

  test("CASE 4: multi-color display preserves full string", () => {
    expect(
      salesOrderLineColor({ name: "Preview Cup", color: "RED / WHITE / BLACK" })
    ).toBe("RED / WHITE / BLACK");
    expect(
      salesOrderLineColor({ name: "Preview Cup", color: "Merah, Hitam, Putih" })
    ).toBe("Merah, Hitam, Putih");
  });

  test("CASE 5: missing color does not borrow another line's color", () => {
    const lines = [
      { name: "Preview Cup", color: "RED" },
      { name: "Preview Cup", color: null },
      { name: "Preview Cup", color: "" },
    ];
    const mapped = salesOrderLineColorsByIndex(lines);
    expect(mapped[0].color).toBe("RED");
    expect(mapped[1].color).toBe("");
    expect(mapped[2].color).toBe("");
  });

  test("CASE 6: refresh/re-fetch uses persisted backend value as-is", () => {
    const fromApi = { name: "Preview Cup", color: "Biru" };
    expect(salesOrderLineColor(fromApi)).toBe("Biru");
    // Re-fetch payload with updated persisted color
    expect(salesOrderLineColor({ ...fromApi, color: "Merah, Hitam, Putih" })).toBe(
      "Merah, Hitam, Putih"
    );
  });

  test("does not invent color from product name", () => {
    expect(salesOrderLineColor({ name: "Cup Bening Klir", color: null })).toBe("");
  });

  test("renders Accurate structured Warna exactly", () => {
    expect(salesOrderLineColor({ color: "PUTIH, HIJAU, PINK" })).toBe("PUTIH, HIJAU, PINK");
    expect(salesOrderLineColor({ color: "BENING" })).toBe("BENING");
    expect(salesOrderLineColor({ color: null, notes: "Warna Cetak: HITAM" })).toBe("");
  });
});
