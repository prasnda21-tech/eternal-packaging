'''Purchase documents + payment submission list filters.'''
from __future__ import annotations

import io
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('MONGO_URL', 'mongodb://127.0.0.1:27017')
os.environ.setdefault('DB_NAME', 'eternal_packaging_purchase_docs_test')
os.environ.setdefault('JWT_SECRET', 'test-secret-key-12345678901234567890')
os.environ.setdefault('CORS_ORIGINS', '*')
os.environ['STORAGE_PROVIDER'] = 'local'

import server
from server import app, current_user
from services import purchase_payments as pay


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture(autouse=True)
def bind_motor(tmp_path, monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient

    monkeypatch.setenv('LOCAL_STORAGE_DIR', str(tmp_path / 'objstore'))
    try:
        from services import storage as obj_storage
        for name in ('_reset_provider_for_tests', '_reset_provider', '_clear_cached_provider'):
            fn = getattr(obj_storage, name, None)
            if callable(fn):
                fn()
                break
    except Exception:
        pass
    server.client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    server.db = server.client[os.environ['DB_NAME']]


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    db = server.db
    for name in ('users', 'purchase_orders', 'purchase_payments', 'files'):
        await db[name].delete_many({})
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    for name in ('users', 'purchase_orders', 'purchase_payments', 'files'):
        await db[name].delete_many({})


ACTOR = {
    'id': 'doc-actor',
    'user_id': 'doc-actor',
    'email': 'doc-actor@example.com',
    'name': 'Doc Actor',
    'role': 'admin',
    'status': 'active',
    'permissions': [
        'purchase_orders.view',
        'purchase_orders.create',
        'purchase_receipts.view',
        'purchase_receipts.receive',
        'purchase_invoices.view',
        'purchase_invoices.create',
        'purchase_payments.view',
        'purchase_payments.create',
    ],
}

MINI_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
    b'\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
)


async def client_ctx():
    app.dependency_overrides[current_user] = lambda: ACTOR
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')


@pytest.mark.anyio
async def test_purchase_documents_upload_list_preview_delete_no_mongo_binary():
    await server.db.users.update_one({'id': ACTOR['id']}, {'$set': ACTOR}, upsert=True)
    po_id = str(uuid.uuid4())
    await server.db.purchase_orders.insert_one({'id': po_id, 'po_number': 'PO-DOC-1', 'cancelled': False})

    async with await client_ctx() as c:
        up = await c.post(
            '/api/purchase-documents',
            data={
                'entity_type': 'purchase_order',
                'entity_id': po_id,
                'document_name': 'Nota penawaran',
            },
            files={'file': ('nota.png', io.BytesIO(MINI_PNG), 'image/png')},
        )
        assert up.status_code in (200, 201), up.text
        doc = up.json()['document']
        assert doc['document_name'] == 'Nota penawaran'
        assert doc['uploaded_by_name']
        assert doc['uploaded_at']
        assert 'storage_path' not in doc

        listed = await c.get(
            '/api/purchase-documents',
            params={'entity_type': 'purchase_order', 'entity_id': po_id},
        )
        assert listed.status_code == 200
        assert listed.json()['count'] == 1

        content = await c.get(f"/api/purchase-documents/{doc['id']}/content")
        assert content.status_code == 200
        assert content.content.startswith(b'\x89PNG')

        raw = await server.db.files.find_one({'id': doc['id']})
        assert raw and raw.get('storage_path')
        assert 'data' not in raw and 'data_url' not in raw and 'bytes' not in raw

        deleted = await c.delete(f"/api/purchase-documents/{doc['id']}")
        assert deleted.status_code == 200
        listed2 = await c.get(
            '/api/purchase-documents',
            params={'entity_type': 'purchase_order', 'entity_id': po_id},
        )
        assert listed2.json()['count'] == 0


@pytest.mark.anyio
async def test_payment_list_filters_diajukan_selesai_keeps_submission_separate():
    draft = {
        'id': str(uuid.uuid4()),
        'payment_number': 'PP-DRAFT',
        'submission_status': pay.SUBMISSION_NOT_SUBMITTED,
        'settlement_status': pay.SETTLEMENT_UNPAID,
        'amount': 1000,
        'cancelled': False,
        'created_at': '2026-09-12T00:00:00+00:00',
    }
    submitted = {
        'id': str(uuid.uuid4()),
        'payment_number': 'PP-SUB',
        'submission_status': pay.SUBMISSION_SUBMITTED,
        'settlement_status': pay.SETTLEMENT_UNPAID,
        'submitted_at': '2026-09-12T10:00:00+00:00',
        'amount': 2000,
        'cancelled': False,
        'created_at': '2026-09-12T01:00:00+00:00',
    }
    paid = {
        'id': str(uuid.uuid4()),
        'payment_number': 'PP-PAID',
        'submission_status': pay.SUBMISSION_SUBMITTED,
        'settlement_status': pay.SETTLEMENT_PAID,
        'submitted_at': '2026-09-11T10:00:00+00:00',
        'paid_at': '2026-09-12T12:00:00+00:00',
        'amount': 3000,
        'cancelled': False,
        'created_at': '2026-09-11T01:00:00+00:00',
    }
    await server.db.purchase_payments.insert_many([draft, submitted, paid])

    diajukan = await pay.list_purchase_payments(server.db, status_filter='DIAJUKAN')
    assert {r['payment_number'] for r in diajukan} == {'PP-SUB'}
    assert diajukan[0]['list_status'] == 'DIAJUKAN'
    assert diajukan[0]['submission_status'] == pay.SUBMISSION_SUBMITTED
    assert diajukan[0]['settlement_status'] != pay.SETTLEMENT_PAID
    assert diajukan[0]['submitted_at']

    selesai = await pay.list_purchase_payments(server.db, status_filter='SELESAI')
    assert {r['payment_number'] for r in selesai} == {'PP-PAID'}
    assert selesai[0]['list_status'] == 'SELESAI'
    assert selesai[0]['settlement_status'] == pay.SETTLEMENT_PAID

    default = await pay.list_purchase_payments(server.db)
    assert {r['payment_number'] for r in default} == {'PP-SUB', 'PP-PAID'}
