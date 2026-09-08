export const INITIAL_SIGNATURE_MODAL = { open: false, role: null, draft: null };

export function signatureModalReducer(state, action) {
  switch (action?.type) {
    case "open":
      if (action.readOnly) return state;
      return { open: true, role: action.role, draft: null };
    case "stroke":
      return { ...state, draft: action.dataUrl || null };
    case "clear":
      return { ...state, draft: null };
    case "cancel":
      return INITIAL_SIGNATURE_MODAL;
    case "save":
      return INITIAL_SIGNATURE_MODAL;
    default:
      return state;
  }
}

export function applyLocalSignature(local, role, dataUrl) {
  const next = { ...(local || {}) };
  if (role === "recipient") next.recipient = dataUrl || null;
  if (role === "driver") next.driver = dataUrl || null;
  return next;
}

export function signaturePreviewState(dataUrl, savedSig, delivered) {
  const saved = !!(savedSig && (savedSig.signature_storage_path || savedSig.recipient_signed_at || savedSig.driver_signed_at || savedSig.driver_signature_storage_path));
  const local = !!(dataUrl && String(dataUrl).startsWith("data:image/"));
  return {
    saved: saved || local,
    readOnly: !!delivered,
    previewUrl: dataUrl || null,
  };
}
