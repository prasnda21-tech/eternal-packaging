import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertCircle, Plus, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate } from "../lib/constants";
import { errDetail, friendlyLoadError, purchaseStatusPillClass, todayYmd, purchaseRowInteractionProps } from "../lib/purchaseUi";
import { fmtQty } from "../components/qtyUi";
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";
import MasterItemSearchSelect from "../components/MasterItemSearchSelect";
import PurchasingFlowNav from "./PurchasingFlowNav";
import PurchaseCancelModal from "./PurchaseCancelModal";
import PurchaseActivityHistory from "./PurchaseActivityHistory";
import "./purchasePages.css";

export default function PermintaanPembelian() {
  const [searchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("purchase_requests.create");
  const canCreatePo = hasPerm("purchase_orders.create");
  const initial = searchParams.get("action") === "create" ? "create" : "list";
  const [view, setView] = useState(initial);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [q, setQ] = useState("");
  const [detail, setDetail] = useState(null);
  const [cancelTarget, setCancelTarget] = useState(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelErr, setCancelErr] = useState("");

  const [requestDate, setRequestDate] = useState(todayYmd());
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState([{ item_name: "", item_code: "", unit: "pcs", requested_qty: "", master_item_id: "", accurate_item_id: "" }]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await api.listPurchaseRequests({ q });
      setRows(res.requests || []);
    } catch (ex) {
      setErr(friendlyLoadError(ex, "Tidak dapat memuat permintaan. Periksa koneksi server lalu coba lagi."));
    } finally {
      setLoading(false);
    }
  }, [q]);

  useEffect(() => { if (view === "list") load(); }, [load, view]);

  const openCreate = async () => {
    setView("create");
  };

  const addLine = () => setLines((p) => [...p, { item_name: "", item_code: "", unit: "pcs", requested_qty: "", master_item_id: "", accurate_item_id: "" }]);
  const removeLine = (i) => setLines((p) => (p.length <= 1 ? p : p.filter((_, idx) => idx !== i)));
  const setLine = (i, patch) => setLines((p) => p.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));

  const pickItem = (i, it) => {
    if (!it) return setLine(i, { master_item_id: "", accurate_item_id: "", item_name: "", item_code: "", unit: "pcs" });
    setLine(i, {
      master_item_id: it.id || it.master_item_id || "",
      accurate_item_id: it.accurate_item_id || "",
      item_name: it.item_name || "",
      item_code: it.item_code || "",
      unit: it.unit || "pcs",
    });
  };

  const save = async () => {
    if (busy) return;
    if (lines.some((l) => !l.master_item_id)) {
      setErr("Setiap baris harus memilih item dari Master Item.");
      return;
    }
    setBusy(true);
    setErr("");
    setOk("");
    try {
      const res = await api.createPurchaseRequest({
        request_date: requestDate,
        notes,
        lines: lines.map((l, idx) => ({ ...l, line_idx: idx, requested_qty: l.requested_qty })),
      });
      setView("detail");
      setDetail(res.request);
      setOk(`Permintaan ${res.request?.request_number || ""} berhasil disimpan.`);
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  const openDetail = async (id) => {
    setErr("");
    try {
      const res = await api.getPurchaseRequest(id);
      setDetail(res.request);
      setView("detail");
    } catch (ex) {
      setErr(errDetail(ex));
    }
  };

  const openCancel = (row) => {
    setCancelErr("");
    setCancelTarget(row);
  };

  const confirmCancel = async () => {
    if (!cancelTarget || cancelBusy) return;
    setCancelBusy(true);
    setCancelErr("");
    try {
      await api.cancelPurchaseRequest(cancelTarget.id);
      setOk(`Permintaan ${cancelTarget.request_number || ""} dihapus dari daftar aktif.`);
      setCancelTarget(null);
      if (view === "detail" && detail?.id === cancelTarget.id) {
        setView("list");
        setDetail(null);
      }
      await load();
    } catch (ex) {
      setCancelErr(errDetail(ex));
    } finally {
      setCancelBusy(false);
    }
  };

  return (
    <div className="prod-page purchase-page" data-testid="permintaan-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1 data-testid="permintaan-title">Permintaan Pembelian</h1>
          <p className="subhead">Ajukan kebutuhan barang sebelum dibuat menjadi pesanan pembelian.</p>
        </div>
        {view === "list" && canCreate && (
          <button className="primary" onClick={openCreate} data-testid="btn-create-permintaan"><Plus size={14} /> Buat Permintaan</button>
        )}
        {view !== "list" && (
          <button className="ghost" onClick={() => { setView("list"); load(); }}>Kembali ke daftar</button>
        )}
      </div>
      <PurchasingFlowNav />
      {err && (
        <div className="purchase-error-panel" role="alert">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <AlertCircle size={16} color="#a53f2b" /><span>{err}</span>
          </div>
          {view === "list" && <button type="button" className="ghost sm" onClick={load}>Coba Lagi</button>}
        </div>
      )}
      {ok && <p className="purchase-inline-ok" data-testid="permintaan-success">{ok}</p>}

      {view === "list" && (
        <>
          <div className="purchase-search-bar is-toolbar">
            <form className="search-inline" onSubmit={(e) => { e.preventDefault(); load(); }}>
              <Search size={14} />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cari no permintaan / item" data-testid="permintaan-search" />
            </form>
          </div>
          <div className="purchase-table-wrap">
            <table className="prod-table purchase-table purchase-table-pr" data-testid="permintaan-table">
              <colgroup>
                <col className="col-pr" />
                <col className="col-date" />
                <col className="col-qty" />
                <col className="col-ordered" />
                <col className="col-sisa" />
                <col className="col-status" />
              </colgroup>
              <thead>
                <tr>
                  <th>No PR</th>
                  <th>Tanggal</th>
                  <th className="num">Qty</th>
                  <th className="num th-wrap">Sudah Dipesan</th>
                  <th className="num">Sisa</th>
                  <th className="cell-status">Status</th>
                </tr>
              </thead>
              <tbody>
                {loading && <tr className="purchase-empty-row"><td colSpan={6} className="muted small">Memuat…</td></tr>}
                {!loading && rows.length === 0 && (
                  <tr className="purchase-empty-row">
                    <td colSpan={6} className="muted small" data-testid="permintaan-empty">Belum ada permintaan.</td>
                  </tr>
                )}
                {rows.map((r) => {
                  const qty = Number(r.total_requested_qty || 0);
                  const sisa = Number(r.total_remaining_order_qty || 0);
                  const ordered = Math.max(qty - sisa, 0);
                  return (
                    <tr
                      key={r.id}
                      {...purchaseRowInteractionProps(() => openDetail(r.id), { testId: `permintaan-row-${r.id}` })}
                    >
                      <td className="mono cell-clip">{r.request_number}</td>
                      <td className="nowrap">{r.request_date || formatDisplayDate(r.created_at)}</td>
                      <td className="num mono">{fmtQty(qty)}</td>
                      <td className="num mono">{fmtQty(ordered)}</td>
                      <td className="num mono">{fmtQty(sisa)}</td>
                      <td className="cell-status">
                        <span className={`purchase-status-badge ${purchaseStatusPillClass(r.status)}`}>{r.status}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {view === "create" && (
        <div className="card purchase-detail-card" data-testid="permintaan-create-form">
          <h2 className="purchase-form-title">Buat Permintaan Pembelian</h2>
          <p className="purchase-form-helper">Isi tanggal dan daftar barang yang dibutuhkan.</p>
          <FormProgress
            percentage={Math.round(([requestDate, lines.some(l => l.item_name || l.master_item_id)].filter(Boolean).length / 2) * 100)}
            completed={[requestDate, lines.some(l => l.item_name || l.master_item_id)].filter(Boolean).length}
            total={2}
          />
          <FormSection
            id="permintaan-header"
            title="Informasi Permintaan"
            status={computeStatus([{ value: requestDate, isRequired: true }])}
          >
            <div className="purchase-form-grid">
              <FormField id="permintaan-date-field" label="Tanggal" isRequired value={requestDate}>
                <input type="date" value={requestDate} onChange={(e) => setRequestDate(e.target.value)} data-testid="permintaan-date" />
              </FormField>
              <FormField id="permintaan-notes-field" label="Catatan" value={notes}>
                <input value={notes} onChange={(e) => setNotes(e.target.value)} />
              </FormField>
            </div>
          </FormSection>
          <FormSection
            id="permintaan-lines"
            title="Daftar Item"
            status={lines.some(l => l.master_item_id) ? "complete" : "incomplete"}
          >
            <div className="purchase-table-wrap">
              <table className="prod-table purchase-lines-table" data-testid="permintaan-lines">
                <colgroup>
                  <col className="col-item-line" />
                  <col className="col-code-line" />
                  <col className="col-unit-line" />
                  <col className="col-qty-line" />
                  <col className="col-action-line" />
                </colgroup>
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Kode</th>
                    <th>Satuan</th>
                    <th className="num">Qty</th>
                    <th>Aksi</th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l, i) => (
                    <tr key={i}>
                      <td>
                        <MasterItemSearchSelect
                          testId={`line-item-${i}`}
                          value={
                            l.master_item_id
                              ? {
                                  accurate_item_id: l.accurate_item_id,
                                  id: l.master_item_id,
                                  master_item_id: l.master_item_id,
                                  item_name: l.item_name,
                                  item_code: l.item_code,
                                  unit: l.unit,
                                }
                              : null
                          }
                          onChange={(item) => pickItem(i, item)}
                          placeholder="Cari Master Item…"
                        />
                      </td>
                      <td className="mono">{l.item_code || "—"}</td>
                      <td className="unit-cell"><input value={l.unit} readOnly tabIndex={-1} /></td>
                      <td className="qty-cell"><input type="number" min="0" value={l.requested_qty} onChange={(e) => setLine(i, { requested_qty: e.target.value })} data-testid={`line-qty-${i}`} /></td>
                      <td className="action-cell"><button type="button" className="ghost sm" onClick={() => removeLine(i)}>Hapus</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button className="ghost purchase-add-line" type="button" onClick={addLine} data-testid="add-request-line">Tambah baris</button>
          </FormSection>
          <div className="purchase-form-actions">
            <button className="ghost" type="button" onClick={() => setView("list")}>Batal</button>
            <button className="primary" disabled={busy} onClick={save} data-testid="save-permintaan">{busy ? "Menyimpan…" : "Simpan Permintaan"}</button>
          </div>
        </div>
      )}

      {view === "detail" && detail && (
        <div className="card purchase-detail-card" data-testid="permintaan-detail">
          <div className="purchase-detail-meta">
            <div>
              <span className="label">No PR</span>
              <span className="value mono" data-testid="detail-request-number">{detail.request_number}</span>
            </div>
            <div>
              <span className="label">Tanggal</span>
              <span className="value">{detail.request_date || "—"}</span>
            </div>
            <div>
              <span className="label">Status</span>
              <span className={purchaseStatusPillClass(detail.status)}>{detail.status}</span>
            </div>
          </div>
          <div className="purchase-table-wrap">
            <table className="prod-table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Kode</th>
                  <th>Satuan</th>
                  <th className="num">Qty</th>
                  <th className="num">Sudah dipesan</th>
                  <th className="num">Sisa</th>
                </tr>
              </thead>
              <tbody>
                {(detail.lines || []).map((l) => (
                  <tr key={l.line_id} data-testid={`detail-line-${l.line_id}`}>
                    <td>{l.item_name}</td>
                    <td className="mono">{l.item_code || "—"}</td>
                    <td>{l.unit || "pcs"}</td>
                    <td className="num">{fmtQty(l.requested_qty)}</td>
                    <td className="num">{fmtQty(l.cumulative_ordered_qty)}</td>
                    <td className="num">{fmtQty(l.remaining_order_qty)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <PurchaseActivityHistory doc={detail} testId="permintaan-activity" />
          <div className="purchase-detail-actions">
            {canCreate && (
              <button
                type="button"
                className="ghost danger"
                data-testid="btn-hapus-pr-detail"
                disabled={detail.can_cancel === false}
                title={detail.can_cancel === false ? (detail.cancel_block_reason || "") : undefined}
                onClick={() => openCancel(detail)}
              >
                Hapus
              </button>
            )}
            {canCreatePo && Number(detail.total_remaining_order_qty) > 0 && (
              <Link className="primary" data-testid="btn-buat-pesanan-pembelian" to={`/pembelian/pesanan-pembelian?request_id=${detail.id}&action=create`}>Buat Pesanan</Link>
            )}
          </div>
        </div>
      )}

      <PurchaseCancelModal
        open={Boolean(cancelTarget)}
        title="Hapus Permintaan Pembelian?"
        body="Dokumen akan dikeluarkan dari daftar aktif. Riwayat pembatalan tetap tersimpan."
        busy={cancelBusy}
        error={cancelErr}
        onCancel={() => !cancelBusy && setCancelTarget(null)}
        onConfirm={confirmCancel}
      />
    </div>
  );
}
