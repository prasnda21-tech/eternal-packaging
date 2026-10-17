import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { formatDate } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import {
  opnameLocationLabel,
  opnameWarehouseChoices,
  preselectedWarehouseId,
  warehouseLocationLabel,
} from "../lib/physicalStockView";
import {
  STOCK_OPNAME_TZ_LABEL,
  formatWibDateTime,
  isoToWibLocalInput,
  wibLocalInputToIso,
} from "../lib/stockOpnameTime";
import { stockOpnameErrorMessage } from "../lib/stockOpnameErrors";

function newKey() {
  return globalThis.crypto?.randomUUID?.() || `opname-${Date.now()}-${Math.random().toString(16).slice(2)}`;
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

function modeLabel(mode) {
  return mode === "operational" ? "Operational" : "Freeze";
}

function sessionMode(session) {
  return session?.mode === "operational" ? "operational" : "freeze";
}

export default function StockOpname() {
  const [searchParams] = useSearchParams();
  const requestedWarehouseId = String(searchParams.get("warehouse_id") || "").trim();
  const [branches, setBranches] = useState([]);
  const [warehouses, setWarehouses] = useState([]);
  const [warehouseId, setWarehouseId] = useState("");
  const [createMode, setCreateMode] = useState("freeze");
  const [sessions, setSessions] = useState([]);
  const [session, setSession] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [timeDrafts, setTimeDrafts] = useState({});
  const [bulkCountAt, setBulkCountAt] = useState("");
  const [applyBulkTime, setApplyBulkTime] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmKey, setConfirmKey] = useState(newKey);
  const [successNote, setSuccessNote] = useState("");

  const choices = useMemo(
    () => opnameWarehouseChoices(branches, warehouses),
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
        const operational = opnameWarehouseChoices(branchRows, warehouseRows);
        // Prefer warehouse_id from Stock Saat Ini deep-link when it is in scope;
        // otherwise only auto-select when exactly one warehouse.
        const fromLink = requestedWarehouseId
          && operational.some((row) => row.id === requestedWarehouseId)
          ? requestedWarehouseId
          : "";
        setWarehouseId(fromLink || preselectedWarehouseId(operational));
        if (operational.length === 0) {
          setError("Tidak ada gudang operasional aktif pada cabang yang tersedia.");
        }
      })
      .catch((err) => { if (!cancelled) setError(stockOpnameErrorMessage(err)); });
    return () => { cancelled = true; };
  }, [requestedWarehouseId]);

  const sessionDraftKey = useMemo(() => {
    if (!session) return "";
    const lineKey = (session.lines || [])
      .map((line) => `${line.line_id}:${countText(line.physical_count_qty)}:${line.physical_count_at || ""}:${line.imported_at || ""}`)
      .join("|");
    return `${session.id}|${session.status}|${session.updated_at || ""}|${lineKey}`;
  }, [session]);

  useEffect(() => {
    if (!session) {
      setDrafts({});
      setTimeDrafts({});
      return;
    }
    const nextQty = {};
    const nextTime = {};
    for (const line of session.lines || []) {
      nextQty[line.line_id] = countText(line.physical_count_qty);
      nextTime[line.line_id] = isoToWibLocalInput(line.physical_count_at);
    }
    setDrafts(nextQty);
    setTimeDrafts(nextTime);
    setApplyBulkTime(false);
    setBulkCountAt("");
  }, [sessionDraftKey, session]);

  async function refreshList() {
    const rows = await api.stockOpnameSessions();
    setSessions(Array.isArray(rows) ? rows : []);
  }

  async function openSession(id) {
    const doc = await api.stockOpnameSession(id);
    setSession(doc);
    setConfirmKey(newKey());
    setError("");
    setSuccessNote("");
  }

  async function start() {
    const warehouse = choices.find((row) => row.id === warehouseId);
    if (!warehouse) {
      setError("Pilih gudang operasional.");
      return;
    }
    setBusy(true);
    setError("");
    setSuccessNote("");
    try {
      const active = sessions.find((row) => (
        row.warehouse_id === warehouse.id && ["DRAFT", "COUNTING", "REVIEW"].includes(row.status)
      ));
      // Never silently change mode of an already-running session.
      const created = active || await api.createStockOpnameSession({
        branch_id: warehouse.branch_id,
        warehouse_id: warehouse.id,
        mode: createMode === "operational" ? "operational" : "freeze",
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
      setError(stockOpnameErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function saveCounts() {
    const lines = [];
    const mode = sessionMode(session);
    const bulkIso = applyBulkTime ? wibLocalInputToIso(bulkCountAt) : null;
    if (applyBulkTime && !bulkIso) {
      setError("Perbaiki input: centang terapkan waktu hitung ke banyak baris memerlukan waktu WIB yang valid.");
      return;
    }
    for (const line of session?.lines || []) {
      const raw = drafts[line.line_id];
      const timeRaw = timeDrafts[line.line_id];
      const hasQty = !(raw === "" || raw === undefined);
      const lineTimeIso = wibLocalInputToIso(timeRaw);
      const physicalCountAt = applyBulkTime && hasQty ? bulkIso : lineTimeIso;
      if (!hasQty && !physicalCountAt) continue;
      const row = { line_id: line.line_id };
      if (hasQty) {
        // Blank is omitted (not zero). Explicit 0 is sent as "0".
        row.physical_count_qty = raw;
      }
      if (physicalCountAt) {
        row.physical_count_at = physicalCountAt;
      } else if (mode === "operational" && timeRaw === "" && line.physical_count_at) {
        // User cleared a previously set count time.
        row.physical_count_at = null;
      }
      if (row.physical_count_qty !== undefined || Object.prototype.hasOwnProperty.call(row, "physical_count_at")) {
        lines.push(row);
      }
    }
    if (!lines.length) {
      setError("Perbaiki input: isi Stock Fisik. Kosong bukan nol. Waktu hitung diisi terpisah (bukan waktu impor).");
      return;
    }
    setBusy(true);
    setError("");
    setSuccessNote("");
    try {
      setSession(await api.countStockOpnameSession(session.id, { lines }));
    } catch (err) {
      setError(stockOpnameErrorMessage(err));
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
      setError(stockOpnameErrorMessage(err));
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
    setSuccessNote("");
    try {
      const imported = await api.stockOpnameCountImport(session.id, file);
      setSession(imported);
      // Import records qty + imported_at only — never treat import time as count time.
      setSuccessNote("Impor selesai. Waktu impor tercatat terpisah; isi waktu hitung fisik (WIB) sebelum review/konfirmasi (mode operational).");
    } catch (err) {
      setError(stockOpnameErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function review() {
    setBusy(true);
    setError("");
    setSuccessNote("");
    try {
      setSession(await api.reviewStockOpnameSession(session.id));
      await refreshList();
    } catch (err) {
      setError(stockOpnameErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (busy || session?.status === "CONFIRMED") return;
    setBusy(true);
    setError("");
    setSuccessNote("");
    try {
      const confirmed = await api.confirmStockOpnameSession(session.id, { idempotency_key: confirmKey });
      if (confirmed?.status !== "CONFIRMED") {
        setError("Konfirmasi belum berhasil di backend. Tidak menampilkan sukses.");
        setSession(confirmed);
        return;
      }
      setSession(confirmed);
      setSuccessNote("Stock Opname terkonfirmasi oleh backend.");
      await refreshList();
    } catch (err) {
      const status = err?.response?.status;
      setError(stockOpnameErrorMessage(err));
      if (status === 409 && session?.id) {
        try {
          // Keep inputs/times; refresh API numbers for re-review. Do not auto-retry posting.
          setSession(await api.stockOpnameSession(session.id));
        } catch (_) {
          /* keep current session */
        }
      }
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    setBusy(true);
    setError("");
    setSuccessNote("");
    try {
      setSession(await api.cancelStockOpnameSession(session.id, { reason: "Dibatalkan dari Stock Opname" }));
      await refreshList();
    } catch (err) {
      setError(stockOpnameErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const counting = session?.status === "COUNTING";
  const reviewState = session?.status === "REVIEW";
  const editable = counting;
  const lines = session?.lines || [];
  const mode = sessionMode(session);
  const isOperational = mode === "operational";

  return (
    <div className="prod-page stock-page" data-testid="stock-opname-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1>Stock Opname</h1>
          <p className="subhead">Isi Stock Fisik hasil hitung. Selisih dan target dihitung oleh ERP (bukan rumus di layar).</p>
        </div>
        <Link className="ghost" to="/persediaan/stock-saat-ini" data-testid="stock-opname-back">Kembali</Link>
      </div>

      {error && <div className="signal-strip" role="alert" data-testid="stock-opname-error">{error}</div>}
      {successNote && <div className="signal-strip" data-testid="stock-opname-success">{successNote}</div>}

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
                <option key={row.id} value={row.id}>{warehouseLocationLabel(row, branches)}</option>
              ))}
            </select>
          </label>
          {(() => {
            const activeForWarehouse = sessions.find((row) => (
              row.warehouse_id === warehouseId && ["DRAFT", "COUNTING", "REVIEW"].includes(row.status)
            ));
            if (activeForWarehouse) {
              return (
                <div data-testid="opname-existing-active" style={{ display: "grid", gap: 6 }}>
                  <p className="muted small" data-testid="opname-mode-hint">
                    Sesi aktif ditemukan: {activeForWarehouse.opname_number || activeForWarehouse.id}
                    {" — "}Mode {modeLabel(sessionMode(activeForWarehouse))} (terkunci).
                    Dropdown mode di bawah hanya untuk sesi baru; sesi existing tidak diubah.
                  </p>
                  <button type="button" className="primary" onClick={() => openSession(activeForWarehouse.id)} disabled={busy} data-testid="opname-open-existing">
                    Buka Sesi Existing
                  </button>
                </div>
              );
            }
            return (
              <>
                <label>
                  Mode sesi baru
                  <select
                    value={createMode}
                    onChange={(event) => setCreateMode(event.target.value === "operational" ? "operational" : "freeze")}
                    data-testid="opname-create-mode"
                  >
                    <option value="freeze">Freeze — pergerakan stok gudang diblok selama hitung</option>
                    <option value="operational">Operational — pergerakan tetap berjalan, diperhitungkan saat konfirmasi</option>
                  </select>
                </label>
                <p className="muted small" data-testid="opname-mode-hint">
                  Default freeze untuk sesi baru. Mode sesi yang sudah berjalan tidak diganti dari layar ini.
                </p>
                <button type="button" className="primary" onClick={start} disabled={busy || !warehouseId} data-testid="opname-start">
                  Mulai Stock Opname
                </button>
              </>
            );
          })()}
          {sessions.length > 0 && (
            <div data-testid="opname-session-list" style={{ display: "grid", gap: 4, width: "100%" }}>
              <p className="muted small">Sesi tersimpan</p>
              {sessions.map((row) => (
                <button
                  key={row.id}
                  type="button"
                  className="ghost"
                  data-testid={`opname-session-${row.opname_number || row.id}`}
                  onClick={() => openSession(row.id)}
                  disabled={busy}
                  style={{ justifyContent: "flex-start", textAlign: "left" }}
                >
                  {row.opname_number || row.id} — {statusLabel(row.status)} — {modeLabel(sessionMode(row))}
                  {" — "}{row.warehouse_code || row.warehouse_id}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {session && (
        <section data-testid="stock-opname-session">
          <p data-testid="opname-status">Status: {statusLabel(session.status)}</p>
          <p data-testid="opname-mode">Mode: {modeLabel(mode)}</p>
          <p data-testid="opname-warehouse-name">Lokasi: {opnameLocationLabel(session)}</p>
          <p data-testid="opname-cutoff">Tanggal Cutoff: {session.cutoff_at ? formatDate(session.cutoff_at) : "-"}</p>
          <p data-testid="opname-item-count">Jumlah Barang: {session.item_count ?? lines.length}</p>
          <p data-testid="opname-number">Nomor: {session.opname_number}</p>
          {(counting || reviewState) && !isOperational && (
            <p data-testid="opname-freeze">Gudang dibekukan. Pergerakan fisik gudang ini ditahan sampai konfirmasi atau pembatalan.</p>
          )}
          {(counting || reviewState) && isOperational && (
            <p data-testid="opname-operational-banner">
              Mode operational: pergerakan gudang tetap berjalan. Target = hasil hitung + net pergerakan setelah waktu hitung.
              Angka review dari API; final diverifikasi ulang saat konfirmasi.
            </p>
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
                {(isOperational || counting) && (
                  <div data-testid="opname-bulk-time" style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                    <label className="muted small" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <input
                        type="checkbox"
                        checked={applyBulkTime}
                        onChange={(event) => setApplyBulkTime(event.target.checked)}
                        data-testid="opname-apply-bulk-time"
                      />
                      Terapkan satu waktu hitung ke baris yang diisi qty (pilihan eksplisit)
                    </label>
                    <label className="muted small">
                      Waktu hitung massal ({STOCK_OPNAME_TZ_LABEL})
                      <input
                        type="datetime-local"
                        value={bulkCountAt}
                        onChange={(event) => setBulkCountAt(event.target.value)}
                        disabled={!applyBulkTime || busy}
                        data-testid="opname-bulk-count-at"
                      />
                    </label>
                  </div>
                )}
                <button type="button" className="primary" onClick={saveCounts} disabled={busy} data-testid="opname-save-counts">Simpan Hitung</button>
                <button type="button" className="primary" onClick={review} disabled={busy} data-testid="opname-review">Review Stock Opname</button>
              </>
            )}
            {reviewState && (
              <>
                <p data-testid="opname-confirm-warning">
                  {isOperational
                    ? "Konfirmasi memposting adjustment dari target API (hitung + net setelah waktu hitung − buku saat posting). Angka di tabel adalah pratinjau API; final diverifikasi ulang saat konfirmasi."
                    : "Konfirmasi akan menyesuaikan Stock Fisik sesuai hasil hitung."}
                </p>
                <button
                  type="button"
                  className="primary"
                  onClick={confirm}
                  disabled={busy || session.status === "CONFIRMED"}
                  data-testid="opname-confirm"
                >
                  Konfirmasi Stock Opname
                </button>
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
                {isOperational && <th>Buku Saat Review</th>}
                <th>Qty Pesanan</th>
                <th>Qty Belum Terkirim</th>
                <th>Stock Fisik</th>
                <th>Waktu Hitung ({STOCK_OPNAME_TZ_LABEL})</th>
                <th>Waktu Impor</th>
                {isOperational && <th>Net Setelah Hitung</th>}
                {isOperational && <th>Target Fisik</th>}
                <th>{isOperational ? "Adjustment (perkiraan)" : "Selisih"}</th>
                <th>Qty Tersedia Setelah Opname</th>
                <th>Status Hitung</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => {
                const raw = drafts[line.line_id] ?? "";
                const timeRaw = timeDrafts[line.line_id] ?? "";
                const previewCount = raw === "" ? null : Number(raw);
                const bookForFreezeDiff = Number(line.book_qty || 0);
                const previewDifference = previewCount === null || Number.isNaN(previewCount)
                  ? null
                  : previewCount - bookForFreezeDiff;
                const previewAvailable = previewCount === null || Number.isNaN(previewCount)
                  ? null
                  : previewCount - Number(line.qty_pesanan || 0) - Number(line.qty_belum_terkirim || 0);
                const apiDifference = line.difference_qty;
                const difference = editable && !isOperational ? previewDifference : apiDifference;
                const available = editable && !isOperational ? previewAvailable : line.qty_tersedia_setelah;
                const counted = raw !== "" && line.physical_count_qty !== null && line.physical_count_qty !== undefined
                  ? true
                  : line.physical_count_qty !== null && line.physical_count_qty !== undefined && !editable;
                const bookLive = line.book_qty_live ?? line.book_qty_at_posting;
                return (
                  <tr key={line.line_id} data-testid={`opname-row-${line.item_code}`} style={difference ? { background: "#fff6e8" } : undefined}>
                    <td>{line.item_code}</td>
                    <td>{line.item_name}</td>
                    <td>{line.base_uom}</td>
                    <td data-testid={`opname-book-${line.item_code}`}>{fmtQty(line.book_qty)}</td>
                    {isOperational && (
                      <td data-testid={`opname-book-live-${line.item_code}`}>
                        {bookLive === null || bookLive === undefined ? "—" : fmtQty(bookLive)}
                      </td>
                    )}
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
                          placeholder="kosong ≠ 0"
                        />
                      ) : (
                        <span data-testid={`opname-count-value-${line.item_code}`}>{fmtQty(line.physical_count_qty)}</span>
                      )}
                    </td>
                    <td>
                      {editable ? (
                        <input
                          type="datetime-local"
                          value={timeRaw}
                          onChange={(event) => setTimeDrafts((prev) => ({ ...prev, [line.line_id]: event.target.value }))}
                          aria-label={`Waktu hitung ${line.item_code} ${STOCK_OPNAME_TZ_LABEL}`}
                          data-testid={`opname-count-at-${line.item_code}`}
                          disabled={applyBulkTime}
                        />
                      ) : (
                        <span data-testid={`opname-count-at-value-${line.item_code}`}>{formatWibDateTime(line.physical_count_at)}</span>
                      )}
                    </td>
                    <td data-testid={`opname-imported-at-${line.item_code}`}>
                      {line.imported_at ? formatWibDateTime(line.imported_at) : "—"}
                    </td>
                    {isOperational && (
                      <td data-testid={`opname-net-${line.item_code}`}>
                        {line.net_movement_after_count === null || line.net_movement_after_count === undefined
                          ? "—"
                          : fmtQty(line.net_movement_after_count)}
                      </td>
                    )}
                    {isOperational && (
                      <td data-testid={`opname-target-${line.item_code}`}>
                        {line.target_qty === null || line.target_qty === undefined ? "—" : fmtQty(line.target_qty)}
                      </td>
                    )}
                    <td data-testid={`opname-diff-${line.item_code}`}>
                      {difference === null || difference === undefined || Number.isNaN(difference) ? "" : fmtQty(difference)}
                    </td>
                    <td>{available === null || available === undefined || Number.isNaN(available) ? "" : fmtQty(available)}</td>
                    <td>{(editable ? raw !== "" : counted) ? "Sudah dihitung" : "Belum dihitung"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {isOperational && (counting || reviewState) && (
            <p className="muted small" data-testid="opname-api-calc-note">
              Net / target / adjustment ditampilkan dari respons backend. Waktu di form adalah {STOCK_OPNAME_TZ_LABEL}; API menerima ISO UTC.
              Waktu impor bukan waktu hitung.
            </p>
          )}
        </section>
      )}

      <section data-testid="opname-history">
        <h2>Riwayat Stock Opname</h2>
        <table className="prod-table">
          <thead>
            <tr>
              <th>Nomor / Session</th>
              <th>Lokasi</th>
              <th>Mode</th>
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
                <td data-testid={`opname-history-location-${row.opname_number}`}>{opnameLocationLabel(row)}</td>
                <td>{modeLabel(sessionMode(row))}</td>
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
