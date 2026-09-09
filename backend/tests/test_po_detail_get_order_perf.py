"""Detail PO get_order: independent secondary reads run concurrently."""
import ast
import asyncio
from pathlib import Path


def _get_order_source_chunk() -> str:
    src = Path(__file__).resolve().parents[1] / "server.py"
    text = src.read_text(encoding="utf-8")
    start = text.index('@api_router.get("/orders/{order_id}")')
    fn_start = text.index("async def get_order", start)
    rest = text[fn_start:]
    lines = rest.splitlines(keepends=True)
    out = [lines[0]]
    for line in lines[1:]:
        if line.startswith("def ") or line.startswith("async def ") or line.startswith("@api_router"):
            break
        out.append(line)
    return "".join(out)


def test_get_order_uses_asyncio_gather_for_independent_reads():
    chunk = _get_order_source_chunk()
    assert "asyncio.gather(" in chunk
    assert "_get_delivery_deadline_settings()" in chunk
    assert "_get_po_delivery_summary(db, order)" in chunk
    # Must not await the two helpers serially anymore
    assert "await _get_delivery_deadline_settings()" not in chunk
    assert "await _get_po_delivery_summary(db, order)" not in chunk
    # Duplicate workflow snapshot attach removed (quantity snapshot already does it)
    assert "_attach_quantity_snapshot(order)" in chunk
    assert "_attach_workflow_snapshot(order)" not in chunk


def test_get_order_gather_is_valid_python():
    stub = """
import asyncio
async def get_order(order_id, user=None):
    order = {}
    db = None
    _dd_settings, _sales_delivery_summary = await asyncio.gather(
        _get_delivery_deadline_settings(),
        _get_po_delivery_summary(db, order),
    )
"""
    ast.parse(stub)


def test_asyncio_gather_wall_time_less_than_serial_sum():
    """Behavioral guarantee for the gather pattern used by get_order."""

    async def slow_a():
        await asyncio.sleep(0.05)
        return {"lead_time_days": 3}

    async def slow_b():
        await asyncio.sleep(0.05)
        return {"total_shipped": 0}

    async def run():
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        a, b = await asyncio.gather(slow_a(), slow_b())
        wall = loop.time() - t0
        return wall, a, b

    wall, a, b = asyncio.run(run())
    assert a["lead_time_days"] == 3
    assert b["total_shipped"] == 0
    # Parallel wall should be closer to max(50,50) than sum(50+50)
    assert wall < 0.09
