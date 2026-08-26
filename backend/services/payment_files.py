"""Payment proof file cap. Existing files are never overwritten to make room."""
from __future__ import annotations

from typing import Optional, Sequence

MAX_PAYMENT_PROOFS = 5


def payment_proof_reject_reason(existing: Optional[Sequence]) -> Optional[str]:
    n = len(existing or [])
    if n >= MAX_PAYMENT_PROOFS:
        return (
            f"Maksimum {MAX_PAYMENT_PROOFS} file bukti payment per PO. "
            "File yang sudah diunggah tidak diubah."
        )
    return None
