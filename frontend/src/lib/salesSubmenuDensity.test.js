import { loadedRangeLabel, showLoadedDocumentCount } from "./salesSubmenuDensity";

describe("sales submenu density presentation", () => {
  test("shows a count only after a successful unfiltered load", () => {
    expect(showLoadedDocumentCount({ loading: false, error: "", query: "" })).toBe(true);
    expect(showLoadedDocumentCount({ loading: true, error: "", query: "" })).toBe(false);
    expect(showLoadedDocumentCount({ loading: false, error: "gagal", query: "" })).toBe(false);
    expect(showLoadedDocumentCount({ loading: false, error: "", query: "  ider " })).toBe(false);
  });

  test("zero is a valid unfiltered total", () => {
    expect(loadedRangeLabel(0, "faktur")).toBe("Menampilkan 0 faktur");
    expect(loadedRangeLabel(2, "pengiriman")).toBe("Menampilkan 1–2 dari 2 pengiriman");
  });
});