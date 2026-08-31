import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  FileText,
  Plus,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate } from "../lib/constants";

function getTodayString() {
  const d = new Date();
  return d.toISOString().split("T")[0];
}

export default function FakturPenjualan() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { user, hasPerm } = useAuth();

  const canCreate =
    hasPerm("sales.invoice.create") ||
    hasPerm("sales_order.create") ||
    hasPerm("po.edit") ||
    user?.role === "admin";
  const canEdit =
    hasPerm("sales.invoice.edit") ||
    hasPerm("sales.invoice.create") ||
    hasPerm("sales_order.create") ||
    hasPerm("po.edit") ||
    user?.role === "admin";

  const queryPoId = searchParams.get("po_id") || "";
  const initialMode =
    searchParams.get("action") === "create" || Boolean(queryPoId) ? "create" : "list";
  const [viewMode, setViewMode] = useState(initialMode);

  // List state
  const [invoices, setInvoices] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listErr, setListErr] = useState("");
  const [searchQuery, setSearchQuery] = useState("");

  // Create state
  const [availablePOs, setAvailablePOs] = useState([]);
  const [loadingPOs, setLoadingPOs] = useState(false);
  const [selectedPoId, setSelectedPoId] = useState(queryPoId);
  const [previewData, setPreviewData] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewErr, setPreviewErr] = useState("");

  const [invoiceDate, setInvoiceDate] = useState(getTodayString());
  const [invoiceNotes, setInvoiceNotes] = useState("");
  const [linePrices, setLinePrices] = useState({});
  const [formBusy, setFormBusy] = useState(false);
  const [formErr, setFormErr] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  // Detail / Edit state
  const [selectedInvoiceId, setSelectedInvoiceId] = useState(null);
  const [detailData, setDetailData] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailErr, setDetailErr] = useState("");
  const [detailPrices, setDetailPrices] = useState({});
  const [detailDate, setDetailDate] = useState("");
  const [detailNotes, setDetailNotes] = useState("");
  const [detailSaving, setDetailSaving] = useState(false);

  // Load Invoices List
  const loadInvoices = useCallback(async () => {
    setListLoading(true);
    setListErr("");
    try {
      const params = {};
      if (searchQuery.trim()) params.q = searchQuery.trim();
      const res = await api.listSalesInvoices(params);
      setInvoices(res.invoices || []);
    } catch (e) {
      const det = e.response?.data?.detail;
      setListErr(
        typeof det === "object"
          ? det.message
          : det || "Gagal memuat daftar Faktur Penjualan"
      );
    } finally {
      setListLoading(false);
    }
  }, [searchQuery]);

  // Load Available POs for selection
  const loadAvailablePOs = useCallback(async () => {
    if (!canCreate) return;
    setLoadingPOs(true);
    try {
      const res = await api.listSalesOrders({ status: "aktif" });
      setAvailablePOs((res.orders || []).filter((o) => !o.cancelled));
    } catch {
      // Best effort load
    } finally {
      setLoadingPOs(false);
    }
  }, [canCreate]);

  // Load PO preview when creating invoice from PO
  const loadPoPreview = useCallback(async (poId) => {
    if (!poId) {
      setPreviewData(null);
      return;
    }
    setPreviewLoading(true);
    setPreviewErr("");
    setFormErr("");
    try {
      const data = await api.getPoInvoicePreview(poId);
      setPreviewData(data);
      const initialPrices = {};
      (data.lines || []).forEach((l) => {
        initialPrices[l.line_idx] = l.unit_price || "";
      });
      setLinePrices(initialPrices);
    } catch (e) {
      const det = e.response?.data?.detail;
      setPreviewErr(
        typeof det === "object"
          ? det.message
          : det || "Gagal memuat data PO untuk Faktur"
      );
    } finally {
      setPreviewLoading(false);
    }
  }, []);

  useEffect(() => {
    if (viewMode === "list") {
      loadInvoices();
    } else if (viewMode === "create") {
      loadAvailablePOs();
      if (selectedPoId) {
        loadPoPreview(selectedPoId);
      }
    }
  }, [viewMode, loadInvoices, loadAvailablePOs, loadPoPreview, selectedPoId]);

  // Handle PO selector change
  const handleSelectPo = (e) => {
    const pId = e.target.value;
    setSelectedPoId(pId);
    if (pId) {
      setSearchParams({ action: "create", po_id: pId });
      loadPoPreview(pId);
    } else {
      setSearchParams({ action: "create" });
      setPreviewData(null);
    }
  };

  // Live calculation of line totals and grand total for Create form
  const createCalculations = useMemo(() => {
    if (!previewData || !previewData.lines) {
      return { lines: [], grandTotal: 0, totalQty: 0 };
    }
    let totalQty = 0;
    let grandTotal = 0;
    const computedLines = previewData.lines.map((l) => {
      const q = Number(l.quantity) || 0;
      const rawP = linePrices[l.line_idx];
      const p = Math.max(0, Number(rawP) || 0);
      const lineTotal = q * p;
      totalQty += q;
      grandTotal += lineTotal;
      return {
        ...l,
        unitPrice: rawP !== undefined ? rawP : 0,
        numericPrice: p,
        lineTotal,
      };
    });
    return { lines: computedLines, grandTotal, totalQty };
  }, [previewData, linePrices]);

  // Handle Save Invoice
  const handleSaveInvoice = async (e) => {
    e.preventDefault();
    if (!selectedPoId || !previewData) {
      setFormErr("Silakan pilih Pesanan Penjualan (PO) terlebih dahulu.");
      return;
    }
    setFormBusy(true);
    setFormErr("");
    setSuccessMsg("");

    try {
      const linesPayload = (previewData.lines || []).map((l) => {
        const rawP = linePrices[l.line_idx];
        const numP = Number(rawP);
        if (numP < 0) {
          throw new Error(`Harga satuan untuk '${l.item_name}' tidak boleh negatif.`);
        }
        return {
          line_idx: l.line_idx,
          line_id: l.line_id,
          unit_price: isNaN(numP) ? 0 : numP,
        };
      });

      const payload = {
        po_id: selectedPoId,
        invoice_date: invoiceDate || getTodayString(),
        notes: invoiceNotes.trim(),
        lines: linesPayload,
      };

      const res = await api.createSalesInvoice(payload);
      setSuccessMsg(res.message || "Faktur Penjualan berhasil dibuat");
      setTimeout(() => {
        setSearchParams({});
        setViewMode("list");
        loadInvoices();
      }, 900);
    } catch (err) {
      const det = err.response?.data?.detail;
      setFormErr(
        typeof det === "object"
          ? det.message
          : det || err.message || "Gagal menyimpan Faktur Penjualan"
      );
    } finally {
      setFormBusy(false);
    }
  };

  // Open Detail / Edit
  const handleOpenDetail = async (invId) => {
    setSelectedInvoiceId(invId);
    setViewMode("detail");
    setDetailLoading(true);
    setDetailErr("");
    setFormErr("");
    setSuccessMsg("");
    try {
      const res = await api.getSalesInvoice(invId);
      const inv = res.invoice || res;
      setDetailData(inv);
      setDetailDate(inv.invoice_date || "");
      setDetailNotes(inv.notes || "");
      const prices = {};
      (inv.lines || []).forEach((l) => {
        prices[l.line_idx] = l.unit_price != null ? l.unit_price : 0;
      });
      setDetailPrices(prices);
    } catch (e) {
      const det = e.response?.data?.detail;
      setDetailErr(
        typeof det === "object"
          ? det.message
          : det || "Gagal memuat detail Faktur Penjualan"
      );
    } finally {
      setDetailLoading(false);
    }
  };

  // Live calculation for Detail / Edit form
  const detailCalculations = useMemo(() => {
    if (!detailData || !detailData.lines) {
      return { lines: [], grandTotal: 0, totalQty: 0 };
    }
    let totalQty = 0;
    let grandTotal = 0;
    const computedLines = detailData.lines.map((l) => {
      const q = Number(l.quantity) || 0;
      const rawP = detailPrices[l.line_idx];
      const p = Math.max(0, Number(rawP) || 0);
      const lineTotal = q * p;
      totalQty += q;
      grandTotal += lineTotal;
      return {
        ...l,
        unitPrice: rawP !== undefined ? rawP : 0,
        numericPrice: p,
        lineTotal,
      };
    });
    return { lines: computedLines, grandTotal, totalQty };
  }, [detailData, detailPrices]);

  // Handle Update Invoice
  const handleUpdateInvoice = async (e) => {
    e.preventDefault();
    if (!selectedInvoiceId || !detailData) return;
    setDetailSaving(true);
    setDetailErr("");
    setSuccessMsg("");

    try {
      const linesPayload = (detailData.lines || []).map((l) => {
        const rawP = detailPrices[l.line_idx];
        const numP = Number(rawP);
        if (numP < 0) {
          throw new Error(`Harga satuan untuk '${l.item_name}' tidak boleh negatif.`);
        }
        return {
          line_idx: l.line_idx,
          line_id: l.line_id,
          unit_price: isNaN(numP) ? 0 : numP,
        };
      });

      const payload = {
        invoice_date: detailDate,
        notes: detailNotes.trim(),
        lines: linesPayload,
      };

      const res = await api.updateSalesInvoice(selectedInvoiceId, payload);
      const updated = res.invoice || res;
      setDetailData(updated);
      setSuccessMsg("Perubahan Faktur Penjualan berhasil disimpan");
      setTimeout(() => setSuccessMsg(""), 2500);
    } catch (err) {
      const det = err.response?.data?.detail;
      setDetailErr(
        typeof det === "object"
          ? det.message
          : det || err.message || "Gagal memperbarui Faktur Penjualan"
      );
    } finally {
      setDetailSaving(false);
    }
  };

  return (
    <div
      className="page-container"
      data-testid="faktur-penjualan-page"
      style={{ padding: 24, maxWidth: 1200, margin: "0 auto" }}
    >
      {/* HEADER SECTION */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 20,
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {viewMode !== "list" ? (
            <button
              type="button"
              className="ghost sm"
              onClick={() => {
                setSearchParams({});
                setViewMode("list");
              }}
              data-testid="back-to-list-btn"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 12px",
                borderRadius: 6,
                border: "1px solid #cbd5e1",
                background: "#f8fafc",
                cursor: "pointer",
              }}
            >
              <ArrowLeft size={16} /> Kembali
            </button>
          ) : (
            <div
              style={{
                width: 40,
                height: 40,
                borderRadius: 10,
                background: "#f3e8ff",
                color: "#9333ea",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <FileText size={22} />
            </div>
          )}
          <div>
            <h1 style={{ margin: 0, fontSize: "1.5rem", fontWeight: 700, color: "#0f172a" }}>
              {viewMode === "create"
                ? "Buat Faktur Penjualan"
                : viewMode === "detail"
                ? "Detail Faktur Penjualan"
                : "Faktur Penjualan"}
            </h1>
            <p style={{ margin: "2px 0 0 0", fontSize: "0.85rem", color: "#64748b" }}>
              {viewMode === "create"
                ? "Buat Faktur Penjualan berdasarkan snapshot exact dari Pesanan Penjualan (PO)."
                : viewMode === "detail"
                ? `Dokumen Faktur #${detailData?.invoice_number || detailData?.id || selectedInvoiceId}`
                : "Daftar Faktur Penjualan berdasarkan data Pesanan Penjualan"}
            </p>
          </div>
        </div>

        {viewMode === "list" && canCreate && (
          <button
            type="button"
            className="primary"
            onClick={() => {
              setSelectedPoId("");
              setPreviewData(null);
              setSearchParams({ action: "create" });
              setViewMode("create");
            }}
            data-testid="btn-create-invoice"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 16px",
              borderRadius: 8,
              background: "#0284c7",
              color: "#fff",
              border: "none",
              cursor: "pointer",
              fontWeight: 600,
              fontSize: "0.9rem",
            }}
          >
            <Plus size={16} /> + Buat Faktur dari PO
          </button>
        )}
      </div>

      {/* SUCCESS BANNER */}
      {successMsg && (
        <div
          className="alert-box success"
          data-testid="success-alert"
          style={{
            background: "#ecfdf5",
            border: "1px solid #a7f3d0",
            color: "#047857",
            padding: "12px 16px",
            borderRadius: 8,
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <CheckCircle2 size={18} />
            <span>{successMsg}</span>
          </div>
          <button
            type="button"
            onClick={() => setSuccessMsg("")}
            style={{ background: "none", border: "none", cursor: "pointer", color: "#047857" }}
          >
            <X size={16} />
          </button>
        </div>
      )}

      {/* VIEW: LIST */}
      {viewMode === "list" && (
        <section
          className="panel"
          style={{
            background: "#fff",
            border: "1px solid #e2e8f0",
            borderRadius: 10,
            padding: 20,
            boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
          }}
        >
          {listErr && (
            <div
              className="alert-box error"
              style={{
                background: "#fef2f2",
                border: "1px solid #fecaca",
                color: "#b91c1c",
                padding: "10px 14px",
                borderRadius: 8,
                marginBottom: 16,
                display: "flex",
                alignItems: "center",
                gap: 10,
              }}
            >
              <AlertCircle size={16} />
              <span>{listErr}</span>
            </div>
          )}

          {/* Search & Refresh Toolbar */}
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              justifyContent: "space-between",
              alignItems: "center",
              gap: 12,
              marginBottom: 16,
            }}
          >
            <div style={{ position: "relative", minWidth: 280, flex: "1 1 300px" }}>
              <Search
                size={16}
                style={{
                  position: "absolute",
                  left: 10,
                  top: "50%",
                  transform: "translateY(-50%)",
                  color: "#94a3b8",
                }}
              />
              <input
                type="text"
                placeholder="Cari No PO / Customer / Sales / Faktur..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") loadInvoices();
                }}
                data-testid="search-invoice-input"
                style={{
                  width: "100%",
                  padding: "8px 12px 8px 34px",
                  border: "1px solid #cbd5e1",
                  borderRadius: 6,
                  fontSize: "0.9rem",
                  boxSizing: "border-box",
                }}
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery("")}
                  style={{
                    position: "absolute",
                    right: 8,
                    top: "50%",
                    transform: "translateY(-50%)",
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: "#94a3b8",
                  }}
                >
                  <X size={14} />
                </button>
              )}
            </div>

            <button
              type="button"
              onClick={loadInvoices}
              disabled={listLoading}
              title="Refresh data"
              data-testid="refresh-invoice-btn"
              style={{
                padding: "8px 14px",
                borderRadius: 6,
                border: "1px solid #cbd5e1",
                background: "#f8fafc",
                color: "#475569",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontWeight: 500,
                fontSize: "0.85rem",
              }}
            >
              <RefreshCw size={14} className={listLoading ? "spin" : ""} /> Refresh
            </button>
          </div>

          {/* Invoices Table */}
          <div style={{ overflowX: "auto" }}>
            <table
              style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.9rem" }}
              data-testid="invoices-table"
            >
              <thead>
                <tr style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
                  <th style={{ padding: "10px 12px" }}>Identitas Faktur</th>
                  <th style={{ padding: "10px 12px" }}>No PO</th>
                  <th style={{ padding: "10px 12px" }}>Customer</th>
                  <th style={{ padding: "10px 12px" }}>Tanggal</th>
                  <th style={{ padding: "10px 12px", textAlign: "right" }}>Total Qty</th>
                  <th style={{ padding: "10px 12px", textAlign: "right" }}>Total Faktur</th>
                  <th style={{ padding: "10px 12px", textAlign: "center" }}>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {listLoading ? (
                  <tr>
                    <td colSpan={7} style={{ padding: 32, textAlign: "center", color: "#64748b" }}>
                      Memuat daftar Faktur Penjualan...
                    </td>
                  </tr>
                ) : invoices.length === 0 ? (
                  <tr>
                    <td
                      colSpan={7}
                      style={{ padding: "36px 20px", textAlign: "center", color: "#64748b" }}
                      data-testid="empty-invoices"
                    >
                      <div
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        <div
                          style={{
                            width: 44,
                            height: 44,
                            borderRadius: 12,
                            background: "#f3e8ff",
                            color: "#9333ea",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            marginBottom: 12,
                          }}
                        >
                          <FileText size={24} />
                        </div>
                        <div style={{ fontWeight: 600, color: "#1e293b", fontSize: "1rem", marginBottom: 4 }}>
                          Belum ada Faktur Penjualan
                        </div>
                        <p style={{ margin: "0 0 16px 0", fontSize: "0.875rem", color: "#64748b", maxWidth: 400 }}>
                          Buat faktur pertama berdasarkan Pesanan Penjualan yang sudah ada.
                        </p>
                        {canCreate && (
                          <button
                            type="button"
                            className="primary"
                            onClick={() => {
                              setSelectedPoId("");
                              setPreviewData(null);
                              setSearchParams({ action: "create" });
                              setViewMode("create");
                            }}
                            data-testid="empty-create-btn"
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 6,
                              padding: "8px 16px",
                              borderRadius: 8,
                              background: "#0284c7",
                              color: "#fff",
                              border: "none",
                              cursor: "pointer",
                              fontWeight: 600,
                              fontSize: "0.85rem",
                            }}
                          >
                            <Plus size={14} /> + Buat Faktur dari PO
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ) : (
                  invoices.map((inv) => (
                    <tr
                      key={inv.id}
                      style={{ borderBottom: "1px solid #f1f5f9" }}
                      data-testid={`invoice-row-${inv.id}`}
                    >
                      <td style={{ padding: "10px 12px", fontWeight: 700, color: "#9333ea" }}>
                        <span className="mono">{inv.invoice_number || inv.id}</span>
                      </td>
                      <td style={{ padding: "10px 12px", fontWeight: 500 }}>
                        <Link
                          to={`/po/${inv.po_id}`}
                          style={{ color: "#0284c7", textDecoration: "none" }}
                          data-testid={`po-link-${inv.po_number}`}
                          title="Buka PO"
                        >
                          {inv.po_number || "-"}
                        </Link>
                      </td>
                      <td style={{ padding: "10px 12px", fontWeight: 500, color: "#1e293b" }}>
                        <div>{inv.customer || "-"}</div>
                        {inv.outlet && (
                          <div style={{ fontSize: "0.75rem", color: "#64748b" }}>{inv.outlet}</div>
                        )}
                      </td>
                      <td style={{ padding: "10px 12px", color: "#475569" }}>
                        {formatDisplayDate(inv.invoice_date || inv.created_at)}
                      </td>
                      <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 600, color: "#334155" }}>
                        {Number(inv.total_qty || 0).toLocaleString("id-ID")} pcs
                      </td>
                      <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 700, color: "#0f172a" }}>
                        Rp {Number(inv.grand_total || 0).toLocaleString("id-ID")}
                      </td>
                      <td style={{ padding: "10px 12px", textAlign: "center" }}>
                        <button
                          type="button"
                          onClick={() => handleOpenDetail(inv.id)}
                          data-testid={`view-invoice-btn-${inv.id}`}
                          style={{
                            padding: "5px 12px",
                            fontSize: "0.8rem",
                            borderRadius: 6,
                            border: "1px solid #cbd5e1",
                            background: "#f8fafc",
                            color: "#334155",
                            cursor: "pointer",
                            fontWeight: 500,
                          }}
                        >
                          Lihat / Edit
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* VIEW: CREATE */}
      {viewMode === "create" && (
        <div>
          {/* Step 1: PO Selector Panel */}
          <section
            className="panel"
            style={{
              background: "#fff",
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              padding: 20,
              marginBottom: 20,
              boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
            }}
          >
            <header style={{ borderBottom: "1px solid #f1f5f9", paddingBottom: 12, marginBottom: 16 }}>
              <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#1e293b" }}>
                1. Pilih Pesanan Penjualan (PO Asal)
              </h3>
              <p style={{ margin: "4px 0 0 0", fontSize: "0.85rem", color: "#64748b" }}>
                Pilih PO aktif untuk membuat snapshot rincian barang dan penetapan harga satuan faktur.
              </p>
            </header>

            <div style={{ maxWidth: 540 }}>
              <label
                style={{
                  display: "block",
                  fontSize: "0.85rem",
                  fontWeight: 600,
                  color: "#334155",
                  marginBottom: 6,
                }}
              >
                Nomor PO <span style={{ color: "#ef4444" }}>*</span>
              </label>
              <select
                value={selectedPoId}
                onChange={handleSelectPo}
                disabled={previewLoading || formBusy}
                data-testid="po-selector"
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  border: "1px solid #cbd5e1",
                  borderRadius: 6,
                  fontSize: "0.95rem",
                  boxSizing: "border-box",
                }}
              >
                <option value="">-- Pilih PO --</option>
                {availablePOs.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.po_number} — {p.customer} ({Number(p.total_quantity || 0).toLocaleString("id-ID")} pcs)
                  </option>
                ))}
              </select>
            </div>

            {previewErr && (
              <div
                style={{
                  color: "#dc2626",
                  marginTop: 12,
                  fontSize: "0.875rem",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <AlertCircle size={16} />
                <span>{previewErr}</span>
              </div>
            )}
          </section>

          {/* Step 2: PO Snapshot & Item Pricing Table */}
          {previewLoading ? (
            <div style={{ padding: 32, textAlign: "center", color: "#64748b" }}>
              Memuat data PO snapshot...
            </div>
          ) : previewData ? (
            <form onSubmit={handleSaveInvoice}>
              {/* Snapshot Header Info */}
              <section
                className="panel"
                style={{
                  background: "#fff",
                  border: "1px solid #e2e8f0",
                  borderRadius: 10,
                  padding: 20,
                  marginBottom: 20,
                  boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
                }}
                data-testid="po-snapshot-card"
              >
                <header style={{ borderBottom: "1px solid #f1f5f9", paddingBottom: 12, marginBottom: 16 }}>
                  <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#1e293b" }}>
                    2. Informasi PO Snapshot & Tanggal Faktur
                  </h3>
                  <p style={{ margin: "4px 0 0 0", fontSize: "0.85rem", color: "#64748b" }}>
                    Snapshot data referensi Pesanan Penjualan dan tanggal terbit faktur.
                  </p>
                </header>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                    gap: 16,
                  }}
                >
                  <div>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        display: "block",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: 2,
                      }}
                    >
                      No PO
                    </span>
                    <strong data-testid="snapshot-po-number" style={{ fontSize: "0.95rem", color: "#0f172a" }}>
                      {previewData.po_number || "-"}
                    </strong>
                  </div>
                  <div>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        display: "block",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: 2,
                      }}
                    >
                      Tanggal PO
                    </span>
                    <span style={{ fontSize: "0.95rem", color: "#334155" }}>
                      {formatDisplayDate(previewData.order_date)}
                    </span>
                  </div>
                  <div>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        display: "block",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: 2,
                      }}
                    >
                      Customer
                    </span>
                    <strong data-testid="snapshot-customer" style={{ fontSize: "0.95rem", color: "#0f172a" }}>
                      {previewData.customer || "-"}
                    </strong>
                  </div>
                  <div>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        display: "block",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: 2,
                      }}
                    >
                      Outlet / Cabang
                    </span>
                    <span style={{ fontSize: "0.95rem", color: "#334155" }}>
                      {previewData.outlet || "-"}
                    </span>
                  </div>
                  <div>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        display: "block",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: 2,
                      }}
                    >
                      Sales
                    </span>
                    <span style={{ fontSize: "0.95rem", color: "#334155" }}>
                      {previewData.sales || "-"}
                    </span>
                  </div>
                  <div>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        display: "block",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: 2,
                      }}
                    >
                      Penerima / PIC
                    </span>
                    <span style={{ fontSize: "0.95rem", color: "#334155" }}>
                      {previewData.recipient || "-"}
                    </span>
                  </div>
                  <div style={{ gridColumn: "1 / -1" }}>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        display: "block",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: 2,
                      }}
                    >
                      Alamat Pengiriman
                    </span>
                    <span style={{ fontSize: "0.9rem", color: "#334155" }}>
                      {previewData.address || "-"}
                    </span>
                  </div>
                </div>

                {/* Additional invoice fields */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                    gap: 16,
                    marginTop: 16,
                    paddingTop: 16,
                    borderTop: "1px solid #f1f5f9",
                  }}
                >
                  <div>
                    <label
                      style={{
                        fontSize: "0.85rem",
                        fontWeight: 600,
                        color: "#334155",
                        display: "block",
                        marginBottom: 6,
                      }}
                    >
                      Tanggal Faktur <span style={{ color: "#ef4444" }}>*</span>
                    </label>
                    <input
                      type="date"
                      value={invoiceDate}
                      onChange={(e) => setInvoiceDate(e.target.value)}
                      data-testid="invoice-date-input"
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        border: "1px solid #cbd5e1",
                        borderRadius: 6,
                        fontSize: "0.95rem",
                        boxSizing: "border-box",
                      }}
                    />
                  </div>
                  <div style={{ gridColumn: "2 / -1" }}>
                    <label
                      style={{
                        fontSize: "0.85rem",
                        fontWeight: 600,
                        color: "#334155",
                        display: "block",
                        marginBottom: 6,
                      }}
                    >
                      Catatan Faktur (Opsional)
                    </label>
                    <input
                      type="text"
                      placeholder="Catatan tambahan faktur penjualan..."
                      value={invoiceNotes}
                      onChange={(e) => setInvoiceNotes(e.target.value)}
                      data-testid="invoice-notes-input"
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        border: "1px solid #cbd5e1",
                        borderRadius: 6,
                        fontSize: "0.95rem",
                        boxSizing: "border-box",
                      }}
                    />
                  </div>
                </div>
              </section>

              {/* Table Line Items */}
              <section
                className="panel"
                style={{
                  background: "#fff",
                  border: "1px solid #e2e8f0",
                  borderRadius: 10,
                  padding: 20,
                  marginBottom: 20,
                  boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
                }}
              >
                <header style={{ borderBottom: "1px solid #f1f5f9", paddingBottom: 12, marginBottom: 16 }}>
                  <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#1e293b" }}>
                    3. Rincian Barang & Input Harga Satuan
                  </h3>
                  <p style={{ margin: "4px 0 0 0", fontSize: "0.85rem", color: "#64748b" }}>
                    Kuantitas diambil langsung dari PO (read-only). Masukkan Harga Satuan untuk setiap baris barang.
                  </p>
                </header>

                <div style={{ overflowX: "auto" }}>
                  <table
                    style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.9rem" }}
                    data-testid="invoice-items-table"
                  >
                    <thead>
                      <tr style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
                        <th style={{ padding: "10px 12px", width: 40 }}>#</th>
                        <th style={{ padding: "10px 12px", minWidth: 220 }}>Barang / Jasa</th>
                        <th style={{ padding: "10px 12px", width: 110, textAlign: "right" }}>Qty PO</th>
                        <th style={{ padding: "10px 12px", width: 80, textAlign: "center" }}>Unit</th>
                        <th style={{ padding: "10px 12px", width: 220, textAlign: "right" }}>
                          Harga Satuan (Rp) <span style={{ color: "#ef4444" }}>*</span>
                        </th>
                        <th style={{ padding: "10px 12px", width: 200, textAlign: "right" }}>Total Line (Rp)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {createCalculations.lines.map((li, index) => (
                        <tr
                          key={li.line_idx}
                          style={{ borderBottom: "1px solid #f1f5f9" }}
                          data-testid={`create-line-${li.line_idx}`}
                        >
                          <td style={{ padding: "10px 12px", color: "#64748b", fontWeight: 600 }}>
                            {index + 1}
                          </td>
                          <td style={{ padding: "10px 12px" }}>
                            <div style={{ fontWeight: 600, color: "#1e293b" }} data-testid={`item-name-${li.line_idx}`}>
                              {li.item_name}
                            </div>
                            {li.color && (
                              <div style={{ fontSize: "0.75rem", color: "#64748b" }}>Warna: {li.color}</div>
                            )}
                          </td>
                          <td
                            style={{ padding: "10px 12px", textAlign: "right", fontWeight: 600, color: "#334155" }}
                            data-testid={`item-qty-${li.line_idx}`}
                          >
                            {Number(li.quantity || 0).toLocaleString("id-ID")}
                          </td>
                          <td style={{ padding: "10px 12px", textAlign: "center", color: "#64748b" }}>
                            {li.unit || "pcs"}
                          </td>
                          <td style={{ padding: "8px 12px", textAlign: "right" }}>
                            <input
                              type="number"
                              min="0"
                              step="any"
                              value={linePrices[li.line_idx] !== undefined ? linePrices[li.line_idx] : ""}
                              onChange={(e) => {
                                const val = e.target.value;
                                setLinePrices((prev) => ({ ...prev, [li.line_idx]: val }));
                              }}
                              placeholder="0"
                              data-testid={`unit-price-input-${li.line_idx}`}
                              style={{
                                width: "100%",
                                padding: "6px 10px",
                                border: "1px solid #cbd5e1",
                                borderRadius: 6,
                                boxSizing: "border-box",
                                fontWeight: 600,
                                textAlign: "right",
                                fontSize: "0.95rem",
                              }}
                            />
                          </td>
                          <td
                            style={{ padding: "10px 12px", textAlign: "right", fontWeight: 700, color: "#0f172a" }}
                            data-testid={`line-total-${li.line_idx}`}
                          >
                            Rp {Number(li.lineTotal || 0).toLocaleString("id-ID")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Grand Total Summary Footer */}
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginTop: 16,
                    padding: "14px 18px",
                    background: "#f8fafc",
                    borderRadius: 8,
                    border: "1px solid #e2e8f0",
                    flexWrap: "wrap",
                    gap: 12,
                  }}
                >
                  <div style={{ fontSize: "0.9rem", color: "#475569" }}>
                    Total Kuantitas PO:{" "}
                    <strong data-testid="total-qty-summary">
                      {Number(createCalculations.totalQty || 0).toLocaleString("id-ID")}
                    </strong>{" "}
                    pcs
                  </div>
                  <div style={{ fontSize: "1.1rem", color: "#0f172a", fontWeight: 700 }}>
                    Total Faktur Penjualan:{" "}
                    <span
                      data-testid="grand-total-summary"
                      style={{ color: "#0369a1", fontSize: "1.25rem", fontWeight: 800 }}
                    >
                      Rp {Number(createCalculations.grandTotal || 0).toLocaleString("id-ID")}
                    </span>
                  </div>
                </div>
              </section>

              {formErr && (
                <div
                  className="alert-box error"
                  data-testid="create-error-alert"
                  style={{
                    background: "#fef2f2",
                    border: "1px solid #fecaca",
                    color: "#b91c1c",
                    padding: "10px 14px",
                    borderRadius: 8,
                    marginBottom: 16,
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                  }}
                >
                  <AlertCircle size={16} />
                  <span>{formErr}</span>
                </div>
              )}

              {/* Submit / Cancel Buttons */}
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 12, marginTop: 20 }}>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setSearchParams({});
                    setViewMode("list");
                  }}
                  disabled={formBusy}
                  style={{
                    padding: "10px 20px",
                    borderRadius: 8,
                    border: "1px solid #cbd5e1",
                    background: "#fff",
                    cursor: "pointer",
                    fontWeight: 600,
                  }}
                >
                  Batal
                </button>
                <button
                  type="submit"
                  className="primary"
                  disabled={formBusy}
                  data-testid="save-invoice-btn"
                  style={{
                    padding: "10px 24px",
                    borderRadius: 8,
                    background: formBusy ? "#94a3b8" : "#0284c7",
                    color: "#fff",
                    border: "none",
                    cursor: formBusy ? "not-allowed" : "pointer",
                    fontWeight: 600,
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  {formBusy ? "Menyimpan..." : "Simpan Faktur Penjualan"}
                </button>
              </div>
            </form>
          ) : (
            <div style={{ padding: 40, textAlign: "center", color: "#64748b" }}>
              Silakan pilih Pesanan Penjualan pada bagian 1 di atas untuk memulai.
            </div>
          )}
        </div>
      )}

      {/* VIEW: DETAIL / EDIT */}
      {viewMode === "detail" && (
        <div>
          {detailLoading ? (
            <div style={{ padding: 40, textAlign: "center", color: "#64748b" }}>
              Memuat detail Faktur Penjualan...
            </div>
          ) : detailData ? (
            <form onSubmit={handleUpdateInvoice}>
              {/* Snapshot Header Info */}
              <section
                className="panel"
                style={{
                  background: "#fff",
                  border: "1px solid #e2e8f0",
                  borderRadius: 10,
                  padding: 20,
                  marginBottom: 20,
                  boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
                }}
                data-testid="detail-snapshot-card"
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    borderBottom: "1px solid #f1f5f9",
                    paddingBottom: 12,
                    marginBottom: 16,
                    flexWrap: "wrap",
                    gap: 8,
                  }}
                >
                  <div>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                      }}
                    >
                      Identitas Faktur
                    </span>
                    <h2
                      style={{ margin: "2px 0 0 0", fontSize: "1.25rem", fontWeight: 700, color: "#9333ea" }}
                      data-testid="detail-invoice-number"
                    >
                      {detailData.invoice_number || detailData.id}
                    </h2>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <span style={{ fontSize: "0.75rem", color: "#64748b", display: "block" }}>
                      Dibuat pada
                    </span>
                    <span style={{ fontSize: "0.875rem", fontWeight: 600, color: "#334155" }}>
                      {formatDisplayDate(detailData.created_at)}
                    </span>
                  </div>
                </div>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                    gap: 16,
                  }}
                >
                  <div>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        display: "block",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: 2,
                      }}
                    >
                      No PO
                    </span>
                    <Link
                      to={`/po/${detailData.po_id}`}
                      style={{
                        fontWeight: 600,
                        color: "#0284c7",
                        textDecoration: "none",
                        fontSize: "0.95rem",
                      }}
                      data-testid="detail-po-number"
                    >
                      {detailData.po_number || "-"}
                    </Link>
                  </div>
                  <div>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        display: "block",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: 2,
                      }}
                    >
                      Customer
                    </span>
                    <strong data-testid="detail-customer" style={{ fontSize: "0.95rem", color: "#0f172a" }}>
                      {detailData.customer || "-"}
                    </strong>
                  </div>
                  <div>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        display: "block",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: 2,
                      }}
                    >
                      Outlet / Cabang
                    </span>
                    <span style={{ fontSize: "0.95rem", color: "#334155" }}>
                      {detailData.outlet || "-"}
                    </span>
                  </div>
                  <div>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        display: "block",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: 2,
                      }}
                    >
                      Sales
                    </span>
                    <span style={{ fontSize: "0.95rem", color: "#334155" }}>
                      {detailData.sales || "-"}
                    </span>
                  </div>
                  <div>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        display: "block",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: 2,
                      }}
                    >
                      Penerima / PIC
                    </span>
                    <span style={{ fontSize: "0.95rem", color: "#334155" }}>
                      {detailData.recipient || "-"}
                    </span>
                  </div>
                  <div style={{ gridColumn: "1 / -1" }}>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        color: "#64748b",
                        display: "block",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: 2,
                      }}
                    >
                      Alamat Pengiriman
                    </span>
                    <span style={{ fontSize: "0.9rem", color: "#334155" }}>
                      {detailData.address || "-"}
                    </span>
                  </div>
                </div>

                {/* Editable Date & Notes */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                    gap: 16,
                    marginTop: 16,
                    paddingTop: 16,
                    borderTop: "1px solid #f1f5f9",
                  }}
                >
                  <div>
                    <label
                      style={{
                        fontSize: "0.85rem",
                        fontWeight: 600,
                        color: "#334155",
                        display: "block",
                        marginBottom: 6,
                      }}
                    >
                      Tanggal Faktur
                    </label>
                    <input
                      type="date"
                      value={detailDate}
                      onChange={(e) => setDetailDate(e.target.value)}
                      disabled={!canEdit}
                      data-testid="detail-date-input"
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        border: "1px solid #cbd5e1",
                        borderRadius: 6,
                        fontSize: "0.95rem",
                        boxSizing: "border-box",
                        background: !canEdit ? "#f1f5f9" : "#fff",
                      }}
                    />
                  </div>
                  <div style={{ gridColumn: "2 / -1" }}>
                    <label
                      style={{
                        fontSize: "0.85rem",
                        fontWeight: 600,
                        color: "#334155",
                        display: "block",
                        marginBottom: 6,
                      }}
                    >
                      Catatan Faktur
                    </label>
                    <input
                      type="text"
                      value={detailNotes}
                      onChange={(e) => setDetailNotes(e.target.value)}
                      disabled={!canEdit}
                      data-testid="detail-notes-input"
                      placeholder="Catatan..."
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        border: "1px solid #cbd5e1",
                        borderRadius: 6,
                        fontSize: "0.95rem",
                        boxSizing: "border-box",
                        background: !canEdit ? "#f1f5f9" : "#fff",
                      }}
                    />
                  </div>
                </div>
              </section>

              {/* Table Line Items */}
              <section
                className="panel"
                style={{
                  background: "#fff",
                  border: "1px solid #e2e8f0",
                  borderRadius: 10,
                  padding: 20,
                  marginBottom: 20,
                  boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
                }}
              >
                <header style={{ borderBottom: "1px solid #f1f5f9", paddingBottom: 12, marginBottom: 16 }}>
                  <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#1e293b" }}>
                    Rincian Barang & Harga Faktur
                  </h3>
                </header>

                <div style={{ overflowX: "auto" }}>
                  <table
                    style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.9rem" }}
                    data-testid="detail-items-table"
                  >
                    <thead>
                      <tr style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
                        <th style={{ padding: "10px 12px", width: 40 }}>#</th>
                        <th style={{ padding: "10px 12px", minWidth: 220 }}>Barang / Jasa</th>
                        <th style={{ padding: "10px 12px", width: 110, textAlign: "right" }}>Qty</th>
                        <th style={{ padding: "10px 12px", width: 80, textAlign: "center" }}>Unit</th>
                        <th style={{ padding: "10px 12px", width: 220, textAlign: "right" }}>Harga Satuan (Rp)</th>
                        <th style={{ padding: "10px 12px", width: 200, textAlign: "right" }}>Total Line (Rp)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detailCalculations.lines.map((li, index) => (
                        <tr
                          key={li.line_idx}
                          style={{ borderBottom: "1px solid #f1f5f9" }}
                          data-testid={`detail-line-${li.line_idx}`}
                        >
                          <td style={{ padding: "10px 12px", color: "#64748b", fontWeight: 600 }}>
                            {index + 1}
                          </td>
                          <td style={{ padding: "10px 12px" }}>
                            <div style={{ fontWeight: 600, color: "#1e293b" }}>{li.item_name}</div>
                            {li.color && (
                              <div style={{ fontSize: "0.75rem", color: "#64748b" }}>Warna: {li.color}</div>
                            )}
                          </td>
                          <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 600, color: "#334155" }}>
                            {Number(li.quantity || 0).toLocaleString("id-ID")}
                          </td>
                          <td style={{ padding: "10px 12px", textAlign: "center", color: "#64748b" }}>
                            {li.unit || "pcs"}
                          </td>
                          <td style={{ padding: "8px 12px", textAlign: "right" }}>
                            <input
                              type="number"
                              min="0"
                              step="any"
                              value={detailPrices[li.line_idx] !== undefined ? detailPrices[li.line_idx] : ""}
                              onChange={(e) => {
                                const val = e.target.value;
                                setDetailPrices((prev) => ({ ...prev, [li.line_idx]: val }));
                              }}
                              disabled={!canEdit}
                              data-testid={`detail-unit-price-input-${li.line_idx}`}
                              style={{
                                width: "100%",
                                padding: "6px 10px",
                                border: "1px solid #cbd5e1",
                                borderRadius: 6,
                                boxSizing: "border-box",
                                fontWeight: 600,
                                textAlign: "right",
                                fontSize: "0.95rem",
                                background: !canEdit ? "#f1f5f9" : "#fff",
                              }}
                            />
                          </td>
                          <td
                            style={{ padding: "10px 12px", textAlign: "right", fontWeight: 700, color: "#0f172a" }}
                            data-testid={`detail-line-total-${li.line_idx}`}
                          >
                            Rp {Number(li.lineTotal || 0).toLocaleString("id-ID")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Grand Total Footer */}
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginTop: 16,
                    padding: "14px 18px",
                    background: "#f8fafc",
                    borderRadius: 8,
                    border: "1px solid #e2e8f0",
                    flexWrap: "wrap",
                    gap: 12,
                  }}
                >
                  <div style={{ fontSize: "0.9rem", color: "#475569" }}>
                    Total Kuantitas:{" "}
                    <strong>{Number(detailCalculations.totalQty || 0).toLocaleString("id-ID")}</strong> pcs
                  </div>
                  <div style={{ fontSize: "1.1rem", color: "#0f172a", fontWeight: 700 }}>
                    Total Faktur Penjualan:{" "}
                    <span
                      data-testid="detail-grand-total"
                      style={{ color: "#0369a1", fontSize: "1.25rem", fontWeight: 800 }}
                    >
                      Rp {Number(detailCalculations.grandTotal || 0).toLocaleString("id-ID")}
                    </span>
                  </div>
                </div>
              </section>

              {detailErr && (
                <div
                  className="alert-box error"
                  data-testid="detail-error-alert"
                  style={{
                    background: "#fef2f2",
                    border: "1px solid #fecaca",
                    color: "#b91c1c",
                    padding: "10px 14px",
                    borderRadius: 8,
                    marginBottom: 16,
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                  }}
                >
                  <AlertCircle size={16} />
                  <span>{detailErr}</span>
                </div>
              )}

              {/* Action Buttons */}
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 12, marginTop: 20 }}>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setSearchParams({});
                    setViewMode("list");
                    loadInvoices();
                  }}
                  style={{
                    padding: "10px 20px",
                    borderRadius: 8,
                    border: "1px solid #cbd5e1",
                    background: "#fff",
                    cursor: "pointer",
                    fontWeight: 600,
                  }}
                >
                  Kembali ke Daftar
                </button>
                {canEdit && (
                  <button
                    type="submit"
                    className="primary"
                    disabled={detailSaving}
                    data-testid="save-detail-invoice-btn"
                    style={{
                      padding: "10px 24px",
                      borderRadius: 8,
                      background: detailSaving ? "#94a3b8" : "#0284c7",
                      color: "#fff",
                      border: "none",
                      cursor: detailSaving ? "not-allowed" : "pointer",
                      fontWeight: 600,
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                    }}
                  >
                    {detailSaving ? "Menyimpan..." : "Simpan Perubahan"}
                  </button>
                )}
              </div>
            </form>
          ) : (
            <div style={{ padding: 40, textAlign: "center", color: "#64748b" }}>
              Data Faktur Penjualan tidak ditemukan.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
