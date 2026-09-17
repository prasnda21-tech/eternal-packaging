"""Regression test for parallel PO line eligibility evaluation."""
from __future__ import annotations

import asyncio

import pytest

import services.line_product_replacement as replacement_mod


@pytest.mark.anyio
async def test_all_line_eligibility_checks_run_concurrently_and_keep_order(monkeypatch):
    active = 0
    peak = 0

    async def fake_evaluate(_db, _order, line_idx):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return {"line_idx": line_idx}

    monkeypatch.setattr(
        replacement_mod,
        "evaluate_line_product_replacement",
        fake_evaluate,
    )
    result = await replacement_mod.evaluate_all_line_product_replacements(
        object(),
        {"line_items": [{}, {}, {}]},
    )

    assert peak == 3
    assert [row["line_idx"] for row in result] == [0, 1, 2]
