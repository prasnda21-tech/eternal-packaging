"""Exercise the real auth boundary without importing the ERP startup."""
import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import jwt


def test_auth_uses_db_branch_assignment_not_token_snapshot():
    tree = ast.parse((Path(__file__).parents[1] / 'server.py').read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'current_user')
    fn.args.args[0].annotation = None
    class Users:
        async def find_one(self, *args):
            return {'id': 'u', 'email': 'u@test', 'role': 'crm', 'branch_ids': ['ygy'], 'primary_branch_id': 'ygy'}
    env = {'db': SimpleNamespace(users=Users()), 'jwt': jwt, 'os': os, 'JWT_ALGORITHM': 'HS256',
           'enforce_sales_eternal_surface': lambda *a: None}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), '<auth>', 'exec'), env)
    os.environ['JWT_SECRET'] = 'test-only-secret-with-at-least-32-characters'
    token = jwt.encode({'email': 'u@test', 'branch_ids': ['smg'], 'branch_id': 'smg'}, os.environ['JWT_SECRET'], algorithm='HS256')
    request = SimpleNamespace(cookies={'access_token': token}, headers={}, query_params={}, path_params={})
    user = asyncio.run(env['current_user'](request))
    assert user['branch_ids'] == ['ygy']
    assert user['primary_branch_id'] == 'ygy'
    assert user['branch_id'] == 'ygy'


def test_http_auth_branch_reads_and_assignment_changes(monkeypatch):
    """Real HTTP dependency + JWT + Mongo query, using isolated dummy records."""
    import pytest
    import httpx
    from fastapi import FastAPI, Depends, Request, HTTPException
    AsyncMongoMockClient = pytest.importorskip("mongomock_motor").AsyncMongoMockClient
    from services.operational_branch import operational_query
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://production.test")
    monkeypatch.setenv("JWT_SECRET", "test-only-secret-with-at-least-32-characters")
    tree = ast.parse((Path(__file__).parents[1] / 'server.py').read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'current_user')
    db = AsyncMongoMockClient().isolated_http
    env = {'db': db, 'jwt': jwt, 'os': os, 'JWT_ALGORITHM': 'HS256',
           'Request': Request, 'HTTPException': HTTPException,
           'enforce_sales_eternal_surface': lambda *a: None}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), '<auth>', 'exec'), env)
    app = FastAPI()
    @app.get("/orders")
    async def rows(user=Depends(env['current_user'])):
        return await db.orders.find(await operational_query(db,user), {'_id':0}).to_list(None)
    @app.get("/orders/{order_id}")
    async def detail(order_id: str, user=Depends(env['current_user'])):
        return await db.orders.find_one({'id':order_id},{'_id':0})
    async def check():
        await db.branches.insert_many([{'id':'ygy','code':'YGY'},{'id':'smg','code':'SMG'}])
        await db.orders.insert_many([
            {'id':'po-ygy','branch_id':'50','operational_branch_id':'ygy'},
            {'id':'po-smg','branch_id':'50','operational_branch_id':'smg'}])
        for role in ['crm','kepala_produksi','admin']:
            email=role+'@test'
            await db.users.insert_one({'id':role,'email':email,'role':role,'branch_ids':['ygy'],'primary_branch_id':'ygy','status':'active'})
            token=jwt.encode({'email':email,'branch_ids':['smg']},os.environ['JWT_SECRET'],algorithm='HS256')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test",cookies={'access_token':token}) as client:
                result=await client.get('/orders')
                assert result.status_code==200
                assert [row['id'] for row in result.json()]==['po-ygy']
                assert (await client.get('/orders/po-smg')).status_code==403
                await db.users.update_one({'id':role},{'$set':{'branch_ids':['smg'],'primary_branch_id':'smg'}})
                assert [row['id'] for row in (await client.get('/orders')).json()]==['po-smg']
                await db.users.update_one({'id':role},{'$unset':{'branch_ids':'','primary_branch_id':''}})
                result=await client.get('/orders')
                assert result.status_code==403 and 'belum ditetapkan' in result.json()['detail']
    asyncio.run(check())
