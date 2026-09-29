export const PAGE_SIZE = 10;
export function moneyValue(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
export function moneyText(value) {
  const n = moneyValue(value);
  return n === null ? '—' : `Rp ${n.toLocaleString('id-ID')}`;
}
export function documentStatus(row, kind) {
  if (kind === 'receipt') {
    if (row.financial_status === 'cancelled' || row.accounting_status === 'reversed') return 'cancelled';
    return row.financial_status === 'active' ? 'active' : 'legacy';
  }
  if (row.financial_status === 'cancelled') return 'cancelled';
  if (row.issue_state && row.issue_state !== 'issued') return 'processing';
  if (row.financial_status !== 'finalized') return 'draft';
  return ({LUNAS:'paid', DP:'partial', 'BELUM DIBAYAR':'unpaid'})[row.payment_status] || 'unknown';
}
export function filterDocuments(rows, kind, {status='', start='', end='', columns={}} = {}) {
  const dateKey = kind === 'invoice' ? 'invoice_date' : 'receipt_date';
  return rows.filter(row => {
    const date = String(row[dateKey] || '').slice(0,10);
    if ((status && documentStatus(row,kind) !== status) || (start && (!date || date < start)) || (end && (!date || date > end))) return false;
    return Object.entries(columns).every(([key,query]) => {
      const statusLabel = key === 'financial_status' ? ({cancelled:'Dibatalkan',processing:'Sedang diproses',draft:'Draft lama'})[documentStatus(row,kind)] || (kind === 'invoice' ? 'Terbit Final' : 'Aktif') : '';
      const text = key === 'financial_status' ? `${row[key] || ''} ${statusLabel}` : key === 'identity' ? [row.customer,row.invoice_number,row.receipt_number,row.po_number].join(' ') : String(row[key] ?? '');
      return text.toLocaleLowerCase('id-ID').includes(query.trim().toLocaleLowerCase('id-ID'));
    });
  });
}
export function summarizeDocuments(rows, kind) {
  const active = rows.filter(row => kind === 'invoice'
    ? row.financial_status === 'finalized' && (!row.issue_state || row.issue_state === 'issued')
    : row.financial_status !== 'cancelled' && row.accounting_status !== 'reversed');
  const fields = kind === 'invoice' ? ['grand_total','paid_total','outstanding_amount'] : ['amount','allocated_amount'];
  return fields.map(field => {
    const values = active.map(row => moneyValue(field === 'allocated_amount' ? row.allocated_amount ?? row.amount : row[field]));
    return values.some(value => value === null) ? null : values.reduce((sum,value) => sum + value,0);
  });
}
