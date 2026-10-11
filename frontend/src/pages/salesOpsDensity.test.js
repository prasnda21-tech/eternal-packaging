import fs from "fs";
import path from "path";

const read = (rel) => fs.readFileSync(path.join(__dirname, rel), "utf8");

describe("Phase 4 sales ops density contracts", () => {
  const soCss = read("pesananPenjualan.css");
  const sdCss = read("salesSubmenuDensity.css");
  const pesanan = read("PesananPenjualan.jsx");
  const penerimaan = read("PenerimaanPenjualan.jsx");
  const faktur = read("FakturPenjualan.jsx");

  test("list rows are compact (38px) without shrinking body text below 13px", () => {
    expect(soCss).toMatch(/\.so-list-workspace \.so-list-table td\s*\{[^}]*height:\s*38px/s);
    expect(sdCss).toMatch(/\.sd-density \.sd-list-table td\s*\{[^}]*height:\s*38px/s);
    expect(soCss).toMatch(/font-size:\s*13px/);
    expect(sdCss).toMatch(/font-size:13px/);
    expect(soCss).not.toMatch(/height:\s*60px/);
    expect(sdCss).not.toMatch(/height:60px/);
  });

  test("chrome packs tabs with search; dead right padding removed", () => {
    expect(pesanan).toMatch(/so-list-chrome/);
    expect(penerimaan).toMatch(/sd-list-chrome/);
    expect(soCss).not.toMatch(/padding-right:\s*84px/);
    expect(sdCss).not.toMatch(/padding-right:84px/);
    expect(sdCss).not.toMatch(/min-width:1240px/);
  });

  test("ops labels stay simple; receipt edit/cancel not activated by copy", () => {
    expect(pesanan).toMatch(/>Pesanan Penjualan</);
    expect(pesanan).not.toMatch(/Seluruh Pesanan Penjualan/);
    expect(pesanan).toMatch(/Hanya HOLD/);
    expect(pesanan).not.toMatch(/<option value="selesai">Selesai<\/option>/);
    expect(penerimaan).toMatch(/<th>Jurnal<\/th>/);
    expect(penerimaan).toMatch(/\bPosting\b/);
    expect(penerimaan).toMatch(/\bBalik\b/);
    expect(penerimaan).not.toMatch(/Post Accounting/);
    expect(penerimaan).not.toMatch(/Simpan Perubahan Penerimaan/);
    expect(faktur).toMatch(/Cari no PO, pelanggan/);
  });
});
