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
