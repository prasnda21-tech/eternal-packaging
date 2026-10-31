/**
 * Deterministic local fixtures for Dashboard Keuangan / Produksi concept preview.
 * No API / production data. All rollups must stay internally consistent.
 */

export const BRANCHES = [
  { id: "all", label: "Semua cabang" },
  { id: "yogya", label: "Yogyakarta" },
  { id: "semarang", label: "Semarang" },
];

export const AS_OF_DATE = "2026-10-31";

/** Concept panel stages (not 1:1 with STATUS_PO_WORKFLOW_STAGES). */
export const CONCEPT_STAGES = [
  { key: "penjadwalan", label: "Penjadwalan" },
  { key: "sablon", label: "Sablon" },
  { key: "qc", label: "QC" },
  { key: "loading", label: "Loading" },
  { key: "pengiriman", label: "Pengiriman" },
];

const FINANCE_BASE = {
  all: {
    omzet: 780_000_000,
    hpp: 546_000_000,
    opex: 140_000_000,
    cashBank: 186_000_000,
    ar: {
      current: 120_000_000,
      d1_30: 72_000_000,
      d31_60: 38_000_000,
      d60_plus: 18_000_000,
    },
    followUps: [
      {
        id: "inv-1",
        customer: "CV Nusantara Plastik",
        invoiceNo: "INV-YG-2401",
        dueDate: "2026-10-10",
        remaining: 28_500_000,
        status: "late",
        lateDays: 21,
        branchId: "yogya",
      },
      {
        id: "inv-2",
        customer: "PT Mitra Kemasan Indo",
        invoiceNo: "INV-SM-1188",
        dueDate: "2026-10-25",
        remaining: 12_750_000,
        status: "late",
        lateDays: 6,
        branchId: "semarang",
      },
      {
        id: "inv-3",
        customer: "UD Berkah Printing",
        invoiceNo: "INV-YG-2410",
        dueDate: "2026-11-12",
        remaining: 9_200_000,
        status: "current",
        lateDays: 0,
        branchId: "yogya",
      },
      {
        id: "inv-4",
        customer: "PT Sari Wadah Abadi",
        invoiceNo: "INV-SM-1202",
        dueDate: "2026-11-05",
        remaining: 15_400_000,
        status: "current",
        lateDays: 0,
        branchId: "semarang",
      },
    ],
  },
  yogya: {
    omzet: 460_000_000,
    hpp: 322_000_000,
    opex: 82_000_000,
    cashBank: 112_000_000,
    ar: {
      current: 70_000_000,
      d1_30: 40_000_000,
      d31_60: 22_000_000,
      d60_plus: 10_000_000,
    },
  },
  semarang: {
    omzet: 320_000_000,
    hpp: 224_000_000,
    opex: 58_000_000,
    cashBank: 74_000_000,
    ar: {
      current: 50_000_000,
      d1_30: 32_000_000,
      d31_60: 16_000_000,
      d60_plus: 8_000_000,
    },
  },
};

/** Period keys filter P&L; cash/AR stay as-of AS_OF_DATE. */
const PERIOD_SCALE = {
  "2026-10-01_2026-10-31": 1,
  "2026-09-01_2026-09-30": 0.88,
  "2026-08-01_2026-08-31": 0.72,
};

function scaleMoney(n, scale) {
  return Math.round(n * scale);
}

function derivePl(row, scale) {
  const omzet = scaleMoney(row.omzet, scale);
  const hpp = scaleMoney(row.hpp, scale);
  const opex = scaleMoney(row.opex, scale);
  const gross = omzet - hpp;
  const net = gross - opex;
  const marginPct = omzet ? (gross / omzet) * 100 : 0;
  return { omzet, hpp, opex, gross, net, marginPct };
}

function arTotal(ar) {
  return ar.current + ar.d1_30 + ar.d31_60 + ar.d60_plus;
}

function financeFollowUps(branchId) {
  const all = FINANCE_BASE.all.followUps;
  if (branchId === "all") return all;
  return all.filter((r) => r.branchId === branchId);
}

export function buildFinanceSnapshot({ branchId, dateFrom, dateTo }) {
  const key = `${dateFrom}_${dateTo}`;
  const scale = PERIOD_SCALE[key];
  if (scale == null) {
    return {
      empty: true,
      branchId,
      dateFrom,
      dateTo,
      asOfDate: AS_OF_DATE,
      periodLabel: `${dateFrom} → ${dateTo}`,
    };
  }
  const base = FINANCE_BASE[branchId] || FINANCE_BASE.all;
  const pl = derivePl(base, scale);
  const ar = { ...base.ar };
  const followUps = financeFollowUps(branchId).map((row) => ({
    ...row,
    remaining: scaleMoney(row.remaining, Math.min(1, scale + 0.05)),
  }));
  return {
    empty: false,
    branchId,
    dateFrom,
    dateTo,
    asOfDate: AS_OF_DATE,
    periodLabel: `${dateFrom} → ${dateTo}`,
    pl,
    cashBank: base.cashBank,
    ar,
    arTotal: arTotal(ar),
    followUps,
  };
}

const PRODUCTION_POS = [
  {
    id: "po-1",
    poNumber: "PO-YG-8841",
    customer: "CV Nusantara Plastik",
    label: "urgent",
    stage: "sablon",
    dueDate: "2026-10-31",
    sisaProduksi: 8_000,
    sisaKirim: 12_000,
    branchId: "yogya",
    late: true,
    jobs: 2,
  },
  {
    id: "po-2",
    poNumber: "PO-SM-5520",
    customer: "PT Mitra Kemasan Indo",
    label: "urgent",
    stage: "penjadwalan",
    dueDate: "2026-11-01",
    sisaProduksi: 15_000,
    sisaKirim: 15_000,
    branchId: "semarang",
    late: true,
    jobs: 1,
  },
  {
    id: "po-3",
    poNumber: "PO-YG-8855",
    customer: "UD Berkah Printing",
    label: "buffer",
    stage: "qc",
    dueDate: "2026-11-03",
    sisaProduksi: 4_500,
    sisaKirim: 6_000,
    branchId: "yogya",
    late: false,
    jobs: 1,
  },
  {
    id: "po-4",
    poNumber: "PO-SM-5531",
    customer: "PT Sari Wadah Abadi",
    label: "normal",
    stage: "loading",
    dueDate: "2026-11-04",
    sisaProduksi: 0,
    sisaKirim: 9_500,
    branchId: "semarang",
    late: false,
    jobs: 1,
  },
  {
    id: "po-5",
    poNumber: "PO-YG-8860",
    customer: "Toko Plastik Maju",
    label: "normal",
    stage: "pengiriman",
    dueDate: "2026-11-05",
    sisaProduksi: 0,
    sisaKirim: 3_200,
    branchId: "yogya",
    late: false,
    jobs: 1,
  },
  {
    id: "po-6",
    poNumber: "PO-SM-5544",
    customer: "CV Indo Flex Pack",
    label: "buffer",
    stage: "sablon",
    dueDate: "2026-10-30",
    sisaProduksi: 6_200,
    sisaKirim: 6_200,
    branchId: "semarang",
    late: true,
    jobs: 2,
  },
  {
    id: "po-7",
    poNumber: "PO-YG-8872",
    customer: "PT Prima Wrap",
    label: "normal",
    stage: "penjadwalan",
    dueDate: "2026-11-06",
    sisaProduksi: 20_000,
    sisaKirim: 20_000,
    branchId: "yogya",
    late: false,
    jobs: 1,
  },
  {
    id: "po-8",
    poNumber: "PO-SM-5550",
    customer: "CV Guna Kemas",
    label: "normal",
    stage: "qc",
    dueDate: "2026-11-07",
    sisaProduksi: 3_800,
    sisaKirim: 5_000,
    branchId: "semarang",
    late: false,
    jobs: 1,
  },
];

/** Extra job-count padding so stage job totals ≠ unique PO (concept note). */
const STAGE_JOB_PAD = {
  all: { penjadwalan: 10, sablon: 5, qc: 8, loading: 5, pengiriman: 8 },
  yogya: { penjadwalan: 6, sablon: 3, qc: 4, loading: 2, pengiriman: 4 },
  semarang: { penjadwalan: 4, sablon: 2, qc: 4, loading: 3, pengiriman: 4 },
};

const CAPACITY = {
  all: [
    { id: "auto", name: "Mesin otomatis", planned: 24_000, capacity: 28_000 },
    { id: "semi-a", name: "Semi otomatis A", planned: 12_500, capacity: 16_000 },
    { id: "semi-b", name: "Semi otomatis B", planned: 6_000, capacity: 10_000 },
  ],
  yogya: [
    { id: "auto", name: "Mesin otomatis", planned: 14_000, capacity: 16_000 },
    { id: "semi-a", name: "Semi otomatis A", planned: 7_500, capacity: 9_000 },
    { id: "semi-b", name: "Semi otomatis B", planned: 3_500, capacity: 6_000 },
  ],
  semarang: [
    { id: "auto", name: "Mesin otomatis", planned: 10_000, capacity: 12_000 },
    { id: "semi-a", name: "Semi otomatis A", planned: 5_000, capacity: 7_000 },
    { id: "semi-b", name: "Semi otomatis B", planned: 2_500, capacity: 4_000 },
  ],
};

const PROD_PERIOD_OK = new Set([
  "2026-10-01_2026-10-31",
  "2026-09-01_2026-09-30",
]);

const PROD_DONE_SCALE = {
  "2026-10-01_2026-10-31": 1,
  "2026-09-01_2026-09-30": 0.9,
};

export function buildProductionSnapshot({ branchId, dateFrom, dateTo }) {
  const key = `${dateFrom}_${dateTo}`;
  if (!PROD_PERIOD_OK.has(key)) {
    return {
      empty: true,
      branchId,
      dateFrom,
      dateTo,
      asOfDate: AS_OF_DATE,
    };
  }
  const scale = PROD_DONE_SCALE[key];
  const rows = PRODUCTION_POS.filter(
    (p) => branchId === "all" || p.branchId === branchId,
  );
  const activePoUnique = new Set(rows.map((r) => r.poNumber)).size;
  const latePoUnique = new Set(rows.filter((r) => r.late).map((r) => r.poNumber)).size;
  const capacity = (CAPACITY[branchId] || CAPACITY.all).map((c) => ({
    ...c,
    planned: Math.round(c.planned * scale),
    utilPct: c.capacity ? (Math.round(c.planned * scale) / c.capacity) * 100 : 0,
  }));
  const targetPcs = capacity.reduce((s, c) => s + c.planned, 0);
  const donePcs = Math.round(targetPcs * 0.625);
  const pad = STAGE_JOB_PAD[branchId] || STAGE_JOB_PAD.all;
  const stageQueue = CONCEPT_STAGES.map((st) => {
    const stagePos = rows.filter((r) => r.stage === st.key);
    const jobFromPo = stagePos.reduce((s, r) => s + r.jobs, 0);
    const jobCount = jobFromPo + (pad[st.key] || 0);
    return {
      ...st,
      jobCount,
      uniquePo: new Set(stagePos.map((r) => r.poNumber)).size,
    };
  });
  return {
    empty: false,
    branchId,
    dateFrom,
    dateTo,
    asOfDate: AS_OF_DATE,
    kpis: {
      activePoUnique,
      latePoUnique,
      targetPcs,
      donePcs,
      donePct: targetPcs ? (donePcs / targetPcs) * 100 : 0,
    },
    stageQueue,
    capacity,
    priorities: rows,
  };
}

export const DEFAULT_FILTERS = {
  branchId: "all",
  dateFrom: "2026-10-01",
  dateTo: "2026-10-31",
};
