"""Master Barang/Jasa: Accurate item sync, idempotent upsert, PO resolution.

Uses in-memory fake Mongo + fake Accurate client. Does not hit live Accurate.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS, effective_permissions
from services.accurate import (
    AccurateClient,
    _MOCK_ITEMS,
    _MOCK_SALES_ORDERS,
    _extract_accurate_item_id,
    _normalize_accurate_so,
    normalize_accurate_item,
)
from services.master_items import (
    COLLECTION,
    UNKNOWN_ITEM_MSG,
    MasterItemUnresolvedError,
    build_list_query,
    list_master_items,
    lookup_masters_by_accurate_ids,
    public_master_item,
    resolve_po_line_items,
    sync_master_items,
    upsert_master_item,
)


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
    def __init__(self):
        self.docs = []

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


class FakeDB:
    def __init__(self):
        self.master_items = FakeCollection()

    def __getitem__(self, name):
        if name == COLLECTION:
            return self.master_items
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
        assert "Sinkronkan Master Barang/Jasa" in str(e)
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
        for li in so["line_items"]:
            assert li.get("accurate_item_id"), so["po_number"]


# ---------- RBAC ----------
def test_rbac_catalog_and_defaults():
    assert "master_items.view" in ALL_PERMISSIONS
    assert "master_items.sync" in ALL_PERMISSIONS
    assert "nav.inventory.master_items" in ALL_PERMISSIONS
    admin = set(ROLE_DEFAULT_PERMISSIONS["admin"])
    assert "master_items.view" in admin
    assert "master_items.sync" in admin
    helper = effective_permissions("helper", None)
    assert "master_items.view" not in helper
    assert "master_items.sync" not in helper
    view_only = effective_permissions("helper", {"master_items.view": True, "nav.inventory.master_items": True})
    assert "master_items.view" in view_only
    assert "master_items.sync" not in view_only
    both = effective_permissions("helper", {
        "master_items.view": True,
        "master_items.sync": True,
        "nav.inventory.master_items": True,
    })
    assert "master_items.sync" in both
