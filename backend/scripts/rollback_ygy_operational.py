#!/usr/bin/env python3
"""Rollback only metadata recorded by migrate_ygy_operational.py.

Default dry-run. Requires paused writes. Conflicts stop rollback; never overwrite
post-cutover changes. A newly created YGY master is retained to preserve refs.
"""
import argparse
import asyncio
import os


async def restore(db, records, apply=False):
    restored = 0
    for entry in reversed(records):
        if 'created' in entry:
            continue  # Retain stable master, including any newer references.
        collection = db[entry['collection']]
        current = await collection.find_one({'_id': entry['_id']})
        if not current:
            raise RuntimeError('Dokumen hilang; hentikan rollback.')
        after = entry['after']
        before = entry['before']
        if all((key not in current if key in entry['absent'] else current.get(key) == before.get(key)) for key in after):
            continue  # Journal was flushed before an unapplied write, or already restored.
        if any(current.get(key) != value for key, value in after.items()):
            raise RuntimeError('Metadata berubah setelah migrasi; rollback perlu review.')
        if apply:
            update = {}
            if before: update['$set'] = before
            if entry['absent']: update['$unset'] = {k: '' for k in entry['absent']}
            result = await collection.update_one({'_id': entry['_id'], **after}, update)
            if result.matched_count != 1:
                raise RuntimeError('Konflik saat rollback.')
        restored += 1
    return {'mode': 'APPLY' if apply else 'DRY_RUN', 'restored_or_would_restore': restored}


async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    from bson import json_util
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup'); parser.add_argument('--apply', action='store_true')
    args=parser.parse_args()
    with open(args.backup) as f: records=[json_util.loads(line) for line in f if line.strip()]
    client=AsyncIOMotorClient(os.environ['MONGO_URL'])
    try: print(await restore(client[os.environ['DB_NAME']], records, args.apply))
    finally: client.close()

if __name__ == '__main__': asyncio.run(main())
