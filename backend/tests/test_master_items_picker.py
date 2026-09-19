"""Targeted tests for GET /master-items?picker=true fast path."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.master_items import (
    COLLECTION,
    list_master_items,
    list_master_items_picker,
    upsert_master_item,
)


def _await(coro):
    return asyncio.run(coro)


class CountingCursor:
    def __init__(self, docs, counts):
        self._docs = [dict(d) for d in docs]
        self._counts = counts

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


class CountingCollection:
    def __init__(self):
        self.docs = []
        self.counts = {
            "find": 0,
            "count_documents": 0,
            "distinct": 0,
        }

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
                if "$in" in v and dv not in v["$in"]:
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
        out = dict(doc)
        if proj and proj.get("_id") == 0:
            out.pop("_id", None)
        return out

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if self._match(d, query):
                return self._proj(d, proj)
        return None

    def find(self, query, proj=None):
        self.counts["find"] += 1
        matched = [self._proj(d, proj) for d in self.docs if self._match(d, query)]
        return CountingCursor(matched, self.counts)

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
        self.docs.append(new)

    async def count_documents(self, query):
        self.counts["count_documents"] += 1
        return sum(1 for d in self.docs if self._match(d, query))

    async def distinct(self, field):
        self.counts["distinct"] += 1
        vals = []
        for d in self.docs:
            v = d.get(field)
            if v not in vals:
                vals.append(v)
        return vals


class CountingDB:
    def __init__(self):
        self.master_items = CountingCollection()

    def __getitem__(self, name):
        if name == COLLECTION:
            return self.master_items
        raise KeyError(name)


def _seed(db):
    rows = [
        {
            "accurate_item_id": "A100023",
            "item_code": "100023",
            "item_name": "LID PET Strawless PLP",
            "unit": "pcs",
            "is_active": True,
            "item_type": "INVENTORY",
        },
        {
            "accurate_item_id": "A100123",
            "item_code": "100123",
            "item_name": "LID PET Strawless Starindo",
            "unit": "pcs",
            "is_active": True,
            "item_type": "INVENTORY",
        },
        {
            "accurate_item_id": "A9",
            "item_code": "CUP-16",
            "item_name": "Cup 16oz",
            "unit": "pcs",
            "is_active": True,
            "item_type": "INVENTORY",
        },
        {
            "accurate_item_id": "AINACT",
            "item_code": "100099",
            "item_name": "LID PET Inactive",
            "unit": "pcs",
            "is_active": False,
            "item_type": "INVENTORY",
        },
    ]
    for r in rows:
        _await(upsert_master_item(db, r))
    db.master_items.counts = {"find": 0, "count_documents": 0, "distinct": 0}


def test_picker_q_lid_returns_relevant_items():
    db = CountingDB()
    _seed(db)
    page = _await(list_master_items_picker(db, q="lid", status="active", page_size=40))
    names = [i["item_name"] for i in page["items"]]
    assert "LID PET Strawless PLP" in names
    assert "LID PET Strawless Starindo" in names
    assert "LID PET Inactive" not in names
    assert "summary" not in page
    assert "item_types" not in page
    assert "total" not in page
    assert "page_count" not in page


def test_picker_q_lid_pet_returns_200_shape():
    db = CountingDB()
    _seed(db)
    page = _await(list_master_items_picker(db, q="LID PET", status="active", page_size=40))
    assert any(i["item_name"] == "LID PET Strawless PLP" for i in page["items"])


def test_picker_q_100023_finds_plp():
    db = CountingDB()
    _seed(db)
    page = _await(list_master_items_picker(db, q="100023", status="active", page_size=40))
    assert len(page["items"]) == 1
    assert page["items"][0]["item_code"] == "100023"
    assert page["items"][0]["item_name"] == "LID PET Strawless PLP"


def test_picker_no_result_empty_items():
    db = CountingDB()
    _seed(db)
    page = _await(list_master_items_picker(db, q="barang-tidak-ada-xyz", status="active", page_size=40))
    assert page["items"] == []


def test_picker_active_excludes_inactive():
    db = CountingDB()
    _seed(db)
    page = _await(list_master_items_picker(db, q="LID PET", status="active", page_size=40))
    assert all(i.get("is_active") is True for i in page["items"])
    assert not any(i["item_name"] == "LID PET Inactive" for i in page["items"])


def test_picker_path_is_single_find_no_summary_ops():
    db = CountingDB()
    _seed(db)
    _await(list_master_items_picker(db, q="lid", status="active", page_size=40))
    assert db.master_items.counts["find"] == 1
    assert db.master_items.counts["count_documents"] == 0
    assert db.master_items.counts["distinct"] == 0


def test_full_list_without_picker_keeps_existing_payload_and_heavy_ops():
    db = CountingDB()
    _seed(db)
    page = _await(list_master_items(db, q="lid", status="active", page=1, page_size=40))
    assert "items" in page
    assert "total" in page
    assert "page" in page
    assert "page_size" in page
    assert "page_count" in page
    assert "summary" in page
    assert "item_types" in page
    assert page["summary"]["total"] == 4
    assert "INVENTORY" in page["item_types"]
    assert db.master_items.counts["count_documents"] >= 4
    assert db.master_items.counts["distinct"] == 1
    assert db.master_items.counts["find"] >= 2


def test_picker_caps_at_40():
    db = CountingDB()
    for i in range(50):
        _await(upsert_master_item(db, {
            "accurate_item_id": f"N{i}",
            "item_code": f"C{i:03d}",
            "item_name": f"Lid item {i}",
            "unit": "pcs",
            "is_active": True,
        }))
    db.master_items.counts = {"find": 0, "count_documents": 0, "distinct": 0}
    page = _await(list_master_items_picker(db, q="Lid", status="active", page_size=999))
    assert len(page["items"]) == 40
    assert db.master_items.counts["find"] == 1
    assert db.master_items.counts["count_documents"] == 0
    assert db.master_items.counts["distinct"] == 0


def test_route_branches_to_picker_before_heavy_list():
    src = Path(__file__).resolve().parents[1].joinpath("server.py").read_text()
    start = src.index("async def master_items_get")
    end = src.index("@api_router.post(\"/master-items/sync\")")
    block = src[start:end]
    assert "picker: bool = False" in block
    assert "if picker:" in block
    assert "list_master_items_picker" in block
    assert block.index("if picker:") < block.index("return await list_master_items(")
