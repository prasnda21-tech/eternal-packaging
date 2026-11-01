import { formatTrackingPageDate } from "./constants";

export const CUSTOMER_STATUS_LABEL = {
  completed: "Selesai",
  in_progress: "Proses",
  waiting: "Menunggu",
};

export const STAGE_SHORT = {
  "Order Masuk": "Order Masuk",
  Design: "Design",
  "Approval Design": "Approval Design",
  Payment: "Payment",
  "Approval Payment": "Approval Payment",
  Penjadwalan: "Penjadwalan",
  Screener: "Screener",
  "Persiapan Helper": "Persiapan",
  Operator: "Operator",
  "Konfirmasi Sample": "Konfirmasi Sample",
  Cetak: "Cetak",
  Packing: "Packing",
  "Surat Jalan": "Surat Jalan",
  Loading: "Loading",
  Pengiriman: "Pengiriman",
};

export const RAIL_DISPLAY_ORDER = [
  "Order Masuk",
  "Design",
  "Approval Design",
  "Payment",
  "Approval Payment",
  "Penjadwalan",
  "Screener",
  "Persiapan Helper",
  "Operator",
  "Konfirmasi Sample",
  "Cetak",
  "Packing",
  "Surat Jalan",
  "Loading",
  "Pengiriman",
];

const RAIL_XS = [10, 30, 50, 70, 90];
const RAIL_YS = [15.6, 50, 84.4];
export const RAIL_SLOTS = [
  { x: RAIL_XS[0], y: RAIL_YS[0] },
  { x: RAIL_XS[1], y: RAIL_YS[0] },
  { x: RAIL_XS[2], y: RAIL_YS[0] },
  { x: RAIL_XS[3], y: RAIL_YS[0] },
  { x: RAIL_XS[4], y: RAIL_YS[0] },
  { x: RAIL_XS[4], y: RAIL_YS[1] },
  { x: RAIL_XS[3], y: RAIL_YS[1] },
  { x: RAIL_XS[2], y: RAIL_YS[1] },
  { x: RAIL_XS[1], y: RAIL_YS[1] },
  { x: RAIL_XS[0], y: RAIL_YS[1] },
  { x: RAIL_XS[0], y: RAIL_YS[2] },
  { x: RAIL_XS[1], y: RAIL_YS[2] },
  { x: RAIL_XS[2], y: RAIL_YS[2] },
  { x: RAIL_XS[3], y: RAIL_YS[2] },
  { x: RAIL_XS[4], y: RAIL_YS[2] },
];

export const RAIL_SEG_PATHS = [
  "M100 56 H300",
  "M300 56 H500",
  "M500 56 H700",
  "M700 56 H900",
  "M900 56 C960 56 960 180 900 180",
  "M900 180 H700",
  "M700 180 H500",
  "M500 180 H300",
  "M300 180 H100",
  "M100 180 C40 180 40 304 100 304",
  "M100 304 H300",
  "M300 304 H500",
  "M500 304 H700",
  "M700 304 H900",
];

export const STAGE_BLURB = {
  "Order Masuk": "Pesanan diterima",
  Design: "Design sedang disiapkan",
  "Approval Design": "Menunggu persetujuan Anda",
  Payment: "Menunggu pembayaran",
  "Approval Payment": "Pembayaran sedang diperiksa",
  Penjadwalan: "Dalam antrean produksi",
  Screener: "Persiapan produksi",
  "Persiapan Helper": "Persiapan produksi",
  Operator: "Produksi berjalan",
  "Konfirmasi Sample": "Sample sedang dikonfirmasi",
  Cetak: "Proses cetak",
  Packing: "Pesanan sedang dipacking",
  "Surat Jalan": "Dokumen pengiriman disiapkan",
  Loading: "Pesanan sedang dimuat",
  Pengiriman: "Pesanan dalam proses pengiriman",
};

const APPROVAL_PATH_PREFIX = "/customer/design-approval/";

export function trackingStatusLabel(status) {
  return CUSTOMER_STATUS_LABEL[status] || "Menunggu";
}

/** Customer-facing Estimasi Pengiriman from public summary.estimasi (never legacy deadline). */
export function formatEstimasiPengiriman(estimasi, formatDateFn = formatTrackingDate) {
  const est = estimasi && typeof estimasi === "object" ? estimasi : null;
  if (!est) {
    return { text: "—", displayState: "empty", source: "non_final", final: false };
  }
  const displayState = String(est.display_state || "");
  if (displayState === "menunggu_approval" || (est.final !== true && displayState !== "belum_dijadwalkan" && !est.date)) {
    return {
      text: "Menunggu Approval",
      displayState: "menunggu_approval",
      source: "non_final",
      final: false,
    };
  }
  if (displayState === "belum_dijadwalkan" || (est.source === "non_final" && !est.date && displayState !== "menunggu_approval")) {
    return {
      text: "Belum Dijadwalkan",
      displayState: "belum_dijadwalkan",
      source: "non_final",
      final: false,
    };
  }
  if (est.final && est.date) {
    return {
      text: formatDateFn(est.date) || "—",
      displayState: "final",
      source: est.source === "urgent_manual" ? "urgent_manual" : "automatic",
      final: true,
      date: est.date,
    };
  }
  return {
    text: "Menunggu Approval",
    displayState: "menunggu_approval",
    source: "non_final",
    final: false,
  };
}

export function trackingDisplayLabel(row) {
  const label = row?.label != null ? String(row.label).trim() : "";
  if (label) return label;
  return trackingStatusLabel(row?.status);
}

export function trackingStatusClass(status) {
  if (status === "completed") return "tracking-badge is-done";
  if (status === "in_progress") return "tracking-badge is-run";
  return "tracking-badge is-wait";
}

export function stageBlurb(stage, row, designApproval) {
  if (stage === "Approval Design") {
    const status = designApproval?.status;
    if (!status) return "Menunggu link approval";
    if (status === "active") return "Menunggu persetujuan Anda";
    if (status === "approved") return "Design disetujui";
    if (status === "rejected") return trackingDisplayLabel(row);
    return trackingDisplayLabel(row);
  }
  const fromMap = STAGE_BLURB[stage];
  if (fromMap) return fromMap;
  return trackingDisplayLabel(row);
}

export function currentHeadline(workflow, designApproval) {
  if (designApproval?.status === "active") return "Sedang Approval Design";
  const rows = Array.isArray(workflow) ? workflow : [];
  const running = rows.find((r) => r.status === "in_progress");
  if (running?.stage) return `Sedang ${running.stage}`;
  if (rows.length && rows.every((r) => r.status === "completed")) return "Selesai";
  const waiting = rows.find((r) => r.status === "waiting");
  if (waiting?.stage) return `Menunggu ${waiting.stage}`;
  return "Pesanan diproses";
}

export function approvalActionPath(designApproval) {
  const path = String(designApproval?.path || "").trim();
  if (designApproval?.status !== "active") return "";
  if (!path.startsWith(APPROVAL_PATH_PREFIX)) return "";
  if (path.includes("?") || path.includes("#")) return "";
  return path;
}

export function hasApprovalCta(designApproval) {
  return Boolean(approvalActionPath(designApproval));
}

export function formatTrackingDate(value) {
  return formatTrackingPageDate(value);
}

export function formatItemQty(item) {
  if (item?.quantity == null || item.quantity === "") return "—";
  const n = Number(item.quantity);
  const qty = Number.isFinite(n) ? n.toLocaleString("id-ID") : String(item.quantity);
  return item.unit ? `${qty} ${item.unit}` : qty;
}

export function formatTrackingDateShort(value) {
  return formatTrackingPageDate(value);
}

export function stageShortLabel(stage) {
  return STAGE_SHORT[stage] || String(stage || "").split(" ")[0] || stage;
}

export function railTone(status) {
  if (status === "completed") return "done";
  if (status === "in_progress") return "run";
  if (status === "rejected" || status === "problem") return "danger";
  return "wait";
}

export function segmentTone(fromStatus, toStatus) {
  if (fromStatus === "rejected" || toStatus === "rejected") return "danger";
  if (fromStatus === "completed" && toStatus === "completed") return "done";
  if (fromStatus === "completed" && toStatus === "in_progress") return "run";
  if (fromStatus === "in_progress" && toStatus === "in_progress") return "run";
  return "wait";
}

function pickStage(by, name) {
  return by[name] || { stage: name, status: "waiting" };
}

export function workflowRailModel(workflow) {
  const rows = Array.isArray(workflow) ? workflow : [];
  const by = Object.fromEntries(rows.map((r) => [r.stage, r]));
  const nodes = RAIL_DISPLAY_ORDER.map((stage, i) => {
    const row = pickStage(by, stage);
    const slot = RAIL_SLOTS[i];
    return {
      stage: row.stage,
      status: row.status,
      label: row.label,
      displayIndex: i,
      x: slot.x,
      y: slot.y,
    };
  });
  const segments = RAIL_SEG_PATHS.map((d, i) => ({
    d,
    from: nodes[i].stage,
    to: nodes[i + 1].stage,
    tone: segmentTone(nodes[i].status, nodes[i + 1].status),
  }));
  return {
    branched: false,
    displayOrder: RAIL_DISPLAY_ORDER.slice(),
    nodes,
    segments,
  };
}

export function compactItemSummary(items) {
  const list = Array.isArray(items) ? items : [];
  if (!list.length) return { item: "—", qty: "—" };
  if (list.length === 1) return { item: itemLineLabel(list[0]), qty: formatItemQty(list[0]) };
  return { item: `${list.length} item`, qty: `${list.length} baris` };
}

export function documentTiles(data) {
  const designDocs = data?.documents?.design_approved || [];
  const sjDocs = data?.documents?.surat_jalan || [];
  const sjPreview = sjDocs[0]?.preview_url || "";
  const evidence = data?.shipments_evidence || [];
  const proofs = (data?.delivery_proofs || []).slice(0, 5);
  const approval = data?.design_approval;
  const approvalPath = approvalActionPath(approval);
  const designHref = designDocs[0]?.preview_url || approval?.preview_url || "";
  const designAvail = Boolean(designHref || approvalPath);
  const shipHref = proofs[0]?.url || evidence.find((ev) => ev?.photos?.[0]?.url)?.photos?.[0]?.url || "";
  const shipAvail = Boolean(shipHref);
  return [
    {
      key: "design",
      label: "Design",
      available: designAvail,
      status: designAvail ? "Tersedia" : "Belum tersedia",
      href: designHref || approvalPath,
      image: String(designDocs[0]?.content_type || "").startsWith("image/") || Boolean(approval?.preview_url),
      kind: "design",
      approval: Boolean(approvalPath),
    },
    {
      key: "payment",
      label: "Pembayaran",
      available: false,
      status: "Belum tersedia",
      href: "",
      kind: "payment",
    },
    {
      key: "sj",
      label: "Surat Jalan",
      available: Boolean(sjPreview),
      status: sjDocs[0]?.has_signed ? "Bertanda tangan" : (sjPreview ? "Tersedia" : "Belum tersedia"),
      href: sjPreview,
      kind: "pdf",
    },
    {
      key: "ship",
      label: "Pengiriman",
      available: shipAvail,
      status: shipAvail ? "Tersedia" : "Belum tersedia",
      href: shipHref,
      image: true,
      kind: "photo",
    },
  ];
}

export function itemLineLabel(item) {
  const name = String(item?.name || "").trim() || "Item";
  const color = String(item?.color || "").trim();
  return color ? `${name} · ${color}` : name;
}

export function documentRows(data) {
  const rows = [];
  const designDocs = data?.documents?.design_approved || [];
  const approval = data?.design_approval;
  if (designDocs.length) {
    designDocs.forEach((doc, idx) => {
      rows.push({
        key: `design-${doc.id || idx}`,
        title: "Design / Approval",
        status: "Disetujui",
        available: true,
        actionLabel: "Lihat",
        href: doc.preview_url,
        kind: "design",
        image: String(doc.content_type || "").startsWith("image/"),
      });
    });
  } else if (approval?.status === "approved") {
    rows.push({
      key: "design-approved",
      title: "Design / Approval",
      status: "Disetujui",
      available: Boolean(approval.preview_url),
      actionLabel: approval.preview_url ? "Lihat" : "",
      href: approval.preview_url || "",
      kind: "design",
      image: Boolean(approval.preview_url),
    });
  } else if (approval?.status === "rejected") {
    rows.push({
      key: "design-rejected",
      title: "Design / Approval",
      status: "Revisi diminta",
      available: Boolean(approval.preview_url),
      actionLabel: approval.preview_url ? "Lihat" : "",
      href: approval.preview_url || "",
      kind: "design",
      image: Boolean(approval.preview_url),
      danger: true,
    });
  }

  const sjDocs = data?.documents?.surat_jalan || [];
  sjDocs.forEach((doc, idx) => {
    rows.push({
      key: `sj-${doc.id || idx}`,
      title: doc.sj_number || doc.name || "Surat Jalan",
      status: "Tersedia",
      available: Boolean(doc.preview_url),
      actionLabel: "Lihat PDF",
      href: doc.preview_url,
      kind: "pdf",
    });
  });

  const evidence = data?.shipments_evidence || [];
  const proofs = (data?.delivery_proofs || []).slice(0, 5);
  if (evidence.length) {
    evidence.forEach((ev, i) => {
      const photos = ev.photos || [];
      rows.push({
        key: `ev-${ev.confirmation_id || ev.shipment_id || i}`,
        title: "Bukti Pengiriman",
        status: photos.length || ev.recipient?.url ? "Tersedia" : "Belum tersedia",
        available: photos.length > 0 || Boolean(ev.recipient?.url),
        actionLabel: photos[0]?.url ? "Lihat Foto" : (ev.recipient?.url ? "Lihat" : ""),
        href: photos[0]?.url || ev.recipient?.url || "",
        kind: "photo",
        image: true,
      });
    });
  } else if (proofs.length) {
    proofs.forEach((p) => {
      rows.push({
        key: `proof-${p.id}`,
        title: "Bukti Pengiriman",
        status: "Tersedia",
        available: Boolean(p.url),
        actionLabel: "Lihat Foto",
        href: p.url,
        kind: "photo",
        image: true,
      });
    });
  }

  return rows;
}
