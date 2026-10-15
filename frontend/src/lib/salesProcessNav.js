/**
 * Contextual sales-process navigation (Workflow 9.0).
 * Read-only links only — never open create modes via po_id.
 */

import { NAV_KEYS, canSeeNav, salesNavVisible } from "./navAccess";

/**
 * @param {object} opts
 * @param {(key: string) => boolean} opts.hasPerm
 * @param {string} [opts.orderId]
 * @param {string} [opts.poNumber]
 * @param {string} [opts.invoiceId] proven invoice id only
 * @returns {Array<{ id: string, to: string, label: string, kind: 'module'|'related' }>}
 */
export function buildSalesProcessNavLinks({
  hasPerm,
  orderId = "",
  poNumber = "",
  invoiceId = "",
} = {}) {
  if (typeof hasPerm !== "function") return [];

  const sales = salesNavVisible(hasPerm);
  const links = [];
  const po = String(poNumber || "").trim();
  const inv = String(invoiceId || "").trim();
  // orderId reserved for future related-doc links when API exposes safe ids

  if (sales.salesOrder) {
    links.push({
      id: "pesanan",
      to: "/penjualan/pesanan-penjualan",
      label: "Pesanan",
      kind: "module",
    });
  }

  const canSchedule =
    canSeeNav(hasPerm, NAV_KEYS.schedule) || hasPerm("schedule.view");
  if (canSchedule) {
    links.push({
      id: "produksi",
      to: "/produksi",
      label: "Produksi",
      kind: "module",
    });
  }

  if (sales.salesDeliveryOrder) {
    // Module list only — `?po_id=` opens create mode on Pengiriman.
    links.push({
      id: "pengiriman",
      to: "/penjualan/pengiriman-pesanan",
      label: "Pengiriman",
      kind: "module",
    });
  }

  if (sales.salesInvoice) {
    if (inv) {
      links.push({
        id: "faktur",
        to: `/penjualan/faktur-penjualan?invoice_id=${encodeURIComponent(inv)}`,
        label: "Faktur terkait",
        kind: "related",
      });
    } else {
      links.push({
        id: "faktur",
        to: "/penjualan/faktur-penjualan",
        label: "Faktur",
        kind: "module",
      });
    }
  }

  if (sales.salesReceipt) {
    // Module list only — `?invoice_id=` opens create modal on Penerimaan.
    links.push({
      id: "penerimaan",
      to: "/penjualan/penerimaan-penjualan",
      label: "Penerimaan",
      kind: "module",
    });
  }

  const canStatusPo =
    sales.statusPo ||
    canSeeNav(hasPerm, NAV_KEYS.statusPo) ||
    hasPerm("schedule.view");
  if (canStatusPo) {
    if (po) {
      links.push({
        id: "status-po",
        to: `/laporan/status-po?po=${encodeURIComponent(po)}`,
        label: "Status PO",
        kind: "related",
      });
    } else {
      links.push({
        id: "status-po",
        to: "/laporan/status-po",
        label: "Status PO",
        kind: "module",
      });
    }
  }

  return links;
}
