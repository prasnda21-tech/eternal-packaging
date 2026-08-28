/** Penjadwalan draft persistence — keep item/operator selections across sequential Set. */

export function mapPersistedSplits(splits) {
  return (splits || []).map((s) => ({
    operator_email: s.operator_email || "",
    operator_name: s.operator_name || "",
    quantity: s.quantity,
    id: s.id,
    stage: s.stage,
    line_idx: s.line_idx ?? "",
    work_date: s.work_date || "",
    qty_completed: Number(s.qty_completed || 0),
    qty_cancelled: Number(s.qty_cancelled || 0),
  }));
}

export function splitsFingerprint(splits) {
  return JSON.stringify(
    (splits || []).map((s) => ({
      id: s.id || "",
      operator_email: s.operator_email || "",
      line_idx: s.line_idx ?? "",
      quantity: Number(s.quantity || 0),
      work_date: s.work_date || "",
      qty_completed: Number(s.qty_completed || 0),
    })),
  );
}

export function patchScheduleRow(splits, index, patch) {
  return (splits || []).map((row, i) => (i === index ? { ...row, ...patch } : row));
}

export function shouldApplyRemoteSplits({
  dirty,
  incomingFingerprint,
  savedFingerprint,
  incomingUpdatedAt,
  savedUpdatedAt,
}) {
  if (dirty) return false;
  if (incomingFingerprint && incomingFingerprint === savedFingerprint) return false;
  if (savedUpdatedAt && incomingUpdatedAt && String(incomingUpdatedAt) < String(savedUpdatedAt)) {
    return false;
  }
  return true;
}

export function splitsAfterMutation(ok, localSplits, remoteSplits) {
  if (!ok) return localSplits;
  return mapPersistedSplits(remoteSplits);
}
