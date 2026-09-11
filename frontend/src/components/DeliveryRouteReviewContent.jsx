import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatApiError } from "../lib/errors";
import { formatDate } from "../lib/constants";
import {
  canAssignDeliveryDriver,
  canOptimizeFromDeliverySchedule,
  DESKTOP_DELIVERY_SCHEDULE_PATH,
  MOBILE_DELIVERY_SCHEDULE_PATH,
} from "../lib/mobileDeliverySchedule";
import {
  buildBulkAssignConfirm,
  driverDisplayLabel,
  eligibilityReasonText,
  eligibilityStatusLabel,
  excludeFromSelection,
  locationSetupStatusLabel,
  parseReviewOrderIds,
  selectAllReviewRows,
  summarizeSelection,
  toggleId,
} from "../lib/deliveryRouteBridge";
import { navigateToOptimasiRute } from "../lib/mobileOpsShell";
import MobileTopBar from "../mobile/MobileTopBar";

/**
 * Shared delivery-schedule → route-optimize review UI.
 * shell="mobile" → PWA chrome (MobileTopBar); shell="desktop" → AppShell page chrome.
 */
export default function DeliveryRouteReviewContent({
  backPath,
  shell = "mobile",
} = {}) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { hasPerm } = useAuth();
  const allowed = canOptimizeFromDeliverySchedule(hasPerm);
  const canAssign = canAssignDeliveryDriver(hasPerm);
  const scheduleBack =
    backPath
    || (shell === "desktop" ? DESKTOP_DELIVERY_SCHEDULE_PATH : MOBILE_DELIVERY_SCHEDULE_PATH);
  const isDesktop = shell === "desktop";
  // Stabilize on search string — URLSearchParams identity can churn and re-fire load().
  const idsQuery = params.toString();
  const orderIds = useMemo(() => parseReviewOrderIds(idsQuery), [idsQuery]);

  const [rows, setRows] = useState([]);
  const [summary, setSummary] = useState(null);
  const [groups, setGroups] = useState([]);
  const [selected, setSelected] = useState([]);
  const [busy, setBusy] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState(null);

  const [drivers, setDrivers] = useState([]);
  const [driversErr, setDriversErr] = useState("");
  const [rowDraft, setRowDraft] = useState({});
  const [rowSaving, setRowSaving] = useState({});
  const [bulkDriverId, setBulkDriverId] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [assignMsg, setAssignMsg] = useState("");

  const load = useCallback(async () => {
    if (!allowed) return;
    setBusy(true);
    setErr("");
    try {
      const data = await api.reviewScheduleRouteOptimize(orderIds);
      setRows(data.rows || []);
      setSummary(data.summary || null);
      setGroups(data.groups || []);
      // Selection includes ineligible rows so bulk driver assign works before Optimalkan Rute.
      setSelected(selectAllReviewRows(data.rows || []));
      setRowDraft({});
    } catch (e) {
      setErr(formatApiError(e, "Gagal memuat review optimasi."));
      setRows([]);
    } finally {
      setBusy(false);
    }
  }, [allowed, orderIds]);

  const loadDrivers = useCallback(async () => {
    if (!canAssign) return;
    setDriversErr("");
    try {
      const data = await api.listEligibleDeliveryDrivers();
      setDrivers(data.drivers || []);
    } catch (e) {
      setDrivers([]);
      setDriversErr(formatApiError(e, "Gagal memuat daftar driver."));
    }
  }, [canAssign]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    loadDrivers();
  }, [loadDrivers]);

  const selectionSummary = useMemo(() => summarizeSelection(rows, selected), [rows, selected]);
  const bulkDriver = useMemo(
    () => drivers.find((d) => String(d.id) === String(bulkDriverId)) || null,
    [drivers, bulkDriverId],
  );

  const btnGhost = isDesktop ? "ghost sm" : "mop-btn-ghost";
  const btnGhostSm = isDesktop ? "ghost sm" : "mop-btn-ghost sm";
  const btnPrimary = isDesktop ? "primary" : "mop-btn-primary";

  const onConfirm = async () => {
    if (submitting) return;
    const ids = selectionSummary.groups.flatMap((g) => g.rows.map((r) => r.sales_order_id));
    if (!ids.length) return;
    setSubmitting(true);
    setErr("");
    try {
      const data = await api.optimizeFromSchedule(ids);
      setResult(data);
      if (data?.ok || data?.partial) {
        await load();
      }
    } catch (e) {
      setErr(formatApiError(e, "Gagal membuat/optimasi rute."));
    } finally {
      setSubmitting(false);
    }
  };

  const applyRowDriver = async (orderId) => {
    const draftId = String(rowDraft[orderId] || "").trim();
    if (!draftId || rowSaving[orderId]) return;
    const current = rows.find((r) => String(r.sales_order_id) === String(orderId));
    if (String(current?.driver_user_id || "") === draftId) {
      setAssignMsg("Driver sudah sama — tidak diubah.");
      return;
    }
    const driver = drivers.find((d) => String(d.id) === draftId);
    const label = driver?.name || draftId;
    if (!window.confirm(`Ubah driver menjadi: ${label}?`)) return;

    setRowSaving((prev) => ({ ...prev, [orderId]: true }));
    setAssignMsg("");
    setErr("");
    try {
      const data = await api.assignDeliveryDriver(orderId, draftId);
      const status = data?.result?.status;
      setAssignMsg(
        status === "unchanged"
          ? `Driver tidak berubah untuk ${orderId}.`
          : `Driver disimpan: ${data?.result?.driver_name || label}`,
      );
      await load();
    } catch (e) {
      setErr(formatApiError(e, "Gagal menyimpan driver."));
    } finally {
      setRowSaving((prev) => {
        const next = { ...prev };
        delete next[orderId];
        return next;
      });
    }
  };

  const applyBulkDriver = async () => {
    if (bulkBusy || !bulkDriver || !selected.length) return;
    const confirmMeta = buildBulkAssignConfirm(rows, selected, bulkDriver);
    if (confirmMeta.changeCount === 0) {
      setAssignMsg("Semua baris terpilih sudah memakai driver ini.");
      return;
    }
    const ok = window.confirm(
      `${confirmMeta.selectedCount} pengiriman dipilih\n` +
        `Driver akan diubah menjadi: ${confirmMeta.driverName}\n` +
        (confirmMeta.unchangedCount
          ? `(${confirmMeta.unchangedCount} sudah memakai driver ini — tidak ditulis ulang)\n`
          : "") +
        "Lanjutkan?",
    );
    if (!ok) return;

    setBulkBusy(true);
    setAssignMsg("");
    setErr("");
    try {
      const data = await api.bulkAssignDeliveryDriver(selected, bulkDriver.id);
      const c = data?.counts || {};
      setAssignMsg(
        `Bulk driver: updated ${c.updated || 0}, unchanged ${c.unchanged || 0}, failed ${c.failed || 0}`,
      );
      if (data?.ok === false && !(c.updated > 0)) {
        setErr("Bulk assign gagal untuk semua baris terpilih.");
      }
      await load();
    } catch (e) {
      setErr(formatApiError(e, "Gagal menerapkan driver ke terpilih."));
    } finally {
      setBulkBusy(false);
    }
  };

  const rootClass = isDesktop
    ? "ship-status-page ds-delivery-review-root"
    : "mop-schedule-root mop-delivery-review-root";
  const mainClass = isDesktop
    ? "mds-review-page ds-review-page"
    : "mop-content mds-review-page";
  const errClass = isDesktop ? "error" : "mop-error";
  const emptyClass = isDesktop ? "empty" : "mop-empty";

  return (
    <div className={rootClass} data-testid={isDesktop ? "ds-review-root" : "mds-review-root"}>
      {!isDesktop ? (
        <MobileTopBar
          kicker="ETERNAL PACKAGING"
          title="Review Pengiriman Terpilih"
          onBack={() => navigate(scheduleBack)}
        />
      ) : null}
      {/* Do not use mop-schedule-page here: its overflow:hidden + non-scrolling flex layout
          traps native <select> / toolbar taps on PWA/mobile WebKit. */}
      <main className={mainClass} data-testid="mds-review-page">
        {isDesktop ? (
          <header className="ship-status-heading" data-testid="ds-review-heading">
            <p className="eyebrow">Produksi</p>
            <h1>Review Pengiriman Terpilih</h1>
          </header>
        ) : null}
        {!allowed && (
          <div className={errClass} data-testid="mds-review-forbidden">
            <p>Anda tidak punya izin optimasi rute.</p>
            <Link to={scheduleBack} className={btnGhost}>Kembali</Link>
          </div>
        )}
        {allowed && (
          <>
            <div className="mds-review-toolbar">
              <Link to={scheduleBack} className={btnGhost} data-testid="mds-review-back">
                Kembali
              </Link>
              <button
                type="button"
                className={btnGhost}
                data-testid="mds-review-select-all"
                onClick={() => setSelected(selectAllReviewRows(rows))}
                disabled={busy || rows.length === 0}
              >
                Pilih Semua
              </button>
              <button
                type="button"
                className={btnGhost}
                data-testid="mds-review-exclude"
                onClick={() => setSelected(excludeFromSelection(selected, selected))}
                disabled={!selected.length}
              >
                Keluarkan dari Pilihan
              </button>
            </div>

            {canAssign && (
              <div className="mds-review-bulk-driver" data-testid="mds-review-bulk-driver">
                <label className="mds-driver-label" htmlFor="mds-bulk-driver">
                  Driver
                </label>
                <select
                  id="mds-bulk-driver"
                  className="mds-driver-select"
                  data-testid="mds-review-bulk-driver-select"
                  value={bulkDriverId}
                  onChange={(e) => setBulkDriverId(String(e.target.value))}
                  disabled={bulkBusy || busy || !!driversErr}
                >
                  <option value="">Pilih Driver</option>
                  {drivers.map((d) => (
                    <option key={String(d.id)} value={String(d.id)}>
                      {d.name || d.id}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className={btnGhost}
                  data-testid="mds-review-bulk-apply"
                  disabled={bulkBusy || !bulkDriverId || !selected.length}
                  onClick={applyBulkDriver}
                >
                  {bulkBusy ? "Menerapkan..." : "Terapkan ke Terpilih"}
                </button>
              </div>
            )}

            {busy && <p className={emptyClass} data-testid="mds-review-loading">Memuat review...</p>}
            {err ? <div className={errClass} data-testid="mds-review-error">{err}</div> : null}
            {driversErr ? <div className={errClass} data-testid="mds-review-drivers-error">{driversErr}</div> : null}
            {assignMsg ? <div className="mds-review-assign-msg" data-testid="mds-review-assign-msg">{assignMsg}</div> : null}

            <div className="mds-review-summary" data-testid="mds-review-summary">
              <span>Dipilih: {selectionSummary.selected}</span>
              <span>Eligible: {selectionSummary.eligible}</span>
              <span>Tidak eligible: {selectionSummary.ineligible}</span>
              <span>Driver: {selectionSummary.drivers}</span>
              <span>Rute dibuat: {selectionSummary.routes_to_create}</span>
            </div>

            {selectionSummary.groups.length > 0 && (
              <div className="mds-review-groups" data-testid="mds-review-groups">
                {selectionSummary.groups.map((g) => (
                  <div key={g.driver_user_id} className="mds-review-group">
                    <strong>{g.driver_name || g.driver_user_id}</strong>
                    <span>{g.rows.length} pengiriman</span>
                  </div>
                ))}
              </div>
            )}

            <div className="mds-review-list" data-testid="mds-review-list">
              {rows.map((row) => {
                const id = String(row.sales_order_id);
                const checked = selected.includes(id);
                const draft = rowDraft[id] !== undefined ? rowDraft[id] : (row.driver_user_id || "");
                const dirty = String(draft || "") !== String(row.driver_user_id || "");
                return (
                  <div
                    key={id}
                    className={`mds-review-row${row.eligible ? "" : " is-ineligible"}`}
                    data-testid={`mds-review-row-${id}`}
                  >
                    <label className="mds-review-check">
                      <input
                        type="checkbox"
                        checked={checked}
                        data-testid={`mds-review-check-${id}`}
                        onChange={() => setSelected((prev) => toggleId(prev, id))}
                      />
                    </label>
                    <div className="mds-review-body">
                      <div className="mds-review-title">
                        <strong>{row.customer_name || "—"}</strong>
                        <span className="mono">{row.order_number || id}</span>
                      </div>
                      <div className="mds-review-meta">
                        <span>{row.address || "Alamat —"}</span>
                        <span>SJ: {row.sj_label || "—"}</span>
                        <span data-testid={`mds-review-driver-label-${id}`}>
                          Driver: {driverDisplayLabel(row)}
                        </span>
                        <span>Tanggal: {formatDate(row.effective_delivery_date, { dateOnly: true })}</span>
                        <span>{row.optimization_status_label}</span>
                      </div>
                      <div className="mds-review-status" data-testid={`mds-review-status-${id}`}>
                        <span data-testid={`mds-review-location-status-${id}`}>
                          Status Atur Lokasi: {locationSetupStatusLabel(row)}
                        </span>
                        <span data-testid={`mds-review-eligibility-status-${id}`}>
                          Eligibility: {eligibilityStatusLabel(row)}
                        </span>
                        {!row.eligible ? (
                          <span
                            className="mds-review-reason"
                            data-testid={`mds-review-reason-${id}`}
                          >
                            Alasan: {eligibilityReasonText(row)}
                          </span>
                        ) : null}
                      </div>
                      {canAssign && (
                        <div className="mds-review-row-driver" data-testid={`mds-review-pick-driver-${id}`}>
                          <label className="mds-driver-label" htmlFor={`mds-driver-${id}`}>
                            Pilih Driver
                          </label>
                          <select
                            id={`mds-driver-${id}`}
                            className="mds-driver-select"
                            data-testid={`mds-review-driver-select-${id}`}
                            value={draft ? String(draft) : ""}
                            onChange={(e) =>
                              setRowDraft((prev) => ({ ...prev, [id]: String(e.target.value) }))
                            }
                            disabled={!!rowSaving[id] || busy}
                          >
                            <option value="">
                              {row.driver_user_id ? "— pilih driver —" : "Belum Ada Driver"}
                            </option>
                            {drivers.map((d) => (
                              <option key={String(d.id)} value={String(d.id)}>
                                {d.name || d.id}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            className={btnGhostSm}
                            data-testid={`mds-review-driver-save-${id}`}
                            disabled={!dirty || !draft || !!rowSaving[id]}
                            onClick={() => applyRowDriver(id)}
                          >
                            {rowSaving[id] ? "Menyimpan..." : "Simpan"}
                          </button>
                        </div>
                      )}
                    </div>
                    <button
                      type="button"
                      className={btnGhostSm}
                      data-testid={`mds-review-exclude-${id}`}
                      onClick={() => setSelected((prev) => excludeFromSelection(prev, [id]))}
                    >
                      Keluarkan
                    </button>
                  </div>
                );
              })}
              {!busy && rows.length === 0 && (
                <p className={emptyClass} data-testid="mds-review-empty">Tidak ada pengiriman dalam filter aktif.</p>
              )}
            </div>

            <div className="mds-review-actions">
              <button
                type="button"
                className={btnPrimary}
                data-testid="mds-review-confirm"
                disabled={submitting || selectionSummary.eligible === 0}
                onClick={onConfirm}
              >
                {submitting ? "Mengoptimasi..." : "Optimalkan Rute"}
              </button>
            </div>

            {result && (
              <div className="mds-review-result" data-testid="mds-review-result">
                <p>
                  {result.ok
                    ? "Optimasi selesai."
                    : result.partial
                      ? "Sebagian rute berhasil dibuat."
                      : "Optimasi gagal."}
                </p>
                {(result.results || []).map((g) => (
                  <div key={g.driver_user_id} className="mds-review-result-row">
                    <span>{g.driver_name || g.driver_user_id}</span>
                    <span>{g.ok ? "OK" : g.error || "Gagal"}</span>
                    {g.route_id ? (
                      <button
                        type="button"
                        className="mds-opt-link"
                        data-testid={`mds-review-open-${g.route_id}`}
                        onClick={() => navigateToOptimasiRute(navigate, g.route_id, {
                          from: `${window.location.pathname}${window.location.search}`,
                        })}
                      >
                        Lihat Rute
                      </button>
                    ) : null}
                  </div>
                ))}
                <button
                  type="button"
                  className={btnGhost}
                  data-testid="mds-review-done"
                  onClick={() => navigate(scheduleBack)}
                >
                  Selesai
                </button>
              </div>
            )}

            {/* keep groups from server for debug density without clutter */}
            {groups.length === 0 && summary ? null : null}
          </>
        )}
      </main>
    </div>
  );
}
