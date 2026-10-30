#!/usr/bin/env python3
"""One-time owner-approved Yogyakarta operational cutover. Default: NO WRITES.

Run while operational writes are paused. --apply requires --all-existing-ygy
and a new --backup file. This is never invoked by app startup or deploy.
Accurate IDs, amounts, quantities, stock, journals and historical dates stay intact.
"""
import argparse
import asyncio
import copy
import json
import os
import uuid
from pathlib import Path

COLLECTIONS = ('users', 'orders', 'pra_pos', 'sales_invoices', 'sales_deliveries', 'sales_receipts')
FIELD = 'operational_branch_id'


def changes_for(collection, doc, ygy):
    changes = {}
    if collection == 'users':
        desired = {'branch_ids': [ygy], 'primary_branch_id': ygy, 'branch_id': ygy}
        # Global authority is still derived from role, not this assignment.
        for key, value in desired.items():
            if doc.get(key) != value:
                changes[key] = value
    else:
        if doc.get(FIELD) != ygy:
            changes[FIELD] = ygy
        if collection == 'orders' and doc.get('shipments'):
            shipments = copy.deepcopy(doc['shipments'])
            for shipment in shipments:
                shipment[FIELD] = ygy
            if shipments != doc['shipments']:
                changes['shipments'] = shipments
    return changes


def compare_query(doc, changes):
    return {'_id': doc['_id'], **{
        key: doc[key] if key in doc else {'$exists': False} for key in changes
    }}


async def run(db, args):
    from bson import json_util
    marker = await db.migration_status.find_one({'_id': 'ygy_operational_v1'})
    if marker and marker.get('completed'):
        raise RuntimeError('Cutover YGY sudah selesai; jangan memetakan ulang data cabang baru.')
    rows = await db.branches.find({'code': 'YGY'}).to_list(3)
    if len(rows) > 1 or (rows and rows[0].get('active') is False):
        raise RuntimeError('Master YGY duplikat/nonaktif; selesaikan melalui Direktur/Holding.')
    ygy = rows[0]['id'] if rows else str(uuid.uuid5(uuid.NAMESPACE_URL, 'eternal-packaging/branch/YGY'))
    backup = None
    if args.apply:
        if not args.all_existing_ygy or not args.backup:
            raise RuntimeError('--apply memerlukan --all-existing-ygy dan --backup baru')
        fd = os.open(args.backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        backup = os.fdopen(fd, 'w')
    summary = {'mode': 'APPLY' if args.apply else 'DRY_RUN', 'ygy_id': ygy, 'collections': {}}
    try:
        if not rows:
            summary['master'] = 'created' if args.apply else 'would_create'
            if backup:
                master = {'id': ygy, 'code': 'YGY', 'name': 'Yogyakarta', 'active': True}
                backup.write(json_util.dumps({'collection': 'branches', 'created': master}) + '\n')
                backup.flush(); os.fsync(backup.fileno())
                # Existing unique code/index and explicit preflight protect duplicate creation.
                await db.branches.insert_one(master)
        for name in COLLECTIONS:
            scanned = changed = 0
            async for doc in db[name].find({}):
                scanned += 1
                changes = changes_for(name, doc, ygy)
                if not changes:
                    continue
                changed += 1
                if backup:
                    record = {'collection': name, '_id': doc['_id'],
                              'before': {k: doc[k] for k in changes if k in doc},
                              'absent': [k for k in changes if k not in doc], 'after': changes}
                    backup.write(json_util.dumps(record) + '\n')
                    backup.flush(); os.fsync(backup.fileno())
                    result = await db[name].update_one(compare_query(doc, changes), {'$set': changes})
                    if result.matched_count != 1:
                        raise RuntimeError(f'Konflik dokumen {name}; hentikan penulisan operasional sebelum melanjutkan.')
            summary['collections'][name] = {'scanned': scanned, 'would_change' if not args.apply else 'changed': changed}
        if args.apply:
            await db.migration_status.update_one(
                {'_id': 'ygy_operational_v1'},
                {'$set': {'completed': True, 'ygy_id': ygy, 'backup': str(args.backup)}},
                upsert=True)
    finally:
        if backup:
            backup.close()
    print(json.dumps(summary, indent=2))


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--all-existing-ygy', action='store_true')
    parser.add_argument('--backup')
    args = parser.parse_args()
    from motor.motor_asyncio import AsyncIOMotorClient
    # No default DB: accidentally running against another database must fail.
    client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    try:
        await run(client[os.environ['DB_NAME']], args)
    finally:
        client.close()


if __name__ == '__main__':
    asyncio.run(main())
