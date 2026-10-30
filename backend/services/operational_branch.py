"""Internal operational authority; Accurate branch_id remains an external identity.

No implicit data migration or writes on read. Legacy documents with an internal
branch_id remain readable; numeric external ids require the explicit YGY cutover.
"""
from fastapi import HTTPException
from services.branch_access import assigned_branch_ids, has_global_branch_scope

MANAGERS = frozenset({'direktur_utama', 'holding_team'})
FIELD = 'operational_branch_id'


def require_branch_manager(user):
    if str((user or {}).get('role') or '').lower() not in MANAGERS:
        raise HTTPException(403, 'Hanya Direktur dan Holding dapat mengatur akses cabang.')


def document_branch(doc):
    return str((doc or {}).get(FIELD) or (doc or {}).get('branch_id') or '').strip()


async def active_branch(db, branch_id):
    doc = await db.branches.find_one({'id': str(branch_id)}, {'_id': 0})
    if not doc or doc.get('active') is False:
        raise HTTPException(400, 'Cabang tidak ditemukan atau nonaktif.')
    return doc


async def validate_assignment(db, actor, ids, primary):
    require_branch_manager(actor)
    ids = assigned_branch_ids({'branch_ids': ids})
    primary = str(primary or '').strip()
    if not ids or primary not in ids:
        raise HTTPException(400, 'Pilih minimal satu cabang dan cabang utama dari pilihan tersebut.')
    for bid in ids:
        await active_branch(db, bid)
    return {'branch_ids': ids, 'primary_branch_id': primary, 'branch_id': primary}


async def operational_access(db, user):
    if has_global_branch_scope(user):
        return {'scope': 'ALL', 'branch_ids': None}
    ids = assigned_branch_ids(user)
    if not ids:
        raise HTTPException(403, 'Akses cabang belum ditetapkan. Hubungi Direktur/Holding.')
    for bid in ids:
        await active_branch(db, bid)
    return {'scope': 'ASSIGNED', 'branch_ids': ids}


def scope_query(access):
    if access.get('scope') == 'ALL':
        return {}
    ids = list(access.get('branch_ids') or [])
    return {'$and': [{'$or': [
        {FIELD: {'$in': ids}},
        {'$and': [{FIELD: {'$in': [None, '']}}, {'branch_id': {'$in': ids}}]},
    ]}]}


async def operational_query(db, user):
    return scope_query(await operational_access(db, user))


async def require_document_branch(db, user, doc):
    access = await operational_access(db, user)
    if access['scope'] != 'ALL' and document_branch(doc) not in access['branch_ids']:
        raise HTTPException(403, 'Akses cabang dokumen ditolak.')


async def resolve_write_branch(db, user, requested=None):
    access = await operational_access(db, user)
    bid = str(requested or user.get('primary_branch_id') or user.get('branch_id') or '').strip()
    if not bid and access['scope'] != 'ALL' and len(access['branch_ids']) == 1:
        bid = access['branch_ids'][0]
    if not bid:
        branches = await db.branches.find({'active': {'$ne': False}}, {'_id': 0}).to_list(1000)
        branches = [b for b in branches if b.get('active') is not False]
        if len(branches) == 1 and branches[0].get('code') == 'YGY':
            bid = branches[0]['id']
        else:
            raise HTTPException(400, 'Pilih cabang operasional untuk transaksi ini.')
    await active_branch(db, bid)
    if access['scope'] != 'ALL' and bid not in access['branch_ids']:
        raise HTTPException(403, 'Akses cabang ditolak.')
    return bid


async def options(db, user):
    access = await operational_access(db, user)
    rows = await db.branches.find({'active': {'$ne': False}}, {'_id': 0}).to_list(1000)
    rows = [r for r in rows if r.get('active') is not False and
            (access['scope'] == 'ALL' or r['id'] in access['branch_ids'])]
    primary = str(user.get('primary_branch_id') or user.get('branch_id') or '')
    if primary not in {r['id'] for r in rows}:
        primary = rows[0]['id'] if len(rows) == 1 else ''
    return {'branches': [{'id':r['id'], 'code':r.get('code'), 'name':r.get('name')} for r in rows],
            'primary_branch_id': primary, 'global_access': access['scope'] == 'ALL'}


async def guard_document_request(db, request, user):
    """Apply branch checks to authenticated document URLs, including writes/export.

    Public/customer-token routes do not use current_user. No route guessing from
    arbitrary IDs: only registered route parameters and known ERP document paths.
    """
    params = getattr(request, 'path_params', {}) or {}
    if not params:
        return
    po_id = params.get('order_id') or params.get('po_id')
    if po_id:
        doc = await db.orders.find_one({'id': str(po_id)}, {'_id': 0, 'id': 1, FIELD: 1, 'branch_id': 1})
        if doc:
            await require_document_branch(db, user, doc)
        return
    path = str(getattr(getattr(request, 'url', None), 'path', ''))
    for segment, collection, param in [
        ('/sales-invoices/', 'sales_invoices', 'invoice_id'),
        ('/sales-deliveries/', 'sales_deliveries', 'delivery_id'),
        ('/sales-receipts/', 'sales_receipts', 'receipt_id'),
    ]:
        doc_id = params.get(param) or params.get('id')
        if segment in path and doc_id:
            doc = await db[collection].find_one({'id': str(doc_id)}, {'_id': 0, FIELD: 1, 'branch_id': 1, 'po_id': 1})
            if doc:
                await require_document_branch(db, user, doc)
            return
