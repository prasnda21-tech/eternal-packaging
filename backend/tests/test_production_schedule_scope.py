"""Qty/work_date default scope — not stage — plus staged page semantics."""
import asyncio
from datetime import datetime, timezone, timedelta

from services.production_dashboard_http import (
    fetch_production_schedule_orders_by_ids,
    fetch_production_schedule_orders_page,
    parse_reconcile_ids,
    scoped_read_meta,
    wib_month_date_bounds,
)
from services.production_schedule_scope import (
    order_matches_default_production_schedule_scope,
    work_date_keys,
)


WIB = timezone(timedelta(hours=7))
TODAY = "2026-11-15"


def _split(qty, done, work_date, *, line_idx=0, printable=True, **extra):
    return {
        "id": extra.pop("id", f"s-{work_date}-{line_idx}"),
        "quantity": qty,
        "qty_completed": done,
        "work_date": work_date,
        "line_idx": line_idx,
        "line_printable": printable,
        "operator_name": extra.pop("operator_name", "Op"),
        **extra,
    }


def _order(oid, **extra):
    base = {
        "id": oid,
        "cancelled": False,
        "current_stage": 8,
        "work_date": None,
        "line_items": [{"production_process": "print"}],
        "operator_splits": [],
    }
    base.update(extra)
    return base


def test_current_month_100_percent_included():
    o = _order(
        "cur-done",
        work_date="2026-11-02",
        operator_splits=[_split(1000, 1000, "2026-11-02")],
    )
    assert order_matches_default_production_schedule_scope(o, today_key=TODAY) is True


def test_prior_month_partial_included_even_if_stage_14():
    o = _order(
        "old-partial",
        current_stage=14,
        work_date="2026-09-01",
        operator_splits=[_split(1000, 400, "2026-09-01")],
    )
    assert order_matches_default_production_schedule_scope(o, today_key=TODAY) is True


def test_prior_month_proven_100_excluded_even_if_stage_lt_14():
    o = _order(
        "old-done",
        current_stage=8,
        work_date="2026-09-01",
        operator_splits=[_split(1000, 1000, "2026-09-01")],
    )
    assert order_matches_default_production_schedule_scope(o, today_key=TODAY) is False


def test_unknown_progress_prior_month_included():
    o = _order(
        "unknown",
        work_date="2026-08-01",
        operator_splits=[{
            "id": "s1",
            "work_date": "2026-08-01",
            "line_idx": 0,
            "line_printable": True,
            # no quantity → no measurable unit
        }],
    )
    assert work_date_keys(o)[0] == "2026-08-01"
    assert "2026-08-01" in work_date_keys(o)
    assert order_matches_default_production_schedule_scope(o, today_key=TODAY) is True


def test_unscheduled_aktif_included_selesai_excluded():
    aktif = _order("u1", current_stage=8, work_date=None, operator_splits=[])
    selesai = _order("u2", current_stage=14, work_date=None, operator_splits=[])
    assert order_matches_default_production_schedule_scope(aktif, today_key=TODAY) is True
    assert order_matches_default_production_schedule_scope(selesai, today_key=TODAY) is False


def test_cross_month_unfinished_kept():
    o = _order(
        "cross",
        work_date="2026-10-01",
        operator_splits=[
            _split(1000, 1000, "2026-10-01", line_idx=0, id="a"),
            _split(800, 100, "2026-09-15", line_idx=1, id="b"),
        ],
        line_items=[{"production_process": "print"}, {"production_process": "print"}],
    )
    assert order_matches_default_production_schedule_scope(o, today_key=TODAY) is True


def test_future_only_excluded():
    o = _order(
        "fut",
        work_date="2026-12-01",
        operator_splits=[_split(500, 0, "2026-12-01")],
    )
    assert order_matches_default_production_schedule_scope(o, today_key=TODAY) is False


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    async def to_list(self, n):
        return list(self._docs[:n])


def _split_unfinished(split: dict) -> bool:
    try:
        qty = int(split.get("quantity") or 0)
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        return False
    try:
        done = int(split.get("qty_completed") or 0)
    except (TypeError, ValueError):
        done = 0
    return done < qty


def _field_text_match(doc, field, pattern: str) -> bool:
    import re
    raw = doc.get(field)
    if raw is None:
        return False
    return re.search(pattern, str(raw), flags=re.I) is not None


def _elem_match(splits, cond: dict) -> bool:
    for split in splits or []:
        ok = True
        for key, rule in cond.items():
            val = split.get(key)
            if isinstance(rule, dict):
                if rule.get("$exists") is True and key not in split:
                    ok = False
                    break
                if "$nin" in rule and val in rule["$nin"]:
                    ok = False
                    break
                if "$lt" in rule and not (val is not None and str(val) < str(rule["$lt"])):
                    ok = False
                    break
                if "$gt" in rule:
                    try:
                        num = float(val or 0)
                    except (TypeError, ValueError):
                        num = 0
                    if not (num > float(rule["$gt"])):
                        ok = False
                        break
                if "$gte" in rule and not (val is not None and str(val) >= str(rule["$gte"])):
                    ok = False
                    break
                if "$lte" in rule and not (val is not None and str(val) <= str(rule["$lte"])):
                    ok = False
                    break
            elif val != rule:
                ok = False
                break
        if ok:
            return True
    return False


class _FakeOrders:
    def __init__(self, docs):
        self.docs = list(docs)

    async def count_documents(self, q):
        return len(self._match(q))

    def find(self, q, projection=None):
        matched = self._match(q)
        # Keep id ascending like production cursor.
        matched.sort(key=lambda d: str(d.get("id")))
        if not projection:
            return _FakeCursor(matched)
        include = {
            key: flag for key, flag in projection.items()
            if key != "_id" and flag
        }
        # Mongo ``{"_id": 0}`` means all fields except _id.
        if not include:
            return _FakeCursor([
                {k: v for k, v in doc.items() if k != "_id"}
                for doc in matched
            ])
        nested: dict = {}
        top = []
        for key in include:
            if "." in key:
                root, child = key.split(".", 1)
                nested.setdefault(root, []).append(child)
            else:
                top.append(key)
        projected = []
        for doc in matched:
            row = {key: doc.get(key) for key in top if key in doc or key == "id"}
            for root, children in nested.items():
                src = doc.get(root)
                if isinstance(src, list):
                    row[root] = [
                        {child: item.get(child) for child in children}
                        for item in src
                        if isinstance(item, dict)
                    ]
            projected.append(row)
        return _FakeCursor(projected)

    def _match(self, q):
        return [d for d in self.docs if self._doc_match(d, q)]

    def _doc_match(self, doc, q):
        if not q:
            return True
        if "$and" in q:
            return all(self._doc_match(doc, part) for part in q["$and"])
        if "$or" in q:
            return any(self._doc_match(doc, part) for part in q["$or"])
        if "$not" in q:
            return not self._doc_match(doc, q["$not"])
        if "$expr" in q:
            # Approximate unfinished_split_expr_clause: any split qty > qty_completed.
            return any(_split_unfinished(s) for s in (doc.get("operator_splits") or []))
        for key, cond in q.items():
            if key == "id" and isinstance(cond, dict) and "$gt" in cond:
                if not (str(doc.get("id")) > str(cond["$gt"])):
                    return False
                continue
            if key == "id" and isinstance(cond, dict) and "$in" in cond:
                if str(doc.get("id")) not in {str(x) for x in cond["$in"]}:
                    return False
                continue
            if key == "cancelled" and isinstance(cond, dict) and "$ne" in cond:
                if doc.get("cancelled") == cond["$ne"]:
                    return False
                continue
            if key == "current_stage" and isinstance(cond, dict) and "$lt" in cond:
                if not (int(doc.get("current_stage") or 0) < int(cond["$lt"])):
                    return False
                continue
            if key in ("po_number", "customer", "customer_name", "outlet") and isinstance(cond, dict) and "$regex" in cond:
                if not _field_text_match(doc, key, cond["$regex"]):
                    return False
                continue
            if key in (
                "tanggal_pengiriman",
                "effective_delivery_date",
                "delivery_deadline_effective",
                "scheduled_delivery_date",
                "delivery_deadline_manual",
                "deadline",
            ) and isinstance(cond, dict):
                val = doc.get(key)
                if "$gte" in cond and not (val and str(val)[:10] >= cond["$gte"]):
                    return False
                if "$lte" in cond and not (val and str(val)[:10] <= cond["$lte"]):
                    return False
                continue
            if key == "work_date":
                if isinstance(cond, dict):
                    wd = doc.get("work_date")
                    if cond.get("$exists") is True and "work_date" not in doc:
                        return False
                    if "$nin" in cond and wd in cond["$nin"]:
                        return False
                    if "$lt" in cond and not (wd and str(wd) < cond["$lt"]):
                        return False
                    if "$gte" in cond and not (wd and str(wd) >= cond["$gte"]):
                        return False
                    if "$lte" in cond and not (wd and str(wd) <= cond["$lte"]):
                        return False
                    if cond.get("$exists") is False and "work_date" in doc and doc.get("work_date") not in (None, ""):
                        return False
                elif cond is None:
                    if doc.get("work_date") not in (None, ""):
                        return False
                elif cond == "":
                    if doc.get("work_date") != "":
                        return False
                continue
            if key == "operator_splits.quantity" and isinstance(cond, dict) and "$gt" in cond:
                splits = doc.get("operator_splits") or []
                if not any((s.get("quantity") or 0) > cond["$gt"] for s in splits):
                    return False
                continue
            if key == "operator_splits.work_date" and isinstance(cond, dict):
                splits = doc.get("operator_splits") or []
                ok = False
                for s in splits:
                    wd = s.get("work_date")
                    if not wd:
                        continue
                    if "$gte" in cond and str(wd) < cond["$gte"]:
                        continue
                    if "$lte" in cond and str(wd) > cond["$lte"]:
                        continue
                    if "$lt" in cond and not (str(wd) < cond["$lt"]):
                        continue
                    ok = True
                    break
                if not ok:
                    return False
                continue
            if key == "operator_splits":
                splits = doc.get("operator_splits") or []
                if isinstance(cond, dict) and "$exists" in cond and cond["$exists"] is False:
                    if splits:
                        return False
                    continue
                if isinstance(cond, dict) and "$size" in cond:
                    if len(splits) != cond["$size"]:
                        return False
                    continue
                if isinstance(cond, dict) and "$elemMatch" in cond:
                    if not _elem_match(splits, cond["$elemMatch"]):
                        return False
                    continue
                if isinstance(cond, dict) and "$not" in cond:
                    inner = cond["$not"]
                    if isinstance(inner, dict) and "$elemMatch" in inner:
                        if _elem_match(splits, inner["$elemMatch"]):
                            return False
                        continue
                    if any(s.get("work_date") not in (None, "") for s in splits):
                        return False
                    continue
                continue
        return True


class _FakeDb:
    def __init__(self, docs):
        self.orders = _FakeOrders(docs)


async def _collect_default(docs, *, limit=1000, now=None):
    db = _FakeDb(docs)
    cursor = None
    all_ids = []
    pages = 0
    last = None
    while True:
        page = await fetch_production_schedule_orders_page(
            db,
            {"cancelled": {"$ne": True}},
            {"_id": 0},
            limit=limit,
            schedule_scope="default",
            cursor=cursor,
            now=now or datetime(2026, 11, 15, 12, 0, tzinfo=WIB),
        )
        pages += 1
        last = page
        for o in page["orders"]:
            all_ids.append(str(o["id"]))
        cursor = page.get("next_cursor")
        if not cursor:
            break
        if pages > 50:
            break
    return all_ids, last, pages


def test_staged_default_keeps_old_unfinished_beyond_first_1000_deadline_noise():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=WIB)
    docs = []
    # 1200 old completed rows with early deadlines (would dominate naive deadline cap).
    for i in range(1200):
        docs.append(_order(
            f"done-{i:04d}",
            current_stage=8,
            work_date="2026-01-01",
            deadline=f"2026-01-{(i % 28) + 1:02d}",
            operator_splits=[_split(100, 100, "2026-01-01")],
        ))
    # Stage>=14 but qty unfinished — must survive.
    docs.append(_order(
        "unfin-stage14",
        current_stage=14,
        work_date="2026-02-01",
        deadline="2026-12-31",
        operator_splits=[_split(500, 50, "2026-02-01")],
    ))
    # Stage<14 but qty finished — must not enter default.
    docs.append(_order(
        "fin-stage8",
        current_stage=8,
        work_date="2026-02-01",
        deadline="2026-12-30",
        operator_splits=[_split(500, 500, "2026-02-01")],
    ))
    # Current month row.
    docs.append(_order(
        "cur-month",
        work_date="2026-11-10",
        deadline="2026-11-20",
        operator_splits=[_split(200, 200, "2026-11-10")],
    ))

    ids, last, pages = asyncio.run(_collect_default(docs, limit=50, now=now))
    assert "unfin-stage14" in ids
    assert "cur-month" in ids
    assert "fin-stage8" not in ids
    assert last["scope_complete"] is True
    assert last["next_cursor"] is None
    # Completeness meta remains honest for full catalog size.
    meta = scoped_read_meta(last["matched_count"], len(ids), 50)
    assert last["matched_count"] == 1203
    assert meta["matched_count"] == 1203
    assert pages >= 1


def test_all_scope_pages_include_completed_history():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=WIB)
    docs = [
        _order("a", work_date="2026-01-01", operator_splits=[_split(10, 10, "2026-01-01")]),
        _order("b", work_date="2026-02-01", operator_splits=[_split(10, 10, "2026-02-01")]),
        _order("c", work_date="2026-03-01", operator_splits=[_split(10, 0, "2026-03-01")]),
    ]
    db = _FakeDb(docs)

    async def _run():
        page1 = await fetch_production_schedule_orders_page(
            db, {}, {"_id": 0}, limit=2, schedule_scope="all", cursor=None, now=now,
        )
        assert len(page1["orders"]) == 2
        assert page1["next_cursor"]
        assert page1["scope_complete"] is False
        page2 = await fetch_production_schedule_orders_page(
            db, {}, {"_id": 0}, limit=2, schedule_scope="all", cursor=page1["next_cursor"], now=now,
        )
        ids = [o["id"] for o in page1["orders"] + page2["orders"]]
        assert set(ids) == {"a", "b", "c"}
        assert page2["scope_complete"] is True

    asyncio.run(_run())


def test_wib_month_bounds_still_jakarta():
    start, end = wib_month_date_bounds(datetime(2026, 10, 31, 17, 30, tzinfo=timezone.utc))
    assert start == "2026-11-01"
    assert end == "2026-11-30"


def test_peek_after_last_match_yields_null_cursor():
    """Lean peek must not leave a false next_cursor after the final match."""
    now = datetime(2026, 11, 15, 12, 0, tzinfo=WIB)
    docs = [
        _order(
            "a-match",
            work_date="2026-11-02",
            operator_splits=[_split(100, 0, "2026-11-02")],
        ),
        # Finished prior month — outside narrowed candidate unfinished/month clauses
        # when quantity complete; must not create empty follow-up pages.
        _order(
            "z-done",
            work_date="2026-01-01",
            operator_splits=[_split(100, 100, "2026-01-01")],
        ),
    ]
    db = _FakeDb(docs)

    async def _run():
        page = await fetch_production_schedule_orders_page(
            db, {"cancelled": {"$ne": True}}, {"_id": 0},
            limit=10, schedule_scope="default", cursor=None, now=now,
        )
        assert [o["id"] for o in page["orders"]] == ["a-match"]
        assert page["next_cursor"] is None
        assert page["scope_complete"] is True

    asyncio.run(_run())


def test_no_infinite_empty_pages_when_false_candidates_remain():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=WIB)
    docs = [
        _order(
            "keep",
            work_date="2026-11-05",
            operator_splits=[_split(50, 10, "2026-11-05")],
        ),
    ]
    # Future-only unfinished enters $expr candidate pool but Python matcher excludes.
    for i in range(40):
        docs.append(_order(
            f"noise-{i:03d}",
            work_date="2026-12-15",
            operator_splits=[_split(100, 0, "2026-12-15", id=f"n-{i}")],
        ))
    ids, last, pages = asyncio.run(_collect_default(docs, limit=5, now=now))
    assert ids == ["keep"]
    assert last["next_cursor"] is None
    assert last["scope_complete"] is True
    assert pages < 20


def _build_finished_noise(n: int, prefix: str):
    docs = []
    for i in range(n):
        docs.append(_order(
            f"{prefix}-{i:06d}",
            work_date="2026-01-01",
            operator_splits=[_split(10, 10, "2026-01-01", id=f"{prefix}-s-{i}")],
        ))
    return docs


def test_large_fixture_unfinished_at_tail_is_found():
    """Finished noise must not hide a lexicographic-tail unfinished match."""
    now = datetime(2026, 11, 15, 12, 0, tzinfo=WIB)
    docs = _build_finished_noise(10_000, "fin")
    # Many current-month matches so the tail unfinished needs multiple pages.
    for i in range(1_200):
        docs.append(_order(
            f"mon-{i:05d}",
            work_date="2026-11-05",
            operator_splits=[_split(5, 0, "2026-11-05", id=f"m-{i}")],
        ))
    docs.append(_order(
        "zzz-unfin",
        current_stage=14,
        work_date="2026-03-01",
        outlet="Outlet Ekor",
        po_number="PO-TAIL",
        customer="Customer Ekor",
        operator_splits=[_split(500, 1, "2026-03-01")],
    ))
    ids, last, pages = asyncio.run(_collect_default(docs, limit=200, now=now))
    assert "zzz-unfin" in ids
    assert last["scope_complete"] is True
    assert pages >= 2


def test_50k_and_100k_finished_noise_still_surfaces_tail_unfinished():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=WIB)
    for n in (50_000, 100_000):
        docs = _build_finished_noise(n, f"n{n}")
        docs.append(_order(
            "zzz-tail",
            work_date="2026-02-01",
            operator_splits=[_split(90, 10, "2026-02-01")],
        ))
        ids, last, _pages = asyncio.run(_collect_default(docs, limit=50, now=now))
        assert "zzz-tail" in ids, f"failed at n={n}"
        assert last["scope_complete"] is True
        assert last["next_cursor"] is None


def test_server_search_finds_outlet_outside_first_page():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=WIB)
    docs = []
    for i in range(30):
        docs.append(_order(
            f"a-{i:03d}",
            work_date="2026-11-01",
            outlet="Outlet Biasa",
            po_number=f"PO-{i}",
            operator_splits=[_split(10, 0, "2026-11-01", id=f"a-{i}")],
        ))
    docs.append(_order(
        "z-target",
        work_date="2026-11-02",
        outlet="Kopi Spesial XYZ",
        po_number="PO-TARGET",
        customer="Brand Target",
        operator_splits=[_split(20, 0, "2026-11-02")],
    ))
    db = _FakeDb(docs)

    async def _run():
        page = await fetch_production_schedule_orders_page(
            db, {"cancelled": {"$ne": True}}, {"_id": 0},
            limit=10, schedule_scope="default", cursor=None, now=now,
            q="Kopi Spesial",
        )
        ids = [o["id"] for o in page["orders"]]
        assert ids == ["z-target"]
        assert page["scope_complete"] is True

        hist = await fetch_production_schedule_orders_page(
            db, {"cancelled": {"$ne": True}}, {"_id": 0},
            limit=10, schedule_scope="all", cursor=None, now=now,
            work_date_from="2026-01-01", work_date_to="2026-01-31",
        )
        # No January rows in this fixture → empty complete.
        assert hist["orders"] == []
        assert hist["scope_complete"] is True

        # Add January history and ensure range returns it under schedule_scope=all.
        db.orders.docs.append(_order(
            "old-hist",
            work_date="2026-01-15",
            outlet="Histori Outlet",
            operator_splits=[_split(10, 10, "2026-01-15")],
        ))
        hist2 = await fetch_production_schedule_orders_page(
            db, {"cancelled": {"$ne": True}}, {"_id": 0},
            limit=10, schedule_scope="all", cursor=None, now=now,
            work_date_from="2026-01-01", work_date_to="2026-01-31",
        )
        assert [o["id"] for o in hist2["orders"]] == ["old-hist"]

    asyncio.run(_run())


def test_refresh_flag_restarts_from_head():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=WIB)
    docs = [
        _order("a", work_date="2026-11-01", operator_splits=[_split(10, 0, "2026-11-01")]),
        _order("b", work_date="2026-11-02", operator_splits=[_split(10, 0, "2026-11-02")]),
        _order("c", work_date="2026-11-03", operator_splits=[_split(10, 0, "2026-11-03")]),
    ]
    db = _FakeDb(docs)

    async def _run():
        page1 = await fetch_production_schedule_orders_page(
            db, {}, {"_id": 0}, limit=1, schedule_scope="default", cursor=None, now=now,
        )
        assert page1["orders"][0]["id"] == "a"
        assert page1["next_cursor"]
        page_refresh = await fetch_production_schedule_orders_page(
            db, {}, {"_id": 0}, limit=1, schedule_scope="default",
            cursor=page1["next_cursor"], refresh=True, now=now,
        )
        assert page_refresh["orders"][0]["id"] == "a"
        assert page_refresh["refresh"] is True

    asyncio.run(_run())


def test_cancelled_excluded_from_default_base_query():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=WIB)
    docs = [
        _order(
            "open",
            work_date="2026-11-01",
            operator_splits=[_split(10, 0, "2026-11-01")],
        ),
        _order(
            "canc",
            cancelled=True,
            work_date="2026-11-01",
            operator_splits=[_split(10, 0, "2026-11-01")],
        ),
    ]
    ids, last, _pages = asyncio.run(_collect_default(docs, limit=10, now=now))
    assert ids == ["open"]
    assert last["scope_complete"] is True


def test_data_change_between_pages_does_not_loop():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=WIB)
    docs = [
        _order("a", work_date="2026-11-01", operator_splits=[_split(10, 0, "2026-11-01")]),
        _order("b", work_date="2026-11-02", operator_splits=[_split(10, 0, "2026-11-02")]),
        _order("c", work_date="2026-11-03", operator_splits=[_split(10, 0, "2026-11-03")]),
    ]
    db = _FakeDb(docs)

    async def _run():
        page1 = await fetch_production_schedule_orders_page(
            db, {}, {"_id": 0}, limit=1, schedule_scope="default", cursor=None, now=now,
        )
        # Insert id between a and b while paging — stable $gt cursor must continue.
        db.orders.docs.append(_order(
            "a2", work_date="2026-11-01", operator_splits=[_split(5, 0, "2026-11-01")],
        ))
        page2 = await fetch_production_schedule_orders_page(
            db, {}, {"_id": 0}, limit=10, schedule_scope="default",
            cursor=page1["next_cursor"], now=now,
        )
        ids = [o["id"] for o in page1["orders"] + page2["orders"]]
        assert "a" in ids
        assert "b" in ids
        assert "c" in ids
        # Newly inserted a2 may appear on later full refresh; must not infinite-loop.
        assert page2["scope_complete"] is True or page2["next_cursor"] is None or "a2" in [
            o["id"] for o in page2["orders"]
        ]

    asyncio.run(_run())


def test_parse_reconcile_ids_dedupes_and_truncates():
    ids, truncated = parse_reconcile_ids("a,b,a, c", limit=2)
    assert ids == ["a", "b"]
    assert truncated is True
    ids2, truncated2 = parse_reconcile_ids(["x", "y"], limit=10)
    assert ids2 == ["x", "y"]
    assert truncated2 is False


def test_reconcile_by_ids_reports_found_and_missing_under_access_scope():
    docs = [
        _order("keep", work_date="2026-08-01", operator_splits=[_split(100, 40, "2026-08-01")]),
        _order(
            "canc",
            cancelled=True,
            work_date="2026-08-01",
            operator_splits=[_split(100, 40, "2026-08-01")],
        ),
        _order(
            "done",
            work_date="2026-08-01",
            operator_splits=[_split(100, 100, "2026-08-01")],
        ),
    ]
    db = _FakeDb(docs)

    async def _run():
        page = await fetch_production_schedule_orders_by_ids(
            db,
            {"cancelled": {"$ne": True}},
            {"_id": 0},
            ["keep", "canc", "done", "ghost"],
            limit=10,
        )
        assert page["mode"] == "reconcile"
        assert set(page["found_ids"]) == {"keep", "done"}
        assert set(page["missing_ids"]) == {"canc", "ghost"}
        assert {o["id"] for o in page["orders"]} == {"keep", "done"}
        # Finished prior-month still returned so client can drop via default scope.
        done = next(o for o in page["orders"] if o["id"] == "done")
        assert done["operator_splits"][0]["qty_completed"] == 100

    asyncio.run(_run())


def test_reconcile_truncation_is_explicit():
    docs = [_order(f"id-{i}", work_date="2026-11-01", operator_splits=[_split(1, 0, "2026-11-01")]) for i in range(5)]
    db = _FakeDb(docs)

    async def _run():
        page = await fetch_production_schedule_orders_by_ids(
            db, {}, {"_id": 0}, [f"id-{i}" for i in range(5)], limit=2,
        )
        assert page["reconcile_truncated"] is True
        assert page["requested_ids"] == ["id-0", "id-1"]
        assert page["missing_ids"] == []

    asyncio.run(_run())
