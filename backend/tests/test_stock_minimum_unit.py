"""Stock minimum thresholds — null ≠ 0; evaluate statuses."""
from services.stock_minimum import evaluate_minimum_status, PERM_MANAGE, PERM_VIEW


def test_permissions_are_separate():
    assert PERM_MANAGE == "stock_minimum.manage"
    assert PERM_VIEW == "current_stock.view"
    assert PERM_MANAGE != "current_stock.update"


def test_unset_vs_zero():
    unset = evaluate_minimum_status(
        stock_dapat_dijual=5,
        sellable_status="READY",
        minimum_qty=None,
        minimum_configured=False,
    )
    assert unset["status"] == "MINIMUM_UNSET"
    assert unset["is_violation"] is False

    zero = evaluate_minimum_status(
        stock_dapat_dijual=0,
        sellable_status="READY",
        minimum_qty=0,
        minimum_configured=True,
    )
    assert zero["is_violation"] is True
    assert zero["status"] == "BELOW_OR_AT_MINIMUM"


def test_at_and_below_and_safe_and_negative():
    at = evaluate_minimum_status(
        stock_dapat_dijual=10,
        sellable_status="READY",
        minimum_qty=10,
        minimum_configured=True,
    )
    assert at["is_violation"] is True

    below = evaluate_minimum_status(
        stock_dapat_dijual=3,
        sellable_status="READY",
        minimum_qty=10,
        minimum_configured=True,
    )
    assert below["is_violation"] is True

    safe = evaluate_minimum_status(
        stock_dapat_dijual=12,
        sellable_status="READY",
        minimum_qty=10,
        minimum_configured=True,
    )
    assert safe["is_violation"] is False
    assert safe["status"] == "OK"

    neg = evaluate_minimum_status(
        stock_dapat_dijual=-5,
        sellable_status="READY",
        minimum_qty=0,
        minimum_configured=True,
    )
    assert neg["is_violation"] is True


def test_incomplete_source_never_safe():
    slot = evaluate_minimum_status(
        stock_dapat_dijual=100,
        sellable_status="INCOMPLETE",
        minimum_qty=1,
        minimum_configured=True,
    )
    assert slot["status"] == "SOURCE_INCOMPLETE"
    assert slot["is_violation"] is False
    assert slot["evaluable"] is False
