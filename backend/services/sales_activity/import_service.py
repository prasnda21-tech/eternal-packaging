"""Safe Excel migration: preview has zero mutation; confirm stays PAUSED_MIGRATION."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.sales_activity import events as event_store
from services.sales_activity import ownership as ownership_svc
from services.sales_activity.clock import iso_now, now_utc
from services.sales_activity.constants import (
    ACTIVATION_DONE,
    ACTIVATION_NOT_STARTED,
    ACTOR_IMPORT,
    ACTOR_USER,
    AUTOMATION_ACTIVE,
    AUTOMATION_PAUSED_MIGRATION,
    BATCH_ACTIVATED,
    BATCH_CONFIRMED,
    BATCH_PARSED,
    COL_ACTIVITIES,
    COL_BATCHES,
    COL_CONFLICTS,
    COL_OUTLETS,
    CONFLICT_AMBIGUOUS_OUTLET,
    CONFLICT_AMBIGUOUS_PO,
    CONFLICT_AMBIGUOUS_SALES,
    CONFLICT_UNMAPPED_SALES,
    EVENT_MIGRATION_ACTIVATED,
    EVENT_MIGRATION_IMPORTED,
    KIND_CUSTOMER,
    KIND_DORMANT,
    KIND_PROSPECT,
    POOL_DORMANT,
    SOURCE_IMPORT,
    USER_AMBIGUOUS,
    USER_MATCHED,
    USER_UNMATCHED,
)
from services.sales_activity.import_parser import SalesImportParseError, parse_sales_workbook
from services.sales_activity.orders import classify_customer, last_order_at, last_valid_sales_order, match_outlet_to_orders
from services.sales_activity.outlets import find_strong_duplicate
from services.sales_activity.pool import enter_pool
from services.sales_activity.settings import load_settings, rule_snapshot, select_rule
from services.sales_activity.users import list_users, map_sales_name
from services.sales_activity.util import (
    actor_id,
    compute_windows,
    new_id,
    normalize_text,
    normalize_whatsapp,
    public_doc,
    stamp_created,
    strong_text,
)


def _source_identity(file_hash: str, kind: str, row: dict) -> str:
    key = row.get("outlet_code") or normalize_text(row.get("name") or row.get("outlet_name") or "")
    extra = row.get("activity_at") or ""
    sales = normalize_text(row.get("sales_name") or "")
    return f"import:{file_hash}:{kind}:{row.get('source_sheet')}:{row.get('source_row')}:{key}:{extra}:{sales}"


async def _load_recent_orders(db) -> list:
    return await db.orders.find({"cancelled": {"$ne": True}}, {"_id": 0}).sort("order_date", -1).to_list(2000)


def _latest_activity_for_outlet(outlet_row: dict, activities: List[dict]) -> Optional[dict]:
    name = normalize_text(outlet_row.get("name"))
    code = strong_text(outlet_row.get("outlet_code"))
    matches = []
    for act in activities:
        if code and strong_text(act.get("outlet_code")) == code:
            matches.append(act)
            continue
        if name and normalize_text(act.get("outlet_name")) == name:
            matches.append(act)
    if not matches:
        return None
    matches.sort(key=lambda a: (a.get("activity_at") or "", a.get("source_row") or 0))
    return matches[-1]


async def preview_import(db, filename: str, content: bytes, user: dict) -> dict:
    try:
        parsed = parse_sales_workbook(filename, content)
    except SalesImportParseError as exc:
        raise HTTPException(400, exc.message) from exc

    existing = await db[COL_BATCHES].find_one({"file_hash": parsed["file_hash"]}, {"_id": 0})
    users = await list_users(db)
    orders = await _load_recent_orders(db)

    sales_map = {}
    for raw in {a.get("sales_name") for a in parsed["activities"] if a.get("sales_name")} | {o.get("sales_name") for o in parsed["outlets"] if o.get("sales_name")}:
        sales_map[raw] = map_sales_name(raw, users)

    outlet_reviews = []
    conflicts = []
    matched = unmatched = conflict_count = duplicate_count = 0
    for row in parsed["outlets"]:
        keys = {
            "outlet_code": strong_text(row.get("outlet_code")),
            "whatsapp_norm": normalize_whatsapp(row.get("whatsapp")),
        }
        existing_outlet = await find_strong_duplicate(db, keys)
        latest = _latest_activity_for_outlet(row, parsed["activities"])
        sales_name = (latest or {}).get("sales_name") or row.get("sales_name")
        mapping = sales_map.get(sales_name) if sales_name else {"status": USER_UNMATCHED, "input": sales_name, "user": None, "candidates": []}
        po_match = match_outlet_to_orders(row, orders)
        review = {
            "source_row": row["source_row"],
            "outlet": row,
            "existing_outlet_id": existing_outlet.get("id") if existing_outlet else None,
            "duplicate": bool(existing_outlet),
            "latest_activity": latest,
            "sales_mapping": mapping,
            "po_match": {
                "status": po_match.get("status"),
                "po_number": (po_match.get("order") or {}).get("po_number"),
                "customer": (po_match.get("order") or {}).get("customer"),
            },
            "automation_status": AUTOMATION_PAUSED_MIGRATION,
            "eligible": False,
        }
        if existing_outlet:
            duplicate_count += 1
        if po_match.get("status") == "AMBIGUOUS":
            conflict_count += 1
            conflicts.append({"type": CONFLICT_AMBIGUOUS_PO, "source_row": row["source_row"], "outlet_name": row.get("name")})
        if mapping["status"] == USER_MATCHED:
            matched += 1
        elif mapping["status"] == USER_AMBIGUOUS:
            unmatched += 1
            conflict_count += 1
            conflicts.append({"type": CONFLICT_AMBIGUOUS_SALES, "source_row": row["source_row"], "sales_name": sales_name})
        else:
            unmatched += 1
            if sales_name:
                conflict_count += 1
                conflicts.append({"type": CONFLICT_UNMAPPED_SALES, "source_row": row["source_row"], "sales_name": sales_name})
        outlet_reviews.append(review)

    preview = {
        "message": "Import tidak menjalankan automation.",
        "outlets": outlet_reviews,
        "activities": parsed["activities"],
        "sales_mappings": sales_map,
        "row_errors": parsed["row_errors"],
        "sheets": parsed["sheets"],
        "conflicts": conflicts,
    }

    if existing:
        if existing.get("status") == BATCH_CONFIRMED or existing.get("status") == BATCH_ACTIVATED:
            return {"batch": existing, "preview": existing.get("preview") or preview, "already_imported": True}
        await db[COL_BATCHES].update_one({"id": existing["id"]}, {"$set": {
            "preview": preview,
            "matched_count": matched,
            "unmatched_count": unmatched,
            "conflict_count": conflict_count,
            "duplicate_count": duplicate_count,
            "total_outlet_rows": parsed["total_outlet_rows"],
            "total_activity_rows": parsed["total_activity_rows"],
            "updated_at": iso_now(),
        }})
        existing.update({"preview": preview, "matched_count": matched, "unmatched_count": unmatched, "conflict_count": conflict_count})
        return {"batch": existing, "preview": preview, "already_imported": False}

    batch = stamp_created({
        "id": new_id(),
        "original_filename": filename,
        "file_hash": parsed["file_hash"],
        "created_by": user.get("email"),
        "created_by_user_id": actor_id(user),
        "source": SOURCE_IMPORT,
        "status": BATCH_PARSED,
        "activation_status": ACTIVATION_NOT_STARTED,
        "activated_at": None,
        "activated_by": None,
        "total_outlet_rows": parsed["total_outlet_rows"],
        "total_activity_rows": parsed["total_activity_rows"],
        "matched_count": matched,
        "unmatched_count": unmatched,
        "conflict_count": conflict_count,
        "duplicate_count": duplicate_count,
        "imported_count": 0,
        "preview": preview,
    })
    await db[COL_BATCHES].insert_one(batch)
    return {"batch": public_doc(batch), "preview": preview, "already_imported": False}


async def get_batch(db, batch_id: str) -> dict:
    doc = await db[COL_BATCHES].find_one({"id": batch_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Import batch tidak ditemukan")
    return doc


async def confirm_import(db, batch_id: str, user: dict) -> dict:
    batch = await get_batch(db, batch_id)
    if batch.get("status") in {BATCH_CONFIRMED, BATCH_ACTIVATED}:
        return {"batch": batch, "imported": False, "message": "Batch sudah diimpor. Tidak ada duplikasi."}
    preview = batch.get("preview") or {}
    outlets = preview.get("outlets") or []
    activities = preview.get("activities") or []
    imported = 0
    for review in outlets:
        row = review.get("outlet") or {}
        identity = _source_identity(batch["file_hash"], "outlet", row)
        existing = await db[COL_OUTLETS].find_one({"source_identity": identity}, {"_id": 0})
        if existing:
            continue
        keys = {
            "outlet_code": strong_text(row.get("outlet_code")),
            "whatsapp_norm": normalize_whatsapp(row.get("whatsapp")),
        }
        strong_hit = await find_strong_duplicate(db, keys)
        mapping = review.get("sales_mapping") or {}
        po = review.get("po_match") or {}
        unresolved = mapping.get("status") != USER_MATCHED
        unresolved_reason = None
        if mapping.get("status") == USER_AMBIGUOUS:
            unresolved_reason = CONFLICT_AMBIGUOUS_SALES
        elif mapping.get("status") != USER_MATCHED:
            unresolved_reason = CONFLICT_UNMAPPED_SALES if mapping.get("input") else "NO_SALES"
        if po.get("status") == "AMBIGUOUS":
            unresolved = True
            unresolved_reason = CONFLICT_AMBIGUOUS_PO
        owner = mapping.get("user") if mapping.get("status") == USER_MATCHED else None
        customer_kind = KIND_CUSTOMER if po.get("status") == "MATCHED" else KIND_PROSPECT
        last_so = None
        linked_customer = row.get("name")
        linked_outlet = row.get("name")
        linked_cid = None
        if po.get("status") == "MATCHED":
            last = await last_valid_sales_order(db, customer_name=row.get("name"), outlet_name=row.get("name"))
            if last:
                last_so = last_order_at(last)
                linked_customer = last.get("customer")
                linked_outlet = last.get("outlet") or row.get("name")
                linked_cid = last.get("customer_id")
        if strong_hit:
            outlet_id = strong_hit["id"]
            outlet_doc = strong_hit
        else:
            outlet_doc = stamp_created({
                "id": new_id(),
                "outlet_code": keys.get("outlet_code"),
                "name": row.get("name"),
                "address": row.get("address") or "",
                "maps_url": row.get("maps_url") or "",
                "instagram": row.get("instagram") or "",
                "marketplace": row.get("marketplace") or "",
                "category": row.get("category") or "",
                "pic_name": row.get("pic_name") or "",
                "pic_title": row.get("pic_title") or "",
                "whatsapp": row.get("whatsapp") or "",
                "lead_source": row.get("lead_source") or "",
                "source": SOURCE_IMPORT,
                "import_batch_id": batch_id,
                "source_row": row.get("source_row"),
                "source_identity": identity,
                "linked_customer_name": linked_customer,
                "linked_outlet_name": linked_outlet,
                "linked_customer_id": linked_cid,
                "customer_kind": customer_kind,
                "created_by_user_id": actor_id(user),
                "identity_keys": keys,
            })
            try:
                await db[COL_OUTLETS].insert_one(outlet_doc)
            except DuplicateKeyError:
                existing = await db[COL_OUTLETS].find_one({"source_identity": identity}, {"_id": 0})
                if existing:
                    outlet_doc = existing
                else:
                    raise
            outlet_id = outlet_doc["id"]

        latest = review.get("latest_activity")
        last_activity_at = (latest or {}).get("activity_at")
        snap = await ownership_svc.get_ownership(db, outlet_id)
        if not snap:
            snap = ownership_svc.empty_ownership(outlet_id, source=SOURCE_IMPORT, automation_status=AUTOMATION_PAUSED_MIGRATION)
            snap.update({
                "current_owner_id": (owner or {}).get("id"),
                "current_owner_name": (owner or {}).get("name"),
                "ownership_started_at": None,
                "timer_anchor_at": None,
                "eligible_at": None,
                "warning_at": None,
                "inactivity_eligible_at": None,
                "import_batch_id": batch_id,
                "ownership_unresolved": unresolved,
                "unresolved_reason": unresolved_reason,
                "customer_kind": customer_kind,
                "last_valid_sales_order_at": last_so.isoformat() if last_so else None,
                "last_activity_at": last_activity_at,
            })
            await ownership_svc.replace_ownership(db, snap)
            await event_store.append_event(
                db,
                outlet_id=outlet_id,
                event_type=EVENT_MIGRATION_IMPORTED,
                previous_owner_id=None,
                new_owner_id=(owner or {}).get("id"),
                actor_type=ACTOR_IMPORT,
                actor_user_id=actor_id(user),
                reason="Excel migration confirm",
                source=SOURCE_IMPORT,
                import_batch_id=batch_id,
                correlation_key=f"import-outlet:{identity}",
                payload={"historical_activity_at": last_activity_at, "automation_status": AUTOMATION_PAUSED_MIGRATION},
            )
        if unresolved:
            await db[COL_CONFLICTS].update_one(
                {"import_batch_id": batch_id, "source_identity": identity},
                {"$setOnInsert": {
                    "id": new_id(),
                    "import_batch_id": batch_id,
                    "conflict_type": unresolved_reason or CONFLICT_UNMAPPED_SALES,
                    "outlet_id": outlet_id,
                    "source_row": row.get("source_row"),
                    "payload": review,
                    "resolution": "PENDING",
                    "source_identity": identity,
                    "created_at": iso_now(),
                }},
                upsert=True,
            )
        imported += 1

    for act in activities:
        identity = _source_identity(batch["file_hash"], "activity", act)
        exists = await db[COL_ACTIVITIES].find_one({"source_identity": identity}, {"_id": 0, "id": 1})
        if exists:
            continue
        outlet = None
        if act.get("outlet_code"):
            outlet = await db[COL_OUTLETS].find_one({"identity_keys.outlet_code": act["outlet_code"]}, {"_id": 0})
        if not outlet and act.get("outlet_name"):
            outlet = await db[COL_OUTLETS].find_one({
                "import_batch_id": batch_id,
                "name": {"$regex": f"^{act.get('outlet_name')}$", "$options": "i"},
            }, {"_id": 0})
        if not outlet and act.get("outlet_name"):
            outlet = await db[COL_OUTLETS].find_one({
                "name": {"$regex": f"^{act.get('outlet_name')}$", "$options": "i"},
            }, {"_id": 0})
        if not outlet:
            continue
        type_id = normalize_text(act.get("activity_type") or "visit").replace(" ", "_")
        doc = stamp_created({
            "id": new_id(),
            "outlet_id": outlet["id"],
            "activity_at": act.get("activity_at"),
            "sales_user_id": None,
            "sales_name": act.get("sales_name"),
            "activity_type": type_id,
            "result": act.get("result") or "",
            "next_action": act.get("next_action") or "",
            "next_follow_up_at": act.get("next_follow_up_at"),
            "notes": act.get("notes") or "",
            "evidence_type": act.get("evidence_type") or "",
            "evidence_link": act.get("evidence_link") or "",
            "source": SOURCE_IMPORT,
            "import_batch_id": batch_id,
            "source_row": act.get("source_row"),
            "source_identity": identity,
            "created_by_user_id": actor_id(user),
            "counts_as_valid": True,
            "resets_inactivity": False,
        })
        try:
            await db[COL_ACTIVITIES].insert_one(doc)
        except DuplicateKeyError:
            continue

    await db[COL_BATCHES].update_one({"id": batch_id}, {"$set": {
        "status": BATCH_CONFIRMED,
        "imported_count": imported,
        "updated_at": iso_now(),
    }})
    batch = await get_batch(db, batch_id)
    return {
        "batch": batch,
        "imported": True,
        "message": "Import selesai. Automation tetap PAUSED_MIGRATION. Import tidak menjalankan automation.",
    }


async def activation_preview(db, batch_id: str) -> dict:
    batch = await get_batch(db, batch_id)
    if batch.get("status") not in {BATCH_CONFIRMED, BATCH_ACTIVATED}:
        raise HTTPException(409, "Konfirmasi import dulu sebelum aktivasi")
    settings = await load_settings(db)
    outlets = await db[COL_OUTLETS].find({"import_batch_id": batch_id}, {"_id": 0}).to_list(5000)
    now = now_utc()
    prospects = customers = unresolved = dormant = remain_pool = 0
    details = []
    for outlet in outlets:
        snap = await ownership_svc.get_ownership(db, outlet["id"])
        last = await last_valid_sales_order(
            db,
            customer_name=outlet.get("linked_customer_name") or outlet.get("name"),
            outlet_name=outlet.get("linked_outlet_name") or outlet.get("name"),
            customer_id=outlet.get("linked_customer_id") or "",
        )
        kind, iso = classify_customer(last, int((settings.get("dormancy") or {}).get("threshold_days") or 90), now)
        if snap and snap.get("ownership_unresolved"):
            unresolved += 1
        if kind == KIND_PROSPECT:
            prospects += 1
        elif kind == KIND_CUSTOMER:
            customers += 1
        else:
            dormant += 1
            remain_pool += 1
        details.append({
            "outlet_id": outlet["id"],
            "name": outlet.get("name"),
            "customer_kind": kind,
            "last_valid_sales_order_at": iso,
            "unresolved": bool(snap and snap.get("ownership_unresolved")),
            "will_enter_pool": kind == KIND_DORMANT,
        })
    rule = settings.get("default_rule") or {}
    return {
        "batch": batch,
        "message": "Aktivasi akan memulai timer dari waktu aktivasi, bukan tanggal historis.",
        "prospects_to_activate": prospects,
        "existing_customers": customers,
        "unresolved_ownerships": unresolved,
        "potential_dormant_customers": dormant,
        "records_that_will_enter_pool": remain_pool,
        "rule": {
            "ownership_duration_days": rule.get("ownership_duration_days"),
            "timer_mode": rule.get("timer_mode"),
            "dormant_threshold_days": (settings.get("dormancy") or {}).get("threshold_days"),
            "dormant_destination": "POOL",
        },
        "details": details,
    }


async def activate_migration(db, batch_id: str, user: dict, *, confirm: bool) -> dict:
    if not confirm:
        raise HTTPException(400, "Konfirmasi aktivasi wajib")
    preview = await activation_preview(db, batch_id)
    batch = preview["batch"]
    if batch.get("activation_status") == ACTIVATION_DONE:
        return {"batch": batch, "activated": False, "message": "Batch sudah diaktivasi"}
    settings = await load_settings(db)
    now = iso_now()
    activated = 0
    for item in preview["details"]:
        outlet = await db[COL_OUTLETS].find_one({"id": item["outlet_id"]}, {"_id": 0})
        snap = await ownership_svc.get_ownership(db, item["outlet_id"])
        if not snap or not outlet:
            continue
        if snap.get("ownership_unresolved"):
            continue
        rule = select_rule(settings, outlet)
        patch = {
            "automation_status": AUTOMATION_ACTIVE,
            "customer_kind": item["customer_kind"],
            "last_valid_sales_order_at": item.get("last_valid_sales_order_at"),
            "rule_id": rule.get("rule_id"),
            "rule_version": rule.get("rule_version"),
            "rule_snapshot": rule_snapshot(settings),
        }
        if item["customer_kind"] == KIND_PROSPECT:
            windows = compute_windows(now, now, rule)
            patch.update(windows)
        elif item["customer_kind"] == KIND_CUSTOMER:
            patch.update({
                "ownership_started_at": now,
                "timer_anchor_at": now,
                "eligible_at": None,
                "warning_at": None,
            })
        elif item["customer_kind"] == KIND_DORMANT:
            pool = await enter_pool(db, snap, reason=POOL_DORMANT, suggested_next_owner_id=None)
            patch.update({
                "in_pool": True,
                "pool_id": pool.get("id"),
                "current_owner_id": None,
                "current_owner_name": None,
                "ownership_started_at": now,
                "eligible_at": None,
                "warning_at": None,
                "timer_anchor_at": None,
            })
        updated = await ownership_svc.cas_update(db, snap["outlet_id"], snap["version"], patch)
        if not updated:
            continue
        await event_store.append_event(
            db,
            outlet_id=outlet["id"],
            event_type=EVENT_MIGRATION_ACTIVATED,
            previous_owner_id=snap.get("current_owner_id"),
            new_owner_id=updated.get("current_owner_id"),
            actor_type=ACTOR_USER,
            actor_user_id=actor_id(user),
            rule_id=rule.get("rule_id"),
            rule_version=rule.get("rule_version"),
            reason="Explicit migration activation",
            source=SOURCE_IMPORT,
            import_batch_id=batch_id,
            correlation_key=f"activate:{batch_id}:{outlet['id']}",
            payload={"activation_at": now, "historical_last_activity_at": snap.get("last_activity_at")},
        )
        activated += 1
    await db[COL_BATCHES].update_one({"id": batch_id}, {"$set": {
        "status": BATCH_ACTIVATED,
        "activation_status": ACTIVATION_DONE,
        "activated_at": now,
        "activated_by": user.get("email"),
        "updated_at": now,
    }})
    return {
        "batch": await get_batch(db, batch_id),
        "activated": True,
        "activated_count": activated,
        "preview": preview,
        "message": "Automation diaktifkan dari waktu aktivasi. Tanggal historis tidak diubah.",
    }


async def list_batches(db) -> list:
    rows = await db[COL_BATCHES].find({}, {"_id": 0, "preview": 0}).sort("created_at", -1).to_list(100)
    return rows
