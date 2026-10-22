import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatApiError } from "../lib/errors";
import {
  canOptimizeFromDeliverySchedule,
  DESKTOP_DELIVERY_SCHEDULE_PATH,
  MOBILE_DELIVERY_SCHEDULE_PATH,
} from "../lib/mobileDeliverySchedule";
import {
  eligibilityReasonText,
  eligibilityStatusLabel,
  locationSetupStatusLabel,
  parseReviewOrderIds,
  selectAllReviewRows,
  summarizeSelection,
  toggleId,
} from "../lib/deliveryRouteBridge";
import {
  buildRouteOptEligibilityCreateFleets,
  expandPreviewRowsToSjAtoms,
  getFixtureArmadaCapacity,
  getRouteOptEligibilityPreviewReview,
  isRouteOptEligibilityPreviewEnabled,
  saveFixtureArmadaCapacity,
} from "../lib/deliveryRouteOptEligibilityFixtures";
import {
  capacitySaveErrorText,
  formatArmadaCapacityQty,
  parseArmadaCapacityQtyInput,
} from "../lib/armadaCapacity";
import { fmtQty } from "./qtyUi";
import { formatKm, statusLabel } from "../lib/independentRoute";
import { navigateToOptimasiRute } from "../lib/mobileOpsShell";
import MobileTopBar from "../mobile/MobileTopBar";
import PoIdentityBadges from "./PoIdentityBadges";

const PANEL = {
  border: "1px solid #1e293b",
  background: "#f8fafc",
  borderRadius: 4,
  padding: 14,
};

const CARD_BASE = {
  flex: "1 1 160px",
  border: "1px solid #94a3b8",
  borderRadius: 4,
  padding: "12px 14px",
  cursor: "pointer",
  background: "#fff",
  textAlign: "left",
  minHeight: 88,
};

const CARD_SELECTED = {
  ...CARD_BASE,
  border: "2px solid #0f172a",
  background: "#e2e8f0",
  boxShadow: "inset 0 0 0 1px #0f172a",
};

const PRIMARY_BTN = {
  background: "#0f172a",
  color: "#f8fafc",
  border: "1px solid #020617",
  borderRadius: 4,
  padding: "12px 18px",
  fontWeight: 700,
  fontSize: 15,
  minHeight: 44,
  cursor: "pointer",
};

const PRIMARY_BTN_DISABLED = {
  ...PRIMARY_BTN,
  background: "#64748b",
  borderColor: "#475569",
  cursor: "not-allowed",
  opacity: 0.75,
};

function CreatedRouteSummary({ route, index }) {
  const label = route?.label || `Armada ${index + 1}`;
  const stopCount = Number(route?.stop_count || (route?.order_ids || []).length || 0);
  const urgentCount = Number(route?.urgent_count || 0);
  const status = statusLabel(route?.status) || route?.status || "Dioptimasi";
  const distance =
    route?.estimated_distance_m != null ? formatKm(route.estimated_distance_m) : "—";
  const load = route?.sj_load_qty;
  const sjNumbers = Array.isArray(route?.sj_numbers) ? route.sj_numbers.filter(Boolean) : [];
  return (
    <div
      data-testid={`mds-review-created-route-${index + 1}`}
      style={{
        border: "1px solid #cbd5e1",
        borderRadius: 4,
        padding: 10,
        background: "#fff",
        display: "grid",
        gap: 4,
      }}
    >
      <strong>{label}</strong>
      <div className="muted" style={{ fontSize: 12 }}>
        {stopCount} SJ · {urgentCount} urgent · {distance} · {status}
        {load != null ? ` · Qty SJ ${fmtQty(load)}` : ""}
      </div>
      {sjNumbers.length ? (
        <div className="muted" style={{ fontSize: 12 }} data-testid={`mds-review-created-route-${index + 1}-sj`}>
          {sjNumbers.join(" · ")}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Shared Jadwal Pengiriman → Optimasi Rute review (desktop + PWA).
 * One-stage create+optimize: persists 1 or 2 routes; open via Optimasi Rute.
 */
export default function DeliveryRouteReviewContent({ backPath, shell = "desktop" } = {}) {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { hasPerm } = useAuth();
  const isMobile = shell === "mobile";
  const scheduleBack =
    backPath || (isMobile ? MOBILE_DELIVERY_SCHEDULE_PATH : DESKTOP_DELIVERY_SCHEDULE_PATH);
  const idsQuery = params.toString();
  const fixtureMode = isRouteOptEligibilityPreviewEnabled(idsQuery);
  const allowed = canOptimizeFromDeliverySchedule(hasPerm) || fixtureMode;
  const orderIds = useMemo(() => parseReviewOrderIds(idsQuery), [idsQuery]);

  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState([]);
  const [fleetCount, setFleetCount] = useState(2);
  const [createResult, setCreateResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [err, setErr] = useState("");
  const [cap1Draft, setCap1Draft] = useState("");
  const [cap2Draft, setCap2Draft] = useState("");
  const [savedCapacities, setSavedCapacities] = useState({
    armada_1_capacity_qty_sj: null,
    armada_2_capacity_qty_sj: null,
  });
  const [savingCapacity, setSavingCapacity] = useState(false);
  const [capacitySavedOk, setCapacitySavedOk] = useState(false);
  const [capacityErr, setCapacityErr] = useState("");

  const clearOutcome = useCallback(() => {
    setCreateResult(null);
    setErr("");
  }, []);

  const load = useCallback(async () => {
    // Fixture preview wins before allowed / empty-ID handling.
    if (isRouteOptEligibilityPreviewEnabled(idsQuery)) {
      const data = getRouteOptEligibilityPreviewReview();
      const nextRows = Array.isArray(data?.rows) ? data.rows : [];
      setRows(nextRows);
      setSelected(selectAllReviewRows(nextRows));
      setCreateResult(null);
      setErr("");
      setBusy(false);
      return;
    }
    if (!allowed) return;
    setBusy(true);
    setErr("");
    setCreateResult(null);
    try {
      if (!orderIds.length) {
        setRows([]);
        setSelected([]);
        setErr("Tidak ada PO yang dipilih. Kembali ke Jadwal Pengiriman lalu pilih PO.");
        return;
      }
      const data = await api.reviewScheduleRouteOptimize(orderIds);
      const nextRows = Array.isArray(data?.rows) ? data.rows : [];
      setRows(nextRows);
      setSelected(selectAllReviewRows(nextRows));
    } catch (e) {
      setRows([]);
      setSelected([]);
      setErr(formatApiError(e, "Gagal memuat review optimasi."));
    } finally {
      setBusy(false);
    }
  }, [allowed, idsQuery, orderIds]);

  const loadCapacity = useCallback(async () => {
    setCapacityErr("");
    setCapacitySavedOk(false);
    try {
      const data = fixtureMode
        ? getFixtureArmadaCapacity()
        : await api.getArmadaCapacity();
      const c1 = data?.armada_1_capacity_qty_sj;
      const c2 = data?.armada_2_capacity_qty_sj;
      setSavedCapacities({
        armada_1_capacity_qty_sj: c1 ?? null,
        armada_2_capacity_qty_sj: c2 ?? null,
      });
      setCap1Draft(formatArmadaCapacityQty(c1));
      setCap2Draft(formatArmadaCapacityQty(c2));
    } catch (e) {
      setCapacityErr(formatApiError(e, "Gagal memuat kapasitas armada."));
    }
  }, [fixtureMode]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadCapacity();
  }, [loadCapacity]);

  const selectionSummary = useMemo(() => summarizeSelection(rows, selected), [rows, selected]);

  const eligibleSelectedIds = useMemo(() => {
    const set = new Set(selected.map(String));
    return rows
      .filter((r) => set.has(String(r.sales_order_id)) && r.eligible)
      .map((r) => String(r.sales_order_id));
  }, [rows, selected]);

  const eligibleSelectedSjCount = useMemo(() => {
    const set = new Set(selected.map(String));
    const eligible = rows.filter((r) => set.has(String(r.sales_order_id)) && r.eligible);
    return expandPreviewRowsToSjAtoms(eligible).length;
  }, [rows, selected]);

  const urgentSelectedCount = useMemo(() => {
    const set = new Set(selected.map(String));
    return rows.filter((r) => set.has(String(r.sales_order_id)) && r.eligible && r.urgent).length;
  }, [rows, selected]);

  const disabledReason = useMemo(() => {
    if (busy || optimizing || savingCapacity) return "";
    if (savedCapacities.armada_1_capacity_qty_sj == null || savedCapacities.armada_2_capacity_qty_sj == null) {
      return "Simpan kapasitas Armada 1 dan Armada 2 sebelum Optimasi Rute.";
    }
    if (fleetCount !== 1 && fleetCount !== 2) return "Pilih 1 atau 2 armada.";
    if (fleetCount === 1 && eligibleSelectedSjCount < 1) {
      return "Optimasi 1 Armada membutuhkan minimal 1 Surat Jalan eligible.";
    }
    if (fleetCount === 2 && eligibleSelectedSjCount < 2) {
      return "Optimasi 2 Armada membutuhkan minimal 2 Surat Jalan eligible.";
    }
    return "";
  }, [busy, optimizing, savingCapacity, fleetCount, eligibleSelectedSjCount, savedCapacities]);

  const onSaveCapacity = async () => {
    if (savingCapacity) return;
    const p1 = parseArmadaCapacityQtyInput(cap1Draft);
    const p2 = parseArmadaCapacityQtyInput(cap2Draft);
    if (p1.error || p2.error) {
      setCapacitySavedOk(false);
      setCapacityErr("Kapasitas harus bilangan bulat lebih dari 0.");
      return;
    }
    setSavingCapacity(true);
    setCapacityErr("");
    setCapacitySavedOk(false);
    try {
      const payload = {
        armada_1_capacity_qty_sj: p1.value,
        armada_2_capacity_qty_sj: p2.value,
      };
      const data = fixtureMode
        ? saveFixtureArmadaCapacity(payload)
        : await api.saveArmadaCapacity(payload);
      const c1 = data?.armada_1_capacity_qty_sj ?? p1.value;
      const c2 = data?.armada_2_capacity_qty_sj ?? p2.value;
      setSavedCapacities({
        armada_1_capacity_qty_sj: c1,
        armada_2_capacity_qty_sj: c2,
      });
      setCap1Draft(formatArmadaCapacityQty(c1));
      setCap2Draft(formatArmadaCapacityQty(c2));
      setCapacitySavedOk(true);
    } catch (e) {
      setCapacitySavedOk(false);
      setCapacityErr(capacitySaveErrorText(e, formatApiError(e, "Gagal menyimpan kapasitas.")));
    } finally {
      setSavingCapacity(false);
    }
  };

  const canRunOptimize = !disabledReason && !busy && !optimizing && !savingCapacity;

  const onToggle = (id) => {
    clearOutcome();
    setSelected((prev) => toggleId(prev, id));
  };

  const onFleetCountChange = (next) => {
    const value = Number(next);
    if (value !== 1 && value !== 2) return;
    clearOutcome();
    setFleetCount(value);
  };

  const runOptimize = async () => {
    if (!canRunOptimize) return;
    setOptimizing(true);
    setErr("");
    setCreateResult(null);
    try {
      const data = fixtureMode
        ? buildRouteOptEligibilityCreateFleets({
            orderIds: eligibleSelectedIds,
            fleetCount,
          })
        : await api.createFleetsFromSchedule(eligibleSelectedIds, fleetCount);
      setCreateResult(data);
    } catch (e) {
      setErr(formatApiError(e, "Gagal membuat dan mengoptimalkan rute."));
    } finally {
      setOptimizing(false);
    }
  };

  const openOptimasiRute = () => {
    navigateToOptimasiRute(navigate, "", { from: scheduleBack });
  };

  const createdRoutes = Array.isArray(createResult?.routes) ? createResult.routes : [];
  const createdCount = Number(
    createResult?.created_route_count != null
      ? createResult.created_route_count
      : createdRoutes.length || 0,
  );
  const unassignedRows = Array.isArray(createResult?.unassigned) ? createResult.unassigned : [];
  const rootTestId = isMobile ? "mds-review-root" : "ds-review-root";

  if (!allowed) {
    return (
      <div className={isMobile ? "mapp-page" : "page"} data-testid={rootTestId}>
        {isMobile ? <MobileTopBar title="Optimasi Rute" onBack={() => window.history.back()} /> : null}
        <div className="mds-card" style={{ margin: isMobile ? 12 : 0 }}>
          <div className="mds-card-bd">
            <strong>Akses ditolak</strong>
            <p className="muted">Anda tidak memiliki izin untuk optimasi rute dari jadwal pengiriman.</p>
            <Link className="btn secondary" to={scheduleBack}>
              Kembali
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={isMobile ? "mapp-page" : "page"} data-testid={rootTestId}>
      {isMobile ? (
        <MobileTopBar title="Optimasi Rute" onBack={() => window.history.back()} />
      ) : (
        <div
          className="page-header"
          style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}
        >
          <div>
            <h1 style={{ margin: 0 }}>Optimasi Rute</h1>
            <p className="muted" style={{ margin: "6px 0 0" }}>
              Buat dan simpan rute dari PO eligible, lalu buka melalui menu Optimasi Rute.
            </p>
          </div>
          <Link className="btn secondary" to={scheduleBack} data-testid="mds-review-back">
            Kembali ke Jadwal
          </Link>
        </div>
      )}

      <div style={{ padding: isMobile ? 12 : 0, display: "grid", gap: 12 }}>
        {fixtureMode ? (
          <div className="mds-card" data-testid="mds-review-fixture-banner">
            <div className="mds-card-bd">
              <strong>DATA DUMMY — DEVELOPMENT PREVIEW</strong>
              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                Mode fixture lokal. Tidak memanggil API review/create/optimasi/penyimpanan. Data tidak
                dipersist.
              </div>
            </div>
          </div>
        ) : null}

        {err ? (
          <div className="mds-card" data-testid="mds-review-error">
            <div className="mds-card-bd" style={{ color: "var(--danger, #b00020)" }}>
              {err}
            </div>
          </div>
        ) : null}

        <section className="mds-card">
          <div className="mds-card-hd">
            <strong>PO Terpilih</strong>
          </div>
          <div className="mds-card-bd" style={{ display: "grid", gap: 8 }}>
            <div className="muted" style={{ fontSize: 12 }} data-testid="mds-review-selection-summary">
              Dipilih {selectionSummary.selected} · Eligible {selectionSummary.eligible} · Tidak eligible{" "}
              {selectionSummary.ineligible}
            </div>
            {busy ? (
              <div className="muted">Memuat…</div>
            ) : rows.length === 0 ? (
              <div className="muted">Tidak ada baris untuk direview.</div>
            ) : (
              <div style={{ display: "grid", gap: 8 }}>
                {rows.map((row) => {
                  const id = String(row.sales_order_id);
                  const checked = selected.includes(id);
                  return (
                    <label
                      key={id}
                      className="mds-card"
                      style={{ padding: 10, display: "grid", gap: 4, cursor: "pointer" }}
                      data-testid={`mds-review-row-${id}`}
                    >
                      <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => onToggle(id)}
                          data-testid={`mds-review-check-${id}`}
                        />
                        <div style={{ flex: 1, display: "grid", gap: 2 }}>
                          <div
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              gap: 8,
                              flexWrap: "wrap",
                            }}
                          >
                            <strong>
                              {row.order_number || id}{" "}
                              <PoIdentityBadges order={row} testIdPrefix={`mds-review-po-${row.order_number || id}`} />
                            </strong>
                          </div>
                          <div className="muted" style={{ fontSize: 12 }}>
                            {row.customer_name || "—"}
                            {row.sj_label ? ` · SJ ${row.sj_label}` : ""}
                          </div>
                          <div className="muted" style={{ fontSize: 12 }}>
                            Status Lokasi: {locationSetupStatusLabel(row)} · {eligibilityStatusLabel(row)}
                          </div>
                          {!row.eligible ? (
                            <div
                              className="muted"
                              style={{ fontSize: 12 }}
                              data-testid={`mds-review-reason-${id}`}
                            >
                              {eligibilityReasonText(row)}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        </section>

        <section style={PANEL} data-testid="mds-review-create-panel">
          <div style={{ display: "grid", gap: 10 }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 18, color: "#0f172a" }}>Buat Optimasi Rute</h2>
              <p className="muted" style={{ margin: "6px 0 0", fontSize: 13 }}>
                Pilih jumlah armada. Sistem akan membuat dan menyimpan rute yang dapat dibuka melalui menu
                Optimasi Rute.
              </p>
            </div>

            <div
              data-testid="mds-review-create-summary"
              style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: 13 }}
            >
              <span>
                Eligible: <strong>{eligibleSelectedIds.length}</strong>
              </span>
              <span>
                Excluded: <strong>{selectionSummary.ineligible}</strong>
              </span>
              <span>
                Urgent: <strong>{urgentSelectedCount}</strong>
              </span>
            </div>

            <div data-testid="mds-review-capacity" style={{ display: "grid", gap: 8 }}>
              <div style={{ fontWeight: 700, color: "#0f172a" }}>Pengaturan Kapasitas Armada</div>
              <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
                <label style={{ display: "grid", gap: 4 }}>
                  <span style={{ fontWeight: 700, fontSize: 13 }}>Armada 1</span>
                  <span className="muted" style={{ fontSize: 12 }}>Kapasitas Qty SJ</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={cap1Draft}
                    onChange={(e) => {
                      setCapacitySavedOk(false);
                      setCapacityErr("");
                      setCap1Draft(e.target.value);
                    }}
                    data-testid="mds-review-capacity-armada-1"
                    aria-label="Kapasitas Qty SJ Armada 1"
                    style={{
                      border: "1px solid #94a3b8",
                      borderRadius: 4,
                      padding: "8px 10px",
                      fontSize: 14,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  />
                </label>
                <label style={{ display: "grid", gap: 4 }}>
                  <span style={{ fontWeight: 700, fontSize: 13 }}>Armada 2</span>
                  <span className="muted" style={{ fontSize: 12 }}>Kapasitas Qty SJ</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={cap2Draft}
                    onChange={(e) => {
                      setCapacitySavedOk(false);
                      setCapacityErr("");
                      setCap2Draft(e.target.value);
                    }}
                    data-testid="mds-review-capacity-armada-2"
                    aria-label="Kapasitas Qty SJ Armada 2"
                    style={{
                      border: "1px solid #94a3b8",
                      borderRadius: 4,
                      padding: "8px 10px",
                      fontSize: 14,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  />
                </label>
              </div>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <button
                  type="button"
                  style={savingCapacity ? PRIMARY_BTN_DISABLED : PRIMARY_BTN}
                  data-testid="mds-review-save-capacity"
                  disabled={savingCapacity}
                  onClick={() => void onSaveCapacity()}
                >
                  {savingCapacity ? "Menyimpan..." : "Simpan Kapasitas"}
                </button>
                {capacitySavedOk ? (
                  <span className="muted" style={{ fontSize: 12 }} data-testid="mds-review-capacity-saved">
                    Kapasitas tersimpan
                  </span>
                ) : null}
              </div>
              {capacityErr ? (
                <div style={{ color: "var(--danger, #b00020)", fontSize: 12 }} data-testid="mds-review-capacity-error">
                  {capacityErr}
                </div>
              ) : null}
            </div>

            <div data-testid="mds-review-fleet-count">
              <div style={{ fontWeight: 700, marginBottom: 8, color: "#0f172a" }}>Jumlah Armada</div>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <button
                  type="button"
                  style={fleetCount === 1 ? CARD_SELECTED : CARD_BASE}
                  data-testid="mds-review-fleet-count-1"
                  aria-pressed={fleetCount === 1}
                  onClick={() => onFleetCountChange(1)}
                >
                  <div style={{ fontWeight: 700 }}>1 Armada</div>
                  <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                    Semua Surat Jalan eligible disusun dalam satu rute.
                  </div>
                </button>
                <button
                  type="button"
                  style={fleetCount === 2 ? CARD_SELECTED : CARD_BASE}
                  data-testid="mds-review-fleet-count-2"
                  aria-pressed={fleetCount === 2}
                  onClick={() => onFleetCountChange(2)}
                >
                  <div style={{ fontWeight: 700 }}>2 Armada</div>
                  <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                    SJ eligible dibagi geografis; SJ berbeda dari PO yang sama boleh ke armada berbeda.
                  </div>
                </button>
              </div>
            </div>

            <div style={{ display: "grid", gap: 6 }}>
              <button
                type="button"
                style={canRunOptimize ? PRIMARY_BTN : PRIMARY_BTN_DISABLED}
                data-testid="mds-review-optimasi-armada"
                disabled={!canRunOptimize}
                onClick={() => void runOptimize()}
              >
                {optimizing ? "Membuat dan Mengoptimalkan Rute..." : "Optimasi Rute"}
              </button>
              {!canRunOptimize && disabledReason ? (
                <div className="muted" style={{ fontSize: 12 }} data-testid="mds-review-disabled-reason">
                  {disabledReason}
                </div>
              ) : null}
            </div>
          </div>
        </section>

        {createResult ? (
          <section style={PANEL} data-testid="mds-review-success">
            <div style={{ display: "grid", gap: 10 }}>
              <strong style={{ color: "#0f172a", fontSize: 15 }}>
                {createdCount === 0
                  ? "Tidak ada rute yang dapat dialokasikan pada kapasitas tersimpan."
                  : createdCount === 1
                    ? "1 rute berhasil dibuat dan dioptimalkan."
                    : `${createdCount} rute berhasil dibuat dan dioptimalkan.`}
              </strong>
              {fixtureMode ? (
                <div className="muted" style={{ fontSize: 12 }}>
                  Simulasi lokal — data tidak tersimpan di database.
                </div>
              ) : createResult.reused ? (
                <div className="muted" style={{ fontSize: 12 }}>
                  Hasil sebelumnya dipakai ulang (tanpa duplikasi).
                </div>
              ) : null}
              <div style={{ display: "grid", gap: 8 }} data-testid="mds-review-created-routes">
                {createdRoutes.map((route, index) => (
                  <CreatedRouteSummary key={route.route_id || index} route={route} index={index} />
                ))}
              </div>
              {unassignedRows.length ? (
                <div data-testid="mds-review-unassigned" style={{ display: "grid", gap: 8 }}>
                  <strong style={{ color: "#0f172a", fontSize: 14 }}>Belum Teralokasi</strong>
                  {unassignedRows.map((row) => {
                    const units = Array.isArray(row.sj_units) && row.sj_units.length
                      ? row.sj_units
                      : [{
                          shipment_id: row.sales_order_id,
                          sj_number: row.sj_label || "—",
                          sj_load_qty: row.sj_load_qty,
                        }];
                    return units.map((unit, ui) => (
                      <div
                        key={`${row.sales_order_id}-${unit.shipment_id || ui}`}
                        className="muted"
                        style={{
                          fontSize: 12,
                          border: "1px solid #e2e8f0",
                          borderRadius: 4,
                          padding: 8,
                          background: "#fff",
                        }}
                        data-testid={`mds-review-unassigned-${row.sales_order_id}-${unit.shipment_id || ui}`}
                      >
                        <div>
                          <strong>{row.customer_name || "—"}</strong>
                          {" · "}
                          {row.order_number || row.sales_order_id}{" "}
                          <PoIdentityBadges order={row} testIdPrefix={`mds-review-unassigned-${row.order_number || row.sales_order_id}`} />
                          {" · SJ "}
                          {unit.sj_number || row.sj_label || "—"}
                          {" · Qty SJ "}
                          {fmtQty(unit.sj_load_qty ?? row.sj_load_qty)}
                        </div>
                        <div>{row.reason || row.unassigned_reason || "Kapasitas armada tidak mencukupi"}</div>
                      </div>
                    ));
                  })}
                </div>
              ) : null}
              {createdCount > 0 ? (
                <button
                  type="button"
                  style={PRIMARY_BTN}
                  data-testid="mds-review-open-optimasi-rute"
                  onClick={openOptimasiRute}
                >
                  Buka Optimasi Rute
                </button>
              ) : null}
            </div>
          </section>
        ) : null}
      </div>
    </div>
  );
}
