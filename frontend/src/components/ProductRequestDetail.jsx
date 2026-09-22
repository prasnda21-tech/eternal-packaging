import { useState } from "react";
import { customerSourceLabel, productSourceLabel } from "../lib/productRequest";

export const DETAIL_SECTIONS = [
  { key: "customer", label: "Customer" },
  { key: "product", label: "Produk" },
  { key: "potential", label: "Potensi" },
];

export function displayValue(value) {
  if (value === 0) return "0";
  if (value === "" || value == null) return "—";
  return String(value);
}

export default function ProductRequestDetail({ request, testId = "prq-readonly-detail" }) {
  const [section, setSection] = useState("customer");
  const customer = request?.customer_snapshot || {};
  const product = request?.product_snapshot || {};
  const creator = request?.created_by_snapshot || {};
  const branch = request?.branch_snapshot || {};
  return (
    <div className="prq-detail" data-testid={testId}>
      <div className="prq-detail-tabs" role="tablist" data-testid="prq-detail-tabs">
        {DETAIL_SECTIONS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={section === item.key}
            className={"prq-detail-tab" + (section === item.key ? " is-active" : "")}
            data-testid={`prq-detail-tab-${item.key}`}
            onClick={() => setSection(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <section hidden={section !== "customer"} data-testid="prq-detail-panel-customer">
        <dl className="prq-dl">
          <dt>Nomor</dt><dd>{displayValue(request?.request_number)}</dd>
          <dt>Status</dt><dd>{request?.status === "submitted" ? "Terkirim" : displayValue(request?.status)}</dd>
          <dt>Customer</dt><dd>{customerSourceLabel(request?.customer_source)}</dd>
          <dt>Nama</dt><dd data-testid="prq-detail-customer-name">{displayValue(customer.name)}</dd>
          <dt>PIC</dt><dd>{displayValue(customer.pic)}</dd>
          <dt>WhatsApp</dt><dd>{displayValue(customer.whatsapp || customer.phone)}</dd>
          <dt>Kota</dt><dd>{displayValue(customer.city)}</dd>
          <dt>Alamat</dt><dd>{displayValue(customer.address)}</dd>
        </dl>
      </section>

      <section hidden={section !== "product"} data-testid="prq-detail-panel-product">
        <dl className="prq-dl">
          <dt>Produk</dt><dd>{productSourceLabel(request?.product_source)}</dd>
          <dt>Nama</dt><dd data-testid="prq-detail-product-name">{displayValue(product.item_name || product.name)}</dd>
          <dt>Kode</dt><dd data-testid="prq-detail-product-code">{displayValue(product.item_code)}</dd>
          <dt>Satuan</dt><dd data-testid="prq-detail-product-unit">{displayValue(product.unit)}</dd>
          <dt>Kategori</dt><dd data-testid="prq-detail-product-category">{displayValue(product.category)}</dd>
          <dt>Ukuran</dt><dd data-testid="prq-detail-product-size">{displayValue(product.size)}</dd>
          <dt>Material</dt><dd data-testid="prq-detail-product-material">{displayValue(product.material)}</dd>
          <dt>Model / Spesifikasi</dt><dd data-testid="prq-detail-product-model">{displayValue(product.model || product.specification)}</dd>
        </dl>
      </section>

      <section hidden={section !== "potential"} data-testid="prq-detail-panel-potential">
        <dl className="prq-dl">
          <dt>Qty</dt><dd>{displayValue(request?.potential_qty)}</dd>
          <dt>Frekuensi</dt><dd>{displayValue(request?.frequency)}</dd>
          <dt>Harga</dt><dd>{displayValue(request?.target_price)}</dd>
          <dt>Tanggal</dt><dd>{displayValue(request?.needed_date)}</dd>
          <dt>Sumber Channel</dt><dd data-testid="prq-detail-channel">{displayValue(request?.request_channel)}</dd>
          <dt>Catatan</dt><dd data-testid="prq-detail-note">{displayValue(request?.request_note)}</dd>
          <dt>Pembuat</dt><dd>{displayValue(creator.name)}</dd>
          <dt>Cabang</dt><dd>{displayValue(branch.name)}</dd>
        </dl>
      </section>
    </div>
  );
}
