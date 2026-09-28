import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import PurchaseLifecycleStrip from "./PurchaseLifecycleStrip";

test("renders lifecycle stages with done markers", () => {
  const html = renderToStaticMarkup(
    <MemoryRouter>
      <PurchaseLifecycleStrip
        stages={[
          { key: "pesanan", label: "Pesanan", done: true, primary: "PB-1", secondary: "200 PCS", date: "2026-09-28" },
          { key: "penerimaan", label: "Penerimaan", done: true, primary: "GR-1", secondary: "200 / 200", date: "2026-09-28" },
          { key: "faktur", label: "Faktur", done: true, primary: "FPB-1", secondary: "Rp 7.000", date: "2026-09-28", href: "/pembelian/faktur-pembelian?invoice_id=x" },
          { key: "pembayaran", label: "Pembayaran", done: false, primary: "Belum", secondary: "—" },
        ]}
      />
    </MemoryRouter>,
  );
  expect(html).toContain('data-testid="purchase-lifecycle"');
  expect(html).toContain('data-testid="lifecycle-pesanan"');
  expect(html).toContain("Pesanan");
  expect(html).toContain("/pembelian/faktur-pembelian?invoice_id=x");
});
