import { productionScheduleOperatorIdentity } from "./productionScheduleDisplayRows";
import {
  formatIdQty,
  isPrintableScheduleSplit,
  printableCompletedQty,
} from "./productionScheduleQty";

function qtyInt(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 0;
  return n;
}

function poIdentity(row) {
  return String(row?.id || row?.po_number || "").trim();
}

/** Ringkasan from the current filtered canonical PO rows (not expanded operator rows). */
export function buildProductionScheduleSummary(rows) {
  const seenPo = new Set();
  const operators = new Set();
  let jumlahPo = 0;
  let totalQtyPesanan = 0;
  let qtySudahDicetak = 0;
  let poSelesaiCetak = 0;
  let poBelumSelesaiCetak = 0;

  for (const row of rows || []) {
    const id = poIdentity(row);
    if (!id || seenPo.has(id)) continue;
    seenPo.add(id);
    jumlahPo += 1;
    totalQtyPesanan += Math.max(0, qtyInt(row?.total_quantity));
    const completed = printableCompletedQty(row?.operators);
    qtySudahDicetak += completed;
    const required = Math.max(0, qtyInt(row?.print_qty_total));
    if (required > 0) {
      if (completed >= required) poSelesaiCetak += 1;
      else poBelumSelesaiCetak += 1;
    }
    for (const op of row?.operators || []) {
      if (!isPrintableScheduleSplit(op)) continue;
      const ident = productionScheduleOperatorIdentity(op);
      if (ident) operators.add(ident);
    }
  }

  return {
    jumlahPo,
    totalQtyPesanan,
    qtySudahDicetak,
    sisaQtyCetak: Math.max(totalQtyPesanan - qtySudahDicetak, 0),
    poSelesaiCetak,
    poBelumSelesaiCetak,
    operatorCount: operators.size,
  };
}

export function productionScheduleSummaryLines(metrics) {
  const m = metrics || buildProductionScheduleSummary([]);
  return [
    { key: "po", label: "Jumlah PO", value: formatIdQty(m.jumlahPo) },
    { key: "qty", label: "Total Qty Pesanan", value: formatIdQty(m.totalQtyPesanan) },
    { key: "printed", label: "Qty Sudah Dicetak", value: formatIdQty(m.qtySudahDicetak) },
    { key: "remain", label: "Sisa Qty Cetak", value: formatIdQty(m.sisaQtyCetak) },
    { key: "done", label: "PO Selesai Cetak", value: formatIdQty(m.poSelesaiCetak) },
    { key: "open", label: "PO Belum Selesai Cetak", value: formatIdQty(m.poBelumSelesaiCetak) },
    { key: "ops", label: "Operator", value: formatIdQty(m.operatorCount) },
  ];
}
