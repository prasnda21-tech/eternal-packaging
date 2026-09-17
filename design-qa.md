# Design QA — Ubah Item PO

## Result

PASSED

## Reference

- Production reference: existing `Ganti Barang` modal supplied by the user.
- Target: preserve the existing Eternal Packaging visual language while combining product and quantity editing into one atomic flow.

## Verified

- Desktop modal remains centered, compact, and consistent with the PO detail screen.
- Current product, code, and quantity are visible before editing.
- Replacement product is optional, so quantity-only edits are supported.
- Positive whole-number quantity validation is enforced in UI and API.
- The “Setelah Disimpan” panel updates immediately when quantity changes.
- Save stays disabled for an unchanged product and unchanged quantity.
- Product-only, quantity-only, and combined changes use one explicit save action.
- Responsive rules collapse the current-item grid and make both footer actions full-width on mobile.
- Focused component tests pass and the optimized frontend build succeeds.
- Browser interaction verified quantity `1.000` → `1.200`; summary updated and Save became enabled.
- No application-origin browser console errors were observed. The only logged errors came from the browser automation extension.

## Safety notes

- The backend rechecks line eligibility immediately before persistence.
- Existing downstream locks for samples, Surat Jalan, Loading, and Delivery still apply.
- Product and quantity are written together under the order optimistic lock.
- `total_quantity` is recalculated from all current line items.
