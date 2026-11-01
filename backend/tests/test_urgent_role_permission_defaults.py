"""Owner policy: Urgent permission defaults for Direktur / Holding / HoP."""

from permissions import (
    ALL_PERMISSIONS,
    DIRECTOR_MUST_OPT_IN_PERMISSIONS,
    OPT_IN_ONLY_PERMISSIONS,
    PERMISSION_CATALOG,
    ROLE_DEFAULT_PERMISSIONS,
    director_full_permission_set,
    effective_permissions,
    merge_director_must_opt_in,
    opt_in_permission_granted,
)

MONITOR = "dashboard.production.urgent_requests.view"
REVIEW = "urgent.review"
APPROVE = "urgent.approve"
REQUEST = "urgent.request"


def _label(key: str) -> str:
    for _mod, perm, label in PERMISSION_CATALOG:
        if perm == key:
            return label
    raise AssertionError(f"missing catalog key {key}")


def test_catalog_labels_indonesian_owner_copy():
    assert _label(MONITOR) == "Lihat Pengajuan Urgent Dashboard"
    assert _label(REVIEW) == "Review Pengajuan Urgent"
    assert _label(APPROVE) == "Setujui / Tolak Pengajuan Urgent"
    assert MONITOR in ALL_PERMISSIONS
    assert REVIEW in OPT_IN_ONLY_PERMISSIONS
    assert APPROVE in OPT_IN_ONLY_PERMISSIONS
    assert MONITOR not in OPT_IN_ONLY_PERMISSIONS


def test_head_production_monitor_only():
    hop = set(ROLE_DEFAULT_PERMISSIONS["head_production"])
    assert MONITOR in hop
    assert REVIEW not in hop
    assert APPROVE not in hop
    assert REQUEST not in hop
    assert REVIEW not in effective_permissions("head_production", None)
    assert APPROVE not in effective_permissions("head_production", None)


def test_holding_and_direktur_builtin_defaults():
    holding = set(ROLE_DEFAULT_PERMISSIONS["holding_team"])
    direktur = set(ROLE_DEFAULT_PERMISSIONS["direktur_utama"])
    for role_perms in (holding, direktur):
        assert MONITOR in role_perms
        assert REVIEW in role_perms
        assert APPROVE in role_perms
        assert REQUEST not in role_perms

    assert REVIEW in effective_permissions("holding_team", None)
    assert APPROVE in effective_permissions("holding_team", None)
    assert MONITOR in effective_permissions("holding_team", None)

    assert opt_in_permission_granted("holding_team", None, REVIEW) is True
    assert opt_in_permission_granted("holding_team", None, APPROVE) is True
    assert opt_in_permission_granted("head_production", None, REVIEW) is False


def test_direktur_merge_respects_builtin_and_overrides():
    assert REVIEW in DIRECTOR_MUST_OPT_IN_PERMISSIONS
    assert APPROVE in DIRECTOR_MUST_OPT_IN_PERMISSIONS
    full = director_full_permission_set()
    assert REVIEW not in full
    assert APPROVE not in full

    merged = merge_director_must_opt_in(full, "direktur_utama", None, None)
    assert REVIEW in merged
    assert APPROVE in merged
    assert REQUEST not in merged
    assert MONITOR in merged

    denied = merge_director_must_opt_in(
        full, "direktur_utama", {REVIEW: False, APPROVE: False}, None
    )
    assert REVIEW not in denied
    assert APPROVE not in denied

    # Stored role_permissions without review/approve must not inherit builtins.
    customized = merge_director_must_opt_in(
        full,
        "direktur_utama",
        None,
        {"direktur_utama": [MONITOR, "dashboard.owner.view"]},
    )
    assert MONITOR in customized
    assert REVIEW not in customized
    assert APPROVE not in customized


def test_holding_explicit_override_and_stored_role_doc():
    assert REVIEW not in effective_permissions("holding_team", {REVIEW: False})
    assert APPROVE in effective_permissions("holding_team", {APPROVE: True, REVIEW: False})

    stored = {"holding_team": [MONITOR]}
    eff = effective_permissions("holding_team", None, stored)
    assert MONITOR in eff
    assert REVIEW not in eff
    assert APPROVE not in eff
    assert opt_in_permission_granted("holding_team", None, REVIEW, stored) is False
