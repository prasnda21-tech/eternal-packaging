"""Read-only invoice coverage for already-authorized sales-order list rows."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation


def _qty(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def invoice_coverage(order, invoices):
    expected = {}
    for index, line in enumerate(order.get('line_items') or []):
        key = line.get('line_idx') if line.get('line_idx') is not None else index
        try:
            key = int(key)
        except (TypeError, ValueError):
            return {'status': 'unknown'}
        qty = _qty(line.get('quantity'))
        if qty is None or key in expected:
            return {'status': 'unknown'}
        expected[key] = qty
    expected = {key: qty for key, qty in expected.items() if qty > 0}
    if not expected:
        return {'status': 'unknown'}
    covered = defaultdict(Decimal)
    processing = False
    for invoice in invoices:
        if invoice.get('removed_from_active') or invoice.get('removal_state') in ('pending', 'deleted'):
            continue
        if invoice.get('financial_status') == 'cancelled':
            continue
        issue_state = invoice.get('issue_state')
        if issue_state and issue_state != 'issued':
            processing = True
            continue
        if invoice.get('financial_status') != 'finalized':
            continue
        for line in invoice.get('lines') or []:
            try:
                key = int(line['line_idx'])
            except (KeyError, TypeError, ValueError):
                return {'status': 'unknown'}
            qty = _qty(line.get('quantity'))
            if qty is None or (qty > 0 and key not in expected):
                return {'status': 'unknown'}
            covered[key] += qty or Decimal(0)
    if all(covered[key] >= qty for key, qty in expected.items()):
        status = 'full'
    elif any(covered[key] > 0 for key in expected):
        status = 'partial'
    else:
        status = 'none'
    return {'status': status, 'processing': processing}


async def attach_invoice_coverage(db, orders):
    po_ids = list({row['id'] for row in orders if row.get('id')})
    grouped = defaultdict(list)
    if po_ids:
        cursor = db.sales_invoices.find(
            {'po_id': {'$in': po_ids}},
            {'_id': 0, 'po_id': 1, 'lines.line_idx': 1, 'lines.quantity': 1,
             'financial_status': 1, 'issue_state': 1,
             'removed_from_active': 1, 'removal_state': 1},
        )
        async for invoice in cursor:
            grouped[invoice['po_id']].append(invoice)
    for order in orders:
        order['invoice_summary'] = invoice_coverage(order, grouped[order.get('id')])


def filter_invoice_coverage(orders, status):
    if not status or status == 'all':
        return orders
    if status == 'processing':
        return [o for o in orders if o.get('invoice_summary', {}).get('processing')]
    return [o for o in orders if o.get('invoice_summary', {}).get('status') == status]
