"""Explicit available-to-sell baseline and validated post-cutoff event projection.

Pure implementation: no database writes, physical balance changes, or application startup.
The caller must supply a complete, warehouse-scoped, canonical event stream.
This module alone is NOT a production event integration or activation mechanism.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable


class CutoverError(ValueError):
    pass


def quantity(value):
    if isinstance(value, bool) or value is None:
        raise CutoverError('Invalid quantity')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CutoverError('Invalid quantity') from None
    if not result.is_finite():
        raise CutoverError('Invalid quantity')
    return result


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        raise CutoverError('Cutoff/event time must be an ISO timestamp') from None
    if result.utcoffset() is None:
        raise CutoverError('Timezone required')
    return result


@dataclass(frozen=True)
class Baseline:
    batch_id: str
    warehouse_id: str
    master_item_id: str
    unit: str
    available_qty: Decimal
    cutoff_at: str
    source_sha256: str

    def validate(self):
        if not all((self.batch_id, self.warehouse_id, self.master_item_id, self.unit)):
            raise CutoverError('Missing baseline identity')
        if len(self.source_sha256) != 64 or any(c not in '0123456789abcdef' for c in self.source_sha256):
            raise CutoverError('Source SHA256 required')
        if quantity(self.available_qty) < 0:
            raise CutoverError('Baseline must be nonnegative')
        return timestamp(self.cutoff_at)


@dataclass(frozen=True)
class Reservation:
    key: str  # stable order + physical line identity, never item name
    remaining_qty: Decimal


@dataclass(frozen=True)
class Event:
    event_id: str
    warehouse_id: str
    master_item_id: str
    unit: str
    occurred_at: str
    sequence: int
    kind: str
    qty: Decimal
    reservation_key: str = ''
    reverses_event_id: str = ''


def project_available(baseline: Baseline, *, reservations_at_cutoff: Iterable[Reservation],
                      events: Iterable[Event], evidence_complete: bool, as_of: str):
    """Project an explicitly scoped ATS stream, fail closed on missing evidence.

    RESERVE reduces free stock. SHIP_RESERVED consumes that reservation without
    reducing free stock again. RELEASE only restores remaining reserved quantity.
    RECEIVE_FREE / ISSUE_FREE apply solely to validated unreserved goods.
    REVERSE references a known event; replay validates dependent reservations.
    Imported/synced old documents must retain their original business event time.
    """
    cutoff = baseline.validate()
    end = timestamp(as_of)
    if end < cutoff:
        raise CutoverError('As-of precedes cutoff')
    if not evidence_complete:
        raise CutoverError('Event coverage not verified; ATS must remain unavailable')
    remaining = {}
    for reservation in reservations_at_cutoff:
        amount = quantity(reservation.remaining_qty)
        if not reservation.key or reservation.key in remaining or amount < 0:
            raise CutoverError('Invalid/duplicate opening reservation')
        remaining[reservation.key] = amount
    unique = {}
    positions = set()
    for event in events:
        if (event.warehouse_id, event.master_item_id, event.unit) != (
                baseline.warehouse_id, baseline.master_item_id, baseline.unit):
            raise CutoverError('Foreign warehouse/item/unit event')
        if not event.event_id:
            raise CutoverError('Event identity required')
        if event.event_id in unique:
            if unique[event.event_id] != event:
                raise CutoverError('Idempotency conflict')
            continue
        if isinstance(event.sequence, bool) or not isinstance(event.sequence, int) or event.sequence < 0:
            raise CutoverError('Invalid canonical event sequence')
        if event.sequence in positions:
            raise CutoverError('Duplicate canonical event sequence')
        positions.add(event.sequence)
        unique[event.event_id] = event
    effective = sorted((e for e in unique.values() if cutoff < timestamp(e.occurred_at) <= end),
                       key=lambda e: (timestamp(e.occurred_at), e.sequence))
    reversed_ids = set()
    prior = {}
    for event in effective:
        if event.kind == 'REVERSE':
            original = prior.get(event.reverses_event_id)
            if (not original or original.kind == 'REVERSE' or original.event_id in reversed_ids
                    or quantity(event.qty) != 0):
                raise CutoverError('Invalid/duplicate reversal; opening-history reversal requires review')
            reversed_ids.add(original.event_id)
        prior[event.event_id] = event
    available = quantity(baseline.available_qty)
    effects = []
    for event in effective:
        if event.kind == 'REVERSE' or event.event_id in reversed_ids:
            continue
        qty = quantity(event.qty)
        if qty <= 0:
            raise CutoverError('Event quantity must be positive')
        effect = Decimal(0)
        if event.kind == 'RESERVE':
            if not event.reservation_key:
                raise CutoverError('Stable reservation identity required')
            remaining[event.reservation_key] = remaining.get(event.reservation_key, Decimal(0)) + qty
            effect = -qty
        elif event.kind in ('SHIP_RESERVED', 'RELEASE'):
            if not event.reservation_key or qty > remaining.get(event.reservation_key, Decimal(0)):
                raise CutoverError('Reservation missing or insufficient; refuse inferred free-stock issue')
            remaining[event.reservation_key] -= qty
            if event.kind == 'RELEASE':
                effect = qty
        elif event.kind == 'RECEIVE_FREE':
            effect = qty
        elif event.kind == 'ISSUE_FREE':
            effect = -qty
        else:
            raise CutoverError('Unclassified movement; explicit event mapping required')
        available += effect
        effects.append({'event_id': event.event_id, 'effect': str(effect)})
    return {'available_qty': str(available), 'remaining_reservations': {k:str(v) for k,v in remaining.items()},
            'effects': effects, 'source': 'ACCURATE_ATS_CUTOVER', 'cutoff_at': baseline.cutoff_at,
            'as_of': as_of, 'physical_stock_verified': False}
