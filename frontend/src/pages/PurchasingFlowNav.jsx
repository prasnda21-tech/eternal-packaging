import { NavLink } from "react-router-dom";

export const PURCHASING_FLOW = [
  { to: "/pembelian/permintaan", label: "Permintaan", testId: "purchase-flow-permintaan" },
  { to: "/pembelian/pesanan-pembelian", label: "Pesanan", testId: "purchase-flow-pesanan" },
  { to: "/pembelian/uang-muka", label: "Uang Muka", testId: "purchase-flow-uang-muka" },
  { to: "/pembelian/penerimaan-barang", label: "Penerimaan", testId: "purchase-flow-penerimaan" },
  { to: "/pembelian/faktur-pembelian", label: "Faktur", testId: "purchase-flow-faktur" },
  { to: "/pembelian/pembayaran-pembelian", label: "Pembayaran", testId: "purchase-flow-pembayaran" },
];

/** Compact page-level purchasing workflow nav (does not touch AppShell). */
export default function PurchasingFlowNav() {
  return (
    <nav className="purchase-flow-nav" aria-label="Alur pembelian" data-testid="purchase-flow-nav">
      {PURCHASING_FLOW.map((step) => (
        <NavLink
          key={step.to}
          to={step.to}
          className={({ isActive }) => `purchase-flow-link${isActive ? " is-active" : ""}`}
          data-testid={step.testId}
        >
          {step.label}
        </NavLink>
      ))}
    </nav>
  );
}
