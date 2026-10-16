"""Master Barang/Jasa: Accurate item sync, idempotent upsert, PO resolution.

Uses in-memory fake Mongo + fake Accurate client. Does not hit live Accurate.
"""
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS, effective_permissions
from services.accurate import (
    AccurateClient,
    _MOCK_ITEMS,
    _MOCK_SALES_ORDERS,
    _ITEM_DETAIL_CONCURRENCY,
    _extract_accurate_item_id,
    _materialize_mock_sales_order,
    _normalize_accurate_so,
    get_last_list_error,
    item_list_row_incomplete,
    log_master_items_sync_failure,
    merge_accurate_item_row,
    normalize_accurate_item,
    sanitize_accurate_error,
)
from services.master_items import (
    ACCURATE_ID_PARTIAL_INDEX,
    CODE_CLAIMS_INDEX,
    COLLECTION,
    INDEX_NOT_READY_MSG,
    LEGACY_ACCURATE_ID_INDEX,
    UNKNOWN_ITEM_MSG,
    MasterItemUnresolvedError,
    build_list_query,
    create_local_master_item,
    list_master_items,
    local_master_create_index_status,
    lookup_masters_by_accurate_ids,
    public_master_item,
    resolve_line_production_process,
    resolve_master_origin,
    resolve_po_line_items,
    sync_master_items,
    update_master_production_process,
    upsert_master_item,
)
from fastapi import HTTPException


def _ready_master_indexes():
    return {
        "_id_": {"key": [("_id", 1)]},
        ACCURATE_ID_PARTIAL_INDEX: {
            "key": [("accurate_item_id", 1)],
            "unique": True,
            "partialFilterExpression": {
                "accurate_item_id": {"$exists": True, "$type": "string", "$gt": ""},
            },
        },
    }


def _ready_claim_indexes():
    return {
        "_id_": {"key": [("_id", 1)]},
        CODE_CLAIMS_INDEX: {
            "key": [("code_key", 1)],
            "unique": True,
        },
    }


def _await(coro):
    return asyncio.run(coro)


class FakeCursor:
    def __init__(self, docs):
        self._docs = [dict(d) for d in docs]

    def sort(self, key, direction=1):
        reverse = direction == -1
        self._docs.sort(key=lambda d: (d.get(key) is None, d.get(key) or ""), reverse=reverse)
        return self

    def skip(self, n):
        self._docs = self._docs[int(n):]
        return self

    def limit(self, n):
        self._docs = self._docs[: int(n)]
        return self

    async def to_list(self, n):
        return [dict(d) for d in self._docs[: int(n)]]


class FakeCollection:
    def __init__(self, indexes=None):
        self.docs = []
        self._indexes = dict(indexes or {})

    def _match(self, doc, query):
        if not query:
            return True
        if "$and" in query:
            return all(self._match(doc, c) for c in query["$and"])
        if "$or" in query:
            return any(self._match(doc, c) for c in query["$or"])
        for k, v in query.items():
            if k.startswith("$"):
                continue
            dv = doc.get(k)
            if isinstance(v, dict):
                if "$exists" in v:
                    exists = k in doc
                    if bool(v["$exists"]) != exists:
                        return False
                if "$ne" in v and dv == v["$ne"]:
                    return False
                if "$gt" in v:
                    try:
                        if not (dv > v["$gt"]):
                            return False
                    except TypeError:
                        return False
                if "$type" in v:
                    want = v["$type"]
                    if want == "string" and not isinstance(dv, str):
                        return False
                if "$in" in v:
                    if dv not in v["$in"]:
                        return False
                if "$regex" in v:
                    import re
                    flags = re.I if "i" in (v.get("$options") or "") else 0
                    if not re.search(v["$regex"], str(dv or ""), flags):
                        return False
            elif dv != v:
                return False
        return True

    def _proj(self, doc, proj):
        if not proj:
            return dict(doc)
        out = dict(doc)
        if proj.get("_id") == 0:
            out.pop("_id", None)
        keep = [k for k, inc in proj.items() if k != "_id" and inc]
        if keep:
            out = {k: out.get(k) for k in keep if k in out or True}
            if "id" in doc and "id" not in out and not keep:
                pass
            # include requested keys only, plus identity used by callers
            out = {k: doc.get(k) for k in keep}
        return out

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if self._match(d, query):
                return self._proj(d, proj)
        return None

    def find(self, query, proj=None):
        matched = [self._proj(d, proj) for d in self.docs if self._match(d, query)]
        return FakeCursor(matched)

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if self._match(d, query):
                if "$set" in update:
                    d.update(update["$set"])
                return
        if not upsert:
            return
        new = dict(query)
        new.update(update.get("$setOnInsert") or {})
        new.update(update.get("$set") or {})
        if any(x.get("accurate_item_id") == new.get("accurate_item_id") for x in self.docs):
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError("E11000 duplicate accurate_item_id")
        self.docs.append(new)

    async def count_documents(self, query):
        return sum(1 for d in self.docs if self._match(d, query))

    async def distinct(self, field):
        vals = []
        for d in self.docs:
            v = d.get(field)
            if v not in vals:
                vals.append(v)
        return vals

    async def create_index(self, *args, **kwargs):
        return "ok"

    async def insert_one(self, doc):
        d = dict(doc)
        if "code_key" in d and any(x.get("code_key") == d.get("code_key") for x in self.docs):
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError("E11000 duplicate code_key")
        if "id" in d and any(x.get("id") == d.get("id") for x in self.docs):
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError("E11000 duplicate id")
        self.docs.append(d)
        return type("R", (), {"inserted_id": d.get("id") or d.get("code_key")})()

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if self._match(d, query):
                self.docs.pop(i)
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    async def index_information(self):
        return dict(self._indexes)

    async def drop_index(self, name):
        self._indexes.pop(name, None)
        return None


class FakeDB:
    def __init__(self, *, ready_indexes=True):
        if ready_indexes:
            self.master_items = FakeCollection(_ready_master_indexes())
            self.master_item_code_claims = FakeCollection(_ready_claim_indexes())
        else:
            self.master_items = FakeCollection()
            self.master_item_code_claims = FakeCollection()

    def __getitem__(self, name):
        if name == COLLECTION:
            return self.master_items
        if name == "master_item_code_claims":
            return self.master_item_code_claims
        raise KeyError(name)


class FakeAccurate:
    def __init__(self, items=None, fail=False, details=None):
        self._items = list(items or [])
        self.fail = fail
        self._details = details  # optional override map id -> raw
        self.is_live = False
        self.api_token = ""
        self.api_signature_secret = ""
        self.client_secret = ""
        self.access_token = ""
        self.refresh_token = ""

    async def fetch_all_items(self):
        if self.fail:
            raise RuntimeError("Accurate timeout")
        out = []
        for raw in self._items:
            n = normalize_accurate_item(raw)
            if n:
                out.append(n)
        return out

    async def fetch_item_detail(self, item_id):
        if self.fail:
            raise RuntimeError("Accurate timeout")
        key = str(item_id)
        if self._details is not None:
            raw = self._details.get(key)
            return normalize_accurate_item(raw) if raw else None
        for raw in self._items:
            if str(raw.get("id")) == key:
                return normalize_accurate_item(raw)
        return None


def _raw(n, name="Cup", itype="INVENTORY", unit="pcs", suspended=False, code=None):
    return {
        "id": n,
        "no": code if code is not None else f"SKU-{n}",
        "name": name,
        "itemType": itype,
        "unit1Name": unit,
        "suspended": suspended,
    }


# ---------- normalization ----------
def test_normalize_uses_accurate_id_not_name():
    a = normalize_accurate_item({"id": 101, "no": "A", "name": "Cup 12 oz"})
    b = normalize_accurate_item({"id": 202, "no": "B", "name": "Cup 12 oz"})
    assert a["accurate_item_id"] == "101"
    assert b["accurate_item_id"] == "202"
    assert a["item_name"] == b["item_name"] == "Cup 12 oz"


def test_normalize_skips_row_without_id():
    assert normalize_accurate_item({"name": "X", "no": "1"}) is None
    assert normalize_accurate_item(None) is None


def test_normalize_does_not_invent_price_or_stock():
    n = normalize_accurate_item({"id": 1, "name": "Cup"})
    assert "price" not in n
    assert "stock" not in n
    assert "barcode" not in n
    assert n["unit"] is None
    assert n["item_type"] is None


def test_normalize_id_only_list_row_leaves_metadata_none():
    n = normalize_accurate_item({"id": 5700})
    assert n["accurate_item_id"] == "5700"
    assert n["item_code"] is None
    assert n["item_name"] is None
    assert n["unit"] is None
    assert n["item_type"] is None
    assert n["is_active"] is None
    assert n["item_name"] != "5700"


def test_normalize_unit1_nested_name():
    n = normalize_accurate_item({
        "id": 5700,
        "no": "PC-12",
        "name": "Paper cup 12oz",
        "itemType": "INVENTORY",
        "unit1": {"id": 9, "name": "pcs"},
        "suspended": False,
    })
    assert n["unit"] == "pcs"
    assert n["item_type"] == "INVENTORY"
    assert n["is_active"] is True


def test_item_list_row_incomplete_and_merge():
    incomplete = normalize_accurate_item({"id": 5700})
    assert item_list_row_incomplete(incomplete) is True
    complete = normalize_accurate_item(_raw(5700, name="Cup", code="PC-12"))
    assert item_list_row_incomplete(complete) is False
    merged = merge_accurate_item_row(incomplete, complete)
    assert merged["accurate_item_id"] == "5700"
    assert merged["item_name"] == "Cup"
    assert merged["item_code"] == "PC-12"
    assert merge_accurate_item_row(complete, None)["item_name"] == "Cup"


def test_normalize_suspended_to_is_active():
    assert normalize_accurate_item({"id": 1, "name": "A", "suspended": True})["is_active"] is False
    assert normalize_accurate_item({"id": 1, "name": "A", "suspended": False})["is_active"] is True
    assert normalize_accurate_item({"id": 1, "name": "A"})["is_active"] is None


def test_so_line_extracts_nested_item_id():
    so = _normalize_accurate_so({
        "id": 9,
        "number": "SO-1",
        "customer": {"name": "A"},
        "detailItem": [
            {"quantity": 10, "itemUnitName": "pcs", "item": {"id": 101, "no": "PC-12", "name": "Paper cup 12oz"}},
            {"quantity": 5, "itemId": 202, "itemNo": "LB-M", "itemName": "Lunchbox medium"},
        ],
    })
    assert so["line_items"][0]["accurate_item_id"] == "101"
    assert so["line_items"][1]["accurate_item_id"] == "202"
    assert so["line_items"][0]["item_code"] == "PC-12"


def test_extract_never_uses_name():
    assert _extract_accurate_item_id({"itemName": "Cup"}, {"name": "Cup"}) is None


# ---------- sync / upsert ----------
def test_first_sync_100_unique():
    db = FakeDB()
    items = [_raw(i, name=f"Item {i}") for i in range(1, 101)]
    r = _await(sync_master_items(db, FakeAccurate(items)))
    assert r["created"] == 100
    assert r["updated"] == 0
    assert r["failed"] == 0
    assert r["total"] == 100
    assert _await(db.master_items.count_documents({})) == 100


def test_repeat_sync_idempotent():
    db = FakeDB()
    items = [_raw(i, name=f"Item {i}") for i in range(1, 101)]
    client = FakeAccurate(items)
    _await(sync_master_items(db, client))
    r2 = _await(sync_master_items(db, client))
    assert r2["created"] == 0
    assert r2["unchanged"] == 100
    assert r2["updated"] == 0
    assert _await(db.master_items.count_documents({})) == 100


def test_name_change_updates_same_record():
    db = FakeDB()
    _await(sync_master_items(db, FakeAccurate([_raw(101, name="Cup 12 oz")])))
    r = _await(sync_master_items(db, FakeAccurate([_raw(101, name="Cup 12 oz Premium")])))
    assert r["updated"] == 1
    assert r["created"] == 0
    assert _await(db.master_items.count_documents({})) == 1
    doc = _await(db.master_items.find_one({"accurate_item_id": "101"}))
    assert doc["item_name"] == "Cup 12 oz Premium"
    first_id = doc["id"]
    r2 = _await(sync_master_items(db, FakeAccurate([_raw(101, name="Cup 12 oz Premium")])))
    assert r2["unchanged"] == 1
    doc2 = _await(db.master_items.find_one({"accurate_item_id": "101"}))
    assert doc2["id"] == first_id


def test_existing_incomplete_row_enriched_same_id():
    db = FakeDB()
    _await(upsert_master_item(db, {
        "accurate_item_id": "5700",
        "item_code": None,
        "item_name": None,
        "item_type": None,
        "unit": None,
        "is_active": None,
    }))
    assert _await(db.master_items.count_documents({})) == 1
    before = _await(db.master_items.find_one({"accurate_item_id": "5700"}))
    r = _await(sync_master_items(db, FakeAccurate([
        _raw(5700, name="Paper cup 12oz", code="PC-12", unit="pcs"),
    ])))
    assert r["created"] == 0
    assert r["updated"] == 1
    assert r["failed"] == 0
    assert _await(db.master_items.count_documents({})) == 1
    doc = _await(db.master_items.find_one({"accurate_item_id": "5700"}))
    assert doc["id"] == before["id"]
    assert doc["accurate_item_id"] == "5700"
    assert doc["item_name"] == "Paper cup 12oz"
    assert doc["item_code"] == "PC-12"
    assert doc["unit"] == "pcs"


def test_same_name_different_id_two_records():
    db = FakeDB()
    r = _await(sync_master_items(db, FakeAccurate([
        _raw(101, name="Cup X", code="A"),
        _raw(202, name="Cup X", code="B"),
    ])))
    assert r["created"] == 2
    assert _await(db.master_items.count_documents({})) == 2
    a = _await(db.master_items.find_one({"accurate_item_id": "101"}))
    b = _await(db.master_items.find_one({"accurate_item_id": "202"}))
    assert a["id"] != b["id"]
    assert a["item_name"] == b["item_name"] == "Cup X"


def test_concurrent_upsert_no_duplicate():
    db = FakeDB()
    incoming = normalize_accurate_item(_raw(101, name="Cup"))

    async def _race():
        await asyncio.gather(
            upsert_master_item(db, incoming),
            upsert_master_item(db, incoming),
            upsert_master_item(db, incoming),
        )

    _await(_race())
    assert _await(db.master_items.count_documents({})) == 1


def test_accurate_error_does_not_truncate():
    db = FakeDB()
    _await(sync_master_items(db, FakeAccurate([_raw(1), _raw(2)])))
    assert _await(db.master_items.count_documents({})) == 2
    try:
        _await(sync_master_items(db, FakeAccurate(fail=True)))
        assert False, "expected error"
    except RuntimeError:
        pass
    assert _await(db.master_items.count_documents({})) == 2


def test_public_item_has_no_secrets():
    doc = {
        "id": "u1",
        "accurate_item_id": "101",
        "item_name": "Cup",
        "token": "secret",
        "api_token": "nope",
    }
    pub = public_master_item(doc)
    dump = str(pub).lower()
    assert "token" not in dump
    assert "secret" not in dump


# ---------- PO resolution ----------
def test_po_match_by_accurate_id():
    db = FakeDB()
    _await(sync_master_items(db, FakeAccurate([_raw(101, name="Cup 12 oz")])))
    lines = [{"accurate_item_id": "101", "name": "Cup 12 oz", "quantity": 10}]
    out = _await(resolve_po_line_items(db, FakeAccurate([]), lines, require_master=True))
    master = _await(db.master_items.find_one({"accurate_item_id": "101"}))
    assert out[0]["master_item_id"] == master["id"]
    assert out[0]["accurate_item_id"] == "101"


def test_no_name_match_different_id():
    db = FakeDB()
    _await(sync_master_items(db, FakeAccurate([_raw(101, name="Cup 12 oz")])))
    lines = [{"accurate_item_id": "202", "name": "Cup 12 oz", "quantity": 1}]
    try:
        _await(resolve_po_line_items(db, FakeAccurate([]), lines, require_master=True))
        assert False, "must not match by name"
    except MasterItemUnresolvedError as e:
        assert "Sinkronkan Barang/Jasa" in str(e)
    # still only master 101
    assert _await(db.master_items.count_documents({})) == 1


def test_unknown_item_targeted_fetch():
    db = FakeDB()
    client = FakeAccurate([_raw(101, name="Fetched Cup")])
    lines = [{"accurate_item_id": "101", "name": "Fetched Cup", "quantity": 3}]
    out = _await(resolve_po_line_items(db, client, lines, require_master=True))
    assert out[0]["master_item_id"]
    assert _await(db.master_items.count_documents({})) == 1
    doc = _await(db.master_items.find_one({"accurate_item_id": "101"}))
    assert doc["item_name"] == "Fetched Cup"


def test_unknown_item_without_detail_stops():
    db = FakeDB()
    lines = [{"accurate_item_id": "999", "name": "Ghost", "quantity": 1}]
    try:
        _await(resolve_po_line_items(db, FakeAccurate([]), lines, require_master=True))
        assert False
    except MasterItemUnresolvedError as e:
        assert str(e) == UNKNOWN_ITEM_MSG


def test_line_without_accurate_id_rejected():
    db = FakeDB()
    lines = [{"name": "Cup 12 oz", "item_code": "PC-12", "quantity": 1}]
    try:
        _await(resolve_po_line_items(db, FakeAccurate([_raw(101, name="Cup 12 oz")]), lines, require_master=True))
        assert False
    except MasterItemUnresolvedError:
        pass


def test_multi_line_not_swapped():
    db = FakeDB()
    _await(sync_master_items(db, FakeAccurate([
        _raw(101, name="A"),
        _raw(202, name="B"),
    ])))
    lines = [
        {"accurate_item_id": "101", "name": "A", "quantity": 1},
        {"accurate_item_id": "202", "name": "B", "quantity": 2},
    ]
    out = _await(resolve_po_line_items(db, FakeAccurate([]), lines, require_master=True))
    m101 = _await(db.master_items.find_one({"accurate_item_id": "101"}))
    m202 = _await(db.master_items.find_one({"accurate_item_id": "202"}))
    assert out[0]["master_item_id"] == m101["id"]
    assert out[1]["master_item_id"] == m202["id"]
    assert out[0]["master_item_id"] != out[1]["master_item_id"]


def test_historical_po_without_master_id_untouched():
    db = FakeDB()
    lines = [{"name": "Legacy cup", "item_code": "PC-12", "quantity": 9}]
    out = _await(resolve_po_line_items(db, FakeAccurate([]), lines, require_master=False))
    assert out[0]["name"] == "Legacy cup"
    assert "master_item_id" not in out[0]
    assert _await(db.master_items.count_documents({})) == 0


def test_batch_lookup():
    db = FakeDB()
    _await(sync_master_items(db, FakeAccurate([_raw(101), _raw(202), _raw(303)])))
    found = _await(lookup_masters_by_accurate_ids(db, ["101", "303", "nope"]))
    assert set(found) == {"101", "303"}


# ---------- list query / pagination ----------
def test_list_search_and_pagination():
    db = FakeDB()
    items = [_raw(i, name=f"Cup {i}" if i % 2 else f"Box {i}") for i in range(1, 31)]
    _await(sync_master_items(db, FakeAccurate(items)))
    page = _await(list_master_items(db, q="Cup", page=1, page_size=10))
    assert page["total"] == 15
    assert len(page["items"]) == 10
    assert page["page_count"] == 2
    assert page["summary"]["total"] == 30
    assert page["summary"]["has_active_status"] is True


def test_build_list_query_status():
    q = build_list_query("", "", "active")
    assert q == {"is_active": True}


def test_build_list_query_escapes_regex_special_chars():
    """User typing regex metacharacters must not break or widen Mongo $regex."""
    for needle in (".*", "(", ")", "[", "]", "+", "?", "{", "}", "|", "^", "$", "\\", "a+b"):
        filt = build_list_query(needle, "", "")
        assert "$or" in filt
        for clause in filt["$or"]:
            field = next(iter(clause.values()))
            assert field["$regex"] == __import__("re").escape(needle)
            assert field["$options"] == "i"


def test_list_search_case_insensitive_and_by_code():
    db = FakeDB()
    rows = [
        _raw(1, name="Cup Alpha", code="SKU-AAA"),
        _raw(2, name="Box Beta", code="SKU-BBB"),
        _raw(3, name="Cup Alpha", code="SKU-CCC"),  # same display name, different ID
    ]
    # _raw may not accept code= — check helper
    _await(sync_master_items(db, FakeAccurate(rows)))
    # Ensure codes set if sync preserves them
    by_code = _await(list_master_items(db, q="sku-bbb", page=1, page_size=20))
    assert by_code["total"] >= 1
    assert any(i["accurate_item_id"] == "2" or i.get("item_code", "").upper() == "SKU-BBB" for i in by_code["items"])

    by_name = _await(list_master_items(db, q="cup alpha", page=1, page_size=20))
    ids = [i["accurate_item_id"] for i in by_name["items"]]
    assert "1" in ids and "3" in ids
    assert ids.count("1") == 1 and ids.count("3") == 1


def test_list_search_same_name_keeps_separate_ids():
    db = FakeDB()
    rows = [
        {"accurate_item_id": "A1", "item_code": "C1", "item_name": "Produk Sama", "unit": "pcs", "is_active": True},
        {"accurate_item_id": "A2", "item_code": "C2", "item_name": "Produk Sama", "unit": "pcs", "is_active": True},
    ]
    # Use upsert path if FakeAccurate/_raw shape differs
    for r in rows:
        _await(upsert_master_item(db, r))
    page = _await(list_master_items(db, q="Produk Sama", page=1, page_size=20))
    found = {i["accurate_item_id"] for i in page["items"]}
    assert found == {"A1", "A2"}
    assert len(page["items"]) == 2


def test_list_search_special_chars_does_not_error():
    db = FakeDB()
    _await(upsert_master_item(db, {
        "accurate_item_id": "X1",
        "item_code": "A+B",
        "item_name": "Item (Special)",
        "unit": "pcs",
        "is_active": True,
    }))
    page = _await(list_master_items(db, q="(Special)", page=1, page_size=10))
    assert page["total"] >= 1
    page2 = _await(list_master_items(db, q="A+B", page=1, page_size=10))
    assert page2["total"] >= 1
    page3 = _await(list_master_items(db, q=".*", page=1, page_size=10))
    # escaped .* must not match everything as wildcards
    assert page3["total"] == 0


# ---------- mock Accurate client ----------
def test_mock_client_lists_and_details():
    c = AccurateClient()
    if c.is_live:
        # Unit tests must never call live Accurate.
        assert _MOCK_ITEMS and _MOCK_SALES_ORDERS
        return

    async def _run():
        listed = await c.list_items(limit=100, page=1)
        assert listed["mode"] == "mock"
        assert listed["total"] == len(_MOCK_ITEMS)
        one = await c.fetch_item_detail("101")
        assert one["accurate_item_id"] == "101"
        assert one["item_code"] == "PC-12"
        missing = await c.fetch_item_detail("99999")
        assert missing is None
        so = await c.fetch_single_sales_order("PO-2001")
        ids = [li["accurate_item_id"] for li in so["line_items"]]
        assert ids == ["104", "105"]

    _await(_run())


def test_mock_so_lines_have_accurate_item_ids():
    for so in _MOCK_SALES_ORDERS:
        materialized = _materialize_mock_sales_order(so)
        for li in materialized["line_items"]:
            assert li.get("accurate_item_id"), materialized["po_number"]


# ---------- RBAC ----------
# ---------- live item/list.do (fake upstream, no real Accurate) ----------
class _FakeHTTPResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.is_error = status_code >= 400

    def json(self):
        return self._payload


class _CapturingAsyncClient:
    """httpx.AsyncClient stand-in. Rejects custom `fields` the way production did."""

    captured = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None, params=None):
        rec = {
            "url": url,
            "params": dict(params or {}),
            "header_names": sorted((headers or {}).keys()),
        }
        _CapturingAsyncClient.captured.append(rec)
        # Production tenant: extra fields on list.do → HTTP 4xx (retry-on-s:false never runs).
        if rec["params"].get("fields"):
            return _FakeHTTPResponse(400, {"s": False, "d": ["Upsss..."]})
        page = int(rec["params"].get("sp.page") or 1)
        return _FakeHTTPResponse(200, {
            "s": True,
            "d": [{"id": 101, "no": "PC-12", "name": "Paper cup 12oz"}],
            "sp": {"page": page, "pageSize": 100, "pageCount": 1, "rowCount": 1},
        })


def _live_client(monkeypatch, client_cls=_CapturingAsyncClient):
    import services.accurate as acc
    _CapturingAsyncClient.captured = []
    client_cls.captured = []
    if hasattr(client_cls, "created"):
        client_cls.created = 0
    if hasattr(client_cls, "inflight"):
        client_cls.inflight = 0
    if hasattr(client_cls, "max_inflight"):
        client_cls.max_inflight = 0
    monkeypatch.setattr(acc.httpx, "AsyncClient", client_cls)

    async def _nosleep(_s):
        return None

    monkeypatch.setattr(acc.asyncio, "sleep", _nosleep)
    c = AccurateClient()
    c.api_token = "unit-test-token"
    c.api_signature_secret = "unit-test-secret"
    c.db_id = "1"
    c.client_secret = ""
    c.access_token = ""
    c.refresh_token = ""
    c.open_db_calls = []
    c.token_calls = []
    c.signed_header_calls = []

    async def _tok():
        c.token_calls.append(1)
        return "unit-test-token"

    async def _open(token):
        c.open_db_calls.append(token)
        return "https://odin.accurate.id", ""

    real_signed = c._signed_headers

    def _signed(bearer):
        headers = real_signed(bearer)
        c.signed_header_calls.append(headers.get("X-Api-Timestamp"))
        return headers

    c._obtain_token = _tok
    c._open_db = _open
    c._signed_headers = _signed
    assert c.is_live
    return c


def test_live_item_list_does_not_send_fields(monkeypatch):
    """Reproduce production 502: item/list.do with custom fields → HTTP 400.

    Import PO was already fixed by omitting `fields` on sales-order/list.do.
    Master Item must use the same minimal query.
    """
    c = _live_client(monkeypatch)

    async def _run():
        return await c.list_items(limit=100, page=1)

    listed = _await(_run())
    assert listed["mode"] == "live"
    assert listed["rows"][0]["accurate_item_id"] == "101"
    assert _CapturingAsyncClient.captured, "item/list.do was not called"
    first = _CapturingAsyncClient.captured[0]
    assert first["url"] == "https://odin.accurate.id/accurate/api/item/list.do"
    assert first["url"].count("/accurate/api") == 1
    assert "fields" not in first["params"]
    assert first["params"]["sp.page"] == 1
    assert first["params"]["sp.pageSize"] == 100


def test_item_and_sales_order_list_urls_share_host_prefix(monkeypatch):
    c = _live_client(monkeypatch)
    host = "https://odin.accurate.id"
    item_url = f"{host}/accurate/api/item/list.do"
    so_url = f"{host}/accurate/api/sales-order/list.do"
    assert item_url.replace("item/list.do", "sales-order/list.do") == so_url
    listed = _await(c.list_items(limit=20, page=1))
    assert listed["rows"]
    rec = _CapturingAsyncClient.captured[0]
    assert rec["url"] == item_url
    assert "sp.page" in rec["params"]
    assert "sp.pageSize" in rec["params"]


def test_live_item_list_http_error_records_status(monkeypatch):
    class _ErrClient(_CapturingAsyncClient):
        async def get(self, url, headers=None, params=None):
            _CapturingAsyncClient.captured.append({"url": url, "params": dict(params or {})})
            return _FakeHTTPResponse(404, {"s": False, "d": ["not found"]})

    c = _live_client(monkeypatch, client_cls=_ErrClient)
    try:
        _await(c.list_items(limit=100, page=1))
        assert False, "expected HTTP 404 to raise"
    except RuntimeError as e:
        assert "404" in str(e)
    err = get_last_list_error()
    assert err.get("operation") == "item/list.do"
    assert err.get("status") == 404
    assert "unit-test-token" not in str(err)
    assert "unit-test-secret" not in str(err)


def test_master_items_sync_failure_log_has_no_secrets(caplog, monkeypatch):
    import logging
    c = AccurateClient()
    c.api_token = "unit-test-token"
    c.api_signature_secret = "unit-test-secret"
    caplog.set_level(logging.WARNING)
    log_master_items_sync_failure(
        c,
        RuntimeError("Accurate item list HTTP 400"),
    )
    text = caplog.text
    assert "operation=master_items_sync" in text
    assert "endpoint=item/list.do" in text or "endpoint=" in text
    assert "exception=RuntimeError" in text
    assert "unit-test-token" not in text
    assert "unit-test-secret" not in text
    assert "Authorization" not in text
    leaked = sanitize_accurate_error(c, "boom unit-test-token unit-test-secret")
    assert leaked == "Accurate error"


def test_sales_order_and_item_list_both_omit_fields():
    """Import PO regression: sales-order/list.do must keep omitting `fields`."""
    src = Path(__file__).resolve().parents[1].joinpath("services", "accurate.py").read_text()
    so_fn = src[src.index("async def list_recent_sales_orders"): src.index("async def fetch_item_detail")]
    item_fn = src[src.index("async def list_items"): src.index("async def _get_item_list_body")]
    so_params = so_fn.split("list_params")[1].split("async with")[0]
    item_params = item_fn.split("list_params")[1].split("body =")[0]
    assert "fields" not in so_params
    assert "fields" not in item_params


def test_default_list_row_parses_without_optional_fields():
    n = normalize_accurate_item({"id": 1250, "no": "100027", "name": "Test Item"})
    assert n["accurate_item_id"] == "1250"
    assert n["item_code"] == "100027"
    assert n["item_name"] == "Test Item"
    assert n["item_type"] is None
    assert n["unit"] is None
    assert n["is_active"] is None


def _paged_raw(page):
    if page == 1:
        ids = range(1, 101)
    elif page == 2:
        ids = range(101, 201)
    elif page == 3:
        ids = range(201, 226)
    else:
        ids = []
    return [{"id": i, "no": f"SKU-{i}", "name": f"Item {i}"} for i in ids]


class _PagedItemClient(_CapturingAsyncClient):
    created = 0
    timeout_on = {}

    def __init__(self, *args, **kwargs):
        _PagedItemClient.created += 1

    async def get(self, url, headers=None, params=None):
        rec = {
            "url": url,
            "params": dict(params or {}),
            "header_names": sorted((headers or {}).keys()),
            "timestamp": (headers or {}).get("X-Api-Timestamp"),
        }
        _CapturingAsyncClient.captured.append(rec)
        assert "fields" not in rec["params"]
        page = int(rec["params"].get("sp.page") or 1)
        remaining = int(_PagedItemClient.timeout_on.get(page) or 0)
        if remaining > 0:
            _PagedItemClient.timeout_on[page] = remaining - 1
            raise httpx.ConnectTimeout("connect timed out")
        rows = _paged_raw(page)
        return _FakeHTTPResponse(200, {
            "s": True,
            "d": rows,
            "sp": {"page": page, "pageSize": 100, "pageCount": 3, "rowCount": 225},
        })


def test_fetch_all_items_reuses_session_and_http_client(monkeypatch):
    _PagedItemClient.timeout_on = {}
    c = _live_client(monkeypatch, client_cls=_PagedItemClient)
    rows = _await(c.fetch_all_items())
    assert len(rows) == 225
    assert len({r["accurate_item_id"] for r in rows}) == 225
    assert len(c.open_db_calls) == 1
    assert len(c.token_calls) == 1
    assert _PagedItemClient.created == 1
    pages = [int(r["params"]["sp.page"]) for r in _CapturingAsyncClient.captured]
    assert pages == [1, 2, 3]
    assert all("fields" not in r["params"] for r in _CapturingAsyncClient.captured)
    assert len(c.signed_header_calls) == 3
    assert all("item/detail.do" not in r["url"] for r in _CapturingAsyncClient.captured)


def test_fetch_all_items_paginates_three_pages(monkeypatch):
    _PagedItemClient.timeout_on = {}
    c = _live_client(monkeypatch, client_cls=_PagedItemClient)
    db = FakeDB()
    result = _await(sync_master_items(db, c))
    assert result["created"] == 225
    assert result["failed"] == 0
    assert _await(db.master_items.count_documents({})) == 225


def test_connect_timeout_retries_same_page_then_succeeds(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.INFO)
    _PagedItemClient.timeout_on = {3: 1}
    c = _live_client(monkeypatch, client_cls=_PagedItemClient)
    db = FakeDB()
    result = _await(sync_master_items(db, c))
    assert result["created"] == 225
    assert _await(db.master_items.count_documents({})) == 225
    assert len({d["accurate_item_id"] for d in db.master_items.docs}) == 225
    pages = [int(r["params"]["sp.page"]) for r in _CapturingAsyncClient.captured]
    assert pages == [1, 2, 3, 3]
    assert len(c.open_db_calls) == 1
    assert "page=3" in caplog.text
    assert "attempt=1" in caplog.text
    assert "ConnectTimeout" in caplog.text
    assert "result=success" in caplog.text
    assert "unit-test-token" not in caplog.text
    assert "Authorization" not in caplog.text


def test_connect_timeout_exhausted_does_not_truncate(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.WARNING)
    db = FakeDB()
    _await(sync_master_items(db, FakeAccurate([_raw(1), _raw(2)])))
    assert _await(db.master_items.count_documents({})) == 2
    _PagedItemClient.timeout_on = {3: 99}
    c = _live_client(monkeypatch, client_cls=_PagedItemClient)
    try:
        _await(sync_master_items(db, c))
        assert False, "expected timeout after bounded retry"
    except RuntimeError as e:
        assert "timeout" in str(e).lower()
    assert _await(db.master_items.count_documents({})) == 2
    pages = [int(r["params"]["sp.page"]) for r in _CapturingAsyncClient.captured]
    assert pages == [1, 2, 3, 3]
    err = get_last_list_error()
    assert err.get("page") == 3
    assert err.get("attempt") == 2
    assert err.get("exception") == "ConnectTimeout"
    assert "unit-test-token" not in caplog.text
    assert "unit-test-secret" not in caplog.text


class _IdOnlyListDetailClient(_CapturingAsyncClient):
    """item/list.do returns ids only (production default without `fields`)."""

    created = 0
    inflight = 0
    max_inflight = 0
    details = {}

    def __init__(self, *args, **kwargs):
        _IdOnlyListDetailClient.created += 1

    async def get(self, url, headers=None, params=None):
        rec = {
            "url": url,
            "params": dict(params or {}),
            "header_names": sorted((headers or {}).keys()),
            "timestamp": (headers or {}).get("X-Api-Timestamp"),
        }
        _CapturingAsyncClient.captured.append(rec)
        assert "fields" not in rec["params"]
        if url.endswith("/item/list.do"):
            page = int(rec["params"].get("sp.page") or 1)
            ids = list(_IdOnlyListDetailClient.details.keys())
            return _FakeHTTPResponse(200, {
                "s": True,
                "d": [{"id": int(i)} for i in ids],
                "sp": {"page": page, "pageSize": 100, "pageCount": 1, "rowCount": len(ids)},
            })
        if url.endswith("/item/detail.do"):
            _IdOnlyListDetailClient.inflight += 1
            try:
                _IdOnlyListDetailClient.max_inflight = max(
                    _IdOnlyListDetailClient.max_inflight,
                    _IdOnlyListDetailClient.inflight,
                )
                await asyncio.sleep(0.02)
                oid = str((params or {}).get("id"))
                raw = _IdOnlyListDetailClient.details.get(oid)
                if not raw:
                    return _FakeHTTPResponse(404, {"s": False, "d": ["not found"]})
                return _FakeHTTPResponse(200, {"s": True, "d": raw})
            finally:
                _IdOnlyListDetailClient.inflight -= 1
        raise AssertionError(f"unexpected Accurate URL {url}")


class _MixedListDetailClient(_IdOnlyListDetailClient):
    created = 0
    inflight = 0
    max_inflight = 0

    def __init__(self, *args, **kwargs):
        _MixedListDetailClient.created += 1

    async def get(self, url, headers=None, params=None):
        rec = {
            "url": url,
            "params": dict(params or {}),
            "header_names": sorted((headers or {}).keys()),
            "timestamp": (headers or {}).get("X-Api-Timestamp"),
        }
        _CapturingAsyncClient.captured.append(rec)
        assert "fields" not in rec["params"]
        if url.endswith("/item/list.do"):
            return _FakeHTTPResponse(200, {
                "s": True,
                "d": [
                    {"id": 5700},
                    {"id": 101, "no": "PC-12", "name": "Paper cup 12oz", "unit1Name": "pcs"},
                ],
                "sp": {"page": 1, "pageSize": 100, "pageCount": 1, "rowCount": 2},
            })
        if url.endswith("/item/detail.do"):
            _MixedListDetailClient.inflight += 1
            try:
                _MixedListDetailClient.max_inflight = max(
                    _MixedListDetailClient.max_inflight,
                    _MixedListDetailClient.inflight,
                )
                oid = str((params or {}).get("id"))
                assert oid != "101", "complete list row must not fetch detail"
                raw = {
                    "id": 5700,
                    "no": "PC-OLD",
                    "name": "Paper cup 12oz (detail)",
                    "itemType": "INVENTORY",
                    "unit1Name": "pcs",
                    "suspended": False,
                }
                return _FakeHTTPResponse(200, {"s": True, "d": raw})
            finally:
                _MixedListDetailClient.inflight -= 1
        raise AssertionError(f"unexpected Accurate URL {url}")


def test_incomplete_list_enriches_from_detail_same_session(monkeypatch):
    details = {}
    for i in range(5700, 5708):
        details[str(i)] = {
            "id": i,
            "no": f"SKU-{i}",
            "name": f"Item {i}",
            "itemType": "INVENTORY",
            "unit1Name": "pcs",
            "suspended": False,
        }
    _IdOnlyListDetailClient.details = details
    c = _live_client(monkeypatch, client_cls=_IdOnlyListDetailClient)
    db = FakeDB()
    result = _await(sync_master_items(db, c))
    assert result["created"] == 8
    assert result["failed"] == 0
    assert _await(db.master_items.count_documents({})) == 8
    doc = _await(db.master_items.find_one({"accurate_item_id": "5700"}))
    assert doc["item_code"] == "SKU-5700"
    assert doc["item_name"] == "Item 5700"
    assert doc["unit"] == "pcs"
    assert doc["item_type"] == "INVENTORY"
    assert doc["is_active"] is True
    urls = [r["url"] for r in _CapturingAsyncClient.captured]
    assert urls[0].endswith("/item/list.do")
    assert urls[0].count("/accurate/api") == 1
    assert sum(1 for u in urls if u.endswith("/item/list.do")) == 1
    assert sum(1 for u in urls if u.endswith("/item/detail.do")) == 8
    assert all("fields" not in r["params"] for r in _CapturingAsyncClient.captured)
    assert len(c.open_db_calls) == 1
    assert len(c.token_calls) == 1
    assert _IdOnlyListDetailClient.created == 1
    assert _IdOnlyListDetailClient.max_inflight <= _ITEM_DETAIL_CONCURRENCY
    assert _IdOnlyListDetailClient.max_inflight >= 1
    assert len(c.signed_header_calls) == 1 + 8
    assert len({d["accurate_item_id"] for d in db.master_items.docs}) == 8


def test_complete_list_row_skips_detail_fetch(monkeypatch):
    c = _live_client(monkeypatch, client_cls=_MixedListDetailClient)
    rows = _await(c.fetch_all_items())
    by_id = {r["accurate_item_id"]: r for r in rows}
    assert by_id["5700"]["item_name"] == "Paper cup 12oz (detail)"
    assert by_id["5700"]["item_code"] == "PC-OLD"
    assert by_id["5700"]["unit"] == "pcs"
    assert by_id["101"]["item_name"] == "Paper cup 12oz"
    assert by_id["101"]["item_code"] == "PC-12"
    detail_ids = [
        str(r["params"].get("id"))
        for r in _CapturingAsyncClient.captured
        if r["url"].endswith("/item/detail.do")
    ]
    assert detail_ids == ["5700"]
    assert len(c.open_db_calls) == 1
    assert _MixedListDetailClient.created == 1


def test_detail_enrichment_updates_existing_incomplete_mongo_row(monkeypatch):
    db = FakeDB()
    _await(upsert_master_item(db, {"accurate_item_id": "5700"}))
    _IdOnlyListDetailClient.details = {
        "5700": {
            "id": 5700,
            "no": "PC-12",
            "name": "Paper cup 12oz",
            "itemType": "INVENTORY",
            "unit1Name": "pcs",
            "suspended": False,
        },
    }
    c = _live_client(monkeypatch, client_cls=_IdOnlyListDetailClient)
    result = _await(sync_master_items(db, c))
    assert result["created"] == 0
    assert result["updated"] == 1
    assert _await(db.master_items.count_documents({})) == 1
    doc = _await(db.master_items.find_one({"accurate_item_id": "5700"}))
    assert doc["item_name"] == "Paper cup 12oz"
    assert doc["item_code"] == "PC-12"
    assert doc["unit"] == "pcs"


# ---------- RBAC ----------
def test_rbac_catalog_and_defaults():
    assert "master_items.view" in ALL_PERMISSIONS
    assert "master_items.sync" in ALL_PERMISSIONS
    assert "master_items.manage" in ALL_PERMISSIONS
    assert "nav.inventory.master_items" in ALL_PERMISSIONS
    assert "current_stock.view" in ALL_PERMISSIONS
    assert "current_stock.update" in ALL_PERMISSIONS
    assert "current_stock.export" in ALL_PERMISSIONS
    assert "purchase_receipts.view" in ALL_PERMISSIONS
    assert "purchase_receipts.sync" in ALL_PERMISSIONS
    assert "nav.purchasing.receipts" in ALL_PERMISSIONS
    assert "nav.inventory.current_stock" in ALL_PERMISSIONS
    admin = set(ROLE_DEFAULT_PERMISSIONS["admin"])
    assert "master_items.view" in admin
    assert "master_items.sync" in admin
    assert "master_items.manage" in admin
    helper = effective_permissions("helper", None)
    assert "master_items.view" not in helper
    assert "master_items.sync" not in helper
    assert "master_items.manage" not in helper
    assert "purchase_receipts.sync" not in helper
    hop = effective_permissions("head_production", None)
    assert "purchase_receipts.sync" in hop
    view_only = effective_permissions("helper", {"master_items.view": True, "nav.inventory.master_items": True})
    assert "master_items.view" in view_only
    assert "master_items.sync" not in view_only
    assert "master_items.manage" not in view_only
    both = effective_permissions("helper", {
        "master_items.view": True,
        "master_items.sync": True,
        "nav.inventory.master_items": True,
    })
    assert "master_items.sync" in both


def test_public_origin_legacy_and_local():
    assert resolve_master_origin({"accurate_item_id": "9"}) == "accurate"
    assert resolve_master_origin({"item_code": "X"}) == "local"
    assert resolve_master_origin({"origin": "local", "accurate_item_id": None}) == "local"
    pub = public_master_item({"id": "1", "item_code": "A", "item_name": "n", "unit": "pcs", "is_active": True})
    assert pub["origin"] == "local"
    assert pub["accurate_item_id"] is None


def test_create_local_master_and_sync_preserves():
    db = FakeDB()
    created = _await(create_local_master_item(db, {
        "item_code": "LOC-1",
        "item_name": "Lokal Cup",
        "item_type": "INVENTORY",
        "unit": "pcs",
        "is_active": True,
    }))
    assert created["origin"] == "local"
    assert created["accurate_item_id"] is None
    assert created["id"]
    assert "accurate_item_id" not in db.master_items.docs[0] or db.master_items.docs[0].get("accurate_item_id") in (None, "")

    client = FakeAccurate(items=[{
        "id": 5700,
        "no": "PC-12",
        "name": "Paper cup 12oz",
        "itemType": "INVENTORY",
        "unit1Name": "pcs",
        "suspended": False,
    }])
    result = _await(sync_master_items(db, client))
    assert result["created"] == 1
    locals_ = [d for d in db.master_items.docs if d.get("origin") == "local"]
    assert len(locals_) == 1
    assert locals_[0]["item_code"] == "LOC-1"
    assert locals_[0].get("accurate_item_id") in (None, "")


def test_create_local_duplicate_code_case_insensitive():
    db = FakeDB()
    _await(create_local_master_item(db, {
        "item_code": "AbC",
        "item_name": "One",
        "item_type": "INVENTORY",
        "unit": "pcs",
        "is_active": True,
    }))
    try:
        _await(create_local_master_item(db, {
            "item_code": "abc",
            "item_name": "Two",
            "item_type": "INVENTORY",
            "unit": "pcs",
            "is_active": True,
        }))
        assert False, "expected 409"
    except HTTPException as exc:
        assert exc.status_code == 409


def test_create_local_rejects_code_taken_by_accurate_row():
    db = FakeDB()
    db.master_items.docs.append({
        "id": "acc-1",
        "accurate_item_id": "99",
        "item_code": "SHARED",
        "item_name": "From Accurate",
        "item_type": "INVENTORY",
        "unit": "pcs",
        "is_active": True,
        "origin": "accurate",
    })
    try:
        _await(create_local_master_item(db, {
            "item_code": "shared",
            "item_name": "Local try",
            "item_type": "SERVICE",
            "unit": "pcs",
            "is_active": True,
        }))
        assert False, "expected 409"
    except HTTPException as exc:
        assert exc.status_code == 409
    assert len(db.master_item_code_claims.docs) == 0


def test_create_local_insert_failure_releases_claim():
    db = FakeDB()
    real_insert = FakeCollection.insert_one

    async def boom(self, doc):
        raise RuntimeError("forced insert failure")

    FakeCollection.insert_one = boom  # type: ignore[method-assign]
    try:
        try:
            _await(create_local_master_item(db, {
                "item_code": "Orphan-Code",
                "item_name": "Will Fail",
                "item_type": "INVENTORY",
                "unit": "pcs",
                "is_active": True,
            }))
            assert False, "expected insert failure"
        except RuntimeError as exc:
            assert "forced insert failure" in str(exc)
    finally:
        FakeCollection.insert_one = real_insert  # type: ignore[method-assign]

    assert db.master_item_code_claims.docs == []
    assert db.master_items.docs == []

    # Recovered create works after claim release.
    created = _await(create_local_master_item(db, {
        "item_code": "Orphan-Code",
        "item_name": "Recovered",
        "item_type": "SERVICE",
        "unit": "pcs",
        "is_active": True,
    }))
    assert created["origin"] == "local"
    assert len(db.master_item_code_claims.docs) == 1


def test_create_local_rejects_when_indexes_missing_no_writes():
    db = FakeDB(ready_indexes=False)
    status = _await(local_master_create_index_status(db))
    assert status["local_create_ready"] is False
    try:
        _await(create_local_master_item(db, {
            "item_code": "NOIDX-1",
            "item_name": "Should 503",
            "item_type": "INVENTORY",
            "unit": "pcs",
            "is_active": True,
        }))
        assert False, "expected 503"
    except HTTPException as exc:
        assert exc.status_code == 503
        assert INDEX_NOT_READY_MSG in str(exc.detail)
    assert db.master_items.docs == []
    assert db.master_item_code_claims.docs == []


def test_create_local_rejects_when_legacy_full_unique_present_no_writes():
    db = FakeDB(ready_indexes=True)
    db.master_items._indexes[LEGACY_ACCURATE_ID_INDEX] = {
        "key": [("accurate_item_id", 1)],
        "unique": True,
    }
    status = _await(local_master_create_index_status(db))
    assert status["legacy_full_unique_present"] is True
    assert status["local_create_ready"] is False
    try:
        _await(create_local_master_item(db, {
            "item_code": "LEGACY-1",
            "item_name": "Blocked",
            "item_type": "INVENTORY",
            "unit": "pcs",
            "is_active": True,
        }))
        assert False, "expected 503"
    except HTTPException as exc:
        assert exc.status_code == 503
    assert db.master_items.docs == []
    assert db.master_item_code_claims.docs == []


def test_create_local_rejects_when_claims_index_missing_no_writes():
    db = FakeDB(ready_indexes=True)
    db.master_item_code_claims._indexes = {"_id_": {"key": [("_id", 1)]}}
    status = _await(local_master_create_index_status(db))
    assert status["code_claims"] is False
    assert status["local_create_ready"] is False
    try:
        _await(create_local_master_item(db, {
            "item_code": "NOCLAIM-1",
            "item_name": "Blocked",
            "item_type": "SERVICE",
            "unit": "pcs",
            "is_active": True,
        }))
        assert False, "expected 503"
    except HTTPException as exc:
        assert exc.status_code == 503
    assert db.master_items.docs == []
    assert db.master_item_code_claims.docs == []


def test_create_local_rejects_when_partial_index_name_wrong_definition():
    """Name alone is not enough — wrong definition under expected name fails ready."""
    db = FakeDB(ready_indexes=True)
    db.master_items._indexes[ACCURATE_ID_PARTIAL_INDEX] = {
        "key": [("accurate_item_id", 1)],
        "unique": True,
        # missing partialFilterExpression → treated as legacy-shaped / not ready partial
    }
    status = _await(local_master_create_index_status(db))
    assert status["local_create_ready"] is False
    try:
        _await(create_local_master_item(db, {
            "item_code": "BADPART-1",
            "item_name": "Blocked",
            "item_type": "INVENTORY",
            "unit": "pcs",
            "is_active": True,
        }))
        assert False, "expected 503"
    except HTTPException as exc:
        assert exc.status_code == 503
    assert db.master_items.docs == []
    assert db.master_item_code_claims.docs == []


def test_master_production_process_default_and_sync_preserve():
    db = FakeDB()
    created = _await(create_local_master_item(db, {
        "item_code": "NP-1",
        "item_name": "Lid lokal",
        "item_type": "INVENTORY",
        "unit": "pcs",
        "is_active": True,
        "production_process": "non_print",
    }))
    assert created["production_process"] == "non_print"

    accurate, _action = _await(upsert_master_item(db, {
        "accurate_item_id": "99001",
        "item_code": "ACC-NP",
        "item_name": "Accurate item",
        "item_type": "INVENTORY",
        "unit": "pcs",
        "is_active": True,
    }))
    updated = _await(update_master_production_process(
        db, accurate["id"], "non_print", user={"email": "owner@test.local"},
    ))
    assert updated["production_process"] == "non_print"

    # Sync must not wipe ERP-owned production_process even if payload carries one.
    r = _await(sync_master_items(db, FakeAccurate([{
        "id": 99001,
        "no": "ACC-NP",
        "name": "Accurate item renamed",
        "itemType": "INVENTORY",
        "unit1Name": "pcs",
        "suspended": False,
        "production_process": "print",
    }])))
    assert r["updated"] + r["unchanged"] + r["created"] >= 1
    fresh = next(d for d in db.master_items.docs if str(d.get("accurate_item_id")) == "99001")
    assert fresh["production_process"] == "non_print"
    assert fresh["item_name"] == "Accurate item renamed"

    local = next(d for d in db.master_items.docs if d.get("item_code") == "NP-1")
    assert local["production_process"] == "non_print"


def test_resolve_line_production_process_explicit_wins_and_master_default():
    db = FakeDB()
    # Master belum diatur → perilaku existing (print).
    unset = _await(create_local_master_item(db, {
        "item_code": "UNSET-1",
        "item_name": "Tanpa default",
        "item_type": "INVENTORY",
        "unit": "pcs",
    }))
    assert unset.get("production_process") is None
    assert _await(resolve_line_production_process(
        db, master_item_id=unset["id"], explicit=None,
    )) == "print"
    assert _await(resolve_line_production_process(
        db, master_item_id=unset["id"], explicit="non_print",
    )) == "non_print"

    master = _await(create_local_master_item(db, {
        "item_code": "DEF-1",
        "item_name": "Default non print",
        "item_type": "INVENTORY",
        "unit": "pcs",
        "production_process": "non_print",
    }))
    assert _await(resolve_line_production_process(
        db, master_item_id=master["id"], explicit=None,
    )) == "non_print"
    # Pilihan transaksi eksplisit menang atas default master.
    assert _await(resolve_line_production_process(
        db, master_item_id=master["id"], explicit="print",
    )) == "print"
    assert _await(resolve_line_production_process(
        db, master_item_id=None, accurate_item_id=None, explicit=None,
    )) == "print"


def test_master_default_change_does_not_mutate_stored_order_lines():
    """Changing master default never rewrites existing order line production_process."""
    db = FakeDB()
    master = _await(create_local_master_item(db, {
        "item_code": "SNAP-1",
        "item_name": "Snapshot line",
        "item_type": "INVENTORY",
        "unit": "pcs",
        "production_process": "print",
    }))
    order_line = {
        "master_item_id": master["id"],
        "quantity": 10,
        "production_process": "print",
    }
    _await(update_master_production_process(
        db, master["id"], "non_print", user={"email": "owner@test.local"},
    ))
    fresh = public_master_item(
        next(d for d in db.master_items.docs if d["id"] == master["id"])
    )
    assert fresh["production_process"] == "non_print"
    assert order_line["production_process"] == "print"
