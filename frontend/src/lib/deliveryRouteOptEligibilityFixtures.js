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
  const keyOf = (s) => String(s.assignment_id || s.shipment_id || s.id || "");
  const urgent = stops.filter((s) => s.urgent).sort((a, b) => keyOf(a).localeCompare(keyOf(b)));
  const normal = stops.filter((s) => !s.urgent).sort((a, b) => keyOf(a).localeCompare(keyOf(b)));
  return [...urgent, ...normal].map((s, idx) => ({
    ...s,
    id: keyOf(s),
    assignment_id: keyOf(s),
    sales_order_id: s.sales_order_id,
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
  sjUnits = null,
  sjLoadQty = null,
}) {
  const units = Array.isArray(sjUnits)
    ? sjUnits
    : (sjLabel && sjLabel !== "—"
      ? [{ shipment_id: `sh-${id}`, sj_number: sjLabel, sj_load_qty: 10000 }]
      : []);
  const load = sjLoadQty != null
    ? Number(sjLoadQty)
    : units.reduce((sum, u) => sum + Number(u.sj_load_qty || 0), 0);
  return {
    sales_order_id: id,
    delivery_id: id,
    order_number: po,
    customer_name: customer,
    sj_label: sjLabel,
    sj_numbers: units.map((u) => u.sj_number).filter(Boolean),
    shipment_ids: units.map((u) => u.shipment_id).filter(Boolean),
    sj_units: units,
    sj_load_qty: eligible ? load : 0,
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

export function expandPreviewRowsToSjAtoms(rows) {
  const atoms = [];
  const seen = new Set();
  (rows || []).forEach((r) => {
    const units = (Array.isArray(r.sj_units) ? r.sj_units : []).filter(
      (u) => u && String(u.shipment_id || "").trim() && Number(u.sj_load_qty || 0) > 0,
    );
    if (!units.length) {
      const id = String(r.sales_order_id || r.id || "").trim();
      if (!id || seen.has(id)) return;
      seen.add(id);
      atoms.push({
        ...r,
        id,
        assignment_id: id,
        shipment_id: r.shipment_id || null,
        sj_load_qty: 0,
        sj_units: [],
        shipment_ids: [],
      });
      return;
    }
    units.forEach((u) => {
      const sid = String(u.shipment_id).trim();
      if (!sid || seen.has(sid)) return;
      seen.add(sid);
      atoms.push({
        ...r,
        id: sid,
        assignment_id: sid,
        shipment_id: sid,
        sj_number: u.sj_number || sid,
        sj_label: u.sj_number || sid,
        sj_load_qty: Number(u.sj_load_qty || 0),
        sj_units: [u],
        shipment_ids: [sid],
      });
    });
  });
  return atoms;
}

/**
 * Exactly 3 representative dummy records for capacity SJ atomicity preview.
 * DUMMY 1: one SJ that fits Armada 1.
 * DUMMY 2: same PO, two different SJ that may go to different armadas.
 * DUMMY 3: one SJ larger than both vehicle capacities → Belum Teralokasi.
 */
export function getRouteOptEligibilityPreviewReview() {
  const rows = [
    row({
      id: "preview-cap-d1",
      po: "PO-DUMMY-1",
      customer: "Dummy Toko Satu SJ",
      sjLabel: "SJ-DUMMY-1",
      eligible: true,
      reasons: [],
      lat: -6.12,
      lng: 106.88,
      address: "Jl Preview Jakarta Utara No 1",
      area: "Jakarta Utara",
      sjUnits: [{ shipment_id: "sj-dummy-1", sj_number: "SJ-DUMMY-1", sj_load_qty: 10000 }],
      sjLoadQty: 10000,
    }),
    row({
      id: "preview-cap-d2",
      po: "PO-DUMMY-2",
      customer: "Dummy Toko Dua SJ",
      sjLabel: "SJ-DUMMY-2A, SJ-DUMMY-2B",
      eligible: true,
      reasons: [],
      lat: -6.91,
      lng: 107.61,
      address: "Jl Preview Bandung No 2",
      area: "Bandung",
      sjUnits: [
        { shipment_id: "sj-dummy-2a", sj_number: "SJ-DUMMY-2A", sj_load_qty: 30000 },
        { shipment_id: "sj-dummy-2b", sj_number: "SJ-DUMMY-2B", sj_load_qty: 20000 },
      ],
      sjLoadQty: 50000,
    }),
    row({
      id: "preview-cap-d3",
      po: "PO-DUMMY-3",
      customer: "Dummy Toko Overflow",
      sjLabel: "SJ-DUMMY-3",
      eligible: true,
      reasons: [],
      lat: -7.25,
      lng: 112.75,
      address: "Jl Preview Surabaya No 3",
      area: "Surabaya",
      sjUnits: [{ shipment_id: "sj-dummy-3", sj_number: "SJ-DUMMY-3", sj_load_qty: 50000 }],
      sjLoadQty: 50000,
    }),
  ];

  const eligible = rows.filter((r) => r.eligible);
  return {
    rows,
    summary: {
      selected: rows.length,
      eligible: eligible.length,
      ineligible: rows.length - eligible.length,
      drivers: 0,
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
  const shipmentIds = stops.map((s) => String(s.shipment_id || s.assignment_id || s.id || "")).filter(Boolean);
  const poIds = [];
  const seenPo = new Set();
  stops.forEach((s) => {
    const oid = String(s.sales_order_id || "");
    if (oid && !seenPo.has(oid)) {
      seenPo.add(oid);
      poIds.push(oid);
    }
  });
  return {
    label,
    fleet_key: fleetKey,
    order_ids: poIds,
    shipment_ids: shipmentIds,
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
  const atoms = expandPreviewRowsToSjAtoms(eligible);
  const count = Number(fleetCount);
  if (count !== 1 && count !== 2) {
    throw new Error("fleet_count harus 1 atau 2.");
  }
  if (count === 1) {
    if (atoms.length < 1) {
      throw new Error("Optimasi 1 Armada membutuhkan minimal 1 Surat Jalan eligible.");
    }
    const ordered = orderUrgentFirst(atoms);
    const fleets = [fleetPanel("Armada 1", ordered)];
    const fingerprint = `fixture-fp-1-${ordered.map((s) => s.assignment_id || s.id).join(",")}`;
    return {
      ok: true,
      preview: true,
      preview_fingerprint: fingerprint,
      fleet_count: 1,
      eligible_order_ids: eligible.map((s) => s.sales_order_id),
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
  if (atoms.length < 2) {
    throw new Error("Optimasi 2 Armada membutuhkan minimal 2 Surat Jalan eligible.");
  }
  const parted = partitionPreviewStopsIntoTwoFleets(
    atoms.map((r) => ({
      id: r.assignment_id || r.id,
      lat: r.lat,
      lng: r.lng,
      urgent: r.urgent,
      ...r,
    })),
  );
  const a1 = orderUrgentFirst(parted.armada_1);
  const a2 = orderUrgentFirst(parted.armada_2);
  const fleets = [fleetPanel("Armada 1", a1), fleetPanel("Armada 2", a2)];
  const fingerprint = `fixture-fp-2-${a1.map((s) => s.assignment_id || s.id).join(",")}|${a2.map((s) => s.assignment_id || s.id).join(",")}`;
  return {
    ok: true,
    preview: true,
    preview_fingerprint: fingerprint,
    fleet_count: 2,
    eligible_order_ids: eligible.map((s) => s.sales_order_id),
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
  const cap = getFixtureArmadaCapacity();
  if (cap.armada_1_capacity_qty_sj == null || cap.armada_2_capacity_qty_sj == null) {
    throw new Error("Kapasitas armada belum disimpan. Simpan Kapasitas Qty SJ Armada 1 dan Armada 2 terlebih dahulu.");
  }
  const preview = buildRouteOptEligibilityPreviewFleets({ orderIds, fleetCount });
  const capacities = fleetCount === 1
    ? [cap.armada_1_capacity_qty_sj]
    : [cap.armada_1_capacity_qty_sj, cap.armada_2_capacity_qty_sj];
    const packed = applyFixtureCapacity(preview.fleets || [], capacities);
    const routes = packed.fleets.map((fleet, index) => ({
      route_id: `fixture-route-${index + 1}`,
      label: fleet.label,
      fleet_group_id: fleetCount === 2 ? "fixture-fleet-group" : null,
      order_ids: fleet.order_ids || [],
      shipment_ids: fleet.shipment_ids || [],
      stop_count: Number(fleet.total_orders || (fleet.shipment_ids || []).length || 0),
      urgent_count: Number(fleet.total_urgent || 0),
      sj_load_qty: Number(fleet.sj_load_qty || 0),
      sj_numbers: (fleet.stops || []).map((s) => s.sj_number || s.sj_label).filter(Boolean),
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
    unassigned: packed.unassigned,
    unassigned_reason: packed.unassigned.length ? "Kapasitas armada tidak mencukupi" : null,
    capacities_used: {
      armada_1_capacity_qty_sj: cap.armada_1_capacity_qty_sj,
      armada_2_capacity_qty_sj: fleetCount === 2 ? cap.armada_2_capacity_qty_sj : null,
      capacity_unit: "qty_surat_jalan",
    },
    routes,
    review: preview.review,
    summary: {
      ...preview.summary,
      created_route_count: routes.length,
      reused: false,
      unassigned_count: packed.unassigned.length,
    },
  };
}

let fixtureArmadaCapacity = {
  armada_1_capacity_qty_sj: 40000,
  armada_2_capacity_qty_sj: 20000,
};

export function getFixtureArmadaCapacity() {
  return { ...fixtureArmadaCapacity };
}

export function saveFixtureArmadaCapacity({ armada_1_capacity_qty_sj, armada_2_capacity_qty_sj } = {}) {
  fixtureArmadaCapacity = {
    armada_1_capacity_qty_sj,
    armada_2_capacity_qty_sj,
  };
  return {
    ...getFixtureArmadaCapacity(),
    capacity_unit: "qty_surat_jalan",
  };
}

function applyFixtureCapacity(fleets, capacities) {
  const remaining = capacities.map((c) => Number(c));
  const packed = capacities.map(() => []);
  const queued = [];
  const seen = new Set();
  fleets.forEach((fleet, fi) => {
    (fleet.stops || []).forEach((stop) => {
      const id = String(stop.assignment_id || stop.shipment_id || stop.id || "");
      if (!id || seen.has(id)) return;
      seen.add(id);
      queued.push({ preferred: Math.min(fi, remaining.length - 1), stop });
    });
  });
  const overflow = [];
  queued.forEach(({ preferred, stop }) => {
    const load = Number(stop.sj_load_qty || 0);
    if (load <= remaining[preferred]) {
      packed[preferred].push(stop);
      remaining[preferred] -= load;
    } else {
      overflow.push(stop);
    }
  });
  const unassigned = [];
  overflow.forEach((stop) => {
    const load = Number(stop.sj_load_qty || 0);
    let placed = false;
    for (let i = 0; i < remaining.length; i += 1) {
      if (load <= remaining[i]) {
        packed[i].push(stop);
        remaining[i] -= load;
        placed = true;
        break;
      }
    }
    if (!placed) {
      unassigned.push({
        sales_order_id: stop.sales_order_id,
        order_number: stop.order_number,
        customer_name: stop.customer_name,
        sj_label: stop.sj_label,
        sj_number: stop.sj_number || stop.sj_label,
        sj_units: stop.sj_units || [],
        sj_load_qty: load,
        shipment_id: stop.shipment_id || stop.assignment_id,
        assignment_id: stop.assignment_id || stop.shipment_id,
        shipment_ids: stop.shipment_ids || [stop.shipment_id || stop.assignment_id].filter(Boolean),
        reason: "Kapasitas armada tidak mencukupi",
        unassigned_reason: "Kapasitas armada tidak mencukupi",
      });
    }
  });
  const nextFleets = fleets.map((fleet, i) => {
    const stops = packed[i] || [];
    const shipmentIds = stops.map((s) => String(s.shipment_id || s.assignment_id || s.id || "")).filter(Boolean);
    const poIds = [];
    const seenPo = new Set();
    stops.forEach((s) => {
      const oid = String(s.sales_order_id || "");
      if (oid && !seenPo.has(oid)) {
        seenPo.add(oid);
        poIds.push(oid);
      }
    });
    return {
      ...fleet,
      stops,
      order_ids: poIds,
      shipment_ids: shipmentIds,
      total_orders: stops.length,
      total_urgent: stops.filter((s) => s.urgent).length,
      sj_load_qty: stops.reduce((sum, s) => sum + Number(s.sj_load_qty || 0), 0),
    };
  }).filter((f) => (f.shipment_ids || []).length > 0);
  return { fleets: nextFleets, unassigned };
}
