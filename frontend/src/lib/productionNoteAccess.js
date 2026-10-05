/** Visibility and append rules for Catatan Produksi on Detail PO.

Legacy Detail PO viewers still see the panel on Order Masuk, and non-CRM
roles still append, even when a stored role document lacks the new keys.
`production_note.view` adds the same panel on every desktop workflow stage.
`production_note.create` adds append for role `crm`.
*/

export function productionNotesVisible(onOrderMasuk, hasViewPermission) {
  return !!onOrderMasuk || !!hasViewPermission;
}

export function canAppendProductionNote(role, hasCreatePermission) {
  return role !== "crm" || !!hasCreatePermission;
}
