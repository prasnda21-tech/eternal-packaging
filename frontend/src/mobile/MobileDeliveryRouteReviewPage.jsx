import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatApiError } from "../lib/errors";
import { formatDate } from "../lib/constants";
import {
  canOptimizeFromDeliverySchedule,
  MOBILE_DELIVERY_SCHEDULE_PATH,
} from "../lib/mobileDeliverySchedule";
import {
  excludeFromSelection,
  parseReviewOrderIds,
  selectAllEligible,
  summarizeSelection,
  toggleId,
} from "../lib/deliveryRouteBridge";
import MobileTopBar from "./MobileTopBar";

export default function MobileDeliveryRouteReviewPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { hasPerm } = useAuth();
  const allowed = canOptimizeFromDeliverySchedule(hasPerm);
  const orderIds = useMemo(() => parseReviewOrderIds(params.toString()), [params]);

  const [rows, setRows] = useState([]);
  const [summary, setSummary] = useState(null);
  const [groups, setGroups] = useState([]);
  const [selected, setSelected] = useState([]);
  const [busy, setBusy] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState(null);

  const load = useCallback(async () => {
    if (!allowed) return;
    setBusy(true);
    setErr("");
    try {
      const data = await api.reviewScheduleRouteOptimize(orderIds);
      setRows(data.rows || []);
      setSummary(data.summary || null);
      setGroups(data.groups || []);
      setSelected(selectAllEligible(data.rows || []));
    } catch (e) {
      setErr(formatApiError(e, "Gagal memuat review optimasi."));
      setRows([]);
    } finally {
      setBusy(false);
    }
  }, [allowed, orderIds]);

  useEffect(() => {
    load();
  }, [load]);

  const selectionSummary = useMemo(() => summarizeSelection(rows, selected), [rows, selected]);

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

  return (
    <div className="mop-schedule-root mop-delivery-review-root">
      <MobileTopBar kicker="ETERNAL PACKAGING" title="Review Pengiriman Terpilih" />
      <main className="mop-content mop-schedule-page mds-review-page" data-testid="mds-review-page">
        {!allowed && (
          <div className="mop-error" data-testid="mds-review-forbidden">
            <p>Anda tidak punya izin optimasi rute.</p>
            <Link to={MOBILE_DELIVERY_SCHEDULE_PATH} className="mop-btn-ghost">Kembali</Link>
          </div>
        )}
        {allowed && (
          <>
            <div className="mds-review-toolbar">
              <Link to={MOBILE_DELIVERY_SCHEDULE_PATH} className="mop-btn-ghost" data-testid="mds-review-back">
                Kembali
              </Link>
              <button
                type="button"
                className="mop-btn-ghost"
                data-testid="mds-review-select-all"
                onClick={() => setSelected(selectAllEligible(rows))}
                disabled={busy}
              >
                Pilih Semua
              </button>
              <button
                type="button"
                className="mop-btn-ghost"
                data-testid="mds-review-exclude"
                onClick={() => setSelected(excludeFromSelection(selected, selected))}
                disabled={!selected.length}
              >
                Keluarkan dari Pilihan
              </button>
            </div>

            {busy && <p className="mop-empty" data-testid="mds-review-loading">Memuat review...</p>}
            {err ? <div className="mop-error" data-testid="mds-review-error">{err}</div> : null}

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
                        <span>Driver: {row.driver_name || "—"}</span>
                        <span>Tanggal: {formatDate(row.effective_delivery_date, { dateOnly: true })}</span>
                        <span>{row.optimization_status_label}</span>
                      </div>
                      {!row.eligible && (
                        <div className="mds-review-reason" data-testid={`mds-review-reason-${id}`}>
                          {row.reason || (row.reasons || []).join(", ")}
                        </div>
                      )}
                    </div>
                    <button
                      type="button"
                      className="mop-btn-ghost sm"
                      data-testid={`mds-review-exclude-${id}`}
                      onClick={() => setSelected((prev) => excludeFromSelection(prev, [id]))}
                    >
                      Keluarkan
                    </button>
                  </div>
                );
              })}
              {!busy && rows.length === 0 && (
                <p className="mop-empty" data-testid="mds-review-empty">Tidak ada pengiriman dalam filter aktif.</p>
              )}
            </div>

            <div className="mds-review-actions">
              <button
                type="button"
                className="mop-btn-primary"
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
                      <Link to={`/optimasi-rute/${encodeURIComponent(g.route_id)}`} data-testid={`mds-review-open-${g.route_id}`}>
                        Lihat Rute
                      </Link>
                    ) : null}
                  </div>
                ))}
                <button
                  type="button"
                  className="mop-btn-ghost"
                  data-testid="mds-review-done"
                  onClick={() => navigate(MOBILE_DELIVERY_SCHEDULE_PATH)}
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
