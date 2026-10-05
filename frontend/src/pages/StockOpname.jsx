import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import api from "../lib/api";
import { formatDate } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import { operationalOpeningWarehouses, preselectedWarehouseId } from "../lib/physicalStockView";

function newKey() {
  return globalThis.crypto?.randomUUID?.() || `opname-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function errorText(err) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((row) => row.msg || String(row)).join(" ");
  return err?.message || "Gagal memproses stock opname.";
}

function countText(value) {
  return value === null || value === undefined || value === "" ? "" : String(value);
}

function statusLabel(status) {
  if (status === "DRAFT") return "Draf";
  if (status === "COUNTING") return "Penghitungan";
  if (status === "REVIEW") return "Review";
  if (status === "CONFIRMED") return "Terkonfirmasi";
  if (status === "CANCELLED") return "Dibatalkan";
  return status || "";
}

export default function StockOpname() {
  const [branches, setBranches] = useState([]);
  const [warehouses, setWarehouses] = useState([]);
  const [warehouseId, setWarehouseId] = useState("");
  const [sessions, setSessions] = useState([]);
  const [session, setSession] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmKey, setConfirmKey] = useState(newKey);

  const choices = useMemo(
    () => operationalOpeningWarehouses(branches, warehouses),
    [branches, warehouses],
  );

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.branches(), api.warehouses(), api.stockOpnameSessions()])
      .then(([branchRes, warehouseRes, sessionRes]) => {
        if (cancelled) return;
        const branchRows = branchRes.items || [];
        const warehouseRows = warehouseRes.items || [];
        setBranches(branchRows);
        setWarehouses(warehouseRows);
        setSessions(Array.isArray(sessionRes) ? sessionRes : []);
        const operational = operationalOpeningWarehouses(branchRows, warehouseRows);
        setWarehouseId(preselectedWarehouseId(operational));
      })
      .catch((err) => { if (!cancelled) setError(errorText(err)); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const next = {};
    for (const line of session?.lines || []) {
      next[line.line_id] = countText(line.physical_count_qty);
    }
    setDrafts(next);
  }, [session]);

  async function refreshList() {
    const rows = await api.stockOpnameSessions();
    setSessions(Array.isArray(rows) ? rows : []);
  }

  async function openSession(id) {
    const doc = await api.stockOpnameSession(id);
    setSession(doc);
    setConfirmKey(newKey());
    setError("");
  }

  async function start() {
    const warehouse = choices.find((row) => row.id === warehouseId);
    if (!warehouse) {
      setError("Pilih gudang operasional.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const active = sessions.find((row) => (
        row.warehouse_id === warehouse.id && ["DRAFT", "COUNTING", "REVIEW"].includes(row.status)
      ));
      const created = active || await api.createStockOpnameSession({
        branch_id: warehouse.branch_id,
        warehouse_id: warehouse.id,
        idempotency_key: newKey(),
      });
      const counting = created.status === "DRAFT"
        ? await api.cutoffStockOpnameSession(created.id)
        : created.status === "COUNTING" || created.status === "REVIEW"
          ? await api.stockOpnameSession(created.id)
          : await api.cutoffStockOpnameSession(created.id);
      setSession(counting);
      await refreshList();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function saveCounts() {
    const lines = [];
    for (const line of session?.lines || []) {
      const raw = drafts[line.line_id];
      if (raw === "" || raw === undefined) continue;
      lines.push({ line_id: line.line_id, physical_count_qty: raw });
    }
    if (!lines.length) {
      setError("Isi Stock Fisik. Kosong bukan nol.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      setSession(await api.countStockOpnameSession(session.id, { lines }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function downloadTemplate() {
    setBusy(true);
    setError("");
    try {
      const blob = await api.stockOpnameCountTemplate(session.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `hitung-fisik-${session.opname_number || session.id}.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function importFile(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      setSession(await api.stockOpnameCountImport(session.id, file));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function review() {
    setBusy(true);
    setError("");
    try {
      setSession(await api.reviewStockOpnameSession(session.id));
      await refreshList();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    setBusy(true);
    setError("");
    try {
      setSession(await api.confirmStockOpnameSession(session.id, { idempotency_key: confirmKey }));
      await refreshList();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    setBusy(true);
    setError("");
    try {
      setSession(await api.cancelStockOpnameSession(session.id, { reason: "Dibatalkan dari Stock Opname" }));
      await refreshList();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const counting = session?.status === "COUNTING";
  const reviewState = session?.status === "REVIEW";
  const editable = counting;
  const lines = session?.lines || [];

  return (
    <div className="prod-page stock-page" data-testid="stock-opname-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1>Stock Opname</h1>
          <p className="subhead">Isi Stock Fisik hasil hitung. Selisih dan Qty Tersedia dihitung oleh ERP.</p>
        </div>
        <Link className="ghost" to="/persediaan/stock-saat-ini" data-testid="stock-opname-back">Kembali</Link>
      </div>

      {error && <div className="signal-strip" role="alert" data-testid="stock-opname-error">{error}</div>}

      {!session && (
        <div className="prod-filters" data-testid="stock-opname-start">
          <label>
            Gudang
            <select
              value={warehouseId}
              onChange={(event) => setWarehouseId(event.target.value)}
              data-testid="opname-warehouse"
            >
              <option value="">Pilih gudang</option>
              {choices.map((row) => (
                <option key={row.id} value={row.id}>{row.name || row.code}</option>
              ))}
            </select>
          </label>
          <button type="button" className="primary" onClick={start} disabled={busy || !warehouseId} data-testid="opname-start">
            Mulai Stock Opname
          </button>
        </div>
      )}

      {session && (
        <section data-testid="stock-opname-session">
          <p data-testid="opname-status">Status: {statusLabel(session.status)}</p>
          <p data-testid="opname-warehouse-name">Gudang: {session.warehouse_name || session.warehouse_code}</p>
          <p data-testid="opname-cutoff">Tanggal Cutoff: {session.cutoff_at ? formatDate(session.cutoff_at) : "-"}</p>
          <p data-testid="opname-item-count">Jumlah Barang: {session.item_count ?? lines.length}</p>
          <p data-testid="opname-number">Nomor: {session.opname_number}</p>
          {(counting || reviewState) && (
            <p data-testid="opname-freeze">Gudang dibekukan. Pergerakan fisik gudang ini ditahan sampai konfirmasi atau pembatalan.</p>
          )}
          <div className="stock-heading-actions">
            {counting && (
              <>
                <button type="button" className="ghost" onClick={downloadTemplate} disabled={busy} data-testid="opname-download">
                  Download Excel Hitung Fisik
                </button>
                <label className="ghost" data-testid="opname-import-label">
                  Import Excel Hitung Fisik
                  <input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={importFile} disabled={busy} data-testid="opname-import" />
                </label>
                <button type="button" className="primary" onClick={saveCounts} disabled={busy} data-testid="opname-save-counts">Simpan Hitung</button>
                <button type="button" className="primary" onClick={review} disabled={busy} data-testid="opname-review">Review Stock Opname</button>
              </>
            )}
            {reviewState && (
              <>
                <p data-testid="opname-confirm-warning">Konfirmasi akan menyesuaikan Stock Fisik sesuai hasil hitung.</p>
                <button type="button" className="primary" onClick={confirm} disabled={busy} data-testid="opname-confirm">Konfirmasi Stock Opname</button>
              </>
            )}
            {["DRAFT", "COUNTING", "REVIEW"].includes(session.status) && (
              <button type="button" className="ghost" onClick={cancel} disabled={busy} data-testid="opname-cancel">Batalkan Stock Opname</button>
            )}
            <button type="button" className="ghost" onClick={() => setSession(null)} data-testid="opname-back-list">Daftar Sesi</button>
          </div>

          <table className="prod-table" data-testid="opname-count-table">
            <thead>
              <tr>
                <th>Kode</th>
                <th>Nama Barang</th>
                <th>Satuan Dasar</th>
                <th>Stock Buku</th>
                <th>Qty Pesanan</th>
                <th>Qty Belum Terkirim</th>
                <th>Stock Fisik</th>
                <th>Selisih</th>
                <th>Qty Tersedia Setelah Opname</th>
                <th>Status Hitung</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => {
                const raw = drafts[line.line_id] ?? "";
                const previewCount = raw === "" ? null : Number(raw);
                const previewDifference = previewCount === null || Number.isNaN(previewCount)
                  ? null
                  : previewCount - Number(line.book_qty || 0);
                const previewAvailable = previewCount === null || Number.isNaN(previewCount)
                  ? null
                  : previewCount - Number(line.qty_pesanan || 0) - Number(line.qty_belum_terkirim || 0);
                const difference = editable ? previewDifference : line.difference_qty;
                const available = editable ? previewAvailable : line.qty_tersedia_setelah;
                const counted = raw !== "" && line.physical_count_qty !== null && line.physical_count_qty !== undefined
                  ? true
                  : line.physical_count_qty !== null && line.physical_count_qty !== undefined && !editable;
                return (
                  <tr key={line.line_id} data-testid={`opname-row-${line.item_code}`} style={difference ? { background: "#fff6e8" } : undefined}>
                    <td>{line.item_code}</td>
                    <td>{line.item_name}</td>
                    <td>{line.base_uom}</td>
                    <td data-testid={`opname-book-${line.item_code}`}>{fmtQty(line.book_qty)}</td>
                    <td data-testid={`opname-pesanan-${line.item_code}`}>{fmtQty(line.qty_pesanan)}</td>
                    <td data-testid={`opname-belum-${line.item_code}`}>{fmtQty(line.qty_belum_terkirim)}</td>
                    <td>
                      {editable ? (
                        <input
                          value={raw}
                          onChange={(event) => setDrafts((prev) => ({ ...prev, [line.line_id]: event.target.value }))}
                          inputMode="numeric"
                          aria-label={`Stock Fisik ${line.item_code}`}
                          data-testid={`opname-count-${line.item_code}`}
                        />
                      ) : (
                        fmtQty(line.physical_count_qty)
                      )}
                    </td>
                    <td>{difference === null || Number.isNaN(difference) ? "" : fmtQty(difference)}</td>
                    <td>{available === null || Number.isNaN(available) ? "" : fmtQty(available)}</td>
                    <td>{(editable ? raw !== "" : counted) ? "Sudah dihitung" : "Belum dihitung"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      <section data-testid="opname-history">
        <h2>Riwayat Stock Opname</h2>
        <table className="prod-table">
          <thead>
            <tr>
              <th>Nomor / Session</th>
              <th>Gudang</th>
              <th>Tanggal Cutoff</th>
              <th>Status</th>
              <th>Jumlah Barang</th>
              <th>Jumlah Selisih</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((row) => (
              <tr key={row.id} data-testid={`opname-history-${row.opname_number}`}>
                <td>{row.opname_number}</td>
                <td>{row.warehouse_name || row.warehouse_code}</td>
                <td>{row.cutoff_at ? formatDate(row.cutoff_at) : "-"}</td>
                <td>{statusLabel(row.status)}</td>
                <td>{row.item_count ?? (row.lines || []).length}</td>
                <td>{row.difference_count ?? ""}</td>
                <td><button type="button" className="ghost sm" onClick={() => openSession(row.id)} data-testid={`opname-detail-${row.opname_number}`}>Detail</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
