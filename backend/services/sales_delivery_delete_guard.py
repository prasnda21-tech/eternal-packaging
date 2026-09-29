"""Business evidence protecting one SJ; PO payments are unrelated to deletion."""
from quantity_rules import shipment_identity_keys

IDENTITY = ('shipment_id', 'sales_delivery_id', 'surat_jalan_id', 'sj_file_id',
            'delivery_note_id', 'accurate_shipment_id', 'sj_number',
            'shipment_number', 'delivery_note_number')


def deletion_blocker(order, delivery):
    ships = [s for s in order.get('shipments', []) if isinstance(s, dict)]
    did = str(delivery.get('id') or '').strip()
    number = str(delivery.get('sj_number') or '').strip()
    selected = [s for s in ships if did and did in {
        str(s.get('id') or ''), str(s.get('sales_delivery_id') or '')}]
    if not selected and number:
        selected = [s for s in ships if number in {
            str(s.get('number') or ''), str(s.get('sj_number') or '')}]
    if len(selected) > 1:
        return 'Identitas Surat Jalan ambigu; periksa hubungan SJ sebelum menghapus.'
    keys = {did} if did else set()
    if selected:
        keys.update(shipment_identity_keys(order, selected[0]))
    elif number:
        keys.add(number)
    for f in order.get('surat_jalan_files') or []:
        if did and did in {str(f.get('id') or ''), str(f.get('shipment_id') or '')}:
            keys.update(str(f[k]) for k in ('id', 'file_id', 'shipment_id') if f.get(k))

    def belongs(row):
        ids = {str(row[k]).strip() for k in IDENTITY if row.get(k)}
        if not ids:
            return None
        return bool(ids & keys)

    for sh in selected:
        if (sh.get('loaded_qty') or 0) > 0:
            return 'Surat Jalan tidak dapat dihapus karena sudah memiliki proses Loading.'
        if ((sh.get('delivered_qty') or 0) > 0 or sh.get('delivered')
                or sh.get('delivered_at') or sh.get('status') in ('Terkirim', 'DELIVERED')
                or sh.get('delivery_status') == 'Terkirim'):
            return 'Surat Jalan tidak dapat dihapus karena sudah memiliki proses Pengiriman.'

    groups = [('loading_confirmations', 'Loading'),
              ('delivery_confirmations', 'Pengiriman'),
              ('delivery_proofs', 'bukti Pengiriman'),
              ('delivery_recipient_signatures', 'tanda tangan penerima'),
              ('delivery_driver_signatures', 'tanda tangan pengirim')]
    for field, label in groups:
        rows = list(order.get(field) or [])
        if field.endswith('signatures'):
            active = order.get(field[:-1])
            if isinstance(active, dict) and active:
                rows.append(active)
        for row in rows:
            if not isinstance(row, dict) or row.get('is_deleted') or row.get('cancelled'):
                continue
            match = belongs(row)
            if match:
                return f'Surat Jalan tidak dapat dihapus karena sudah memiliki proses {label}.'
            if match is None:
                return f'Riwayat {label} lama belum terhubung ke SJ tertentu; periksa identitas bukti sebelum menghapus.'
    # PO aggregate delivery status is not evidence that this particular SJ was used.
    return None
