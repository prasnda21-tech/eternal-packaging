import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertCircle, Plus, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate } from "../lib/constants";
import { errDetail, friendlyLoadError, purchaseStatusPillClass, todayYmd, purchaseRowInteractionProps } from "../lib/purchaseUi";
import { fmtQty } from "../components/qtyUi";
import MasterItemSearchSelect from "../components/MasterItemSearchSelect";
import PurchasingFlowNav from "./PurchasingFlowNav";
import PurchaseCancelModal from "./PurchaseCancelModal";
import PurchaseActivityHistory from "./PurchaseActivityHistory";
import PurchaseDocumentLayout from "../components/purchase-document/PurchaseDocumentLayout";
import { actorName, dash, formatLongDate, requestStatusSentence, transactionSteps } from "../components/purchase-document/model";
import { listIdentityBranchId, listIdentityUserId } from "../lib/listIdentity";
import { purchasingListCache } from "../lib/smartListCaches";
import "./purchasePages.css";

export default function PermintaanPembelian() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { hasPerm, user } = useAuth();
  const listUserId = listIdentityUserId(user);
  const listBranchId = listIdentityBranchId(user);
  const canCreate = hasPerm("purchase_requests.create");
  const canCreatePo = hasPerm("purchase_orders.create");
  const initial = searchParams.get("action") === "create" ? "create" : "list";
  const [view, setView] = useState(initial);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState("");
  const [syncErr, setSyncErr] = useState("");
  const [ok, setOk] = useState("");
  const [q, setQ] = useState(() => searchParams.get("q") || "");
  const [detail, setDetail] = useState(null);
  const [cancelTarget, setCancelTarget] = useState(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelErr, setCancelErr] = useState("");
  const requestSeq = useRef(0);
  const prevListUserRef = useRef(listUserId);

  const [requestDate, setRequestDate] = useState(todayYmd());
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState([]);
  const [focusLine, setFocusLine] = useState(null);
  const [focusQty, setFocusQty] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async ({ force = false } = {}) => {
    const cacheKey = purchasingListCache.buildKey({
      u: listUserId,
      b: listBranchId,
      kind: "purchase_requests",
      q,
    });
    const cached = purchasingListCache.get(cacheKey);
    const hasCache = Boolean(cached?.data);
    if (hasCache) {
      setRows(Array.isArray(cached.data.requests) ? cached.data.requests : []);
    } else {
      setRows([]);
    }
    const seq = requestSeq.current + 1;
    requestSeq.current = seq;
    if (hasCache) {
      setRefreshing(true);
      setLoading(false);
    } else {
      setLoading(true);
      setRefreshing(false);
    }
    setErr("");
    try {
      const res = await api.listPurchaseRequests({ q });
      if (seq !== requestSeq.current) return;
      const nextRows = res.requests || [];
      purchasingListCache.set(cacheKey, { requests: nextRows, res });
      setRows(nextRows);
      setSyncErr("");
    } catch (ex) {
      if (seq !== requestSeq.current) return;
      const message = friendlyLoadError(ex, "Tidak dapat memuat permintaan. Periksa koneksi server lalu coba lagi.");
      if (hasCache) setSyncErr(message);
      else setErr(message);
    } finally {
      if (seq === requestSeq.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [listBranchId, listUserId, q]);

  const reloadAuthoritative = useCallback(async () => {
    purchasingListCache.invalidate();
    setSyncErr("");
    await load({ force: true });
  }, [load]);

  useEffect(() => { if (view === "list") load(); }, [load, view]);

  useEffect(() => {
    if (view !== "list") return;
    if (searchParams.get("action") === "create") return;
    const next = new URLSearchParams();
    if (q.trim()) next.set("q", q.trim());
    if (next.toString() === searchParams.toString()) return;
    setSearchParams(next, { replace: true });
  }, [view, q, searchParams, setSearchParams]);

  useEffect(() => {
    if (prevListUserRef.current === listUserId) return;
    purchasingListCache.clear();
    prevListUserRef.current = listUserId;
  }, [listUserId]);

  useEffect(() => {
    if (focusLine == null || view !== "create") return;
    const node = document.querySelector(`[data-testid="line-item-${focusLine}-input"]`);
    if (!node) return;
    node.focus();
    setFocusLine(null);
  }, [focusLine, lines, view]);

  useEffect(() => {
    if (focusQty == null || view !== "create") return;
    const node = document.querySelector(`[data-testid="line-qty-${focusQty}"]`);
    if (!node) return;
    node.focus();
    setFocusQty(null);
  }, [focusQty, lines, view]);

  const openCreate = async () => {
    setRequestDate(todayYmd());
    setNotes("");
    setLines([]);
    setFocusLine(null);
    setFocusQty(null);
    setErr("");
    setOk("");
    setView("create");
  };

  const addLine = () => {
    setFocusLine(lines.length);
    setLines((p) => [...p, { item_name: "", item_code: "", unit: "", requested_qty: "", master_item_id: "", accurate_item_id: "", notes: "" }]);
  };
  const removeLine = (i) => setLines((p) => p.filter((_, idx) => idx !== i));
  const setLine = (i, patch) => setLines((p) => p.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));

  const pickItem = (i, it) => {
    if (!it) return setLine(i, { master_item_id: "", accurate_item_id: "", item_name: "", item_code: "", unit: "" });
    setLine(i, {
      master_item_id: it.id || it.master_item_id || "",
      accurate_item_id: it.accurate_item_id || "",
      item_name: it.item_name || "",
      item_code: it.item_code || "",
      unit: it.unit || "pcs",
    });
    setFocusQty(i);
  };

  const onLineKeyDown = (index, event) => {
    if (event.key !== "Enter" || event.defaultPrevented) return;
    const qty = document.querySelector(`[data-testid="line-qty-${index}"]`);
    if (event.target === qty) {
      event.preventDefault();
      addLine();
      return;
    }
    const search = document.querySelector(`[data-testid="line-item-${index}-input"]`);
    if (event.target !== search || !lines[index]?.master_item_id) return;
    event.preventDefault();
    qty?.focus();
  };

  const pemohon = String(user?.name || user?.email || "").trim() || "—";
  const itemQty = lines.reduce((sum, line) => sum + (Number(line.requested_qty) || 0), 0);
  const itemUnits = [...new Set(lines.map((line) => String(line.unit || "").trim()).filter(Boolean))];
  const itemTotal = itemUnits.length === 1 ? `${fmtQty(itemQty)} ${itemUnits[0]}` : fmtQty(itemQty);

  const save = async () => {
    if (busy) return;
    if (!lines.length || lines.some((l) => !l.master_item_id)) {
      setErr("Setiap baris harus memilih item dari Master Item.");
      return;
    }
    if (lines.some((l) => !(Number(String(l.requested_qty).replace(",", ".")) > 0))) {
      setErr("Qty setiap baris harus lebih besar dari 0.");
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
      await reloadAuthoritative();
    } catch (ex) {
      setCancelErr(errDetail(ex));
    } finally {
      setCancelBusy(false);
    }
  };

  return (
    <div className="prod-page purchase-page" data-testid="permintaan-page">
      {view === "list" && <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1 data-testid="permintaan-title">Permintaan Pembelian</h1>
          <p className="subhead">
            Ajukan kebutuhan barang sebelum dibuat menjadi pesanan pembelian.
            {refreshing ? <span data-testid="permintaan-list-refreshing"> · menyegarkan</span> : null}
          </p>
        </div>
        {canCreate && (
          <button className="primary" onClick={openCreate} data-testid="btn-create-permintaan"><Plus size={14} /> Buat Permintaan</button>
        )}
      </div>}
      {view === "list" && <PurchasingFlowNav />}
      {err && (
        <div className="purchase-error-panel" role="alert">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <AlertCircle size={16} color="#a53f2b" /><span>{err}</span>
          </div>
          {view === "list" && <button type="button" className="ghost sm" onClick={load}>Coba Lagi</button>}
        </div>
      )}
      {ok && <p className="purchase-inline-ok" data-testid="permintaan-success">{ok}</p>}
      {syncErr && (
        <p className="purchase-inline-ok" data-testid="permintaan-list-sync-error" role="status" style={{ color: "#92400e" }}>
          {syncErr} Data di bawah belum disinkronkan ulang.
        </p>
      )}

      {view === "list" && (
        <>
          <div className="purchase-search-bar is-toolbar">
            <form className="search-inline" onSubmit={(e) => { e.preventDefault(); load({ force: true }); }}>
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
        <article className="pr-create" data-testid="permintaan-create-form">
          <header className="pr-create-head">
            <div className="pr-create-title">
              <h1>Permintaan Pembelian</h1>
              <span className="sr-only">Buat Permintaan Pembelian</span>
              <button type="button" className="pr-create-back" onClick={() => { setView("list"); load(); }}>Kembali ke daftar</button>
            </div>
            <div className="pr-create-meta">
              <div><span className="pr-k">Nomor PR</span><span className="pr-v">Otomatis</span></div>
              <div>
                <span className="pr-k">Tanggal</span>
                <input className="pr-date" type="date" value={requestDate} onChange={(e) => setRequestDate(e.target.value)} data-testid="permintaan-date" />
              </div>
              <div><span className="pr-k">Pemohon</span><span className="pr-v">{pemohon}</span></div>
              <div><span className="pr-k">Departemen</span><span className="pr-v pr-muted">—</span></div>
              <div><span className="pr-k">Prioritas</span><span className="pr-v pr-muted">—</span></div>
              <div><span className="pr-k">Status</span><span className="pr-v pr-status">Belum Disimpan</span></div>
            </div>
          </header>
          <div className="pr-create-sheet">
            <div className="pr-create-table-wrap">
              <table data-testid="permintaan-lines">
                <colgroup>
                  <col className="pr-col-code" />
                  <col />
                  <col className="pr-col-spec" />
                  <col className="pr-col-qty" />
                  <col className="pr-col-unit" />
                  <col className="pr-col-wh" />
                  <col className="pr-col-need" />
                  <col className="pr-col-note" />
                  <col className="pr-col-action" />
                </colgroup>
                <thead>
                  <tr>
                    <th>Kode Barang</th>
                    <th>Nama Barang</th>
                    <th>Spesifikasi</th>
                    <th className="num">Qty</th>
                    <th>Satuan</th>
                    <th>Gudang</th>
                    <th>Tanggal Dibutuhkan</th>
                    <th>Keterangan</th>
                    <th className="pr-action-head"><span className="sr-only">Aksi</span></th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l, i) => (
                    <tr key={i} onKeyDown={(event) => onLineKeyDown(i, event)}>
                      <td className="mono pr-code">{l.item_code || "—"}</td>
                      <td className="pr-name">
                        {l.master_item_id ? (
                          <span className="pr-item-name">{l.item_name}</span>
                        ) : (
                          <MasterItemSearchSelect
                            testId={`line-item-${i}`}
                            allowClear={false}
                            value={null}
                            onChange={(item) => pickItem(i, item)}
                            placeholder="Cari master item"
                          />
                        )}
                      </td>
                      <td className="pr-muted">—</td>
                      <td className="num">
                        <input className="pr-qty" type="number" min="0" value={l.requested_qty} onChange={(e) => setLine(i, { requested_qty: e.target.value })} data-testid={`line-qty-${i}`} />
                      </td>
                      <td className="pr-unit">{l.unit || "—"}</td>
                      <td className="pr-muted">—</td>
                      <td className="pr-muted">—</td>
                      <td><input className="pr-note" value={l.notes || ""} onChange={(e) => setLine(i, { notes: e.target.value })} aria-label="Keterangan" /></td>
                      <td className="pr-row-action">
                        <button type="button" className="pr-remove" onClick={() => removeLine(i)}>Hapus</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button className="pr-add" type="button" onClick={addLine} data-testid="add-request-line">+ Tambah Barang</button>
          </div>
          <footer className="pr-create-dock">
            <label className="pr-create-note">
              <span className="pr-k">Catatan Umum</span>
              <input value={notes} onChange={(e) => setNotes(e.target.value)} />
            </label>
            <div className="pr-create-total"><span>Total jumlah item</span><b>{itemTotal}</b></div>
            <button className="primary" disabled={busy} onClick={save} data-testid="save-permintaan">{busy ? "Menyimpan…" : "Simpan Permintaan"}</button>
          </footer>
        </article>
      )}

      {view === "detail" && detail && (
        <PurchaseDocumentLayout
          testId="permintaan-detail"
          title="Permintaan Pembelian"
          number={detail.request_number}
          numberTestId="detail-request-number"
          note={detail.notes || ""}
          steps={transactionSteps(detail)}
          facts={[
            { label: "Tanggal", value: formatLongDate(detail.request_date) },
            { label: "Cabang", value: dash(detail.branch_name || detail.branch) },
            { label: "Pemohon", value: actorName(detail) },
            { label: "Jenis transaksi", value: dash(detail.transaction_type || detail.jenis) },
            { label: "Supplier", value: detail.supplier ? detail.supplier : "Belum ditentukan" },
            { label: "Status", value: requestStatusSentence(detail.status), status: true },
          ]}
          totals={[{ label: "Subtotal", value: "—" }, { label: "Total", value: "—" }]}
          history={<PurchaseActivityHistory doc={detail} testId="permintaan-activity" />}
          extraMenu={[
            { label: "Kembali ke daftar", onClick: () => { setView("list"); load(); } },
            ...(canCreate ? [{ label: "Hapus", onClick: () => openCancel(detail) }] : []),
          ]}
          primary={canCreatePo && Number(detail.total_remaining_order_qty) > 0 ? (
            <Link className="primary" data-testid="btn-buat-pesanan-pembelian" to={`/pembelian/pesanan-pembelian?request_id=${detail.id}&action=create`}>Buat Pesanan</Link>
          ) : null}
        >
          {canCreate && (
            <button
              type="button"
              className="sr-only"
              data-testid="btn-hapus-pr-detail"
              disabled={detail.can_cancel === false}
              title={detail.can_cancel === false ? (detail.cancel_block_reason || "") : undefined}
              onClick={() => openCancel(detail)}
            >
              Hapus
            </button>
          )}
          <table>
            <thead>
              <tr>
                <th>Kode Barang</th>
                <th>Nama Barang</th>
                <th className="num">Qty</th>
                <th>Satuan</th>
                <th>Gudang</th>
                <th>Tanggal Dibutuhkan</th>
                <th className="num">Harga</th>
                <th className="num">Total</th>
                <th>Keterangan</th>
              </tr>
            </thead>
            <tbody>
              {(detail.lines || []).map((l) => (
                <tr key={l.line_id} data-testid={`detail-line-${l.line_id}`}>
                  <td className="mono">{dash(l.item_code)}</td>
                  <td className="pd-name">{dash(l.item_name)}</td>
                  <td className="num">{fmtQty(l.requested_qty)}</td>
                  <td>{dash(l.unit)}</td>
                  <td>{dash(l.warehouse_name || l.warehouse)}</td>
                  <td>{dash(l.needed_date)}</td>
                  <td className="num">—</td>
                  <td className="num">—</td>
                  <td>{dash(l.notes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </PurchaseDocumentLayout>
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
