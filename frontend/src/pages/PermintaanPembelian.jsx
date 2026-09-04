import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { AlertCircle, Plus, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate } from "../lib/constants";
import { errDetail, todayYmd } from "../lib/purchaseUi";
import { fmtQty } from "../components/qtyUi";
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";
import MasterItemSearchSelect from "../components/MasterItemSearchSelect";

export default function PermintaanPembelian() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("purchase_requests.create");
  const initial = searchParams.get("action") === "create" ? "create" : "list";
  const [view, setView] = useState(initial);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [detail, setDetail] = useState(null);

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
      setErr(errDetail(ex));
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
    setBusy(true);
    setErr("");
    try {
      const res = await api.createPurchaseRequest({
        request_date: requestDate,
        notes,
        lines: lines.map((l, idx) => ({ ...l, line_idx: idx, requested_qty: l.requested_qty })),
      });
      setView("detail");
      setDetail(res.request);
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

  return (
    <div className="prod-page" data-testid="permintaan-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1 data-testid="permintaan-title">Permintaan</h1>
          <p className="subhead">Permintaan pembelian material/produk. Baris bernama sama tetap terpisah.</p>
        </div>
        {view === "list" && canCreate && (
          <button className="primary" onClick={openCreate} data-testid="btn-create-permintaan"><Plus size={14} /> Buat Permintaan</button>
        )}
        {view !== "list" && (
          <button className="ghost" onClick={() => { setView("list"); load(); }}>Kembali ke daftar</button>
        )}
      </div>
      {err && <div className="signal-strip" role="alert"><AlertCircle size={16} /><span>{err}</span></div>}

      {view === "list" && (
        <>
          <div className="prod-filters">
            <form className="search-inline" onSubmit={(e) => { e.preventDefault(); load(); }}>
              <Search size={14} />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cari no permintaan / item" data-testid="permintaan-search" />
            </form>
          </div>
          <div className="prod-table-wrap">
            <table className="prod-table" data-testid="permintaan-table">
              <thead>
                <tr>
                  <th>No Permintaan</th>
                  <th>Tanggal</th>
                  <th>Status</th>
                  <th className="num">Sisa dipesan</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {loading && <tr><td colSpan={5} className="muted small">Memuat…</td></tr>}
                {!loading && rows.length === 0 && <tr><td colSpan={5} className="muted small" data-testid="permintaan-empty">Belum ada permintaan.</td></tr>}
                {rows.map((r) => (
                  <tr key={r.id} data-testid={`permintaan-row-${r.id}`}>
                    <td className="mono">{r.request_number}</td>
                    <td>{r.request_date || formatDisplayDate(r.created_at)}</td>
                    <td>{r.status}</td>
                    <td className="num mono">{fmtQty(r.total_remaining_order_qty)}</td>
                    <td>
                      <button className="ghost sm" onClick={() => openDetail(r.id)}>Detail</button>
                      {canCreate && Number(r.total_remaining_order_qty) > 0 && (
                        <button className="primary sm" data-testid={`btn-buat-po-${r.id}`} onClick={() => navigate(`/pembelian/pesanan-pembelian?request_id=${r.id}&action=create`)}>Buat Pesanan Pembelian</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {view === "create" && (
        <div className="card" data-testid="permintaan-create-form">
          <FormProgress
            percentage={Math.round(([requestDate, lines.some(l => l.item_name || l.master_item_id)].filter(Boolean).length / 2) * 100)}
            completed={[requestDate, lines.some(l => l.item_name || l.master_item_id)].filter(Boolean).length}
            total={2}
          />
          <FormSection
            id="permintaan-header"
            title="Informasi Permintaan"
            description="Tanggal dan catatan permintaan."
            status={computeStatus([{ value: requestDate, isRequired: true }])}
          >
            <FormField id="permintaan-date-field" label="Tanggal" isRequired value={requestDate}>
              <input type="date" value={requestDate} onChange={(e) => setRequestDate(e.target.value)} data-testid="permintaan-date" />
            </FormField>
            <FormField id="permintaan-notes-field" label="Catatan" value={notes}>
              <input value={notes} onChange={(e) => setNotes(e.target.value)} />
            </FormField>
          </FormSection>
          <FormSection
            id="permintaan-lines"
            title="Daftar Item"
            description="Tambahkan item yang diminta."
            status={lines.some(l => l.item_name || l.master_item_id) ? "complete" : "incomplete"}
          >
            <table className="prod-table" data-testid="permintaan-lines">
              <thead><tr><th>Item</th><th>Kode</th><th>Satuan</th><th>Qty</th><th></th></tr></thead>
              <tbody>
                {lines.map((l, i) => (
                  <tr key={i}>
                    <td>
                      <MasterItemSearchSelect
                        testId={`line-item-${i}`}
                        value={
                          l.accurate_item_id || l.master_item_id
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
                        placeholder="Cari nama / kode produk…"
                      />
                      {!l.master_item_id && !l.accurate_item_id && (
                        <input
                          value={l.item_name}
                          onChange={(e) => setLine(i, { item_name: e.target.value })}
                          placeholder="Atau nama item bebas"
                          data-testid={`line-item-free-${i}`}
                          style={{ marginTop: 6 }}
                        />
                      )}
                    </td>
                    <td><input value={l.item_code} onChange={(e) => setLine(i, { item_code: e.target.value })} /></td>
                    <td><input value={l.unit} onChange={(e) => setLine(i, { unit: e.target.value })} /></td>
                    <td><input type="number" min="0" value={l.requested_qty} onChange={(e) => setLine(i, { requested_qty: e.target.value })} data-testid={`line-qty-${i}`} /></td>
                    <td><button type="button" className="ghost sm" onClick={() => removeLine(i)}>Hapus</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="ghost" type="button" onClick={addLine} data-testid="add-request-line">Tambah baris</button>
          </FormSection>
          <button className="primary" disabled={busy} onClick={save} data-testid="save-permintaan">{busy ? "Menyimpan…" : "Simpan Permintaan"}</button>
        </div>
      )}

      {view === "detail" && detail && (
        <div className="card" data-testid="permintaan-detail">
          <p><b>No Permintaan</b> <span data-testid="detail-request-number">{detail.request_number}</span></p>
          <p>Status: {detail.status}</p>
          <table className="prod-table">
            <thead><tr><th>Item</th><th>Qty minta</th><th>Sudah dipesan</th><th>Sisa</th></tr></thead>
            <tbody>
              {(detail.lines || []).map((l) => (
                <tr key={l.line_id} data-testid={`detail-line-${l.line_id}`}>
                  <td>{l.item_name}</td>
                  <td className="num">{fmtQty(l.requested_qty)}</td>
                  <td className="num">{fmtQty(l.cumulative_ordered_qty)}</td>
                  <td className="num">{fmtQty(l.remaining_order_qty)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {canCreate && Number(detail.total_remaining_order_qty) > 0 && (
            <Link className="primary" data-testid="btn-buat-pesanan-pembelian" to={`/pembelian/pesanan-pembelian?request_id=${detail.id}&action=create`}>Buat Pesanan Pembelian</Link>
          )}
        </div>
      )}
    </div>
  );
}
