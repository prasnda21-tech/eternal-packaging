import { useEffect, useReducer, useState } from "react";
import SignaturePad from "../../components/SignaturePad";
import { INITIAL_SIGNATURE_MODAL, signatureModalReducer } from "../../lib/mobileSignature";

export default function MobileSignatureModal({
  open,
  role,
  readOnly,
  title,
  context,
  saveLabel = "Simpan Tanda Tangan",
  children,
  onCancel,
  onSave,
}) {
  const [state, dispatch] = useReducer(signatureModalReducer, INITIAL_SIGNATURE_MODAL);
  const [padKey, setPadKey] = useState(0);
  useEffect(() => {
    if (open) {
      setPadKey((k) => k + 1);
      dispatch({ type: "open", role, readOnly });
    } else {
      dispatch({ type: "cancel" });
    }
  }, [open, role, readOnly]);

  if (!open || readOnly) return null;
  const resolvedTitle = title
    || (role === "driver" ? "Tanda Tangan Pengirim" : "Tanda Tangan Penerima");
  return (
    <div className="mop-sig-modal" role="dialog" aria-modal="true" aria-label={resolvedTitle} data-testid="mobile-signature-modal">
      <h2>{resolvedTitle}</h2>
      {context ? <p className="mop-sig-context" data-testid="mobile-sig-context">{context}</p> : null}
      {children}
      <div className="mop-sig-canvas-wrap">
        <SignaturePad
          key={padKey}
          height={280}
          onChange={(dataUrl) => dispatch({ type: "stroke", dataUrl })}
        />
      </div>
      <div className="mop-sig-actions">
        <button type="button" onClick={() => { setPadKey((k) => k + 1); dispatch({ type: "clear" }); }} data-testid="mobile-sig-clear">Hapus</button>
        <button type="button" onClick={onCancel} data-testid="mobile-sig-cancel">Batal</button>
        <button
          type="button"
          className="mop-btn-primary"
          style={{ marginTop: 0 }}
          disabled={!state.draft}
          onClick={() => onSave(state.draft)}
          data-testid="mobile-sig-save"
        >
          {saveLabel}
        </button>
      </div>
    </div>
  );
}
