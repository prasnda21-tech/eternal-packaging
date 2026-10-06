"""Design Cetakan Phase 5B — read-only mutation history from audit_logs."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_NAME", "eternal_packaging_design_cetakan_history_test")

from services.print_design_history import (  # noqa: E402
    ACTION_ASSET_UPLOAD,
    ACTION_ASSET_REMOVE,
    ACTION_BRANCH_ASSET_REMOVE,
    ACTION_BRANCH_ASSET_UPLOAD,
    ACTION_BRANCH_COPY,
    ACTION_BRANCH_RESET,
    ACTION_BRANCH_UPDATE,
    ACTION_UPDATE,
    HISTORY_ACTIONS,
    list_print_design_history,
    serialize_history_row,
)

SAFE_DTO_KEYS = {
    "id",
    "at",
    "action",
    "operation_label",
    "scope",
    "branch_id",
    "branch_name",
    "actor_name",
    "actor_email",
    "changed_fields",
    "asset_slot",
    "summary",
    "source_scope",
    "source_branch_id",
    "target_branch_id",
}


def _match(doc, query):
    query = query or {}
    if "$or" in query:
        if not any(_match(doc, part) for part in query["$or"]):
            return False
        rest = {k: v for k, v in query.items() if k != "$or"}
        return _match(doc, rest) if rest else True
    for key, expected in query.items():
        actual = doc
        for part in str(key).split("."):
            if not isinstance(actual, dict):
                actual = None
                break
            actual = actual.get(part)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, spec):
        if isinstance(spec, list):
            for key, direction in reversed(spec):
                reverse = direction == -1
                self._docs.sort(key=lambda doc: doc.get(key) or "", reverse=reverse)
        return self

    def skip(self, count):
        self._docs = self._docs[count:]
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    async def to_list(self, count):
        return copy.deepcopy(self._docs[:count])


class MockCollection:
    def __init__(self, initial=None):
        self._docs = copy.deepcopy(initial or [])
        self.writes = 0

    def find(self, query=None, projection=None):
        matched = []
        for doc in self._docs:
            if _match(doc, query):
                res = copy.deepcopy(doc)
                if projection and projection.get("_id") == 0:
                    res.pop("_id", None)
                matched.append(res)
        return Cursor(matched)

    async def insert_one(self, doc):
        self.writes += 1
        self._docs.append(copy.deepcopy(doc))


class MockDb:
    def __init__(self, logs=None):
        self.audit_logs = MockCollection(logs)


def _row(**kwargs):
    return {
        "id": kwargs.get("id", "a"),
        "action": kwargs.get("action", ACTION_UPDATE),
        "actor_name": kwargs.get("actor_name", "Dimas Aji"),
        "actor_email": kwargs.get("actor_email", "dimas@example.com"),
        "actor_role": "admin",
        "target_email": "-",
        "before": kwargs.get("before"),
        "after": kwargs.get("after", {"company_name": "Eternal Global", "accent_color": "#cc1f1a", "bank_count": 2}),
        "note": "",
        "at": kwargs.get("at", "2026-10-06T15:42:00+00:00"),
    }


@pytest.mark.anyio
async def test_history_only_design_cetakan_actions_newest_first():
    logs = [
        _row(id="pay", action="hr.payroll.post", at="2026-10-06T16:00:00+00:00"),
        _row(id="fin", action="journal.create", at="2026-10-06T16:01:00+00:00"),
        _row(id="auth", action="user.reset_password", at="2026-10-06T16:02:00+00:00"),
        _row(id="g1", action=ACTION_UPDATE, at="2026-10-06T15:00:00+00:00"),
        _row(
            id="b50",
            action=ACTION_BRANCH_UPDATE,
            at="2026-10-06T15:30:00+00:00",
            after={"branch_id": "50", "operation": "update", "override": {"company_name": "Eternal Jogja"}},
            before={"branch_id": "50", "operation": "update", "override": {}},
        ),
        _row(
            id="logo",
            action=ACTION_BRANCH_ASSET_UPLOAD,
            at="2026-10-06T15:40:00+00:00",
            after={"slot": "company_logo", "branch_id": "50", "storage_path": "eternal-packaging/print-design/x.png", "file_id": "abc"},
        ),
        _row(
            id="reset",
            action=ACTION_BRANCH_RESET,
            at="2026-10-06T15:50:00+00:00",
            before={"branch_id": "50", "operation": "reset", "override": {"company_name": "Eternal Jogja"}},
            after={"branch_id": "50", "operation": "reset", "override": {}},
        ),
    ]
    db = MockDb(logs)
    writes = db.audit_logs.writes
    result = await list_print_design_history(db, scope="all")
    ids = [row["id"] for row in result["items"]]
    assert ids == ["reset", "logo", "b50", "g1"]
    assert "pay" not in ids and "fin" not in ids and "auth" not in ids
    blob = str(result)
    assert "storage_path" not in blob
    assert "file_id" not in blob
    assert "eternal-packaging/print-design" not in blob
    assert db.audit_logs.writes == writes


@pytest.mark.anyio
async def test_tie_break_page_has_more_and_page_size_cap():
    logs = [
        _row(id="b", action=ACTION_UPDATE, at="2026-10-06T15:00:00+00:00"),
        _row(id="a", action=ACTION_UPDATE, at="2026-10-06T15:00:00+00:00"),
        _row(id="c", action=ACTION_UPDATE, at="2026-10-06T14:00:00+00:00"),
    ]
    db = MockDb(logs)
    first = await list_print_design_history(db, page=1, page_size=2)
    assert [row["id"] for row in first["items"]] == ["b", "a"]
    assert first["has_more"] is True
    second = await list_print_design_history(db, page=2, page_size=2)
    assert [row["id"] for row in second["items"]] == ["c"]
    assert second["has_more"] is False
    capped = await list_print_design_history(db, page_size=999)
    assert capped["page_size"] == 50
    defaulted = await list_print_design_history(db)
    assert defaulted["page"] == 1
    assert defaulted["page_size"] == 20


@pytest.mark.anyio
async def test_global_and_branch_serialization_and_actor_fallback():
    named = serialize_history_row(_row(action=ACTION_UPDATE, actor_name="Dimas Aji", actor_email="dimas@example.com"))
    assert named["scope"] == "global"
    assert named["branch_name"] == "Global"
    assert named["branch_id"] is None
    assert named["operation_label"] == "Pengaturan Global diperbarui"
    assert named["changed_fields"] == []
    assert named["actor_name"] == "Dimas Aji"
    assert "before" not in named and "after" not in named
    assert set(named) <= SAFE_DTO_KEYS
    assert "actor_role" not in named and "note" not in named
    assert HISTORY_ACTIONS == (
        ACTION_UPDATE,
        ACTION_ASSET_UPLOAD,
        ACTION_ASSET_REMOVE,
        ACTION_BRANCH_UPDATE,
        ACTION_BRANCH_RESET,
        ACTION_BRANCH_ASSET_UPLOAD,
        ACTION_BRANCH_ASSET_REMOVE,
        ACTION_BRANCH_COPY,
    )

    email_only = serialize_history_row(_row(actor_name="", actor_email="owner@example.com"))
    assert email_only["actor_name"] == "owner@example.com"
    missing = serialize_history_row(_row(actor_name="", actor_email=""))
    assert missing["actor_name"] == "-"

    branch = serialize_history_row(
        _row(
            action=ACTION_BRANCH_UPDATE,
            after={
                "branch_id": "50",
                "operation": "update",
                "override": {
                    "company_name": "Eternal Jogja",
                    "invoice": {"bank_accounts": [{"bank_label": "BCA", "account_number": "1", "account_name": "A"}]},
                },
            },
            before={"branch_id": "50", "operation": "update", "override": {"company_name": "Old"}},
        ),
        {"50": "Yogyakarta"},
    )
    assert branch["scope"] == "branch"
    assert branch["branch_id"] == "50"
    assert branch["branch_name"] == "Yogyakarta"
    labels = [item["label"] for item in branch["changed_fields"]]
    assert "Nama perusahaan" in labels
    assert "Rekening bank" in labels

    reset = serialize_history_row(
        _row(
            action=ACTION_BRANCH_RESET,
            before={"branch_id": "50", "operation": "reset", "override": {
                "company_name": "Eternal Jogja",
                "company_logo": {"has_custom": True, "filename": "logo.png"},
            }},
            after={"branch_id": "50", "operation": "reset", "override": {}},
        )
    )
    reset_labels = [item["label"] for item in reset["changed_fields"]]
    assert reset["operation_label"] == "Cabang dikembalikan ke Global"
    assert "Nama perusahaan" in reset_labels
    assert "Logo perusahaan" in reset_labels

    upload = serialize_history_row(_row(action=ACTION_ASSET_UPLOAD, after={"slot": "prepared_signature_image"}))
    assert upload["operation_label"] == "Tanda tangan Prepared diunggah"
    assert upload["asset_slot"] == "prepared_signature_image"
    remove = serialize_history_row(_row(action=ACTION_BRANCH_ASSET_REMOVE, after={"slot": "company_logo", "branch_id": "50"}))
    assert remove["operation_label"] == "Logo perusahaan dihapus"
    assert remove["branch_id"] == "50"

    unknown = serialize_history_row(
        _row(action=ACTION_BRANCH_UPDATE, after={"operation": "update", "override": {}}, before={"operation": "update", "override": {}})
    )
    assert unknown["branch_name"] == "Cabang tidak diketahui"
    assert unknown["branch_id"] is None
    assert unknown["changed_fields"] == []

    copy_ready = serialize_history_row(
        _row(
            action=ACTION_UPDATE,
            after={"source_scope": "global", "target_branch_id": "50", "storage_path": "secret/path", "token": "abc"},
        )
    )
    assert copy_ready.get("source_scope") == "global"
    assert copy_ready.get("target_branch_id") == "50"
    assert "storage_path" not in copy_ready
    assert "token" not in copy_ready
    assert set(copy_ready) <= SAFE_DTO_KEYS


@pytest.mark.anyio
async def test_filters_unknown_branch_empty_and_invalid_inputs():
    logs = [
        _row(id="g", action=ACTION_UPDATE),
        _row(
            id="b50",
            action=ACTION_BRANCH_UPDATE,
            after={"branch_id": "50", "operation": "update", "override": {"company_name": "X"}},
            before={"branch_id": "50", "operation": "update", "override": {}},
        ),
        _row(
            id="b77",
            action=ACTION_BRANCH_UPDATE,
            after={"branch_id": "77", "operation": "update", "override": {"company_name": "Y"}},
            before={"branch_id": "77", "operation": "update", "override": {}},
        ),
    ]
    db = MockDb(logs)
    global_only = await list_print_design_history(db, scope="global")
    assert [row["id"] for row in global_only["items"]] == ["g"]
    branch_only = await list_print_design_history(db, scope="branch")
    assert {row["id"] for row in branch_only["items"]} == {"b50", "b77"}
    only_50 = await list_print_design_history(db, scope="all", branch_id="50")
    assert [row["id"] for row in only_50["items"]] == ["b50"]
    unknown = await list_print_design_history(db, branch_id="99")
    assert unknown["items"] == []
    assert unknown["has_more"] is False

    with pytest.raises(HTTPException) as bad_scope:
        await list_print_design_history(db, scope="payroll")
    assert bad_scope.value.status_code == 400
    with pytest.raises(HTTPException) as nondigit:
        await list_print_design_history(db, branch_id="50a")
    assert nondigit.value.status_code == 400


@pytest.mark.anyio
async def test_name_lookup_batched_and_malformed_row_does_not_crash():
    calls = []

    async def lookup(bid):
        calls.append(bid)
        if bid == "50":
            raise RuntimeError("accurate down")
        return "Semarang"

    logs = [
        _row(
            id="1",
            action=ACTION_BRANCH_ASSET_UPLOAD,
            at="2026-10-06T15:01:00+00:00",
            after={"slot": "approved_signature_image", "branch_id": "50"},
        ),
        _row(
            id="2",
            action=ACTION_BRANCH_ASSET_UPLOAD,
            at="2026-10-06T15:00:00+00:00",
            after={"slot": "company_logo", "branch_id": "50"},
        ),
        _row(
            id="3",
            action=ACTION_BRANCH_UPDATE,
            at="2026-10-06T14:00:00+00:00",
            after={"branch_id": "77", "operation": "update", "override": {"company_name": "Smg"}},
            before={"branch_id": "77", "operation": "update", "override": {}},
        ),
        {"id": "bad", "action": ACTION_UPDATE, "at": "2026-10-06T13:00:00+00:00", "after": "not-a-dict"},
    ]
    db = MockDb(logs)
    result = await list_print_design_history(db, name_lookup=lookup)
    assert calls.count("50") == 1
    by_id = {row["id"]: row for row in result["items"]}
    assert by_id["1"]["branch_name"] == "Cabang 50"
    assert by_id["3"]["branch_name"] == "Semarang"
    assert by_id["bad"]["operation_label"] == "Pengaturan Global diperbarui"
    assert by_id["bad"]["changed_fields"] == []
    assert by_id["1"]["operation_label"] == "Tanda tangan Approved diunggah"


@pytest.mark.anyio
async def test_copy_history_labels_copied_fields_and_batched_names():
    calls = []

    async def lookup(bid):
        calls.append(bid)
        if bid == "50":
            return "Semarang"
        if bid == "77":
            return "Yogyakarta"
        return None

    logs = [
        _row(
            id="c1",
            action=ACTION_BRANCH_COPY,
            at="2026-10-06T16:00:00+00:00",
            after={
                "branch_id": "77",
                "operation": "copy",
                "source_scope": "global",
                "source_branch_id": None,
                "target_branch_id": "77",
                "mode": "replace",
                "copied_fields": ["company_name", "bank_accounts", "company_logo"],
                "override": {"company_name": "Eternal Global"},
            },
            before={"branch_id": "77", "operation": "copy", "override": {}},
        ),
        _row(
            id="c2",
            action=ACTION_BRANCH_COPY,
            at="2026-10-06T15:00:00+00:00",
            after={
                "branch_id": "77",
                "operation": "copy",
                "source_scope": "branch",
                "source_branch_id": "50",
                "target_branch_id": "77",
                "mode": "replace",
                "copied_fields": ["prepared_signature_image"],
                "override": {},
            },
        ),
    ]
    db = MockDb(logs)
    result = await list_print_design_history(db, name_lookup=lookup)
    assert calls.count("50") == 1
    assert calls.count("77") == 1
    by_id = {row["id"]: row for row in result["items"]}
    assert by_id["c1"]["operation_label"] == "Pengaturan Global disalin ke Cabang Yogyakarta"
    assert by_id["c2"]["operation_label"] == "Pengaturan Cabang Semarang disalin ke Cabang Yogyakarta"
    labels = [item["label"] for item in by_id["c1"]["changed_fields"]]
    assert "Nama perusahaan" in labels
    assert "Rekening bank" in labels
    assert "Logo perusahaan" in labels
    assert "storage_path" not in str(result)
    assert by_id["c1"]["source_scope"] == "global"
    assert by_id["c2"]["source_branch_id"] == "50"
    assert ACTION_BRANCH_COPY in HISTORY_ACTIONS


@pytest.mark.anyio
async def test_page_number_is_bounded():
    db = MockDb([_row(id="a", action=ACTION_UPDATE)])
    first = await list_print_design_history(db, page=1, page_size=20)
    assert first["page"] == 1
    assert first["page_size"] == 20
    assert [row["id"] for row in first["items"]] == ["a"]
    top = await list_print_design_history(db, page=500, page_size=20)
    assert top["page"] == 500
    assert top["page_size"] == 20
    assert top["items"] == []
    capped = await list_print_design_history(db, page=1, page_size=999)
    assert capped["page_size"] == 50
    with pytest.raises(HTTPException) as zero:
        await list_print_design_history(db, page=0)
    assert zero.value.status_code == 400
    with pytest.raises(HTTPException) as negative:
        await list_print_design_history(db, page=-1)
    assert negative.value.status_code == 400
    with pytest.raises(HTTPException) as over:
        await list_print_design_history(db, page=501)
    assert over.value.status_code == 400


def test_startup_declares_audit_action_at_index():
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    assert 'await db.audit_logs.create_index("at")' in server
    assert 'await db.audit_logs.create_index("actor_email")' in server
    assert 'await db.audit_logs.create_index("target_email")' in server
    assert 'await db.audit_logs.create_index([("action", 1), ("at", -1)], name="audit_action_at")' in server
    assert "drop_index" not in server[server.index("audit_logs.create_index"):server.index("audit_action_at") + 40]


def test_history_route_permission_and_preview_has_no_audit():
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    preview = (ROOT / "services" / "print_design_preview.py").read_text(encoding="utf-8")
    idx = server.index("async def design_cetakan_history")
    nxt = server.find("\nasync def ", idx + 10)
    block = server[idx:nxt]
    assert "_design_cetakan_view_perms()" in block
    assert "audit.view" not in block
    helper = server[server.index("def _design_cetakan_view_perms"):server.index("def _design_cetakan_view_perms") + 220]
    assert "settings.design_cetakan.view" in helper
    assert '@api_router.get("/settings/design-cetakan/history")' in server
    preview_idx = server.index("async def design_cetakan_preview")
    preview_block = server[preview_idx:server.find("\nasync def ", preview_idx + 10)]
    assert "_write_audit" not in preview_block
    assert "_write_audit" not in preview
    history_src = (ROOT / "services" / "print_design_history.py").read_text(encoding="utf-8")
    assert "create_index" not in history_src
    assert "$regex" not in history_src
    assert "action.startswith" not in history_src
    assert "startsWith" not in history_src
    assert '"action": {"$in": list(actions)}' in history_src
    copy_idx = server.index("async def design_cetakan_copy")
    copy_block = server[copy_idx:server.find("\nasync def ", copy_idx + 10)]
    assert "settings.design_cetakan.manage" in copy_block
    assert "audit.view" not in copy_block
    assert copy_block.count("_write_audit") == 1
    assert "settings.design_cetakan.branch_copy" in copy_block
    assert "branch_update" not in copy_block
    assert "asset_upload" not in copy_block
