/**
 * Development-only fixtures for Jadwal Pengiriman → Optimasi Armada (one-stage create+optimize).
 *
 * Activation requires BOTH:
 *   - process.env.NODE_ENV === "development"
 *   - query param previewRouteOptEligibility=1
 *
 * Never writes backend/DB/localStorage/sessionStorage/IndexedDB.
 * Never calls review/create/optimize/storage API endpoints.
 */

export const ROUTE_OPT_ELIGIBILITY_PREVIEW_PARAM = "previewRouteOptEligibility";

export function isRouteOptEligibilityPreviewEnabled(search) {
  if (process.env.NODE_ENV !== "development") return false;
  const raw = String(search || "");
  const normalized = raw.startsWith("?") ? raw.slice(1) : raw;
  const params = new URLSearchParams(normalized);
  return params.get(ROUTE_OPT_ELIGIBILITY_PREVIEW_PARAM) === "1";
}

/** Deterministic dummy drivers — never preselected. */
export function getRouteOptEligibilityPreviewDrivers() {
  return [
    { id: "drv-preview-andi", name: "PREVIEW Driver Andi", email: "andi.preview@example.com", role: "driver" },
    { id: "drv-preview-budi", name: "PREVIEW Driver Budi", email: "budi.preview@example.com", role: "driver" },
    { id: "drv-preview-citra", name: "PREVIEW Driver Citra", email: "citra.preview@example.com", role: "driver" },
  ];
}

function haversineM(a, b) {
  const R = 6371000;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

function farthestPair(stops) {
  const ordered = [...stops].sort((x, y) => String(x.id).localeCompare(String(y.id)));
  let best = null;
  for (let i = 0; i < ordered.length; i += 1) {
    for (let j = i + 1; j < ordered.length; j += 1) {
      const d = haversineM(ordered[i], ordered[j]);
      const key = [d, ordered[i].id, ordered[j].id];
      if (!best || key[0] > best[0] || (key[0] === best[0] && (key[1] > best[1] || (key[1] === best[1] && key[2] > best[2])))) {
        best = [d, ordered[i].id, ordered[j].id, ordered[i], ordered[j]];
      }
    }
  }
  const a = best[3];
  const b = best[4];
  return String(a.id) <= String(b.id) ? [a, b] : [b, a];
}

function farthestFrom(seed, candidates) {
  const ordered = [...candidates].sort((x, y) => String(x.id).localeCompare(String(y.id)));
  let best = ordered[0];
  let bestKey = [haversineM(seed, best), best.id];
  for (const c of ordered.slice(1)) {
    const key = [haversineM(seed, c), c.id];
    if (key[0] > bestKey[0] || (key[0] === bestKey[0] && key[1] > bestKey[1])) {
      best = c;
      bestKey = key;
    }
  }
  return best;
}

function nearestMemberDist(fleet, stop) {
  return Math.min(...fleet.map((m) => haversineM(stop, m)));
}

function chooseFleet(stop, fleet1, fleet2) {
  const c1 = nearestMemberDist(fleet1, stop);
  const c2 = nearestMemberDist(fleet2, stop);
  if (Math.abs(c1 - c2) < 1e-6) return 1;
  return c1 < c2 ? 1 : 2;
}

/** Local urgent-seeded geographic partition (fixture-only mirror). */
export function partitionPreviewStopsIntoTwoFleets(stops) {
  const cleaned = [];
  const seen = new Set();
  for (const s of stops || []) {
    const id = String(s.id || "").trim();
    if (!id || seen.has(id)) continue;
    if (!Number.isFinite(Number(s.lat)) || !Number.isFinite(Number(s.lng))) continue;
    seen.add(id);
    cleaned.push({ ...s, id, lat: Number(s.lat), lng: Number(s.lng), urgent: Boolean(s.urgent) });
  }
  if (cleaned.length < 2) {
    throw new Error("Optimasi Armada membutuhkan minimal 2 PO eligible.");
  }
  if (cleaned.length === 2) {
    const [a, b] = [...cleaned].sort((x, y) => String(x.id).localeCompare(String(y.id)));
    return { armada_1: [a], armada_2: [b] };
  }
  const urgents = cleaned.filter((s) => s.urgent);
  let seed1;
  let seed2;
  if (urgents.length >= 2) {
    [seed1, seed2] = farthestPair(urgents);
  } else if (urgents.length === 1) {
    seed1 = urgents[0];
    seed2 = farthestFrom(seed1, cleaned.filter((s) => s.id !== seed1.id));
  } else {
    [seed1, seed2] = farthestPair(cleaned);
  }
  const fleet1 = [seed1];
  const fleet2 = [seed2];
  const assigned = new Set([seed1.id, seed2.id]);
  const remainingU = cleaned.filter((s) => s.urgent && !assigned.has(s.id)).sort((a, b) => a.id.localeCompare(b.id));
  const remainingN = cleaned.filter((s) => !s.urgent && !assigned.has(s.id)).sort((a, b) => a.id.localeCompare(b.id));
  for (const stop of [...remainingU, ...remainingN]) {
    if (chooseFleet(stop, fleet1, fleet2) === 1) fleet1.push(stop);
    else fleet2.push(stop);
  }
  return { armada_1: fleet1, armada_2: fleet2 };
}

function orderUrgentFirst(stops) {
  const keyOf = (s) => String(s.id || s.sales_order_id || "");
  const urgent = stops.filter((s) => s.urgent).sort((a, b) => keyOf(a).localeCompare(keyOf(b)));
  const normal = stops.filter((s) => !s.urgent).sort((a, b) => keyOf(a).localeCompare(keyOf(b)));
  return [...urgent, ...normal].map((s, idx) => ({
    ...s,
    id: keyOf(s),
    sales_order_id: keyOf(s),
    stop_sequence: idx + 1,
    priority_id: s.urgent ? "p1" : "p3",
  }));
}

function row({
  id,
  po,
  customer,
  sjLabel,
  eligible,
  reasons,
  lat,
  lng,
  urgent = false,
  driverUserId = null,
  driverName = null,
  address = "",
  area = "",
}) {
  return {
    sales_order_id: id,
    delivery_id: id,
    order_number: po,
    customer_name: customer,
    sj_label: sjLabel,
    sj_numbers: sjLabel && sjLabel !== "—" ? [sjLabel] : [],
    eligible,
    reasons: reasons || [],
    reason: reasons && reasons.length ? reasons[0] : null,
    lat: lat ?? null,
    lng: lng ?? null,
    address: address || "",
    area: area || "",
    urgent: Boolean(urgent),
    priority: urgent ? "urgent" : "normal",
    driver_user_id: driverUserId || null,
    driver_name: driverName || null,
    optimization_status: "belum_dioptimasi",
    optimization_status_label: "Belum Dioptimasi",
    effective_delivery_date: "2026-09-15",
  };
}

/**
 * ~20 deterministic PO across 4 geographic clusters, ≥5 urgent,
 * mixed SJ/location ineligibles, no preselected route drivers.
 */
export function getRouteOptEligibilityPreviewReview() {
  const clusters = [
    { area: "Jakarta Utara", lat: -6.12, lng: 106.88 },
    { area: "Bandung", lat: -6.91, lng: 107.61 },
    { area: "Surabaya", lat: -7.25, lng: 112.75 },
    { area: "Padang", lat: -0.95, lng: 100.35 },
  ];
  const rows = [];
  let n = 0;
  const urgentSlots = new Set([1, 6, 11, 16, 2]); // 5 urgent across clusters
  for (let c = 0; c < 4; c += 1) {
    for (let j = 0; j < 4; j += 1) {
      n += 1;
      const id = `preview-po-${String(n).padStart(2, "0")}`;
      const urgent = urgentSlots.has(n);
      rows.push(row({
        id,
        po: `PO-PREVIEW-${String(n).padStart(3, "0")}`,
        customer: `Preview Toko ${clusters[c].area} ${j + 1}`,
        sjLabel: `SJ-PREVIEW-${String(n).padStart(3, "0")}`,
        eligible: true,
        reasons: [],
        lat: clusters[c].lat + j * 0.012,
        lng: clusters[c].lng + j * 0.012,
        urgent,
        address: `Jl Preview ${clusters[c].area} No ${j + 1}`,
        area: clusters[c].area,
        driverUserId: n === 3 ? "drv-existing-order" : null,
        driverName: n === 3 ? "Existing Order Driver" : null,
      }));
    }
  }
  // Ineligibles
  rows.push(row({
    id: "preview-inelig-bad-sj",
    po: "PO-PREVIEW-017",
    customer: "Preview Toko SJ Incomplete",
    sjLabel: "—",
    eligible: false,
    reasons: ["Surat Jalan belum siap."],
    lat: -6.2,
    lng: 106.8,
    address: "Jl Preview SJ Incomplete",
    area: "Jakarta",
  }));
  rows.push(row({
    id: "preview-inelig-bad-loc",
    po: "PO-PREVIEW-018",
    customer: "Preview Toko Lokasi Invalid",
    sjLabel: "SJ-PREVIEW-018",
    eligible: false,
    reasons: ["Koordinat belum tersedia"],
    lat: null,
    lng: null,
    address: "Jl Preview tanpa koordinat",
    area: "Jakarta",
  }));
  rows.push(row({
    id: "preview-inelig-both",
    po: "PO-PREVIEW-019",
    customer: "Preview Toko SJ+Lokasi",
    sjLabel: "—",
    eligible: false,
    reasons: ["Surat Jalan belum siap.", "Koordinat belum tersedia"],
    lat: null,
    lng: null,
    address: "",
    area: "",
  }));
  rows.push(row({
    id: "preview-inelig-delivered",
    po: "PO-PREVIEW-020",
    customer: "Preview Toko Terkirim",
    sjLabel: "SJ-PREVIEW-020",
    eligible: false,
    reasons: ["Sudah terkirim"],
    lat: -6.22,
    lng: 106.82,
    address: "Jl Preview Terkirim",
    area: "Jakarta",
  }));

  const eligible = rows.filter((r) => r.eligible);
  return {
    rows,
    summary: {
      selected: rows.length,
      eligible: eligible.length,
      ineligible: rows.length - eligible.length,
      drivers: 1,
      routes_to_create: 0,
    },
    groups: [],
    preview: true,
    preview_note: "Development preview only — no production mutation.",
  };
}


function mapsDirUrl(stops) {
  const pts = (stops || []).filter((s) => Number.isFinite(Number(s.lat)) && Number.isFinite(Number(s.lng)));
  if (!pts.length) return "";
  if (pts.length === 1) return `https://www.google.com/maps?q=${Number(pts[0].lat)},${Number(pts[0].lng)}`;
  return `https://www.google.com/maps/dir/${pts.map((s) => `${Number(s.lat)},${Number(s.lng)}`).join("/")}`;
}

function fleetPanel(label, orderedStops) {
  const stops = (orderedStops || []).map((s, idx) => ({
    ...s,
    stop_sequence: idx + 1,
    priority_id: s.urgent ? "p1" : "p3",
  }));
  const fleetKey = String(label || "").toLowerCase().includes("2") ? "armada_2" : "armada_1";
  return {
    label,
    fleet_key: fleetKey,
    order_ids: stops.map((s) => s.sales_order_id),
    stops,
    total_orders: stops.length,
    total_urgent: stops.filter((s) => s.urgent).length,
    estimated_distance_m: stops.length * 1200,
    segments: [],
    strategy: "NEAREST",
    google_maps_url: mapsDirUrl(stops),
  };
}

/** Local non-persistent preview for 1 or 2 armada (fixture mode only). */
export function buildRouteOptEligibilityPreviewFleets({ orderIds, fleetCount = 2 } = {}) {
  const review = getRouteOptEligibilityPreviewReview();
  const selected = new Set((orderIds || []).map(String));
  const eligible = review.rows.filter((r) => selected.has(String(r.sales_order_id)) && r.eligible);
  const ineligible = review.rows.filter((r) => selected.has(String(r.sales_order_id)) && !r.eligible);
  const count = Number(fleetCount);
  if (count !== 1 && count !== 2) {
    throw new Error("fleet_count harus 1 atau 2.");
  }
  if (count === 1) {
    if (eligible.length < 1) {
      throw new Error("Optimasi 1 Armada membutuhkan minimal 1 PO eligible.");
    }
    const ordered = orderUrgentFirst(eligible);
    const fleets = [fleetPanel("Armada 1", ordered)];
    const fingerprint = `fixture-fp-1-${ordered.map((s) => s.sales_order_id).join(",")}`;
    return {
      ok: true,
      preview: true,
      preview_fingerprint: fingerprint,
      fleet_count: 1,
      eligible_order_ids: ordered.map((s) => s.sales_order_id),
      excluded_rows: ineligible,
      fleets,
      review,
      summary: {
        ...review.summary,
        eligible: eligible.length,
        fleet_count: 1,
        fleet_order_counts: [ordered.length],
      },
    };
  }
  if (eligible.length < 2) {
    throw new Error("Optimasi 2 Armada membutuhkan minimal 2 PO eligible.");
  }
  const parted = partitionPreviewStopsIntoTwoFleets(
    eligible.map((r) => ({
      id: r.sales_order_id,
      lat: r.lat,
      lng: r.lng,
      urgent: r.urgent,
      ...r,
    })),
  );
  const a1 = orderUrgentFirst(parted.armada_1);
  const a2 = orderUrgentFirst(parted.armada_2);
  const fleets = [fleetPanel("Armada 1", a1), fleetPanel("Armada 2", a2)];
  const fingerprint = `fixture-fp-2-${a1.map((s) => s.sales_order_id).join(",")}|${a2.map((s) => s.sales_order_id).join(",")}`;
  return {
    ok: true,
    preview: true,
    preview_fingerprint: fingerprint,
    fleet_count: 2,
    eligible_order_ids: [...a1, ...a2].map((s) => s.sales_order_id),
    excluded_rows: ineligible,
    fleets,
    review,
    summary: {
      ...review.summary,
      eligible: eligible.length,
      fleet_count: 2,
      fleet_order_counts: [a1.length, a2.length],
    },
  };
}


/** Removed from current preview-only flow — kept only to fail loudly if miswired. */
export function buildRouteOptEligibilityPreviewFleetPairResult() {
  throw new Error("create-fleet-pair flow retired — use createFleets only.");
}



/** Local one-stage create+optimize simulation (fixture mode only; zero persistence). */
export function buildRouteOptEligibilityCreateFleets({ orderIds, fleetCount = 2 } = {}) {
  const preview = buildRouteOptEligibilityPreviewFleets({ orderIds, fleetCount });
  const routes = (preview.fleets || []).map((fleet, index) => ({
    route_id: `fixture-route-${index + 1}`,
    label: fleet.label,
    fleet_group_id: fleetCount === 2 ? "fixture-fleet-group" : null,
    order_ids: fleet.order_ids || [],
    stop_count: Number(fleet.total_orders || (fleet.order_ids || []).length || 0),
    urgent_count: Number(fleet.total_urgent || 0),
    estimated_distance_m: fleet.estimated_distance_m ?? null,
    status: "OPTIMIZED",
  }));
  return {
    ok: true,
    created: true,
    reused: false,
    preview: false,
    persisted: false,
    fixture: true,
    fleet_count: preview.fleet_count,
    created_route_count: routes.length,
    fleet_group_id: fleetCount === 2 ? "fixture-fleet-group" : null,
    schedule_input_fingerprint: preview.preview_fingerprint,
    eligible_order_ids: preview.eligible_order_ids,
    excluded_rows: preview.excluded_rows,
    routes,
    review: preview.review,
    summary: {
      ...preview.summary,
      created_route_count: routes.length,
      reused: false,
    },
  };
}
