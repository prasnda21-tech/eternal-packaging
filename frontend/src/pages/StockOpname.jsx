import { useEffect, useMemo, useRef, useState } from "react";
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
  combineWibDateAndTime,
  formatWibDateTime,
  formatWibPeriod,
  isoToWibLocalInput,
} from "../lib/stockOpnameTime";
import { buildStockOpnameCountPayload } from "../lib/stockOpnameCounts";
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
  /** Owner count period — date (YYYY-MM-DD) + time (HH:mm) as WIB wall clock. */
  const [createStartDate, setCreateStartDate] = useState("");
  const [createStartTime, setCreateStartTime] = useState("");
  const [createEndDate, setCreateEndDate] = useState("");
  const [createEndTime, setCreateEndTime] = useState("");
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
  const [importPreview, setImportPreview] = useState(null);
  const [importFileObj, setImportFileObj] = useState(null);
  /** Sync guard: React setState(busy) is too late to stop a double-click race. */
  const confirmInFlightRef = useRef(false);

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
    // Prefill bulk editor from session akhir / default (does not auto-apply until Owner checks).
    setBulkCountAt(
      isoToWibLocalInput(
        session.default_physical_count_at || session.physical_count_period_end_at,
      ) || "",
    );
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
    const operationalCreate = createMode === "operational";
    let periodStartIso = null;
    let periodEndIso = null;
    if (operationalCreate) {
      periodStartIso = combineWibDateAndTime(createStartDate, createStartTime);
      periodEndIso = combineWibDateAndTime(createEndDate, createEndTime);
      if (!periodStartIso || !periodEndIso) {
        setError(
          `Mode Operational wajib mengisi Tanggal/Jam Mulai dan Tanggal/Jam Akhir (${STOCK_OPNAME_TZ_LABEL}).`,
        );
        return;
      }
      if (new Date(periodStartIso).getTime() > new Date(periodEndIso).getTime()) {
        setError("Tanggal/Jam Mulai tidak boleh setelah Tanggal/Jam Akhir.");
        return;
      }
      // Client-side future guard (server re-validates). Same 60s clock-skew
      // tolerance as parse_physical_count_at: now..+60s accepted; beyond +60s rejected.
      if (new Date(periodEndIso).getTime() > Date.now() + 60_000
          || new Date(periodStartIso).getTime() > Date.now() + 60_000) {
        setError(
          "Waktu pelaksanaan hitung terlalu jauh di masa depan (toleransi jam ±60 detik).",
        );
        return;
      }
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
        mode: operationalCreate ? "operational" : "freeze",
        ...(periodStartIso && periodEndIso ? {
          physical_count_period_start_at: periodStartIso,
          physical_count_period_end_at: periodEndIso,
          // Akhir = default waktu hitung baris (bukan mulai).
          default_physical_count_at: periodEndIso,
        } : {}),
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

  function countPayloadOrError() {
    return buildStockOpnameCountPayload({
      sessionLines: session?.lines || [],
      drafts,
      timeDrafts,
      applyBulkTime,
      bulkCountAt,
      mode: sessionMode(session),
    });
  }

  async function saveCounts() {
    const built = countPayloadOrError();
    if (!built.ok) {
      setError(`Perbaiki input: ${built.error}`);
      return false;
    }
    setBusy(true);
    setError("");
    setSuccessNote("");
    try {
      setSession(await api.countStockOpnameSession(session.id, { lines: built.lines }));
      return true;
    } catch (err) {
      setError(stockOpnameErrorMessage(err));
      return false;
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

  async function runCutoff() {
    if (!session?.id || session.status !== "DRAFT") return;
    setBusy(true);
    setError("");
    setSuccessNote("");
    setImportPreview(null);
    setImportFileObj(null);
    try {
      const countingDoc = await api.cutoffStockOpnameSession(session.id);
      setSession(countingDoc);
      setSuccessNote(
        `Cutoff selesai — ${countingDoc.item_count ?? (countingDoc.lines || []).length} barang siap dihitung. Unduh template; isi Stock Fisik (total gabungan polos+tercetak belum kirim).`,
      );
      await refreshList();
    } catch (err) {
      setError(stockOpnameErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function importFile(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !session?.id) return;
    setBusy(true);
    setError("");
    setSuccessNote("");
    setImportPreview(null);
    setImportFileObj(null);
    try {
      const preview = await api.stockOpnameCountImportPreview(session.id, file);
      setImportPreview(preview);
      setImportFileObj(file);
      // Preview only — never treat import time as count time.
    } catch (err) {
      setError(stockOpnameErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function applyImportPreview() {
    if (!session?.id || !importFileObj) return;
    if (!importPreview?.can_apply) {
      setError("Tidak ada baris READY untuk diterapkan.");
      return;
    }
    setBusy(true);
    setError("");
    setSuccessNote("");
    try {
      const imported = await api.stockOpnameCountImport(session.id, importFileObj);
      setSession(imported);
      const applied = imported.import_applied_count ?? importPreview.ready_count ?? 0;
      const rejected = imported.import_preview?.rejected_count ?? importPreview.rejected_count ?? 0;
      const missing = imported.import_preview?.missing_count ?? importPreview.missing_count ?? 0;
      setSuccessNote(
        `Import diterapkan: ${applied} READY. Ditolak ${rejected}. Kosong (belum hitung) ${missing}. Waktu hitung fisik (WIB) diisi terpisah sebelum review (mode operational).`,
      );
      setImportPreview(imported.import_preview || importPreview);
      setImportFileObj(null);
    } catch (err) {
      setError(stockOpnameErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function review() {
    if (busy) return;
    // Flush drafts first so physical_count_at / qty reach the API before REVIEW.
    if (session?.status === "COUNTING") {
      const saved = await saveCounts();
      if (!saved) return;
    }
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
    if (confirmInFlightRef.current || busy || session?.status === "CONFIRMED") return;
    if (session?.status !== "REVIEW" && session?.status !== "PARTIAL") {
      setError(
        `Konfirmasi hanya dari status Review. Status saat ini: ${statusLabel(session?.status)}.`,
      );
      return;
    }
    confirmInFlightRef.current = true;
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
      let statusLabelNow = statusLabel(session?.status);
      if (status === 409 && session?.id) {
        try {
          // Refresh authoritative status/lock view. Do not auto-retry posting.
          const fresh = await api.stockOpnameSession(session.id);
          setSession(fresh);
          statusLabelNow = statusLabel(fresh?.status);
        } catch (_) {
          /* keep current session */
        }
      }
      setError(stockOpnameErrorMessage(err, { sessionStatusLabel: statusLabelNow }));
    } finally {
      confirmInFlightRef.current = false;
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

  const isDraft = session?.status === "DRAFT";
  const counting = session?.status === "COUNTING";
  const reviewState = session?.status === "REVIEW";
  const editable = counting;
  const lines = session?.lines || [];
  const mode = sessionMode(session);
  const isOperational = mode === "operational";

  return (
    <div className="prod-page stock-page stock-opname-page" data-testid="stock-opname-page">
      <div className="page-heading prod-heading stock-opname-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1>Stock Opname</h1>
          <p className="subhead">Isi Stock Fisik hasil hitung. Selisih dan target dihitung oleh ERP (bukan rumus di layar).</p>
        </div>
        <Link className="ghost stock-opname-back" to="/persediaan/stock-saat-ini" data-testid="stock-opname-back">Kembali</Link>
      </div>

      {error && <div className="signal-strip" role="alert" data-testid="stock-opname-error">{error}</div>}
      {successNote && <div className="signal-strip" data-testid="stock-opname-success">{successNote}</div>}

      {!session && (
        <div className="stock-opname-start prod-filters" data-testid="stock-opname-start">
          <label className="stock-opname-field">
            <span>Gudang</span>
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
                <div className="stock-opname-existing" data-testid="opname-existing-active">
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
                <label className="stock-opname-field">
                  <span>Mode sesi baru</span>
                  <select
                    value={createMode}
                    onChange={(event) => setCreateMode(event.target.value === "operational" ? "operational" : "freeze")}
                    data-testid="opname-create-mode"
                  >
                    <option value="freeze">Freeze — pergerakan stok gudang diblok selama hitung</option>
                    <option value="operational">Operational — hitung historis / gudang tetap buka; target = hitung + net setelah waktu hitung</option>
                  </select>
                </label>
                {createMode === "operational" && (
                  <div className="stock-opname-period" data-testid="opname-create-period" lang="id">
                    <div className="stock-opname-period-grid">
                      <div className="stock-opname-period-block" data-testid="opname-create-start">
                        <span className="stock-opname-period-title">Mulai hitung ({STOCK_OPNAME_TZ_LABEL})</span>
                        <div className="stock-opname-period-pair">
                          <label className="stock-opname-field">
                            <span>Tanggal Mulai</span>
                            <input
                              type="date"
                              value={createStartDate}
                              onChange={(event) => setCreateStartDate(event.target.value)}
                              onInput={(event) => setCreateStartDate(event.target.value)}
                              data-testid="opname-create-start-date"
                              aria-required="true"
                            />
                          </label>
                          <label className="stock-opname-field">
                            <span>Jam Mulai</span>
                            <input
                              type="time"
                              step="60"
                              value={createStartTime}
                              onChange={(event) => setCreateStartTime(event.target.value)}
                              onInput={(event) => setCreateStartTime(event.target.value)}
                              data-testid="opname-create-start-time"
                              aria-required="true"
                            />
                          </label>
                        </div>
                      </div>
                      <div className="stock-opname-period-block" data-testid="opname-create-end">
                        <span className="stock-opname-period-title">Akhir hitung ({STOCK_OPNAME_TZ_LABEL})</span>
                        <div className="stock-opname-period-pair">
                          <label className="stock-opname-field">
                            <span>Tanggal Akhir</span>
                            <input
                              type="date"
                              value={createEndDate}
                              onChange={(event) => setCreateEndDate(event.target.value)}
                              onInput={(event) => setCreateEndDate(event.target.value)}
                              data-testid="opname-create-end-date"
                              aria-required="true"
                            />
                          </label>
                          <label className="stock-opname-field">
                            <span>Jam Akhir</span>
                            <input
                              type="time"
                              step="60"
                              value={createEndTime}
                              onChange={(event) => setCreateEndTime(event.target.value)}
                              onInput={(event) => setCreateEndTime(event.target.value)}
                              data-testid="opname-create-end-time"
                              aria-required="true"
                            />
                          </label>
                        </div>
                      </div>
                    </div>
                    <p className="muted small" data-testid="opname-period-note">
                      Hasil fisik dianggap posisi pada waktu akhir, kecuali waktu hitung barang diisi berbeda.
                    </p>
                  </div>
                )}
                <p className="muted small" data-testid="opname-mode-hint">
                  {createMode === "operational"
                    ? "Periode = pelaksanaan hitung. Target = hasil hitung + net mutasi setelah waktu hitung baris. Bukan cutoff Freeze / waktu dibuat sesi."
                    : "Default freeze untuk sesi baru. Mode sesi yang sudah berjalan tidak diganti dari layar ini."}
                </p>
                <button
                  type="button"
                  className="primary"
                  onClick={start}
                  disabled={busy || !warehouseId || (createMode === "operational" && !(
                    createStartDate && createStartTime && createEndDate && createEndTime
                  ))}
                  data-testid="opname-start"
                >
                  Mulai Stock Opname
                </button>
              </>
            );
          })()}
          {sessions.length > 0 && (
            <div className="stock-opname-session-list" data-testid="opname-session-list">
              <p className="muted small">Sesi tersimpan</p>
              {sessions.map((row) => (
                <button
                  key={row.id}
                  type="button"
                  className="ghost stock-opname-session-btn"
                  data-testid={`opname-session-${row.opname_number || row.id}`}
                  onClick={() => openSession(row.id)}
                  disabled={busy}
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
        <section className="stock-opname-session" data-testid="stock-opname-session">
          <div className="stock-opname-summary" data-testid="opname-session-summary">
            <div className="stock-opname-summary-item">
              <span className="stock-opname-summary-label">Nomor</span>
              <strong data-testid="opname-number">{session.opname_number}</strong>
            </div>
            <div className="stock-opname-summary-item">
              <span className="stock-opname-summary-label">Status</span>
              <strong data-testid="opname-status">{statusLabel(session.status)}</strong>
            </div>
            <div className="stock-opname-summary-item">
              <span className="stock-opname-summary-label">Mode</span>
              <strong data-testid="opname-mode">{modeLabel(mode)}</strong>
            </div>
            <div className="stock-opname-summary-item stock-opname-summary-item--wide">
              <span className="stock-opname-summary-label">Cabang / Gudang</span>
              <strong data-testid="opname-warehouse-name">{opnameLocationLabel(session)}</strong>
            </div>
            <div className="stock-opname-summary-item">
              <span className="stock-opname-summary-label">
                {isOperational ? "Cutoff daftar (bukan waktu hitung)" : "Cutoff / awal blokir Freeze"}
              </span>
              <strong data-testid="opname-cutoff">{session.cutoff_at ? formatDate(session.cutoff_at) : "-"}</strong>
            </div>
            {isOperational && (
              <div className="stock-opname-summary-item stock-opname-summary-item--wide">
                <span className="stock-opname-summary-label">Periode hitung ({STOCK_OPNAME_TZ_LABEL})</span>
                <strong data-testid="opname-count-period">
                  {formatWibPeriod(
                    session.physical_count_period_start_at,
                    session.physical_count_period_end_at || session.default_physical_count_at,
                  )}
                </strong>
              </div>
            )}
            {isOperational && (
              <div className="stock-opname-summary-item">
                <span className="stock-opname-summary-label">Default waktu baris (= akhir)</span>
                <strong data-testid="opname-default-count-at">
                  {session.default_physical_count_at || session.physical_count_period_end_at
                    ? formatWibDateTime(
                      session.default_physical_count_at || session.physical_count_period_end_at,
                    )
                    : "—"}
                </strong>
              </div>
            )}
            <div className="stock-opname-summary-item">
              <span className="stock-opname-summary-label">Jumlah barang</span>
              <strong data-testid="opname-item-count">{session.item_count ?? lines.length}</strong>
            </div>
          </div>

          {(counting || reviewState) && !isOperational && (
            <p className="stock-opname-banner stock-opname-banner--freeze" data-testid="opname-freeze">
              Mode Freeze: gudang dibekukan. Pergerakan fisik gudang ini ditahan sampai konfirmasi atau pembatalan.
              Freeze tidak berlaku surut — waktu cutoff di atas adalah awal blokir, bukan tanggal hitung historis.
            </p>
          )}
          {(counting || reviewState) && isOperational && (
            <p className="stock-opname-banner stock-opname-banner--ops" data-testid="opname-operational-banner">
              Mode Operational: target = hasil hitung + net mutasi setelah waktu hitung baris
              (bukan setelah Tanggal Mulai). Waktu baris harus dalam periode mulai–akhir.
              Periode ≠ waktu dibuat sesi ≠ awal blokir Freeze.
            </p>
          )}

          <div className="stock-opname-toolbar" data-testid="opname-toolbar">
            {isDraft && (
              <>
                <p className="stock-opname-confirm-warn" data-testid="opname-draft-hint">
                  Draf tanpa cutoff: daftar barang kosong dan Import Excel belum tersedia.
                  Jalankan cutoff pada gudang yang benar (jangan memakai sesi Freeze usang).
                  Saldo Awal bukan alat rekonsiliasi stok fisik.
                </p>
                <button type="button" className="primary" onClick={runCutoff} disabled={busy} data-testid="opname-cutoff">
                  Mulai Cutoff
                </button>
              </>
            )}
            {counting && (
              <>
                <button type="button" className="ghost" onClick={downloadTemplate} disabled={busy} data-testid="opname-download">
                  Download Excel
                </button>
                <label className="ghost stock-opname-file-btn" data-testid="opname-import-label">
                  Import Excel
                  <input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={importFile} disabled={busy} data-testid="opname-import" />
                </label>
                <button type="button" className="primary" onClick={saveCounts} disabled={busy} data-testid="opname-save-counts">Simpan Hitung</button>
                <button type="button" className="primary" onClick={review} disabled={busy} data-testid="opname-review">Review</button>
              </>
            )}
            {reviewState && (
              <>
                <p className="stock-opname-confirm-warn" data-testid="opname-confirm-warning">
                  {isOperational
                    ? "Konfirmasi memposting adjustment dari target API (hitung + net setelah waktu hitung − buku saat posting). Angka di tabel adalah pratinjau API; final diverifikasi ulang saat konfirmasi."
                    : "Konfirmasi akan menyesuaikan Stock Fisik sesuai hasil hitung."}
                </p>
                <button
                  type="button"
                  className="primary"
                  onClick={confirm}
                  disabled={busy || session.status === "CONFIRMED"}
                  aria-busy={busy || undefined}
                  data-testid="opname-confirm"
                >
                  {busy ? "Mengonfirmasi…" : "Konfirmasi Stock Opname"}
                </button>
              </>
            )}
            {["DRAFT", "COUNTING", "REVIEW"].includes(session.status) && (
              <button type="button" className="ghost" onClick={cancel} disabled={busy} data-testid="opname-cancel">Batalkan</button>
            )}
            <button type="button" className="ghost" onClick={() => { setSession(null); setImportPreview(null); setImportFileObj(null); }} data-testid="opname-back-list">Daftar Sesi</button>
          </div>

          {importPreview && counting && (
            <section className="stock-opname-import-preview" data-testid="opname-import-preview">
              <div className="stock-opname-summary">
                <div className="stock-opname-summary-item">
                  <span className="stock-opname-summary-label">READY</span>
                  <strong data-testid="opname-import-ready-count">{importPreview.ready_count ?? 0}</strong>
                </div>
                <div className="stock-opname-summary-item">
                  <span className="stock-opname-summary-label">Ditolak</span>
                  <strong data-testid="opname-import-rejected-count">{importPreview.rejected_count ?? 0}</strong>
                </div>
                <div className="stock-opname-summary-item">
                  <span className="stock-opname-summary-label">Kosong</span>
                  <strong data-testid="opname-import-missing-count">{importPreview.missing_count ?? 0}</strong>
                </div>
              </div>
              <p className="muted small" data-testid="opname-import-note">{importPreview.note}</p>
              <div className="stock-opname-table-wrap">
                <table className="stock-opname-table" data-testid="opname-import-preview-table">
                  <thead>
                    <tr>
                      <th>Baris</th>
                      <th>Kode</th>
                      <th>Stock Buku</th>
                      <th>Target Fisik</th>
                      <th>Selisih</th>
                      <th>Status</th>
                      <th>Alasan</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...(importPreview.ready || []), ...(importPreview.rejected || []), ...(importPreview.missing || [])]
                      .slice(0, 200)
                      .map((row) => (
                        <tr key={`${row.status}-${row.excel_row}-${row.item_code}`} data-testid={`opname-import-row-${row.excel_row}`}>
                          <td>{row.excel_row}</td>
                          <td>{row.item_code}</td>
                          <td className="opname-col-center">{row.book_qty == null ? "" : fmtQty(row.book_qty)}</td>
                          <td className="opname-col-center">{row.target_physical_qty == null ? "" : fmtQty(row.target_physical_qty)}</td>
                          <td className="opname-col-center">{row.difference_qty == null ? "" : fmtQty(row.difference_qty)}</td>
                          <td>{row.status}</td>
                          <td>{row.reason || ""}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
              <div className="stock-opname-toolbar">
                <button
                  type="button"
                  className="primary"
                  onClick={applyImportPreview}
                  disabled={busy || !importPreview.can_apply || !importFileObj}
                  data-testid="opname-import-apply"
                >
                  Terapkan READY
                </button>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => { setImportPreview(null); setImportFileObj(null); }}
                  disabled={busy}
                  data-testid="opname-import-discard"
                >
                  Buang Preview
                </button>
              </div>
            </section>
          )}

          {counting && (
            <div className="stock-opname-bulk-time" data-testid="opname-bulk-time">
              <label className="stock-opname-bulk-check">
                <input
                  type="checkbox"
                  checked={applyBulkTime}
                  onChange={(event) => setApplyBulkTime(event.target.checked)}
                  data-testid="opname-apply-bulk-time"
                />
                Terapkan satu waktu hitung ke baris yang diisi qty (pilihan eksplisit)
              </label>
              <label className="stock-opname-field stock-opname-field--inline">
                <span>Waktu hitung massal ({STOCK_OPNAME_TZ_LABEL})</span>
                <input
                  type="datetime-local"
                  value={bulkCountAt}
                  onChange={(event) => setBulkCountAt(event.target.value)}
                  onInput={(event) => setBulkCountAt(event.target.value)}
                  disabled={!applyBulkTime || busy}
                  data-testid="opname-bulk-count-at"
                />
              </label>
            </div>
          )}

          <div className="stock-opname-table-wrap">
            <table className="prod-table stock-opname-table" data-testid="opname-count-table">
              <thead>
                <tr>
                  <th>Kode</th>
                  <th>Nama Barang</th>
                  <th>Satuan Dasar</th>
                  <th>Stock Buku</th>
                  {isOperational && <th>Saldo Buku Sekarang</th>}
                  <th>Qty Pesanan</th>
                  <th title="Barang siap kirim belum terbukti keluar — bukan stok fisik. Selisih opname = Stock Fisik − Stock Buku.">
                    Qty Siap Belum Terkirim
                  </th>
                  <th>Hasil Hitung Fisik</th>
                  <th>Tgl/Jam Hitung ({STOCK_OPNAME_TZ_LABEL})</th>
                  <th>Waktu Impor</th>
                  {isOperational && <th>Net Mutasi Setelah Hitung</th>}
                  {isOperational && <th>Target Saldo Sekarang</th>}
                  <th>{isOperational ? "Adjustment (target − buku)" : "Selisih"}</th>
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
                  const belumMissing = line.qty_belum_terkirim === null || line.qty_belum_terkirim === undefined;
                  const previewAvailable = previewCount === null || Number.isNaN(previewCount)
                    ? null
                    : (belumMissing
                      ? previewCount - Number(line.qty_pesanan || 0)
                      : previewCount - Number(line.qty_pesanan || 0) - Number(line.qty_belum_terkirim || 0));
                  const apiDifference = line.difference_qty;
                  const difference = editable && !isOperational ? previewDifference : apiDifference;
                  const available = editable && !isOperational ? previewAvailable : line.qty_tersedia_setelah;
                  const counted = raw !== "" && line.physical_count_qty !== null && line.physical_count_qty !== undefined
                    ? true
                    : line.physical_count_qty !== null && line.physical_count_qty !== undefined && !editable;
                  const bookLive = line.book_qty_live ?? line.book_qty_at_posting;
                  return (
                    <tr
                      key={line.line_id}
                      data-testid={`opname-row-${line.item_code}`}
                      className={difference ? "is-diff" : undefined}
                    >
                      <td className="opname-col-code mono">{line.item_code}</td>
                      <td className="opname-col-name">{line.item_name}</td>
                      <td className="opname-col-center">{line.base_uom}</td>
                      <td className="opname-col-center mono" data-testid={`opname-book-${line.item_code}`}>{fmtQty(line.book_qty)}</td>
                      {isOperational && (
                        <td className="opname-col-center mono" data-testid={`opname-book-live-${line.item_code}`}>
                          {bookLive === null || bookLive === undefined ? "—" : fmtQty(bookLive)}
                        </td>
                      )}
                      <td className="opname-col-center mono" data-testid={`opname-pesanan-${line.item_code}`}>{fmtQty(line.qty_pesanan)}</td>
                      <td
                        className="opname-col-center mono"
                        data-testid={`opname-belum-${line.item_code}`}
                        title="Siap kirim belum terbukti keluar (bukan stok fisik)"
                      >
                        {belumMissing ? "Belum terverifikasi" : fmtQty(line.qty_belum_terkirim)}
                      </td>
                      <td className="opname-col-center">
                        {editable ? (
                          <input
                            className="stock-opname-input"
                            value={raw}
                            onChange={(event) => setDrafts((prev) => ({ ...prev, [line.line_id]: event.target.value }))}
                            inputMode="numeric"
                            aria-label={`Stock Fisik ${line.item_code}`}
                            data-testid={`opname-count-${line.item_code}`}
                            placeholder="kosong ≠ 0"
                          />
                        ) : (
                          <span className="mono" data-testid={`opname-count-value-${line.item_code}`}>{fmtQty(line.physical_count_qty)}</span>
                        )}
                      </td>
                      <td className="opname-col-center">
                        {editable ? (
                          <input
                            className="stock-opname-input stock-opname-input--dt"
                            type="datetime-local"
                            value={timeRaw}
                            onChange={(event) => setTimeDrafts((prev) => ({ ...prev, [line.line_id]: event.target.value }))}
                            onInput={(event) => setTimeDrafts((prev) => ({ ...prev, [line.line_id]: event.target.value }))}
                            aria-label={`Waktu hitung ${line.item_code} ${STOCK_OPNAME_TZ_LABEL}`}
                            data-testid={`opname-count-at-${line.item_code}`}
                            disabled={applyBulkTime}
                          />
                        ) : (
                          <span data-testid={`opname-count-at-value-${line.item_code}`}>{formatWibDateTime(line.physical_count_at)}</span>
                        )}
                      </td>
                      <td className="opname-col-center" data-testid={`opname-imported-at-${line.item_code}`}>
                        {line.imported_at ? formatWibDateTime(line.imported_at) : "—"}
                      </td>
                      {isOperational && (
                        <td className="opname-col-center mono" data-testid={`opname-net-${line.item_code}`}>
                          {line.net_movement_after_count === null || line.net_movement_after_count === undefined
                            ? "—"
                            : fmtQty(line.net_movement_after_count)}
                        </td>
                      )}
                      {isOperational && (
                        <td className="opname-col-center mono" data-testid={`opname-target-${line.item_code}`}>
                          {line.target_qty === null || line.target_qty === undefined ? "—" : fmtQty(line.target_qty)}
                        </td>
                      )}
                      <td className="opname-col-center mono" data-testid={`opname-diff-${line.item_code}`}>
                        {difference === null || difference === undefined || Number.isNaN(difference) ? "" : fmtQty(difference)}
                      </td>
                      <td className="opname-col-center mono">{available === null || available === undefined || Number.isNaN(available) ? "" : fmtQty(available)}</td>
                      <td className="opname-col-center">{(editable ? raw !== "" : counted) ? "Sudah dihitung" : "Belum dihitung"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="muted small" data-testid="opname-belum-note">
            Qty Siap Belum Terkirim = barang siap kirim yang belum terbukti keluar (lingkup cabang), bukan hitungan fisik gudang.
            Selisih / konfirmasi opname memakai Stock Fisik − Stock Buku saja.
          </p>
          {isOperational && (counting || reviewState) && (
            <p className="muted small" data-testid="opname-api-calc-note">
              Net / target / adjustment ditampilkan dari respons backend. Waktu di form adalah {STOCK_OPNAME_TZ_LABEL}; API menerima ISO UTC.
              Waktu impor bukan waktu hitung.
            </p>
          )}
        </section>
      )}

      <section className="stock-opname-history" data-testid="opname-history">
        <h2>Riwayat Stock Opname</h2>
        <div className="stock-opname-table-wrap">
          <table className="prod-table stock-opname-table stock-opname-table--history">
            <thead>
              <tr>
                <th>Nomor / Session</th>
                <th>Lokasi</th>
                <th>Mode</th>
                <th>Periode Hitung</th>
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
                  <td className="opname-col-name">{row.opname_number}</td>
                  <td data-testid={`opname-history-location-${row.opname_number}`}>{opnameLocationLabel(row)}</td>
                  <td className="opname-col-center">{modeLabel(sessionMode(row))}</td>
                  <td
                    className="opname-col-center"
                    data-testid={`opname-history-period-${row.opname_number}`}
                  >
                    {sessionMode(row) === "operational"
                      ? formatWibPeriod(
                        row.physical_count_period_start_at,
                        row.physical_count_period_end_at || row.default_physical_count_at,
                      )
                      : "—"}
                  </td>
                  <td className="opname-col-center">{row.cutoff_at ? formatDate(row.cutoff_at) : "-"}</td>
                  <td className="opname-col-center">{statusLabel(row.status)}</td>
                  <td className="opname-col-center">{row.item_count ?? (row.lines || []).length}</td>
                  <td className="opname-col-center">{row.difference_count ?? ""}</td>
                  <td className="opname-col-center">
                    <button type="button" className="ghost sm" onClick={() => openSession(row.id)} data-testid={`opname-detail-${row.opname_number}`}>Detail</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
