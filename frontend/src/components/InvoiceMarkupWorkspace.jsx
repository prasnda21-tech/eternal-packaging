import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, FileText, Info, Package, Wallet } from "lucide-react";
import api from "../lib/api";
import { formatSalesInvoiceDate } from "../lib/salesInvoiceDate";
import { formatUrgentIdDateTime } from "../lib/urgentPoUi";

function formatRupiah(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "Rp 0";
  return `Rp ${n.toLocaleString("id-ID", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

/** Display-only: "25 Okt 2026, 15.12 WIB". Invalid/missing → em dash. */
export function formatMarkupHistoryTimestamp(iso) {
  const base = formatUrgentIdDateTime(iso);
  if (!base) return "—";
  const m = String(base).match(/^(\d{1,2} \w+ \d{4}) (\d{2}):(\d{2})$/);
  if (!m) return base;
  return `${m[1]}, ${m[2]}.${m[3]} WIB`;
}

function money2(raw) {
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.round((n + Number.EPSILON) * 100) / 100;
}

function computeClientTotals(invoice, prices) {
  const lines = (invoice?.lines || []).map((line) => {
    const qty = Number(line.quantity) || 0;
    const key = line.line_id || String(line.line_idx);
    const raw = prices[key];
    const unit = money2(raw === "" || raw === undefined || raw === null ? line.unit_price : raw);
    const sub = money2(unit * qty);
    return { ...line, markup_unit_price: unit, markup_line_subtotal: sub };
  });
  const subtotal = money2(lines.reduce((s, l) => s + l.markup_line_subtotal, 0));
  const discount = money2(invoice?.discount_amount);
  const after = money2(Math.max(0, subtotal - discount));
  const ppnPercent = money2(invoice?.ppn_percent);
  const ppnAmount = money2((after * ppnPercent) / 100);
  const charges = Array.isArray(invoice?.other_charges) ? invoice.other_charges : [];
  const chargesTotal = money2(charges.reduce((s, c) => s + money2(c.amount), 0));
  const grand = money2(after + ppnAmount + chargesTotal);
  return { lines, subtotal, discount, after, ppnPercent, ppnAmount, charges, chargesTotal, grand };
}

/**
 * Faktur Markup workspace — same UX90 shell as Faktur Penjualan.
 * Editable: unit price + manual bank fields only.
 */
export default function InvoiceMarkupWorkspace({
  invoiceId,
  canCreate = false,
  onClose,
}) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [pdfLoading, setPdfLoading] = useState(null);
  const [err, setErr] = useState("");
  const [invoice, setInvoice] = useState(null);
  const [history, setHistory] = useState([]);
  const [mode, setMode] = useState("history"); // history | create | detail
  const [selected, setSelected] = useState(null);
  const [formTab, setFormTab] = useState("items");
  const [prices, setPrices] = useState({});
  const [bankName, setBankName] = useState("");
  const [accountNumber, setAccountNumber] = useState("");
  const [accountHolder, setAccountHolder] = useState("");
  const idempotencyRef = useRef(null);

  const load = async () => {
    setLoading(true);
    setErr("");
    try {
      const [invRes, histRes] = await Promise.all([
        api.getSalesInvoice(invoiceId),
        api.listSalesInvoiceMarkups(invoiceId),
      ]);
      const inv = invRes?.invoice || invRes;
      setInvoice(inv);
      const items = histRes?.items || [];
      setHistory(items);
      const seed = {};
      (inv?.lines || []).forEach((line) => {
        const key = line.line_id || String(line.line_idx);
        seed[key] = line.unit_price ?? "";
      });
      setPrices(seed);
      if (items.length === 0 && canCreate) {
        setMode("create");
      } else {
        setMode("history");
        setSelected(null);
      }
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det?.message || JSON.stringify(det) : det || "Gagal memuat Faktur Markup.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [invoiceId]);

  const totals = useMemo(() => computeClientTotals(invoice, prices), [invoice, prices]);

  const startCreate = () => {
    if (!canCreate || !invoice) return;
    const seed = {};
    (invoice.lines || []).forEach((line) => {
      const key = line.line_id || String(line.line_idx);
      seed[key] = line.unit_price ?? "";
    });
    setPrices(seed);
    setBankName("");
    setAccountNumber("");
    setAccountHolder("");
    idempotencyRef.current = `markup-${invoiceId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setSelected(null);
    setMode("create");
    setFormTab("items");
    setErr("");
  };

  const openRevision = async (row) => {
    setErr("");
    setPdfLoading(null);
    try {
      const res = await api.getSalesInvoiceMarkup(invoiceId, row.id);
      setSelected(res?.markup || row);
      setMode("detail");
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det?.message || JSON.stringify(det) : det || "Gagal membuka revisi.");
    }
  };

  const saveMarkup = async () => {
    if (!canCreate || !invoice || saving) return;
    setSaving(true);
    setErr("");
    try {
      if (!idempotencyRef.current) {
        idempotencyRef.current = `markup-${invoiceId}-${Date.now()}`;
      }
      const lines = (invoice.lines || []).map((line) => {
        const key = line.line_id || String(line.line_idx);
        return {
          line_id: line.line_id,
          line_idx: line.line_idx,
          markup_unit_price: prices[key],
        };
      });
      const res = await api.createSalesInvoiceMarkup(invoiceId, {
        lines,
        bank_name: bankName,
        account_number: accountNumber,
        account_holder: accountHolder,
        idempotency_key: idempotencyRef.current,
      });
      const created = res?.markup;
      await load();
      if (created) {
        setSelected(created);
        setMode("detail");
      } else {
        setMode("history");
      }
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det?.message || JSON.stringify(det) : det || "Gagal menyimpan Faktur Markup.");
    } finally {
      setSaving(false);
    }
  };

  const printMarkup = async (markupId) => {
    if (!markupId) return;
    setPdfLoading(markupId);
    setErr("");
    try {
      const blob = await api.downloadSalesInvoiceMarkupPdf(invoiceId, markupId);
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank");
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det?.message || JSON.stringify(det) : det || "Gagal mencetak Faktur Markup.");
    } finally {
      setPdfLoading(null);
    }
  };

  if (loading) {
    return (
      <div className="fp-page ux90-fp" data-testid="invoice-markup-workspace">
        <div className="sd-toolbar">
          <button type="button" className="ghost" onClick={onClose} data-testid="markup-back">
            <ArrowLeft size={16} /> Kembali
          </button>
        </div>
        <p data-testid="markup-loading">Memuat Faktur Markup…</p>
      </div>
    );
  }

  if (!invoice) {
    return (
      <div className="fp-page ux90-fp" data-testid="invoice-markup-workspace">
        <div className="sd-toolbar">
          <button type="button" className="ghost" onClick={onClose} data-testid="markup-back">
            <ArrowLeft size={16} /> Kembali
          </button>
        </div>
        <p className="fp-err" data-testid="markup-error">{err || "Faktur tidak ditemukan."}</p>
      </div>
    );
  }

  const showForm = mode === "create";
  const showDetail = mode === "detail" && selected;
  const showHistory = mode === "history";

  const displaySubtotal = showForm ? totals.subtotal : selected?.markup_subtotal;
  const displayDiscount = showForm ? totals.discount : selected?.discount_amount;
  const displayAfter = showForm ? totals.after : selected?.after_discount;
  const displayPpnPct = showForm ? totals.ppnPercent : selected?.ppn_percent;
  const displayPpn = showForm ? totals.ppnAmount : selected?.ppn_amount;
  const displayCharges = showForm ? totals.chargesTotal : selected?.other_charges_total;
  const displayGrand = showForm ? totals.grand : selected?.markup_grand_total;

  return (
    <div className="sd-form fp-create ux90-create-shell ux90-fp-create" data-testid="invoice-markup-workspace">
      <div className="sd-form-header ux90-fp-toolbar">
        <button type="button" className="sd-back" onClick={onClose} data-testid="markup-back">
          <ArrowLeft size={16} /> Kembali ke Faktur
        </button>
        <div className="ux90-fp-toolbar-title">
          <strong data-testid="markup-title">Faktur Markup</strong>
          <span data-testid="markup-invoice-number">{invoice.invoice_number || invoice.id}</span>
        </div>
        <div className="ux90-fp-toolbar-actions">
          {canCreate && mode !== "create" && (
            <button type="button" className="ux90-btn-primary primary" onClick={startCreate} data-testid="markup-new-revision">
              Buat Revisi Baru
            </button>
          )}
          {showDetail && (
            <button type="button" className="ux90-btn-ghost" onClick={() => setMode("history")} data-testid="markup-back-history">
              Riwayat
            </button>
          )}
        </div>
      </div>

      {err && (
        <p className="fp-err" data-testid="markup-error" role="alert">
          {err}
        </p>
      )}

      {showHistory && (
        <div className="sd-form-stage" data-testid="markup-history">
          <div className="ux90-fp-pane-title">
            <h3>Riwayat Faktur Markup</h3>
          </div>
          {history.length === 0 ? (
            <p data-testid="markup-history-empty">
              Belum ada revisi.{canCreate ? " Buat revisi baru untuk mengisi harga dan rekening cetakan." : ""}
            </p>
          ) : (
            <div className="ux90-table-scroll">
              <table className="ux90-table fp-lines">
                <thead>
                  <tr>
                    <th>Revisi</th>
                    <th>Dibuat</th>
                    <th>Pembuat</th>
                    <th className="is-num">Total Markup</th>
                    <th>Rekening</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {history.map((row) => (
                    <tr key={row.id} data-testid={`markup-history-row-${row.revision}`}>
                      <td>Revisi {row.revision}</td>
                      <td data-testid={`markup-history-created-${row.revision}`}>
                        {formatMarkupHistoryTimestamp(row.created_at)}
                      </td>
                      <td>{row.created_by?.name || row.created_by?.email || "-"}</td>
                      <td className="is-num">{formatRupiah(row.markup_grand_total)}</td>
                      <td>
                        {row.bank_name} · {row.account_number}
                      </td>
                      <td>
                        <button type="button" className="sd-act" onClick={() => openRevision(row)} data-testid={`markup-open-${row.revision}`}>
                          Lihat
                        </button>
                        <button
                          type="button"
                          className="sd-act"
                          disabled={pdfLoading === row.id}
                          onClick={() => printMarkup(row.id)}
                          data-testid={`markup-reprint-${row.revision}`}
                        >
                          {pdfLoading === row.id ? "Memuat…" : "Cetak Ulang"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {(showForm || showDetail) && (
        <div
          className="sd-form-fill fp-create-fill ux90-create-shell ux90-fp-filled ux90-fp-doc-host"
          data-form-tab={formTab}
          data-testid={showForm ? "markup-create-form" : "markup-detail-view"}
        >
          <div className="ux90-fp-doc">
            <div className="ux90-fp-doc-header" data-testid="markup-identity">
              <div className="ux90-fp-dh-grid ux90-fp-dh-compact">
                <div className="ux90-fp-dlrow">
                  <label htmlFor="markup-ro-number">No. Faktur</label>
                  <div className="ux90-fp-dlrow-control">
                    <input id="markup-ro-number" className="ux90-input" readOnly value={invoice.invoice_number || ""} data-testid="markup-ro-number" />
                  </div>
                </div>
                <div className="ux90-fp-dlrow">
                  <label htmlFor="markup-ro-customer">Pelanggan</label>
                  <div className="ux90-fp-dlrow-control">
                    <input id="markup-ro-customer" className="ux90-input" readOnly value={invoice.customer || ""} data-testid="markup-ro-customer" />
                  </div>
                </div>
                <div className="ux90-fp-dlrow">
                  <label htmlFor="markup-ro-po">No. PO</label>
                  <div className="ux90-fp-dlrow-control">
                    <input id="markup-ro-po" className="ux90-input" readOnly value={invoice.po_number || ""} data-testid="markup-ro-po" />
                  </div>
                </div>
                <div className="ux90-fp-dlrow">
                  <label htmlFor="markup-ro-date">Tanggal Faktur</label>
                  <div className="ux90-fp-dlrow-control">
                    <input
                      id="markup-ro-date"
                      className="ux90-input"
                      readOnly
                      value={formatSalesInvoiceDate(invoice.invoice_date) || ""}
                      data-testid="markup-ro-date"
                    />
                  </div>
                </div>
                <div className="ux90-fp-dlrow">
                  <label htmlFor="markup-ro-outlet">Outlet / Cabang</label>
                  <div className="ux90-fp-dlrow-control">
                    <input id="markup-ro-outlet" className="ux90-input" readOnly value={invoice.outlet || "-"} data-testid="markup-ro-outlet" />
                  </div>
                </div>
              </div>
            </div>

            <div className="ux90-fp-doc-body">
              <nav className="ux90-fp-vtabs" role="tablist" aria-label="Bagian Faktur Markup">
                <button type="button" role="tab" aria-selected={formTab === "items"} className={formTab === "items" ? "is-active" : ""} data-testid="markup-tab-items" onClick={() => setFormTab("items")}>
                  <Package size={18} aria-hidden="true" />
                  <span>Rincian</span>
                </button>
                <button type="button" role="tab" aria-selected={formTab === "info"} className={formTab === "info" ? "is-active" : ""} data-testid="markup-tab-info" onClick={() => setFormTab("info")}>
                  <Info size={18} aria-hidden="true" />
                  <span>Info</span>
                </button>
                <button type="button" role="tab" aria-selected={formTab === "charges"} className={formTab === "charges" ? "is-active" : ""} data-testid="markup-tab-charges" onClick={() => setFormTab("charges")}>
                  <Wallet size={18} aria-hidden="true" />
                  <span>Biaya</span>
                </button>
              </nav>

              <div className="ux90-fp-pane">
                {formTab === "items" && (
                  <div className="ux90-fp-pane-panel ux90-fp-pane-items fp-items sd-pane-items ux90-invoice-lines" data-pane="items">
                    <div className="ux90-fp-pane-title">
                      <h3>Rincian Barang</h3>
                      <span className="ux90-hint" data-testid="markup-qty-lock-hint">
                        Qty mengikuti faktur asli · hanya harga jual editable
                      </span>
                    </div>
                    <div className="ux90-table-scroll" data-testid="markup-lines-scroll">
                      <table className="ux90-table fp-lines">
                        <thead>
                          <tr>
                            <th>Produk</th>
                            <th className="is-num">Qty</th>
                            <th className="is-center">Unit</th>
                            <th className="is-num">Harga</th>
                            <th className="is-num">Subtotal</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(showForm ? totals.lines : selected?.lines || []).map((line) => {
                            const key = line.line_id || String(line.line_idx);
                            const unitPrice = showForm ? prices[key] : line.markup_unit_price ?? line.unit_price;
                            const sub = showForm ? line.markup_line_subtotal : line.markup_line_subtotal ?? line.line_subtotal;
                            return (
                              <tr key={key} data-testid={`markup-line-${line.line_idx}`}>
                                <td>
                                  <div className="ux90-item-name">{line.item_name}</div>
                                  {line.item_code ? <div className="ux90-item-meta">{line.item_code}</div> : null}
                                </td>
                                <td className="is-num">
                                  <span
                                    className="ux90-qty-locked"
                                    data-testid={`markup-qty-${line.line_idx}`}
                                    aria-label="Qty mengikuti faktur asli"
                                    title="Qty mengikuti faktur asli"
                                  >
                                    {Number(line.quantity || 0).toLocaleString("id-ID")}
                                  </span>
                                </td>
                                <td className="is-center">{line.unit || "pcs"}</td>
                                <td className="is-num">
                                  {showForm ? (
                                    <input
                                      type="number"
                                      min="0"
                                      step="any"
                                      className="ux90-input ux90-num ux90-price-input"
                                      value={unitPrice ?? ""}
                                      onChange={(e) => setPrices((prev) => ({ ...prev, [key]: e.target.value }))}
                                      data-testid={`markup-price-${line.line_idx}`}
                                    />
                                  ) : (
                                    <span data-testid={`markup-price-ro-${line.line_idx}`}>{formatRupiah(unitPrice)}</span>
                                  )}
                                </td>
                                <td className="is-num">{formatRupiah(sub)}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {formTab === "info" && (
                  <div className="ux90-fp-pane-panel ux90-fp-pane-info fp-terms sd-pane-terms" data-pane="info">
                    <div className="ux90-fp-pane-title">
                      <h3>Info Lainnya</h3>
                    </div>
                    <div className="ux90-fp-info-cols">
                      <section className="ux90-fp-info-col" aria-label="Info dokumen">
                        <h4 className="ux90-fp-info-h">Info dokumen</h4>
                        <div className="ux90-fp-dlrow">
                          <label htmlFor="markup-ro-notes">Keterangan</label>
                          <div className="ux90-fp-dlrow-control">
                            <input id="markup-ro-notes" className="ux90-input" type="text" readOnly disabled value={invoice.notes || ""} data-testid="markup-ro-notes" />
                          </div>
                        </div>
                        <div className="ux90-fp-dlrow">
                          <label htmlFor="markup-bank-name">Nama Bank {showForm ? <span className="ux90-req">*</span> : null}</label>
                          <div className="ux90-fp-dlrow-control">
                            <input
                              id="markup-bank-name"
                              type="text"
                              className="ux90-input"
                              value={showForm ? bankName : selected?.bank_name || ""}
                              onChange={(e) => setBankName(e.target.value)}
                              readOnly={!showForm}
                              disabled={!showForm}
                              data-testid="markup-bank-name"
                            />
                          </div>
                        </div>
                        <div className="ux90-fp-dlrow">
                          <label htmlFor="markup-account-number">Nomor Rekening {showForm ? <span className="ux90-req">*</span> : null}</label>
                          <div className="ux90-fp-dlrow-control">
                            <input
                              id="markup-account-number"
                              type="text"
                              className="ux90-input"
                              value={showForm ? accountNumber : selected?.account_number || ""}
                              onChange={(e) => setAccountNumber(e.target.value)}
                              readOnly={!showForm}
                              disabled={!showForm}
                              data-testid="markup-account-number"
                            />
                          </div>
                        </div>
                        <div className="ux90-fp-dlrow">
                          <label htmlFor="markup-account-holder">Nama Pemilik Rekening {showForm ? <span className="ux90-req">*</span> : null}</label>
                          <div className="ux90-fp-dlrow-control">
                            <input
                              id="markup-account-holder"
                              type="text"
                              className="ux90-input"
                              value={showForm ? accountHolder : selected?.account_holder || ""}
                              onChange={(e) => setAccountHolder(e.target.value)}
                              readOnly={!showForm}
                              disabled={!showForm}
                              data-testid="markup-account-holder"
                            />
                          </div>
                        </div>
                      </section>
                      <section className="ux90-fp-info-col" aria-label="Pembayaran dan pajak">
                        <h4 className="ux90-fp-info-h">Pembayaran &amp; pajak</h4>
                        <div className="ux90-fp-dlrow">
                          <label htmlFor="markup-ro-ppn">PPN (%)</label>
                          <div className="ux90-fp-dlrow-control">
                            <input id="markup-ro-ppn" className="ux90-input ux90-num" type="number" readOnly disabled value={displayPpnPct ?? 0} data-testid="markup-ro-ppn" />
                          </div>
                        </div>
                        <div className="ux90-fp-dlrow">
                          <label htmlFor="markup-ro-discount">Diskon Global (Rp)</label>
                          <div className="ux90-fp-dlrow-control">
                            <input id="markup-ro-discount" className="ux90-input ux90-num" type="number" readOnly disabled value={displayDiscount ?? 0} data-testid="markup-ro-discount" />
                          </div>
                        </div>
                        <p className="ux90-doc-hint">Diskon, PPN, dan biaya mengikuti faktur asli (terkunci). Total dihitung ulang dari harga markup.</p>
                      </section>
                    </div>
                  </div>
                )}

                {formTab === "charges" && (
                  <div className="ux90-fp-pane-panel ux90-fp-pane-charges ux90-invoice-terms" data-pane="charges" data-testid="markup-charges-pane">
                    <div className="ux90-fp-pane-title">
                      <h3>Biaya Lainnya</h3>
                      <span className="ux90-hint">Terkunci dari faktur asli</span>
                    </div>
                    {(showForm ? totals.charges : selected?.other_charges || []).length === 0 ? (
                      <p data-testid="markup-charges-empty">Tidak ada biaya lainnya.</p>
                    ) : (
                      <table className="ux90-table">
                        <thead>
                          <tr>
                            <th>Keterangan</th>
                            <th className="is-num">Nominal</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(showForm ? totals.charges : selected?.other_charges || []).map((c, idx) => (
                            <tr key={c.id || idx}>
                              <td>{c.description}</td>
                              <td className="is-num">{formatRupiah(c.amount)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </div>
            </div>

            <div className="ux90-footer invoice-actions ux90-doc-footer ux90-fp-doc-footer">
              <div className="ux90-fp-totals" data-testid="markup-summary">
                <div className="ux90-fp-totals-lines">
                  <div className="ux90-fp-tot-row">
                    <span>Subtotal</span>
                    <strong className="ux90-fp-tot-amt">{formatRupiah(displaySubtotal)}</strong>
                  </div>
                  <div className="ux90-fp-tot-row">
                    <span>Diskon</span>
                    <strong className="ux90-fp-tot-amt">{formatRupiah(displayDiscount)}</strong>
                  </div>
                  <div className="ux90-fp-tot-row">
                    <span>DPP</span>
                    <strong className="ux90-fp-tot-amt">{formatRupiah(displayAfter)}</strong>
                  </div>
                  <div className="ux90-fp-tot-row">
                    <span>PPN ({displayPpnPct}%)</span>
                    <strong className="ux90-fp-tot-amt">{formatRupiah(displayPpn)}</strong>
                  </div>
                  <div className="ux90-fp-tot-row">
                    <span>Biaya Lainnya</span>
                    <strong className="ux90-fp-tot-amt">{formatRupiah(displayCharges)}</strong>
                  </div>
                  <div className="ux90-fp-tot-row is-total">
                    <span>Total Markup</span>
                    <strong className="ux90-fp-tot-amt" data-testid="markup-grand-total">{formatRupiah(displayGrand)}</strong>
                  </div>
                </div>
              </div>
              <div className="ux90-doc-footer-actions">
                {showForm && (
                  <button
                    type="button"
                    className="ux90-btn-ghost"
                    onClick={() => setMode(history.length ? "history" : "create")}
                    disabled={saving}
                    data-testid="markup-cancel-create"
                  >
                    Batal
                  </button>
                )}
                {showForm && canCreate && (
                  <button type="button" className="ux90-btn-primary primary" disabled={saving} onClick={saveMarkup} data-testid="markup-save">
                    <FileText size={16} /> {saving ? "Menyimpan…" : "Simpan & Siap Cetak"}
                  </button>
                )}
                {showDetail && selected && (
                  <button type="button" className="ux90-btn-primary primary" disabled={pdfLoading === selected.id} onClick={() => printMarkup(selected.id)} data-testid="markup-print-detail">
                    <FileText size={16} /> {pdfLoading === selected.id ? "Memuat PDF…" : "Cetak PDF Markup"}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
